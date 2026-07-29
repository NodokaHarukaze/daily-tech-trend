"""details.insight 遅延ロードのブラウザ実機検証（一回限りの手動検証スクリプト）。

背景: 2026-07-28 セッション（トップページ軽量化実装）で、`scrollToHash()` 経由の
ハッシュリンク直接ジャンプ（`.open = true` のプログラム代入で `toggle` イベントが
実際に発火し遅延ロードが動くか）が「ブラウザ操作ツールが本環境に無いため実機確認
できていない」残課題として `tasks/todo.md` に残っていた。本スクリプトは Chromium を
ヘッドレス起動して以下2点を実機確認する:

  A) 通常のクリックによる toggle 発火 → ensureInsights() の fetch → 本文描画
  B) `#topic-<id>` ハッシュ付きURLへの直接アクセス → revealHashTarget() →
     scrollToHash() の `det.open = true` 代入 → toggle イベント発火 → 本文描画

実行方法:
  本プロジェクト (daily-tech-trend) は playwright に依存していない（グローバル
  py -3.11 環境を汚さないため意図的にインストールしていない）。代わりに
  C:\\work\\AmazonAssociate の .venv に既に playwright==1.58.0 + Chromium(1208) が
  インストール済み（スクレイピング用途、%LOCALAPPDATA%\\ms-playwright の共有キャッシュ
  を使用）であるため、これを借用して実行する。AmazonAssociate 側のファイルは一切
  変更しない（他プロジェクトの venv の python.exe をフルパスで呼び出すだけ）。

    C:\\work\\AmazonAssociate\\.venv\\Scripts\\python.exe scripts\\verify_lazy_insight_browser.py

  もし daily-tech-trend 自身に playwright を正式導入する場合は
  `uv pip install playwright==1.58.0 && playwright install chromium` のうえ
  通常の python で実行できる（本スクリプトは playwright 1.4x 以降のsync APIのみ
  使用しており移植性に問題はない）。

このスクリプトは pytest スイートには組み込まない（新規依存の追加可否はユーザー判断）。
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_junction(link_path: Path, target_path: Path) -> None:
    # Windows ディレクトリジャンクション（管理者権限不要）。
    # fetch('/daily-tech-trend/assets/data/...') という絶対パスを
    # GitHub Pages 本番同様のURL構造 (http://host/daily-tech-trend/...) で
    # 再現するために、一時ディレクトリの下に daily-tech-trend という名前で
    # docs/ を指すジャンクションを作る。
    # cmd.exe の出力はシステムのANSIコードページ(cp932想定)で返るため、
    # 呼び出し元がPYTHONUTF8=1等でUTF-8を強制していてもデコードエラーに
    # ならないよう bytes で受けて errors="replace" で表示用に変換する。
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)],
        capture_output=True,
    )
    if result.returncode != 0:
        stdout = result.stdout.decode("cp932", errors="replace")
        stderr = result.stderr.decode("cp932", errors="replace")
        raise RuntimeError(f"mklink /J failed: {stdout} {stderr}")


def _remove_junction(link_path: Path) -> None:
    subprocess.run(["cmd", "/c", "rmdir", str(link_path)], capture_output=True)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright が見つかりません。AmazonAssociate の venv から実行してください:\n"
            r"  C:\work\AmazonAssociate\.venv\Scripts\python.exe scripts\verify_lazy_insight_browser.py",
            file=sys.stderr,
        )
        return 2

    import tempfile

    tmp_root = Path(tempfile.mkdtemp(prefix="dtt_verify_"))
    link_path = tmp_root / "daily-tech-trend"
    port = _free_port()
    server_proc = None
    failures: list[str] = []

    try:
        _make_junction(link_path, DOCS_DIR)

        server_proc = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--directory", str(tmp_root)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # サーバー起動待ち
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("http.server が起動しませんでした")

        base = f"http://127.0.0.1:{port}/daily-tech-trend"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            # --- Test A: クリックによる自然な toggle 発火 -----------------------
            # tech/index.html は `if (!location.hash) toggleAllCats();` により
            # ハッシュ無しアクセス時は全カテゴリが既定で折りたたまれる仕様
            # （page load 後の setTimeout）。実際のユーザーと同じく
            # 「すべて開く」ボタンを押してから対象の details を操作する。
            page = browser.new_page()
            fetch_urls: list[str] = []
            page.on("response", lambda r: fetch_urls.append(r.url) if "insights_tech.json" in r.url else None)
            page.goto(f"{base}/tech/index.html", wait_until="load")
            page.wait_for_function(
                "document.querySelector('.category-section').classList.contains('collapsed')",
                timeout=3000,
            )
            page.click("[data-toggle-all-cats]")

            det = page.locator("details.insight[data-insight-topic]").first
            det.wait_for(state="attached", timeout=5000)
            topic_id = det.get_attribute("data-insight-topic")
            body = det.locator("[data-insight-pending], .insight-body")

            # <details> が閉じている間は body.inner_text() は空文字になる
            # （ネイティブ挙動。閉じたdetailsの子要素は描画されない）ため、
            # 開閉に依存しない textContent で前提条件を確認する。
            pending_text_before = body.evaluate("el => el.textContent")
            assert "読み込み中" in pending_text_before, "前提条件: クリック前はプレースホルダのはず"
            det.locator("summary").click()
            page.wait_for_function(
                "el => !el.textContent.includes('読み込み中')",
                arg=body.element_handle(),
                timeout=5000,
            )
            body_text_after_click = body.inner_text()
            if "読み込み中" in body_text_after_click:
                failures.append(f"[A] クリック後も読み込み中のまま (topic={topic_id})")
            if not fetch_urls:
                failures.append("[A] insights_tech.json への fetch が観測されなかった")
            print(f"[A] click-toggle: topic={topic_id} fetched={bool(fetch_urls)} "
                  f"body_len={len(body_text_after_click)}")
            page.close()

            # --- Test B: #topic-<id> ハッシュ直接アクセス（.open = true 経路） ----
            page2 = browser.new_page()
            fetch_urls2: list[str] = []
            page2.on("response", lambda r: fetch_urls2.append(r.url) if "insights_tech.json" in r.url else None)
            page2.goto(f"{base}/tech/index.html#topic-{topic_id}", wait_until="load")

            det2 = page2.locator(f'details.insight[data-insight-topic="{topic_id}"]')
            is_open = det2.evaluate("el => el.open")
            if not is_open:
                failures.append(f"[B] ハッシュ直接アクセスで details.open が true にならない (topic={topic_id})")

            body2 = det2.locator("[data-insight-pending], .insight-body")
            try:
                page2.wait_for_function(
                    "el => !el.textContent.includes('読み込み中')",
                    arg=body2.element_handle(),
                    timeout=5000,
                )
                body2_text = body2.inner_text()
            except Exception:
                body2_text = body2.inner_text()

            if "読み込み中" in body2_text:
                failures.append(f"[B] ハッシュ直接アクセス後も読み込み中のまま＝toggleイベント未発火 (topic={topic_id})")
            if not fetch_urls2:
                failures.append("[B] ハッシュ直接アクセスで insights_tech.json への fetch が観測されなかった")
            print(f"[B] hash-jump: topic={topic_id} open={is_open} fetched={bool(fetch_urls2)} "
                  f"body_len={len(body2_text)}")
            page2.close()

            browser.close()
    finally:
        if server_proc is not None:
            server_proc.terminate()
            server_proc.wait(timeout=5)
        _remove_junction(link_path)
        try:
            tmp_root.rmdir()
        except OSError:
            pass

    if failures:
        print("\n=== FAIL ===")
        for f in failures:
            print(" -", f)
        return 1

    print("\n=== PASS ===")
    print("クリック(A)・ハッシュ直接アクセス(B)ともに toggle イベントが発火し、遅延ロードが正しく動作することを確認した。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
