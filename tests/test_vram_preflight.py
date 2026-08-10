import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import vram_preflight


class DummyProc:
    def __init__(self, stdout=""):
        self.stdout = stdout


class DummyResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


# --- get_free_vram_mb ---------------------------------------------------

def test_get_free_vram_mb_parses_nvidia_smi_output(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: DummyProc(stdout="15170\n")
    )
    assert vram_preflight.get_free_vram_mb() == 15170


def test_get_free_vram_mb_returns_none_when_nvidia_smi_missing(monkeypatch):
    def _raise(*_a, **_k):
        raise FileNotFoundError("nvidia-smi not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert vram_preflight.get_free_vram_mb() is None


def test_get_free_vram_mb_returns_none_on_nonzero_exit(monkeypatch):
    def _raise(*_a, **_k):
        raise subprocess.CalledProcessError(1, "nvidia-smi")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert vram_preflight.get_free_vram_mb() is None


def test_get_free_vram_mb_returns_none_on_malformed_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: DummyProc(stdout="not a number\n"))
    assert vram_preflight.get_free_vram_mb() is None


# --- try_free_ollama_models ----------------------------------------------

def test_try_free_ollama_models_unloads_each_running_model(monkeypatch):
    posted = []

    def fake_get(url, timeout=10.0):
        return DummyResponse(payload={"models": [{"name": "gpt-oss:20b"}, {"model": "qwen3:14b"}]})

    def fake_post(url, json=None, timeout=10.0):
        posted.append(json)
        return DummyResponse()

    monkeypatch.setattr(vram_preflight.requests, "get", fake_get)
    monkeypatch.setattr(vram_preflight.requests, "post", fake_post)

    assert vram_preflight.try_free_ollama_models() == 2
    assert {p["model"] for p in posted} == {"gpt-oss:20b", "qwen3:14b"}
    assert all(p["keep_alive"] == 0 for p in posted)


def test_try_free_ollama_models_returns_zero_when_ps_unreachable(monkeypatch):
    def fake_get(url, timeout=10.0):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(vram_preflight.requests, "get", fake_get)
    assert vram_preflight.try_free_ollama_models() == 0


def test_try_free_ollama_models_tolerates_individual_post_failures(monkeypatch):
    def fake_get(url, timeout=10.0):
        return DummyResponse(payload={"models": [{"name": "gpt-oss:20b"}]})

    def fake_post(url, json=None, timeout=10.0):
        raise RuntimeError("timeout")

    monkeypatch.setattr(vram_preflight.requests, "get", fake_get)
    monkeypatch.setattr(vram_preflight.requests, "post", fake_post)
    # アンロード要求が失敗しても例外を投げず、成功件数として0を返す
    assert vram_preflight.try_free_ollama_models() == 0


# --- check_preflight -------------------------------------------------------

def test_check_preflight_passes_when_vram_sufficient(monkeypatch):
    monkeypatch.setattr(vram_preflight, "get_free_vram_mb", lambda: 15000)
    ok, reason = vram_preflight.check_preflight(min_free_mb=14000)
    assert ok is True
    assert "15000" in reason


def test_check_preflight_fail_open_when_vram_unknown(monkeypatch):
    monkeypatch.setattr(vram_preflight, "get_free_vram_mb", lambda: None)
    ok, reason = vram_preflight.check_preflight(min_free_mb=14000)
    assert ok is True
    assert "スキップ" in reason


def test_check_preflight_recovers_after_freeing_ollama_models(monkeypatch):
    calls = {"n": 0}

    def fake_free_vram():
        calls["n"] += 1
        # 1回目は不足、アンロード後の2回目は十分
        return 5000 if calls["n"] == 1 else 15000

    monkeypatch.setattr(vram_preflight, "get_free_vram_mb", fake_free_vram)
    monkeypatch.setattr(vram_preflight, "try_free_ollama_models", lambda ollama_base="": 2)

    ok, reason = vram_preflight.check_preflight(min_free_mb=14000)
    assert ok is True
    assert "アンロード" in reason


def test_check_preflight_fails_when_still_insufficient_after_freeing(monkeypatch):
    monkeypatch.setattr(vram_preflight, "get_free_vram_mb", lambda: 5000)
    monkeypatch.setattr(vram_preflight, "try_free_ollama_models", lambda ollama_base="": 1)

    ok, reason = vram_preflight.check_preflight(min_free_mb=14000)
    assert ok is False
    assert "不足" in reason


def test_check_preflight_fails_fast_when_no_models_to_unload(monkeypatch):
    monkeypatch.setattr(vram_preflight, "get_free_vram_mb", lambda: 5000)
    monkeypatch.setattr(vram_preflight, "try_free_ollama_models", lambda ollama_base="": 0)

    ok, reason = vram_preflight.check_preflight(min_free_mb=14000)
    assert ok is False


def test_check_preflight_skips_unload_attempt_when_disabled(monkeypatch):
    monkeypatch.setattr(vram_preflight, "get_free_vram_mb", lambda: 5000)

    def _should_not_be_called(ollama_base=""):
        raise AssertionError("free_if_short=False のときは呼ばれないはず")

    monkeypatch.setattr(vram_preflight, "try_free_ollama_models", _should_not_be_called)

    ok, reason = vram_preflight.check_preflight(min_free_mb=14000, free_if_short=False)
    assert ok is False
    assert "不足" in reason


# --- main (CLI) -------------------------------------------------------------

def test_main_returns_0_when_preflight_passes(monkeypatch, capsys):
    monkeypatch.setattr(vram_preflight, "check_preflight", lambda *a, **k: (True, "十分"))
    assert vram_preflight.main([]) == 0
    assert "十分" in capsys.readouterr().out


def test_main_returns_1_when_preflight_fails(monkeypatch, capsys):
    monkeypatch.setattr(vram_preflight, "check_preflight", lambda *a, **k: (False, "不足"))
    assert vram_preflight.main(["--min-free-mb", "20000"]) == 1
    assert "不足" in capsys.readouterr().out


def test_main_passes_no_free_if_short_flag_through(monkeypatch):
    captured = {}

    def fake_check_preflight(min_free_mb, ollama_base, free_if_short):
        captured["free_if_short"] = free_if_short
        return True, "ok"

    monkeypatch.setattr(vram_preflight, "check_preflight", fake_check_preflight)
    vram_preflight.main(["--no-free-if-short"])
    assert captured["free_if_short"] is False
