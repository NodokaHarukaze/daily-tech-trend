import argparse
import os
import re
import sqlite3
import sys
import time

from llm_insights_api import (
    _extract_json_object,
    _get_lm_content,
    call_llm,
    call_llm_short_news,
    post_ollama,
)
from llm_insights_pipeline import (
    compute_src_hash,
    connect,
    pick_topic_inputs,
    postprocess_insight,
    upsert_insight,
)


def _looks_english(s: str) -> bool:
    letters = sum(c.isascii() and c.isalpha() for c in s)
    return letters >= 20


def _has_japanese(s: str) -> bool:
    return bool(re.search(r"[ぁ-んァ-ン一-龥]", s or ""))


def _now_sec():
    return time.perf_counter()


def _parse_args(argv: list[str]):
    parser = argparse.ArgumentParser(description="Generate LLM insights for topics")
    parser.add_argument("limit", nargs="?", type=int, default=120, help="Maximum topics to process")
    parser.add_argument(
        "--rescue",
        action="store_true",
        help=(
            "Widen the candidate set to include news topics and broken insights. "
            "Rows whose source hash is unchanged are still skipped unless they need repair."
        ),
    )
    parser.add_argument(
        "--max-sec",
        type=int,
        default=int(os.environ.get("LLM_MAX_SEC", "300") or "300"),
        help="Maximum processing time in seconds (default: env LLM_MAX_SEC or 300)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=float(os.environ.get("LLM_DELAY_SEC", "3") or "3"),
        help="Delay in seconds between LLM requests (default: env LLM_DELAY_SEC or 3)",
    )
    return parser.parse_args(argv)


def _row_get(row, key, default=""):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _needs_repair(row) -> bool:
    """既存 insight が壊れており、内容が変わっていなくても作り直すべきかを判定する。

    importance が 0 のまま、または要約が空のまま保存されている行は、
    元記事が更新されていなくても生成し直す価値がある。
    それ以外は同じ入力から同じ結果を作るだけなので再生成しない。
    """
    if int(_row_get(row, "prev_importance", 0) or 0) == 0:
        return True
    return bool(int(_row_get(row, "prev_summary_empty", 0) or 0))


def main():
    t0 = _now_sec()
    args = _parse_args(sys.argv[1:])
    limit = args.limit
    rescue = args.rescue
    max_sec = max(0, int(args.max_sec or 0))
    delay = max(0.0, float(args.delay or 0))

    conn = connect()
    skipped_unchanged = 0
    processed = 0
    try:
        rows = pick_topic_inputs(conn, limit=limit, rescue=rescue)
        print(f"[TIME] llm candidates={len(rows)} limit={limit} rescue={int(rescue)}")

        for r in rows:
            if max_sec and (_now_sec() - t0) >= max_sec:
                print(f"[TIME] llm budget reached sec={_now_sec() - t0:.1f} max_sec={max_sec}")
                break
            topic_id = r["topic_id"]
            try:
                title = (r["topic_title"] or "").strip()
                url = (r["url"] or "").strip()
                body = (r["body"] or "").strip()
                src_hash = compute_src_hash(title, url, body)

                # 内容ハッシュが前回と同じなら、LLM に投げても同じ結果にしかならない。
                # rescue でも同様なので一律スキップし、予算を未生成トピックへ回す。
                # ただし壊れた insight（importance=0 / 要約が空）だけは作り直す。
                prev_hash = (r["prev_src_hash"] or "").strip()
                if prev_hash and (prev_hash == src_hash) and not _needs_repair(r):
                    skipped_unchanged += 1
                    continue

                t1 = _now_sec()
                category = _row_get(r, "category", "other") or "other"
                kind = _row_get(r, "kind", "")
                raw = call_llm(title, category, url, body, kind=kind)
                ins = postprocess_insight(raw, r)
                print(f"[TIME] llm_one topic={topic_id} sec={_now_sec() - t1:.1f}")

                upsert_insight(conn, topic_id, ins, r["src_article_id"], src_hash)
                conn.commit()
                processed += 1
                print(f"[OK] insight saved topic_id={topic_id} imp={ins['importance']} cat={r['category']}")
                if delay > 0:
                    time.sleep(delay)
            except sqlite3.Error:
                # DB異常は全トピックで再発するため握りつぶさず停止させる
                raise
            except Exception as e:
                print(
                    "[WARN] insight skipped "
                    f"topic_id={topic_id} cat={_row_get(r, 'category', '')} source={_row_get(r, 'source', '')} "
                    f"url={_row_get(r, 'url', '')} err={e}"
                )
                continue
    finally:
        conn.close()
    print(
        f"[TIME] step=llm end sec={_now_sec() - t0:.1f} "
        f"processed={processed} skipped_unchanged={skipped_unchanged}"
    )


if __name__ == "__main__":
    main()
