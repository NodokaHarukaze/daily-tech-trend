"""夜間パイプライン開始前に空きVRAMを確認し、不足時は安全側でスキップ判定を返す。

2026-08-04 の Ollama モデルフォールバックによる RAM 28.8GB 枯渇・14時間フリーズ事故を受けた
再発防止策。根本原因（失敗候補の未アンロード）自体は llm_insights_api.py 側で 2026-08-05 に
修正済みだが、それとは独立に「そもそも空きVRAMが乏しい状態でパイプラインを開始しない」という
多重防御を追加する（tasks/todo.md「残存リスク」節の恒久対策候補）。

単体で実行して終了コードで判定する:
    py -3.11 -u src\\vram_preflight.py --min-free-mb 14000
    (exit 0 = 実行してよい, exit 1 = 空きVRAM不足)
"""
import argparse
import subprocess
import sys

import requests

NVIDIA_SMI_CMD = ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"]

# gpt-oss:20b の実測VRAM常駐量(13.9GB, tasks/todo.md 2026-08-09 10:19節)に余裕を加えた既定値
DEFAULT_MIN_FREE_MB = 14000


def get_free_vram_mb(timeout: float = 5.0) -> int | None:
    """nvidia-smi で空きVRAM(MB)を取得する。

    nvidia-smi 非搭載機（PC3のAMD ROCm環境等）や取得失敗時は None を返す。
    """
    try:
        proc = subprocess.run(
            NVIDIA_SMI_CMD, capture_output=True, text=True, timeout=timeout, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    first_line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    try:
        return int(first_line.strip())
    except ValueError:
        return None


def try_free_ollama_models(ollama_base: str = "http://127.0.0.1:11434", timeout: float = 10.0) -> int:
    """Ollamaにロード中の全モデルへ keep_alive=0 を送り即時アンロードする。

    アンロードを試みたモデル数を返す（失敗時は0）。ベストエフォートであり、
    アンロード要求自体が失敗しても例外は投げない。
    """
    try:
        r = requests.get(f"{ollama_base}/api/ps", timeout=timeout)
        r.raise_for_status()
        models = [m.get("name") or m.get("model") for m in (r.json().get("models") or [])]
    except Exception:
        return 0

    unloaded = 0
    for model in models:
        if not model:
            continue
        try:
            requests.post(
                f"{ollama_base}/api/generate",
                json={"model": model, "keep_alive": 0},
                timeout=timeout,
            )
            unloaded += 1
        except Exception:
            pass
    return unloaded


def check_preflight(
    min_free_mb: int = DEFAULT_MIN_FREE_MB,
    ollama_base: str = "http://127.0.0.1:11434",
    free_if_short: bool = True,
) -> tuple[bool, str]:
    """空きVRAMが十分か判定する。(実行してよいか, 理由) を返す。

    nvidia-smi非搭載機ではVRAM取得ができず判定不能なため fail-open（実行を許可）する。
    誤検知でパイプラインが夜間無人実行されないより、実行できる方を優先する。
    """
    free_mb = get_free_vram_mb()
    if free_mb is None:
        return True, "nvidia-smiで空きVRAMを取得できないため判定をスキップ（fail-open）"

    if free_mb >= min_free_mb:
        return True, f"空きVRAM {free_mb}MB >= 必要量 {min_free_mb}MB"

    if not free_if_short:
        return False, f"空きVRAM不足: {free_mb}MB < 必要量 {min_free_mb}MB"

    unloaded = try_free_ollama_models(ollama_base)
    if not unloaded:
        return False, f"空きVRAM不足: {free_mb}MB < 必要量 {min_free_mb}MB（Ollamaアンロード対象なし）"

    free_mb_after = get_free_vram_mb()
    if free_mb_after is not None and free_mb_after >= min_free_mb:
        return True, (
            f"空きVRAM不足({free_mb}MB)のためOllamaモデル{unloaded}件をアンロード、"
            f"解放後 {free_mb_after}MB >= 必要量 {min_free_mb}MB"
        )

    reported = free_mb_after if free_mb_after is not None else free_mb
    return False, f"空きVRAM不足: {reported}MB < 必要量 {min_free_mb}MB（Ollamaアンロード後も不足）"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-free-mb", type=int, default=DEFAULT_MIN_FREE_MB,
        help=f"必要な空きVRAM(MB)。既定値 {DEFAULT_MIN_FREE_MB}",
    )
    parser.add_argument("--ollama-base", default="http://127.0.0.1:11434")
    parser.add_argument(
        "--no-free-if-short", action="store_true",
        help="空きVRAM不足時にOllamaモデルの自動アンロードを試みない",
    )
    args = parser.parse_args(argv)

    ok, reason = check_preflight(
        args.min_free_mb, ollama_base=args.ollama_base, free_if_short=not args.no_free_if_short,
    )
    print(f"[vram_preflight] {reason}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
