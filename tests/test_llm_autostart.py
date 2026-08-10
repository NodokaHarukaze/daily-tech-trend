import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import llm_insights_api


class DummyResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"choices": [{"message": {"content": "{}"}}]}
        self.text = "ok"

    def json(self):
        return self._payload


class DummySession:
    def __init__(self, get_results=None):
        self.get_results = list(get_results or [])
        self.post_calls = 0
        self.ollama_post_calls = 0

    def get(self, url="", *_args, **_kwargs):
        # /api/ps へのリクエストには空のモデルリストを返す
        if "/api/ps" in str(url):
            return DummyResponse(status_code=200, payload={"models": []})
        if not self.get_results:
            raise RuntimeError("not ready")
        result = self.get_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return DummyResponse(status_code=result)

    def post(self, url="", *_args, **_kwargs):
        # /api/generate（モデルロード/アンロード）は別カウント
        if "/api/generate" in str(url):
            self.ollama_post_calls += 1
            return DummyResponse()
        self.post_calls += 1
        return DummyResponse()


def _reset_flags():
    llm_insights_api._OLLAMA_READY = False
    llm_insights_api._AUTOSTART_ATTEMPTED = False
    llm_insights_api._MODEL_PREPARED = False
    llm_insights_api._SELECTED_MODEL = None
    llm_insights_api._FAILED_MODELS = set()
    llm_insights_api._LOAD_ATTEMPTED_MODELS = set()


def test_post_ollama_uses_existing_running_server(monkeypatch):
    _reset_flags()
    session = DummySession(get_results=[200])
    monkeypatch.setattr(llm_insights_api, "_SESSION", session)

    res = llm_insights_api.post_ollama({"model": "x"}, timeout=1)

    assert res.status_code == 200
    assert session.post_calls == 1


def test_post_ollama_autostarts_and_waits_until_ready(monkeypatch):
    _reset_flags()
    session = DummySession(get_results=[RuntimeError("down"), RuntimeError("down"), 200])
    monkeypatch.setattr(llm_insights_api, "_SESSION", session)
    monkeypatch.setenv("OLLAMA_AUTOSTART_CMD", "echo start")
    monkeypatch.setenv("OLLAMA_AUTOSTART_WAIT_SEC", "3")

    launched = {"ok": False}

    def fake_popen(*_args, **_kwargs):
        launched["ok"] = True

    monkeypatch.setattr(llm_insights_api.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(llm_insights_api.time, "sleep", lambda *_args, **_kwargs: None)

    res = llm_insights_api.post_ollama({"model": "x"}, timeout=1)

    assert launched["ok"]
    assert res.status_code == 200
    assert session.post_calls == 1


class DummyModelSession(DummySession):
    def __init__(self, model_ids):
        super().__init__(get_results=[200])
        self.model_ids = model_ids

    def get(self, url="", *_args, **_kwargs):
        if "/api/ps" in str(url):
            return DummyResponse(status_code=200, payload={"models": []})
        return DummyResponse(status_code=200, payload={"data": [{"id": m} for m in self.model_ids]})


def test_pick_usable_model_prefers_requested(monkeypatch):
    _reset_flags()
    monkeypatch.setattr(llm_insights_api, "_SESSION", DummyModelSession(["gpt-oss:20b", "other:model"]))
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    model = llm_insights_api._pick_usable_model()

    assert model == "gpt-oss:20b"


def test_pick_usable_model_falls_back_to_loaded_first(monkeypatch):
    _reset_flags()
    monkeypatch.setattr(llm_insights_api, "_SESSION", DummyModelSession(["local:model-a", "local:model-b"]))
    monkeypatch.setenv("OLLAMA_MODEL", "gpt-oss:20b")

    model = llm_insights_api._pick_usable_model()

    assert model == "local:model-a"


def test_post_ollama_uses_pinned_model_first(monkeypatch):
    """Ollamaはオンデマンドロードのため、指定モデルを先頭で試す"""
    _reset_flags()

    class HybridSession:
        def __init__(self):
            self.post_model = None

        def get(self, url="", *_args, **_kwargs):
            if "/api/ps" in str(url):
                return DummyResponse(status_code=200, payload={"models": []})
            return DummyResponse(status_code=200, payload={"data": [{"id": "local:model-a"}]})

        def post(self, url="", *_args, **kwargs):
            # モデルロード/アンロードのPOSTはスキップ
            if "/api/generate" in str(url):
                return DummyResponse()
            self.post_model = kwargs["json"]["model"]
            return DummyResponse()

    session = HybridSession()
    monkeypatch.setattr(llm_insights_api, "_SESSION", session)
    monkeypatch.setenv("OLLAMA_MODEL", "gpt-oss:20b")

    llm_insights_api.post_ollama({"model": "gpt-oss:20b"}, timeout=1, retries=0)

    assert session.post_model == "gpt-oss:20b"


def test_post_ollama_unloads_failed_candidates_between_attempts(monkeypatch):
    """失敗した候補モデルは次候補を試す前にアンロードされる。

    アンロードせず次候補へ進むと、候補が「全モデル総当たり」になるケースで
    Ollamaが失敗モデルを解放しないまま次々ロードし続けRAMを食い潰す
    (2026-08-04: run_daily.batが14時間PCフリーズした事故の原因)。
    """
    _reset_flags()

    class FailThenSucceedSession:
        def __init__(self):
            self.unload_calls = []
            self.chat_calls = []

        def get(self, url="", *_args, **_kwargs):
            if "/api/ps" in str(url):
                return DummyResponse(status_code=200, payload={"models": []})
            return DummyResponse(
                status_code=200,
                payload={"data": [{"id": "model-a"}, {"id": "model-b"}, {"id": "model-c"}]},
            )

        def post(self, url="", *_args, **kwargs):
            if "/api/generate" in str(url):
                body = kwargs.get("json", {})
                if body.get("keep_alive") == 0:
                    self.unload_calls.append(body.get("model"))
                return DummyResponse()
            model = kwargs["json"]["model"]
            self.chat_calls.append(model)
            if model == "model-c":
                return DummyResponse(status_code=200)
            return DummyResponse(status_code=400)

    session = FailThenSucceedSession()
    monkeypatch.setattr(llm_insights_api, "_SESSION", session)
    monkeypatch.setenv("OLLAMA_MODEL", "model-a")

    res = llm_insights_api.post_ollama({}, timeout=1, retries=0)

    assert res.status_code == 200
    assert session.chat_calls == ["model-a", "model-b", "model-c"]
    assert session.unload_calls == ["model-a", "model-b"]


def test_post_ollama_registers_timeout_failure_and_tries_next_candidate(monkeypatch):
    """タイムアウト等の例外で失敗した候補も_FAILED_MODELSに登録され、
    同一リトライ周回内で再選択されず次候補へ進む。

    登録しないと、次のリトライ周回で_pick_model_candidates()が同じ
    (VRAM不足等で毎回タイムアウトする)モデルを再度候補の先頭に選び、
    ロード→タイムアウト→アンロードを繰り返すだけで他の健全な候補へ
    進めない（2026-08-09発見の残存リスク）。
    """
    _reset_flags()

    class TimeoutThenSucceedSession:
        def __init__(self):
            self.unload_calls = []
            self.chat_calls = []

        def get(self, url="", *_args, **_kwargs):
            if "/api/ps" in str(url):
                return DummyResponse(status_code=200, payload={"models": []})
            return DummyResponse(
                status_code=200,
                payload={"data": [{"id": "model-a"}, {"id": "model-b"}]},
            )

        def post(self, url="", *_args, **kwargs):
            if "/api/generate" in str(url):
                body = kwargs.get("json", {})
                if body.get("keep_alive") == 0:
                    self.unload_calls.append(body.get("model"))
                return DummyResponse()
            model = kwargs["json"]["model"]
            self.chat_calls.append(model)
            if model == "model-a":
                raise TimeoutError("read timed out")
            return DummyResponse(status_code=200)

    session = TimeoutThenSucceedSession()
    monkeypatch.setattr(llm_insights_api, "_SESSION", session)
    monkeypatch.setenv("OLLAMA_MODEL", "model-a")

    res = llm_insights_api.post_ollama({}, timeout=1, retries=1)

    assert res.status_code == 200
    assert session.chat_calls == ["model-a", "model-b"]
    assert "model-a" in llm_insights_api._FAILED_MODELS


def test_unload_model_uses_configurable_timeout_default(monkeypatch):
    """`_unload_model`はハードコードの10秒ではなく、環境変数で調整可能な

    LLM_UNLOAD_TIMEOUT_SEC（既定30秒）をデフォルトタイムアウトに使う。
    RAMスラッシング中はアンロード要求自体が短いタイムアウトで失敗しうるため
    (2026-08-10 12:07セッションが残した残存リスク)、他のタイムアウト定数と
    同じパターンで個別に延長できるようにした。
    """
    _reset_flags()
    calls = []

    class CapturingSession:
        def get(self, url="", *_args, **_kwargs):
            return DummyResponse(status_code=200, payload={"models": []})

        def post(self, url="", *_args, **kwargs):
            calls.append(kwargs.get("timeout"))
            return DummyResponse()

    monkeypatch.setattr(llm_insights_api, "_SESSION", CapturingSession())

    llm_insights_api._unload_model("model-a")

    assert calls == [llm_insights_api.LLM_UNLOAD_TIMEOUT_SEC]
    assert llm_insights_api.LLM_UNLOAD_TIMEOUT_SEC == 30


def test_unload_model_timeout_override_still_works(monkeypatch):
    """呼び出し側が明示的にtimeoutを渡した場合は、そちらを優先する（既存動作の維持）。"""
    _reset_flags()
    calls = []

    class CapturingSession:
        def post(self, url="", *_args, **kwargs):
            calls.append(kwargs.get("timeout"))
            return DummyResponse()

    monkeypatch.setattr(llm_insights_api, "_SESSION", CapturingSession())

    llm_insights_api._unload_model("model-a", timeout=5.0)

    assert calls == [5.0]


def test_pick_model_candidates_respects_exclude_env(monkeypatch):
    """OLLAMA_EXCLUDE_MODELS指定モデルは自動収集の候補から除外される。

    VRAMに収まらないと判明済みの巨大モデル（例: qwen3:30b-a3b）が
    「全モデル総当たり」に混入すると、ロード→タイムアウト→アンロードの
    ムダな周回でRAM/VRAM圧迫のリスクを再現しうるため、既知の除外先を
    明示指定できるようにする。
    """
    _reset_flags()
    monkeypatch.setattr(
        llm_insights_api,
        "_SESSION",
        DummyModelSession(["gpt-oss:20b", "qwen3:30b-a3b"]),
    )
    monkeypatch.setenv("OLLAMA_MODEL", "gpt-oss:20b")
    monkeypatch.setenv("OLLAMA_EXCLUDE_MODELS", "qwen3:30b-a3b")

    candidates = llm_insights_api._pick_model_candidates()

    assert "qwen3:30b-a3b" not in candidates
    assert candidates == ["gpt-oss:20b"]


# 後方互換: post_lmstudio エイリアスが動作すること
def test_post_lmstudio_alias_works(monkeypatch):
    _reset_flags()
    session = DummySession(get_results=[200])
    monkeypatch.setattr(llm_insights_api, "_SESSION", session)

    res = llm_insights_api.post_lmstudio({"model": "x"}, timeout=1)

    assert res.status_code == 200
