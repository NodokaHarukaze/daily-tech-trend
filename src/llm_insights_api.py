import json
import os
import re
import subprocess
import time

import requests

from llm_insights_pipeline import NEWS_FALLBACK_KEY_POINT

# ============================================================
# [接続先ノード] 2026-08-23 変更
# 経緯: 既定の 127.0.0.1 は PC2 を指すが、PC2 は RDP ホストを兼ねている。
#       14.65GB のモデル(second_constantine/gpt-oss-u:20b)が VRAM 16.3GB に
#       収まらず全量 CPU 実行へ落ち、ロード失敗→再投入のループで
#       CPU 1コア占有＋コミット 18.5GB となり RDP の操作が破綻した。
#       （2026-08-04 の「RAM 28.8GB を食い潰して14時間フリーズ」と同根）
#       PC3 (192.168.11.57) は VRAM 48GB で同じモデルを余裕で載せられるため、
#       run_daily.bat から OLLAMA_BASE_URL で PC3 を指すようにした。
# 戻し方: 環境変数 OLLAMA_BASE_URL を未設定にすればローカル(PC2)に戻る。
# ============================================================
OLLAMA_BASE = (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
OLLAMA_URL = f"{OLLAMA_BASE}/v1/chat/completions"
DEFAULT_MODEL = "gpt-oss:20b"
_SESSION = requests.Session()

# --- タイムアウト/リトライ戦略の統一設定 -----------------------------------
# 既存の呼び出し箇所で使われていたマジックナンバーを定数化し、環境変数で上書き可能にする。
# 原則: 長文生成=LONG(120s)、短文生成=SHORT(60s)、ヘルスチェック=HEALTH(4s)
LLM_LONG_TIMEOUT_SEC = int(os.getenv("LLM_LONG_TIMEOUT_SEC", "120"))
LLM_SHORT_TIMEOUT_SEC = int(os.getenv("LLM_SHORT_TIMEOUT_SEC", "60"))
LLM_HEALTH_TIMEOUT_SEC = int(os.getenv("LLM_HEALTH_TIMEOUT_SEC", "4"))
# RAMスラッシング中はアンロード要求自体が10秒では失敗しうるため、他より長めに個別設定可能にする
LLM_UNLOAD_TIMEOUT_SEC = int(os.getenv("LLM_UNLOAD_TIMEOUT_SEC", "30"))
# リトライ戦略: 指数バックオフ（base=1.5）で 3 回まで
LLM_RETRY_COUNT = int(os.getenv("LLM_RETRY_COUNT", "2"))
LLM_RETRY_BASE_SEC = float(os.getenv("LLM_RETRY_BASE_SEC", "1.0"))
LLM_RETRY_EXP_BASE = float(os.getenv("LLM_RETRY_EXP_BASE", "2.0"))
_OLLAMA_READY = False
_AUTOSTART_ATTEMPTED = False
_MODEL_PREPARED = False
_SELECTED_MODEL = None
_FAILED_MODELS = set()
_LOAD_ATTEMPTED_MODELS = set()
# reasoning_effort を受け付けないと判明したモデル。
# Ollama は thinking 非対応モデルに reasoning_effort を送ると
# 400 '"<model>" does not support thinking' を返すため、
# 一度学習したらそのモデルには送らない（_FAILED_MODELS には落とさない）。
_NO_THINKING_MODELS = set()


def _model_settings() -> dict:
    primary = (os.getenv("OLLAMA_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    exclude = {
        m.strip() for m in (os.getenv("OLLAMA_EXCLUDE_MODELS") or "").split(",") if m.strip()
    }
    return {
        "primary": primary,
        "fallback": (os.getenv("OLLAMA_FALLBACK_MODEL") or "").strip(),
        "exclude": exclude,
    }


def _is_ollama_ready(timeout: float = 2.0) -> bool:
    health_url = OLLAMA_URL.rsplit("/chat/completions", 1)[0] + "/models"
    try:
        r = _SESSION.get(health_url, timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def _models_url() -> str:
    return OLLAMA_URL.rsplit("/chat/completions", 1)[0] + "/models"


def _extract_model_ids(data: dict) -> list[str]:
    models = data.get("data") or []
    ids = []
    for item in models:
        if isinstance(item, dict):
            mid = (item.get("id") or "").strip()
            if mid:
                ids.append(mid)
    return ids


# 埋め込み専用モデルの名前パターン（チャット完了に投げると 400 で必ず失敗する）。
# `_pick_model_candidates` でフォールバック候補から除外するために使う。
_EMBEDDING_MODEL_RE = re.compile(r"(?:^|[/_-])(?:bge|nomic-embed|embed(?:ding)?|e5|gte)\b",
                                  re.IGNORECASE)


def _is_embedding_model(name: str) -> bool:
    """名前から埋め込み専用モデルかをヒューリスティックに判定する。

    Ollama の /v1/models 応答にはチャット可否のメタが含まれないため、
    名前パターンで識別する。誤判定があった場合は OLLAMA_MODEL / OLLAMA_FALLBACK_MODEL に
    明示指定することで上書きできる（pinned_model は除外フィルタを通さない）。
    """
    if not name:
        return False
    return bool(_EMBEDDING_MODEL_RE.search(name))


def _available_models(timeout: float = 4.0) -> list[str]:
    r = _SESSION.get(_models_url(), timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}")
    return _extract_model_ids(r.json())


def _pick_model_candidates(timeout: float = 4.0) -> list[str]:
    cfg = _model_settings()
    requested = cfg["primary"]
    fallback = cfg["fallback"]
    exclude = cfg["exclude"]

    try:
        model_ids = _available_models(timeout=timeout)
    except Exception:
        model_ids = []

    candidates = []

    def _add(mid: str, *, skip_embed_filter: bool = False):
        if not mid or mid in candidates or mid in _FAILED_MODELS:
            return
        # 自動収集の候補からは埋め込みモデル・OLLAMA_EXCLUDE_MODELS指定モデルを除外。
        # ユーザー明示指定（requested / fallback / _SELECTED_MODEL）は尊重して通す。
        if not skip_embed_filter and (_is_embedding_model(mid) or mid in exclude):
            return
        candidates.append(mid)

    # 前回成功モデル・明示指定モデルは埋め込みフィルタを通さず尊重する
    _add(_SELECTED_MODEL or "", skip_embed_filter=True)

    if model_ids:
        if requested in model_ids:
            _add(requested, skip_embed_filter=True)
        if fallback and fallback in model_ids:
            _add(fallback, skip_embed_filter=True)
        for mid in model_ids:
            _add(mid)  # 埋め込みはここで弾かれる
        _add(requested, skip_embed_filter=True)
        _add(fallback, skip_embed_filter=True)
    else:
        _add(requested, skip_embed_filter=True)
        _add(fallback, skip_embed_filter=True)

    if not candidates:
        candidates = [requested]

    return candidates


def _pick_usable_model(timeout: float = 4.0) -> str:
    return _pick_model_candidates(timeout=timeout)[0]


def _ensure_ollama_ready() -> None:
    global _OLLAMA_READY, _AUTOSTART_ATTEMPTED
    if _OLLAMA_READY:
        return
    if _is_ollama_ready():
        _OLLAMA_READY = True
        return

    autostart_cmd = (os.getenv("OLLAMA_AUTOSTART_CMD") or "ollama serve").strip()
    if _AUTOSTART_ATTEMPTED:
        raise RuntimeError("Ollama auto-start was attempted but the API is still unavailable.")

    _AUTOSTART_ATTEMPTED = True
    subprocess.Popen(
        autostart_cmd,
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    wait_sec = int(os.getenv("OLLAMA_AUTOSTART_WAIT_SEC", "30"))
    for _ in range(max(wait_sec, 1)):
        if _is_ollama_ready():
            _OLLAMA_READY = True
            return
        time.sleep(1)
    raise RuntimeError(
        f"Ollama is not reachable at {OLLAMA_BASE}. "
        "Start Ollama manually or check OLLAMA_AUTOSTART_CMD / OLLAMA_BASE_URL."
    )


def _get_running_models(timeout: float = 4.0) -> list[str]:
    """Ollama に現在ロードされているモデル一覧を返す"""
    try:
        r = _SESSION.get(f"{OLLAMA_BASE}/api/ps", timeout=timeout)
        if r.status_code >= 400:
            return []
        data = r.json()
        return [m["name"] for m in (data.get("models") or []) if "name" in m]
    except Exception:
        return []


def _unload_model(model: str, timeout: float = LLM_UNLOAD_TIMEOUT_SEC) -> None:
    """指定モデルをOllamaからアンロードする"""
    try:
        _SESSION.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=timeout,
        )
        print(f"[INFO] モデル '{model}' をアンロードしました")
    except Exception as e:
        print(f"[WARN] モデル '{model}' のアンロードに失敗: {e}")


def _load_model(model: str, timeout: float = 120.0) -> None:
    """指定モデルをOllamaにプリロードする"""
    try:
        _SESSION.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": model, "keep_alive": "10m"},
            timeout=timeout,
        )
        print(f"[INFO] モデル '{model}' をロードしました")
    except Exception as e:
        print(f"[WARN] モデル '{model}' のロードに失敗: {e}")


def _ensure_model_prepared() -> None:
    """対象モデル以外をアンロードし、対象モデルがロードされていなければロードする"""
    global _MODEL_PREPARED
    if _MODEL_PREPARED:
        return

    cfg = _model_settings()
    target = cfg["primary"]

    # 現在ロード中のモデルを取得
    running = _get_running_models()
    print(f"[INFO] 現在ロード中のモデル: {running or '(なし)'}")

    # 対象モデル以外を全てアンロード
    for model in running:
        if model != target:
            _unload_model(model)

    # 対象モデルがロードされていなければロード
    if target not in running:
        print(f"[INFO] モデル '{target}' をロード中...")
        _load_model(target)

    _MODEL_PREPARED = True


def _get_lm_content(resp: requests.Response) -> str:
    try:
        data = resp.json()
    except Exception as e:
        raise RuntimeError(f"Ollama returned non-JSON: {resp.text[:500]}") from e

    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:
        raise RuntimeError(f"Ollama unexpected schema: {str(data)[:500]}") from e


def post_ollama(
    payload: dict,
    timeout: int | None = None,
    retries: int | None = None,
    backoff_sec: float | None = None,
):
    """Ollama にチャット補完を POST する。

    timeout: None の場合は LLM_LONG_TIMEOUT_SEC（既定 120 秒）
    retries: None の場合は LLM_RETRY_COUNT（既定 2、合計 3 回試行）
    backoff_sec: None の場合、指数バックオフ（LLM_RETRY_BASE_SEC * LLM_RETRY_EXP_BASE**i）
                 明示指定された場合はその値を倍率として使う
    """
    global _SELECTED_MODEL

    eff_timeout = LLM_LONG_TIMEOUT_SEC if timeout is None else int(timeout)
    eff_retries = LLM_RETRY_COUNT if retries is None else int(retries)

    _ensure_ollama_ready()
    _ensure_model_prepared()
    body = dict(payload)
    last_err = None

    # 呼び出し元がmodelを明示指定している場合はそのモデルを優先
    pinned_model = payload.get("model", "").strip()

    for i in range(eff_retries + 1):
        if pinned_model:
            others = [m for m in _pick_model_candidates() if m != pinned_model]
            candidates = [pinned_model] + others
        else:
            candidates = _pick_model_candidates()
        for model in candidates:
            body["model"] = model
            # reasoning_effort は thinking 対応モデルだけが受け付ける。
            # 非対応と判明済みのモデルには送らない（毎回400を踏まないため）。
            if "reasoning_effort" in payload:
                if model in _NO_THINKING_MODELS:
                    body.pop("reasoning_effort", None)
                else:
                    body["reasoning_effort"] = payload["reasoning_effort"]
            try:
                r = _SESSION.post(OLLAMA_URL, json=body, timeout=eff_timeout)
                if r.status_code >= 400:
                    try:
                        detail = r.json()
                    except Exception:
                        detail = r.text
                    # thinking 非対応による400はモデルの不良ではなくパラメータ不一致。
                    # reasoning_effort を外して同じモデルで1回だけ再試行し、
                    # _FAILED_MODELS には落とさない（候補を無駄に減らさない）。
                    if "reasoning_effort" in body and "does not support thinking" in str(detail):
                        _NO_THINKING_MODELS.add(model)
                        body.pop("reasoning_effort", None)
                        print(f"[INFO] model '{model}' は thinking 非対応。reasoning_effort を外して再試行する")
                        r = _SESSION.post(OLLAMA_URL, json=body, timeout=eff_timeout)

                if r.status_code >= 400:
                    _FAILED_MODELS.add(model)
                    if _SELECTED_MODEL == model:
                        _SELECTED_MODEL = None
                    print(f"[WARN] model '{model}' failed on Ollama; trying next candidate")
                    # 失敗した候補は即アンロードする。アンロードせず次候補へ進むと、
                    # 候補が全モデル総当たり(_pick_model_candidates)になるケースで
                    # Ollamaが失敗モデルを解放しないままロードし続け、RAMを食い潰す
                    # (2026-08-04: 14時間PCフリーズの原因)。
                    _unload_model(model)
                    continue

                _SELECTED_MODEL = model
                return r
            except Exception as e:
                last_err = e
                # HTTP 400系と同様、タイムアウト等の例外も_FAILED_MODELSに登録する。
                # 登録しないと次のリトライ周回で同じ(壊れた/VRAM不足の)モデルを
                # 再度候補に選んでしまい、ロード→タイムアウト→アンロードを
                # 繰り返すだけで他の候補へ進めない（2026-08-09 発見の残存リスク）。
                _FAILED_MODELS.add(model)
                if _SELECTED_MODEL == model:
                    _SELECTED_MODEL = None
                _unload_model(model)

        if i < eff_retries:
            # 指数バックオフ。backoff_sec が明示指定されていれば従来通り線形扱い。
            if backoff_sec is None:
                sleep_for = LLM_RETRY_BASE_SEC * (LLM_RETRY_EXP_BASE ** i)
            else:
                sleep_for = float(backoff_sec) * (i + 1)
            time.sleep(sleep_for)
            continue

    if last_err:
        raise last_err
    raise RuntimeError("Ollama request failed: no model candidate succeeded")


# 後方互換エイリアス（既存の呼び出し元用）
post_lmstudio = post_ollama


def _extract_json_object(text: str) -> str | None:
    if not text:
        return None
    t = text.strip().replace("```json", "").replace("```", "").strip()
    start = t.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(t)):
        c = t[i]
        if escape_next:
            escape_next = False
            continue
        if c == '\\' and in_string:
            escape_next = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return t[start:i + 1]
    return None


def _repair_json_with_llm(bad_text: str) -> dict:
    payload = {
        "model": _pick_usable_model(),
        "messages": [
            {"role": "system", "content": "次のテキストを、有効なJSONだけに修復して返せ。JSON以外は禁止。"},
            {"role": "user", "content": bad_text},
        ],
        "temperature": 0.0,
        "max_tokens": 700,
    }
    r = post_ollama(payload, timeout=LLM_LONG_TIMEOUT_SEC)
    fixed = _get_lm_content(r)
    candidate = _extract_json_object(fixed)
    if not candidate:
        raise ValueError("no json object in repaired response")
    return json.loads(candidate)


_PROMPT_SAMPLE_TEXTS = [
    "技術者目線のコメント（50字以内）",
    "経営者目線のコメント（50字以内）",
    "消費者目線のコメント（50字以内）",
    "技術/セキュリティ/運用の観点（必要なら推測: で）",
    "経営/法務/レピュテーションの観点（必要なら推測: で）",
    "利用者/生活者の観点（必要なら推測: で）",
    "利用者/生活者の観点(必要なら推測: で)",
]


def _normalize_perspectives(raw) -> dict:
    """Normalize perspective keys/values from unstable LLM outputs."""
    normalized = {"engineer": "", "management": "", "consumer": ""}
    if not isinstance(raw, dict):
        return normalized

    key_aliases = {
        "engineer": "engineer",
        "技術者目線": "engineer",
        "技術者": "engineer",
        "エンジニア": "engineer",
        "management": "management",
        "経営者目線": "management",
        "経営": "management",
        "マネジメント": "management",
        "consumer": "consumer",
        "消費者目線": "consumer",
        "利用者目線": "consumer",
        "ユーザー目線": "consumer",
        "生活者目線": "consumer",
    }

    for k, v in raw.items():
        canonical = key_aliases.get(str(k).strip().lower()) or key_aliases.get(str(k).strip())
        if not canonical:
            continue
        text = v.strip() if isinstance(v, str) else ""
        if text:
            # プロンプトのサンプルテキストがそのまま出力された場合は空にする
            if any(sample in text for sample in _PROMPT_SAMPLE_TEXTS):
                text = ""
            normalized[canonical] = text
    return normalized


_DIGEST_MIN_LEN = 80
_DIGEST_MAX_LEN = 260
_DIGEST_PROMPT_SAMPLES = [
    "技術者向けの考え方・推奨行動・注意点を含む200字前後の説明",
    "経営者向けの考え方・推奨行動・注意点を含む200字前後の説明",
    "消費者向けの考え方・推奨行動・注意点を含む200字前後の説明",
    "技術者向けの考え方・推奨行動・注意点（2〜3文）",
    "経営者向けの考え方・推奨行動・注意点（2〜3文）",
    "消費者向けの考え方・推奨行動・注意点（2〜3文）",
]


def _extract_evidence_domain(evidence_urls) -> str:
    """evidence_urls先頭の有効なURLから参考ドメインを抽出。取得不能なら空文字。"""
    from urllib.parse import urlparse

    for u in (evidence_urls or []):
        if not isinstance(u, str) or not u.strip():
            continue
        try:
            netloc = urlparse(u.strip()).netloc
        except Exception:
            netloc = ""
        if netloc:
            return netloc
    return ""


def _normalize_perspective_digest(raw, evidence_urls=None) -> dict:
    """立場別200字サマリーを正規化する。短すぎる(生成失敗疑い)場合は空にしてフォールバック対象にする。"""
    normalized = {"engineer": "", "management": "", "consumer": ""}
    if not isinstance(raw, dict):
        return normalized

    domain = _extract_evidence_domain(evidence_urls)
    suffix = f"（参考: {domain}）" if domain else "（参考情報未取得）"

    key_aliases = {
        "engineer": "engineer", "技術者目線": "engineer", "技術者": "engineer", "エンジニア": "engineer",
        "management": "management", "経営者目線": "management", "経営": "management", "マネジメント": "management",
        "consumer": "consumer", "消費者目線": "consumer", "利用者目線": "consumer", "ユーザー目線": "consumer", "生活者目線": "consumer",
    }

    for k, v in raw.items():
        canonical = key_aliases.get(str(k).strip().lower()) or key_aliases.get(str(k).strip())
        if not canonical:
            continue
        text = v.strip() if isinstance(v, str) else ""
        if any(sample in text for sample in _DIGEST_PROMPT_SAMPLES):
            text = ""
        if not text or len(text) < _DIGEST_MIN_LEN:
            continue
        if "（参考:" in text or "参考情報未取得" in text:
            body_text = text
        else:
            max_body_len = _DIGEST_MAX_LEN - len(suffix)
            if len(text) > max_body_len:
                text = text[:max_body_len].rstrip() + "…"
            body_text = text + suffix
        normalized[canonical] = body_text
    return normalized


def call_llm_perspective_digest(title: str, summary: str, perspectives_short: dict, url: str = "") -> dict:
    """既存の短いperspectives(50字)を土台に、行動につながる200字前後の立場別サマリーを生成する。"""
    short = perspectives_short or {}
    system = (
        "あなたはニュース記事の立場別サマリー作成アシスタント。出力はJSONのみ。前置きの思考や説明文は出力しない。"
        "perspective_digest は {engineer,management,consumer} の3キー。"
        "各値は2〜3文の日本語で、考え方→推奨行動→注意点の順に書く。文字数を数えようとしなくてよい。"
        "本文に明記のない内容は必ず '推測: ' から始める。"
        "同じ文言の使い回し・カタログ的な羅列は禁止。参考情報URLへの言及は書かなくてよい（別途自動付与される）。"
    )
    user = (
        f"タイトル: {title}\n要約: {summary}\nURL: {url}\n"
        f"技術者向け短評: {short.get('engineer', '')}\n"
        f"経営者向け短評: {short.get('management', '')}\n"
        f"消費者向け短評: {short.get('consumer', '')}\n\n"
        "次のJSONを出力:\n"
        "{\n"
        '  "perspective_digest": {\n'
        '    "engineer": "技術者向けの考え方・推奨行動・注意点（2〜3文）",\n'
        '    "management": "経営者向けの考え方・推奨行動・注意点（2〜3文）",\n'
        '    "consumer": "消費者向けの考え方・推奨行動・注意点（2〜3文）"\n'
        "  }\n"
        "}\n"
    )
    payload = {
        "model": _pick_usable_model(),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.4,
        "max_tokens": 900,
        # gpt-oss系は文字数を厳密に狙う指示があると reasoning が肥大化し、
        # max_tokens を使い切って本文が空になることを実機検証で確認済み（finish_reason=length）。
        # reasoning_effort を下げて本文生成に確実にトークンを残す。
        "reasoning_effort": "low",
    }
    r = post_ollama(payload, timeout=LLM_SHORT_TIMEOUT_SEC)
    s = _get_lm_content(r)
    candidate = _extract_json_object(s)
    digest = {}
    if candidate:
        try:
            obj = json.loads(candidate)
            digest = obj.get("perspective_digest") or {}
        except Exception:
            digest = {}
    return _normalize_perspective_digest(digest, evidence_urls=[url] if url else [])


def call_llm_short_news(title: str, body: str, url: str = "") -> dict:
    system = (
        "あなたはニュース記事の要約アシスタント。出力はJSONのみ。"
        "summary は本文/タイトルに明記された事実のみで日本語1文（推測は禁止）。"
        "key_points は配列で最大3つ。事実が足りない場合、残りは必ず '推測: ' で始めて補完する（断定しない）。"
        "perspectives は {engineer,management,consumer} の3キー。短い本文でも必ず埋める。"
        "perspectives は原則 '推測: ' で始める（本文に明記された事実だけで言える場合のみ推測不要）。"
        "importance は 0〜100 の整数を必ず出力する。"
        "同じ内容の繰り返しは禁止。抽象的すぎる文（例:『詳細は本文確認が必要』だけ）は禁止。"
    )
    body_for_llm = (body or "").strip()[:1200]
    title = (title or "").strip()

    user = (
        f"タイトル: {title}\nURL: {url}\n本文:\n{body_for_llm}\n\n"
        "次のJSONを出力:\n"
        "{\n"
        '  "importance": 0,\n'
        '  "summary": "事実のみの日本語1文（推測禁止）",\n'
        '  "key_points": ["箇条書き1","箇条書き2","箇条書き3"],\n'
        '  "perspectives": {\n'
        '    "engineer": "技術/セキュリティ/運用の観点（必要なら推測: で）",\n'
        '    "management": "経営/法務/レピュテーションの観点（必要なら推測: で）",\n'
        '    "consumer": "利用者/生活者の観点（必要なら推測: で）"\n'
        "  },\n"
        '  "inferred": 0\n'
        "}\n"
        "importance は 0〜100 の整数。"
        "inferred は、key_points または perspectives に '推測:' が1つでも含まれる場合 1、それ以外は0。"
    )

    payload = {
        "model": _pick_usable_model(),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.3,
        "max_tokens": 700,
        # gpt-oss系は既定のreasoningが肥大化し、max_tokensを使い切って
        # JSONが途中で切れる（finish_reason=length）。実測: reasoning 1736字 /
        # completion 700トークン全消費 → JSON破断 → 要約がフォールバック文言に
        # 差し替わる事象が news 記事の約85%で発生していた（2026-08-24 調査）。
        # reasoning_effort=low で completion 337トークン・8秒で完結する。
        # thinking非対応モデルへは post_ollama が自動で外す。
        "reasoning_effort": "low",
    }
    r = post_ollama(payload, timeout=LLM_SHORT_TIMEOUT_SEC)
    s = _get_lm_content(r)

    # 空応答だけでなく「JSONとして取り出せない応答」も再試行対象にする。
    # 本文が途中で切れた場合は s が非空のまま解析不能になるため。
    if not s or not _extract_json_object(s):
        payload["messages"][1]["content"] = (
            f"タイトル: {title}\nURL: {url}\n本文:\n{body_for_llm}\n\n"
            "JSONのみで返して:\n"
            "{\n"
            '  "importance": 10,\n'
            '  "summary": "事実のみの日本語1文",\n'
            '  "key_points": ["推測: ..."],\n'
            '  "perspectives": {"engineer":"推測: ...","management":"推測: ...","consumer":"推測: ..."},\n'
            '  "inferred": 1\n'
            "}"
        )
        r = post_ollama(payload, timeout=LLM_SHORT_TIMEOUT_SEC)
        s = _get_lm_content(r)

    candidate = _extract_json_object(s)
    summary = ""
    key_points = []
    perspectives = {"engineer": "", "management": "", "consumer": ""}
    inferred = 0
    importance = 0
    if candidate:
        try:
            obj = json.loads(candidate)
            importance = int(obj.get("importance") or 0)
            summary = (obj.get("summary") or "").strip()
            key_points = obj.get("key_points") or []
            perspectives = _normalize_perspectives(obj.get("perspectives") or {})
            inferred = 1 if int(obj.get("inferred") or 0) == 1 else 0
        except Exception:
            pass

    if not summary:
        summary = title

    kps = [x.strip() for x in (key_points or []) if isinstance(x, str) and x.strip()][:3]
    if not kps:
        kps = [NEWS_FALLBACK_KEY_POINT]

    def _fill_if_empty(v: str, fallback: str) -> str:
        return (v or "").strip() or fallback

    perspectives["engineer"] = _fill_if_empty(perspectives.get("engineer"), "推測: 技術面では、影響範囲（対象サービス/システム）と再発防止策の有無が要確認")
    perspectives["management"] = _fill_if_empty(perspectives.get("management"), "推測: 経営面では、信用/コンプライアンス/説明責任への影響が要確認")
    perspectives["consumer"] = _fill_if_empty(perspectives.get("consumer"), "推測: 利用者目線では、生活への影響や取るべき行動（注意喚起等）の有無が要確認")

    if inferred == 0 and "推測:" in (" ".join(kps) + " " + " ".join(perspectives.values())):
        inferred = 1

    importance = max(0, min(100, int(importance or 0)))

    return {
        "importance": importance,
        "type": "other",
        "summary": summary,
        "key_points": kps,
        "perspectives": perspectives,
        "tags": ["ニュース"],
        "evidence_urls": [url] if url else [],
        "inferred": inferred,
    }


def call_llm(topic_title, category, url, body, kind: str | None = None):
    is_news = (category == "news") or ((kind or "").lower() == "news")
    if is_news:
        return call_llm_short_news(topic_title, body, url=url)

    body = (body or "").strip()[:1200]
    system = (
        "あなたは技術判断を行うエンジニア/企画担当向けのトレンド分析アシスタント。"
        "一般向け説明、感想、前置き、結論以外の余談は禁止。"
        "出力はJSONのみ。JSON以外の文字（挨拶、説明、コードブロック、注釈）を一切出さない。"
        "必ず全フィールドを出力し、欠落や型違いは禁止。"
        "key_pointsは入力textに明記された事実のみ。推測・解釈・一般論は禁止。"
        "evidence_urlsは必ず1つ以上で、入力のevidence_urlを必ず含める。"
        "tagsは1〜5個の配列。短い名詞。重複禁止。本文/要約から抽出し、本文に記載のある固有名詞・技術名を優先する。"
        "OT/セキュリティ関連では tags に sbom / patch_window / safety_impact を含めることを優先する。"
        "算定・規制関連トピックでは、算定対象範囲(scope)と報告義務(reporting_obligation)を必ず抽出する。"
        "再現条件(repro_conditions)と導入前提(deployment_prerequisites)の抽出は必須。本文に明記が無ければ '不明'。"
        "必須キー: importance(int), type(string), summary(string), key_points(array[3]), perspectives(object), tags(array[1..5]), evidence_urls(array[>=1]), compliance(object), implementation_requirements(object)。"
        "perspectivesの各コメントは推論可。"
        "perspectivesは engineer/management/consumer の3キー固定。"
        "complianceは scope/reporting_obligation の2キー固定。本文に明記が無ければ '不明'。"
        "implementation_requirementsは repro_conditions/deployment_prerequisites の2キー固定。本文に明記が無ければ '不明'。"
    )
    user = {"topic_title": topic_title, "category": category, "evidence_url": url, "text": body}
    schema = {
        "importance": "0-100の整数。",
        "type": "security|release|research|incident|biz|other",
        "summary": "日本語100字以内の要約1行（結論→理由の順）",
        "key_points": ["本文に明記された事実1", "本文に明記された事実2", "本文に明記された事実3"],
        "perspectives": {"engineer": "技術者目線のコメント（50字以内）", "management": "経営者目線のコメント（50字以内）", "consumer": "消費者目線のコメント（50字以内）"},
        "tags": ["1〜5個。OT/セキュリティ関連では sbom, patch_window, safety_impact を含める"],
        "evidence_urls": ["根拠URL（最低1つ）"],
        "compliance": {"scope": "算定対象範囲（例: Scope1/2/3、対象製品、対象拠点）。不明なら'不明'", "reporting_obligation": "報告義務（対象事業者、期限、制度名など）。不明なら'不明'"},
        "implementation_requirements": {"repro_conditions": "再現条件（データ条件、設備条件、運転条件、評価条件など）。不明なら'不明'", "deployment_prerequisites": "導入前提（必要機器、接続要件、人員体制、既存システム前提など）。不明なら'不明'"},
    }

    payload = {
        "model": _pick_usable_model(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "次の入力を分析し、スキーマに厳密準拠したJSONのみを返してください。前後に説明文・コードブロックは禁止。"},
            {"role": "user", "content": f"スキーマ: {json.dumps(schema, ensure_ascii=False)}"},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        "temperature": 0.2,
        "max_tokens": 500,
    }
    r = post_ollama(payload, timeout=LLM_LONG_TIMEOUT_SEC)
    text = _get_lm_content(r)
    candidate = _extract_json_object(text)
    if not candidate:
        result = _repair_json_with_llm(text)
    else:
        try:
            result = json.loads(candidate)
        except Exception:
            result = _repair_json_with_llm(candidate)
    # perspectives キーがあれば正規化を通す
    if isinstance(result, dict) and "perspectives" in result:
        result["perspectives"] = _normalize_perspectives(result.get("perspectives") or {})
    return result
