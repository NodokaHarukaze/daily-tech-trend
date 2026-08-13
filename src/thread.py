import logging
import re
from datetime import datetime, timezone
from rapidfuzz import fuzz
from db import connect

logger = logging.getLogger(__name__)

# トピック統合が粗くなりすぎないよう、統合閾値を引き上げる
TOPIC_SIM = 88
NEWS_TOPIC_SIM = 94

# 比較対象を増やしすぎないため、各カテゴリで直近の topic をこの件数だけ比較
CANDIDATE_TOPICS_PER_CAT = 400

NOISE_PATTERNS = [
    r"\bupdate\b", r"\breleased?\b", r"\bannounce[sd]?\b", r"\bintroducing\b",
    r"\bpreview\b", r"\bga\b", r"\bgeneral availability\b",
    r"\bv\d+(\.\d+)*\b", r"\bver\.?\s*\d+(\.\d+)*\b",
    r"\bcve-\d{4}-\d+\b",  # CVEはテーマに寄与するが、同テーマがバラける原因にもなるので一旦ノイズ扱い（後で改善可）
]

def normalize_title(title: str) -> str:
    t = (title or "").lower()
    t = re.sub(r"\[[^\]]+\]|\([^)]+\)", " ", t)  # []や()の補足を落とす
    for p in NOISE_PATTERNS:
        t = re.sub(p, " ", t, flags=re.IGNORECASE)
    t = re.sub(r"[^a-z0-9\u3040-\u30ff\u4e00-\u9fff\s\-_/]", " ", t)  # 日本語も残す
    t = re.sub(r"\s+", " ", t).strip()
    return t

def make_topic_key(norm_title: str) -> str:
    # 先頭だけキーにして、表記揺れに強くする
    return norm_title[:120]

def load_topic_candidates(cur):
    """
    各カテゴリの直近トピック候補を読み込む
    {cat: [(topic_id, topic_title_norm, topic_title_raw), ...]}
    """
    cur.execute("""
        SELECT id, title, kind, region, category
        FROM topics
        ORDER BY id DESC
    """)
    by_cat = {}
    for tid, ttitle, kind, region, cat in cur.fetchall():
        if cat is None or not kind:
            continue
        key = (kind, region or "", cat)
        if len(by_cat.get(key, [])) >= CANDIDATE_TOPICS_PER_CAT:
            continue
        by_cat.setdefault(key, []).append((tid, normalize_title(ttitle or ""), ttitle or ""))
    return by_cat

def find_best_topic(norm_title: str, kind: str, region: str, cat: str, candidates_by_cat):
    best_tid = None
    best_score = -1
    sim_threshold = NEWS_TOPIC_SIM if kind == "news" else TOPIC_SIM
    key = (kind, region or "", cat)
    for tid, tnorm, _traw in candidates_by_cat.get(key, []):
        score = fuzz.token_set_ratio(norm_title, tnorm)
        if score > best_score:
            best_score = score
            best_tid = tid
    if best_score >= sim_threshold:
        return best_tid, best_score
    return None, best_score


def ensure_topic_articles_columns(cur):
    cur.execute("PRAGMA table_info(topic_articles)")
    cols = {r[1] for r in cur.fetchall()}
    if "is_representative" not in cols:
        cur.execute("ALTER TABLE topic_articles ADD COLUMN is_representative INTEGER DEFAULT 0")


def mark_news_representative_articles(cur):
    """news トピックごとの代表記事に is_representative=1 を立てる。

    以前は CTE `ranked` を相関サブクエリ `EXISTS(...)` の中から参照していたが、
    SQLite が CTE をマテリアライズせず対象行ごとにウィンドウ関数クエリを
    再計算するため、実測で約490秒かかっていた。
    代表記事を一時テーブルへ一度だけ確定させ、UPDATE 2本に分けることで解消する。
    判定結果（どの行に1が立つか）は従来と同一。
    """
    cur.execute("DROP TABLE IF EXISTS temp._news_rep")
    cur.execute(
        """
        CREATE TEMP TABLE _news_rep AS
        SELECT topic_id, article_id FROM (
          SELECT
            ta.topic_id,
            ta.article_id,
            ROW_NUMBER() OVER (
              PARTITION BY ta.topic_id
              ORDER BY
                CASE COALESCE(NULLIF(a.source_tier,''), 'secondary') WHEN 'primary' THEN 1 ELSE 0 END DESC,
                datetime(substr(replace(replace(COALESCE(NULLIF(a.published_at,''), a.fetched_at),'T',' '),'+00:00',''),1,19)) DESC,
                a.id DESC
            ) AS rn
          FROM topic_articles ta
          JOIN topics t ON t.id = ta.topic_id
          JOIN articles a ON a.id = ta.article_id
          WHERE COALESCE(t.kind,'')='news'
        )
        WHERE rn = 1
        """
    )
    cur.execute(
        "CREATE UNIQUE INDEX temp.idx__news_rep ON _news_rep(topic_id, article_id)"
    )

    # まず news トピック配下を一旦 0 に落としてから、代表行だけ 1 を立てる
    cur.execute(
        """
        UPDATE topic_articles
        SET is_representative = 0
        WHERE topic_id IN (SELECT id FROM topics WHERE COALESCE(kind,'')='news')
        """
    )
    cur.execute(
        """
        UPDATE topic_articles
        SET is_representative = 1
        WHERE topic_id IN (SELECT id FROM topics WHERE COALESCE(kind,'')='news')
          AND EXISTS (
            SELECT 1 FROM _news_rep r
            WHERE r.topic_id = topic_articles.topic_id
              AND r.article_id = topic_articles.article_id
          )
        """
    )
    cur.execute("DROP TABLE temp._news_rep")

def main():
    import time as _time
    t0 = _time.perf_counter()
    logger.info("step=thread start")
    conn = connect()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    ensure_topic_articles_columns(cur)

    # まず既存 topics を候補として読む
    candidates_by_cat = load_topic_candidates(cur)

    # 直近の articles を処理（必要なら件数増やしてOK）
    cur.execute("""
        SELECT id, title, kind, region, category
        FROM articles
        ORDER BY id DESC
        LIMIT 2000
    """)
    articles = cur.fetchall()

    for aid, title, kind, region, cat in articles:
        if not title or not cat or not kind:
            continue

        # すでに topic に紐付いてたらスキップ
        cur.execute("SELECT 1 FROM topic_articles WHERE article_id=? LIMIT 1", (aid,))
        if cur.fetchone():
            continue

        norm = normalize_title(title)
        if not norm:
            continue

        tid, score = find_best_topic(norm, kind, region, cat, candidates_by_cat)

        if tid is None:
            # 新規 topic
            key = make_topic_key(norm)
            # topic_key が衝突しても title は更新せず、既存を使う
            cur.execute("""
                INSERT OR IGNORE INTO topics(topic_key, title, category, kind, region, created_at)
                VALUES(?,?,?,?,?,?)
            """, (key, title[:200], cat, kind, region, now))

            cur.execute("SELECT id, title FROM topics WHERE topic_key=?", (key,))
            row = cur.fetchone()
            if row:
                tid = row[0]
                # 候補にも追加して、以降の記事が同topicに寄るようにする
                ckey = (kind, region or "", cat)
                candidates_by_cat.setdefault(ckey, []).insert(0, (tid, normalize_title(row[1] or ""), row[1] or ""))

        # 紐付け
        cur.execute("INSERT OR IGNORE INTO topic_articles(topic_id, article_id) VALUES(?,?)", (tid, aid))

    # ツリー（topic内をpublished_at→idで並べ、前→後を親子）
    cur.execute("""
        SELECT ta.topic_id, a.id, COALESCE(a.published_at,''), a.title
        FROM topic_articles ta
        JOIN articles a ON a.id=ta.article_id
        ORDER BY ta.topic_id, COALESCE(a.published_at,''), a.id
    """)
    rows = cur.fetchall()

    by_topic = {}
    for tid, aid, pub, t in rows:
        by_topic.setdefault(tid, []).append((pub, aid))

    for tid, items in by_topic.items():
        cur.execute("DELETE FROM edges WHERE topic_id=?", (tid,))
        for i in range(1, len(items)):
            parent = items[i-1][1]
            child = items[i][1]
            cur.execute("""
                INSERT OR IGNORE INTO edges(topic_id, parent_article_id, child_article_id)
                VALUES(?,?,?)
            """, (tid, parent, child))

    mark_news_representative_articles(cur)

    conn.commit()
    conn.close()
    logger.info("step=thread end sec=%.1f", _time.perf_counter() - t0)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()
