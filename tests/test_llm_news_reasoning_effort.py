"""news 要約が reasoning 肥大化で空振りする問題への防御テスト。

2026-08-24 調査: `call_llm_short_news` が gpt-oss:20b の既定 reasoning で
max_tokens=700 を使い切り、JSON が途中で切れる（finish_reason=length）ため
`_extract_json_object` が None を返し、要約が定型フォールバック文言
（「推測: 本文情報が少ないため…」）へ差し替わっていた。news insight の
約85%が該当していた。

対策:
1. news 用 payload に `reasoning_effort: "low"` を付ける
2. thinking 非対応モデルに送ってしまった場合は post_ollama が自動で外して
   再試行し、そのモデルを `_FAILED_MODELS` に落とさない
3. 応答が JSON として取り出せない場合も再試行する（従来は空応答のみ）
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import llm_insights_api as llm


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _chat_response(content: str) -> _FakeResponse:
    return _FakeResponse(payload={"choices": [{"message": {"content": content}}]})


def _neutralize_ollama(monkeypatch):
    """ネットワーク接続・モデル準備・アンロードを無効化する"""
    monkeypatch.setattr(llm, "_ensure_ollama_ready", lambda *a, **k: None)
    monkeypatch.setattr(llm, "_ensure_model_prepared", lambda *a, **k: None)
    monkeypatch.setattr(llm, "_unload_model", lambda *a, **k: None)
    llm._SELECTED_MODEL = None
    llm._FAILED_MODELS = set()
    llm._NO_THINKING_MODELS = set()


class TestPostOllamaThinkingFallback:
    """thinking 非対応モデルへの reasoning_effort 送信を自己修復する"""

    def test_retries_same_model_without_reasoning_effort(self, monkeypatch):
        _neutralize_ollama(monkeypatch)
        monkeypatch.setattr(llm, "_pick_model_candidates",
                            lambda *a, **k: ["gemma3:12b", "gpt-oss:20b"])
        sent = []

        def fake_post(url, json=None, timeout=None):
            sent.append(dict(json))
            if "reasoning_effort" in json:
                return _FakeResponse(
                    status_code=400,
                    payload={"error": {"message": '"gemma3:12b" does not support thinking'}},
                )
            return _chat_response("ok")

        monkeypatch.setattr(llm._SESSION, "post", fake_post)

        r = llm.post_ollama({"model": "gemma3:12b", "messages": [], "reasoning_effort": "low"},
                            timeout=5, retries=0)

        assert r.status_code == 200
        # 1回目は reasoning_effort 付き、2回目は同じモデルで外して再試行
        assert "reasoning_effort" in sent[0]
        assert sent[1]["model"] == "gemma3:12b"
        assert "reasoning_effort" not in sent[1]
        # パラメータ不一致なのでモデル自体は失敗扱いにしない
        assert "gemma3:12b" not in llm._FAILED_MODELS
        assert "gemma3:12b" in llm._NO_THINKING_MODELS

    def test_known_unsupported_model_skips_reasoning_effort(self, monkeypatch):
        _neutralize_ollama(monkeypatch)
        llm._NO_THINKING_MODELS = {"gemma3:12b"}
        monkeypatch.setattr(llm, "_pick_model_candidates", lambda *a, **k: ["gemma3:12b"])
        sent = []

        def fake_post(url, json=None, timeout=None):
            sent.append(dict(json))
            return _chat_response("ok")

        monkeypatch.setattr(llm._SESSION, "post", fake_post)
        llm.post_ollama({"model": "gemma3:12b", "messages": [], "reasoning_effort": "low"},
                        timeout=5, retries=0)

        assert len(sent) == 1
        assert "reasoning_effort" not in sent[0]

    def test_thinking_capable_model_keeps_reasoning_effort(self, monkeypatch):
        _neutralize_ollama(monkeypatch)
        monkeypatch.setattr(llm, "_pick_model_candidates", lambda *a, **k: ["gpt-oss:20b"])
        sent = []

        def fake_post(url, json=None, timeout=None):
            sent.append(dict(json))
            return _chat_response("ok")

        monkeypatch.setattr(llm._SESSION, "post", fake_post)
        llm.post_ollama({"model": "gpt-oss:20b", "messages": [], "reasoning_effort": "low"},
                        timeout=5, retries=0)

        assert sent[0].get("reasoning_effort") == "low"

    def test_other_400_still_marks_model_failed(self, monkeypatch):
        """thinking 以外の 400 は従来どおりモデルを失敗扱いにする"""
        _neutralize_ollama(monkeypatch)
        monkeypatch.setattr(llm, "_pick_model_candidates",
                            lambda *a, **k: ["broken:1b", "gpt-oss:20b"])

        def fake_post(url, json=None, timeout=None):
            if json["model"] == "broken:1b":
                return _FakeResponse(status_code=400,
                                     payload={"error": {"message": "model not found"}})
            return _chat_response("ok")

        monkeypatch.setattr(llm._SESSION, "post", fake_post)
        r = llm.post_ollama({"model": "broken:1b", "messages": [], "reasoning_effort": "low"},
                            timeout=5, retries=0)

        assert r.status_code == 200
        assert "broken:1b" in llm._FAILED_MODELS


_VALID_NEWS_JSON = json.dumps({
    "importance": 70,
    "summary": "台風18号が沖縄・奄美へ接近する見込み。",
    "key_points": ["24日から風が強まる", "25日に猛烈な風", "気象庁が備えを呼びかけ"],
    "perspectives": {
        "engineer": "推測: 設備の耐風性を確認",
        "management": "推測: BCP の見直しが必要",
        "consumer": "推測: 停電と交通の乱れに備える",
    },
    "inferred": 1,
}, ensure_ascii=False)


class TestCallLlmShortNews:
    """news 用呼び出しが reasoning_effort を付け、破断JSONを再試行する"""

    def test_payload_carries_low_reasoning_effort(self, monkeypatch):
        _neutralize_ollama(monkeypatch)
        monkeypatch.setattr(llm, "_pick_usable_model", lambda *a, **k: "gpt-oss:20b")
        seen = []

        def fake_post_ollama(payload, timeout=None, **k):
            seen.append(dict(payload))
            return _chat_response(_VALID_NEWS_JSON)

        monkeypatch.setattr(llm, "post_ollama", fake_post_ollama)
        out = llm.call_llm_short_news("台風18号", "本文", url="https://example.com/a")

        assert seen[0].get("reasoning_effort") == "low"
        assert out["summary"] == "台風18号が沖縄・奄美へ接近する見込み。"
        assert len(out["key_points"]) == 3
        # フォールバック文言が入っていないこと
        assert "本文情報が少ないため" not in " ".join(out["key_points"])

    def test_truncated_json_triggers_retry(self, monkeypatch):
        """finish_reason=length で途中まで来た応答も再試行対象"""
        _neutralize_ollama(monkeypatch)
        monkeypatch.setattr(llm, "_pick_usable_model", lambda *a, **k: "gpt-oss:20b")
        truncated = '{\n  "importance": 70,\n  "summary": "途中で切れた'
        responses = [_chat_response(truncated), _chat_response(_VALID_NEWS_JSON)]
        calls = []

        def fake_post_ollama(payload, timeout=None, **k):
            calls.append(dict(payload))
            return responses[len(calls) - 1]

        monkeypatch.setattr(llm, "post_ollama", fake_post_ollama)
        out = llm.call_llm_short_news("台風18号", "本文", url="https://example.com/a")

        assert len(calls) == 2
        assert out["summary"] == "台風18号が沖縄・奄美へ接近する見込み。"

    def test_falls_back_when_both_attempts_fail(self, monkeypatch):
        """2回とも壊れていれば従来どおりフォールバックへ落ちる（挙動維持）"""
        _neutralize_ollama(monkeypatch)
        monkeypatch.setattr(llm, "_pick_usable_model", lambda *a, **k: "gpt-oss:20b")
        monkeypatch.setattr(llm, "post_ollama",
                            lambda payload, timeout=None, **k: _chat_response("壊れた応答"))

        out = llm.call_llm_short_news("台風18号", "本文", url="https://example.com/a")

        assert out["summary"] == "台風18号"
        assert "本文情報が少ないため" in out["key_points"][0]
