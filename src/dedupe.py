import logging
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime
from typing import Dict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

from difflib import SequenceMatcher

from db import connect, ensure_column

# カテゴリ別に重複判定しきい値を調整
# 値が低いほど「重複」と判定されやすい。
CATEGORY_THRESHOLDS: Dict[str, int] = {
    "ai": 88,
    "security": 90,
    "cloud": 89,
    "devtools": 90,
    "business": 93,
    "policy": 94,
    "default": 91,
}

TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
}

# 速度劣化を防ぐため、カテゴリ内で比較する候補数を制限する。
CANDIDATE_WINDOW_PER_CATEGORY = {
    "default": 600,
    "business": 420,
    "ai": 520,
}
CANDIDATE_WINDOW_PER_BLOCK = {
    "default": 120,
    "business": 80,
    "ai": 100,
}

# 類似度計算前の軽量フィルタ（大きく離れた候補を除外）
MAX_TOKEN_COUNT_DIFF = 8
MAX_TITLE_LENGTH_DIFF = 48


def _now_sec() -> float:
    return time.perf_counter()


def normalize_title(title: str) -> str:
    if not title:
        return ""
    s = unicodedata.normalize("NFKC", title).lower()
    s = re.sub(r"\s+", " ", s)
    # 記号を空白化して語順比較のノイズを減らす
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_url(url: str) -> str:
    if not url:
        return ""
    p = urlsplit(url)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/")

    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False) if k not in TRACKING_QUERY_KEYS]
    q.sort()
    query = urlencode(q, doseq=True)

    scheme = p.scheme.lower() or "https"
    return urlunsplit((scheme, host, path, query, ""))


def category_threshold(category: str) -> int:
    key = (category or "").strip().lower()
    return CATEGORY_THRESHOLDS.get(key, CATEGORY_THRESHOLDS["default"])


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a or "", b or "").ratio() * 100




def _title_tokens(title: str) -> list[str]:
    return [t for t in (title or "").split() if t]


def _blocking_keys(norm_title: str) -> tuple[str, ...]:
    tokens = _title_tokens(norm_title)
    if not tokens:
        return ("",)
    first = tokens[0]
    pair = " ".join(tokens[:2])
    triple = " ".join(tokens[:3])
    return tuple(k for k in (first, pair, triple) if k)


def _token_set_ratio(a: str, b: str) -> float:
    ta = set((a or "").split())
    tb = set((b or "").split())
    return _token_set_ratio_from_sets(ta, tb)


def _token_set_ratio_from_sets(ta: set[str], tb: set[str]) -> float:
    sa = " ".join(sorted(ta))
    sb = " ".join(sorted(tb))
    return _ratio(sa, sb)


def _window_for_category(window_config: dict[str, int], category: str) -> int:
    key = (category or "").strip().lower()
    return window_config.get(key, window_config["default"])


def _is_viable_candidate(
    norm_title: str,
    title_token_count: int,
    kept_entry: dict,
) -> bool:
    token_diff = abs(title_token_count - kept_entry["token_count"])
    if token_diff > MAX_TOKEN_COUNT_DIFF:
        return False
    char_diff = abs(len(norm_title) - kept_entry["title_length"])
    return char_diff <= MAX_TITLE_LENGTH_DIFF


def _composite_score(title_score: float, url_score: float) -> float:
    # タイトル寄りだがURL一致も加点する複合スコア
    return title_score * 0.75 + url_score * 0.25


def ensure_dedupe_log_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dedupe_judgments (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          article_id INTEGER NOT NULL,
          kept_article_id INTEGER NOT NULL,
          article_source TEXT,
          kept_source TEXT,
          article_category TEXT,
          title_score REAL,
          url_score REAL,
          composite_score REAL,
          threshold REAL,
          title_match INTEGER,
          url_exact_match INTEGER,
          decision TEXT,
          reason TEXT,
          created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_dedupe_judgments_article
        ON dedupe_judgments(article_id)
        """
    )


def log_decision(
    cur,
    *,
    article_id: int,
    kept_article_id: int,
    article_source: str,
    kept_source: str,
    article_category: str,
    title_score: float,
    url_score: float,
    composite_score: float,
    threshold: float,
    title_match: bool,
    url_exact_match: bool,
    decision: str,
    reason: str,
):
    cur.execute(
        """
        INSERT INTO dedupe_judgments(
          article_id, kept_article_id, article_source, kept_source, article_category,
          title_score, url_score, composite_score, threshold,
          title_match, url_exact_match, decision, reason, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            article_id,
            kept_article_id,
            article_source,
            kept_source,
            article_category,
            title_score,
            url_score,
            composite_score,
            threshold,
            1 if title_match else 0,
            1 if url_exact_match else 0,
            decision,
            reason,
            datetime.utcnow().isoformat(timespec="seconds"),
        ),
    )


def main():
    t0 = _now_sec()
    logger.info("step=dedupe start")

    conn = connect()
    cur = conn.cursor()
    ensure_dedupe_log_table(cur)
    # 判定済みフラグ。既に「残す」と判定された記事を毎回再判定しないための増分化用
    ensure_column(cur, "articles", "dedupe_checked", "INTEGER DEFAULT 0")

    cur.execute(
        "SELECT id, source, category, title, url, url_norm, COALESCE(dedupe_checked,0) "
        "FROM articles ORDER BY id DESC"
    )
    rows = cur.fetchall()

    seen_by_category: Dict[str, list[dict]] = {}
    seen_by_category_block: Dict[str, dict[str, list[dict]]] = defaultdict(dict)
    seen_by_norm_url: Dict[str, dict] = {}
    deleted = 0
    candidate_checked = 0
    judged_ids: list[int] = []

    for idx, (i, source, category, title, url, url_norm, already_checked) in enumerate(rows, start=1):
        cat_key = (category or "").strip().lower() or "default"
        norm_title = normalize_title(title)
        norm_url = normalize_url(url_norm or url)
        threshold = category_threshold(category)

        exact_url_kept = seen_by_norm_url.get(norm_url) if norm_url else None
        if exact_url_kept is not None:
            cur.execute("DELETE FROM articles WHERE id=?", (i,))
            log_decision(
                cur,
                article_id=i,
                kept_article_id=exact_url_kept["id"],
                article_source=source,
                kept_source=exact_url_kept["source"],
                article_category=category,
                title_score=0.0,
                url_score=100.0,
                composite_score=100.0,
                threshold=threshold,
                title_match=False,
                url_exact_match=True,
                decision="delete",
                reason="url_exact",
            )
            deleted += 1
            continue

        category_seen = seen_by_category.get(cat_key, [])
        category_block = seen_by_category_block[cat_key]
        category_window = _window_for_category(CANDIDATE_WINDOW_PER_CATEGORY, cat_key)
        block_window = _window_for_category(CANDIDATE_WINDOW_PER_BLOCK, cat_key)
        title_tokens = _title_tokens(norm_title)
        title_token_set = set(title_tokens)
        title_token_count = len(title_tokens)

        blocking_keys = _blocking_keys(norm_title)

        if already_checked:
            # 前回までの実行で「残す」と判定済みの記事は再判定しない。
            # 候補プール（カテゴリ／ブロック／URL索引）への登録だけを行い、
            # 後続の新規記事が従来と同一の候補集合と比較できる状態を保つ。
            # 候補を空にすることで下の for/else が必ず else（＝残す）へ抜ける。
            candidate_slice: list[dict] = []
        else:
            blocked_candidates: list[dict] = []
            seen_ids: set[int] = set()
            for bk in blocking_keys:
                for kept in reversed(category_block.get(bk, [])[-block_window:]):
                    kept_id = kept["id"]
                    if kept_id in seen_ids:
                        continue
                    seen_ids.add(kept_id)
                    blocked_candidates.append(kept)

            if blocked_candidates:
                candidate_slice = blocked_candidates
            else:
                candidate_slice = list(reversed(category_seen[-category_window:]))

        for kept in candidate_slice:
            candidate_checked += 1
            if not _is_viable_candidate(norm_title, title_token_count, kept):
                continue

            kept_title = kept["norm_title"]
            kept_url = kept["norm_url"]

            title_score = _token_set_ratio_from_sets(title_token_set, kept["token_set"])
            title_match = title_score >= (threshold - 5)
            if not title_match:
                continue

            url_score = _ratio(norm_url, kept_url)
            composite = _composite_score(title_score, url_score)
            url_exact_match = bool(norm_url and norm_url == kept_url)

            is_duplicate = url_exact_match or composite >= threshold
            if is_duplicate:
                reason = "url_exact" if url_exact_match else "composite_threshold"
                cur.execute("DELETE FROM articles WHERE id=?", (i,))
                log_decision(
                    cur,
                    article_id=i,
                    kept_article_id=kept["id"],
                    article_source=source,
                    kept_source=kept["source"],
                    article_category=category,
                    title_score=title_score,
                    url_score=url_score,
                    composite_score=composite,
                    threshold=threshold,
                    title_match=title_match,
                    url_exact_match=url_exact_match,
                    decision="delete",
                    reason=reason,
                )
                deleted += 1
                break
        else:
            kept_entry = {
                "id": i,
                "norm_title": norm_title,
                "norm_url": norm_url,
                "source": source,
                "token_set": set(title_tokens),
                "token_count": title_token_count,
                "title_length": len(norm_title),
            }
            category_seen.append(kept_entry)
            seen_by_category[cat_key] = category_seen

            for bk in blocking_keys:
                key_items = category_block.setdefault(bk, [])
                key_items.append(kept_entry)
                if len(key_items) > block_window * 2:
                    del key_items[:-block_window]

            if norm_url:
                seen_by_norm_url[norm_url] = kept_entry

            if not already_checked:
                judged_ids.append(i)

        if idx % 500 == 0:
            logger.info(
                "dedupe progress processed=%d/%d deleted=%d candidate_checked=%d sec=%.1f",
                idx, len(rows), deleted, candidate_checked, _now_sec() - t0,
            )

    # 今回判定して残した記事に判定済みフラグを立て、次回以降の再判定対象から外す
    if judged_ids:
        cur.executemany(
            "UPDATE articles SET dedupe_checked=1 WHERE id=?",
            [(x,) for x in judged_ids],
        )

    # 削除した記事に紐づく topic_articles の孤児行を掃除する
    # （残すとトピックが「記事0件のゾンビ」としてページに現れ、タイムラインが空になる）
    # テスト用の最小DBには topic_articles が無いため存在チェックを挟む
    if cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='topic_articles'"
    ).fetchone():
        cur.execute(
            "DELETE FROM topic_articles WHERE article_id NOT IN (SELECT id FROM articles)"
        )
        orphans_removed = cur.rowcount
        if orphans_removed:
            logger.info("dedupe cleaned orphan topic_articles rows=%d", orphans_removed)

    conn.commit()
    conn.close()
    logger.info(
        "step=dedupe end sec=%.1f deleted=%d total=%d judged=%d candidate_checked=%d",
        _now_sec() - t0, deleted, len(rows), len(judged_ids), candidate_checked,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()
