import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def connect():
    base = Path(__file__).resolve().parent.parent
    return sqlite3.connect(base / "data" / "state.sqlite")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _articles_has_region(conn) -> bool:
    """articles テーブルに region カラムがあるかを検査する（テスト DB 互換）。"""
    try:
        cur = conn.execute("PRAGMA table_info(articles)")
        return any(row[1] == "region" for row in cur.fetchall())
    except Exception:
        return False


def pick_topic_inputs(conn, limit=300, rescue=False):
    """トピック別に LLM 入力を抽出する。

    region (jp/global/other) × kind (news/tech) のバケット単位で
    新しい順に番号を振り、バケット横断でラウンドロビンする。
    これにより jp/news が大量にある日でも global/news が後回しにならない。
    """
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # articles.region が無いテスト DB 互換のために式を切替える
    region_expr = "COALESCE(a.region, '')" if _articles_has_region(conn) else "''"

    sql = f"""
    WITH latest AS (
      SELECT
        ta.topic_id,
        a.id AS article_id,
        a.kind AS kind,
        a.source,
        a.title,
        a.title_ja,
        a.url,
        a.content,
        a.category AS article_category,
        a.published_at,
        a.fetched_at,
        {region_expr} AS region,
        CASE
          WHEN a.kind = 'news' AND {region_expr} = 'global' THEN 'global_news'
          WHEN a.kind = 'news' AND {region_expr} = 'jp'     THEN 'jp_news'
          WHEN a.kind = 'news'                              THEN 'other_news'
          WHEN a.kind = 'tech' AND {region_expr} = 'global' THEN 'global_tech'
          WHEN a.kind = 'tech'                              THEN 'jp_tech'
          ELSE 'other'
        END AS bucket,
        ROW_NUMBER() OVER (
          PARTITION BY ta.topic_id
          ORDER BY COALESCE(a.published_at, a.fetched_at) DESC, a.id DESC
        ) AS rn
      FROM topic_articles ta
      JOIN articles a ON a.id = ta.article_id
      WHERE a.kind IN ('tech','news')
    ),
    pending AS (
      SELECT
        t.id AS topic_id,
        CASE
          WHEN COALESCE(NULLIF(t.category,''),'') = 'news'
            THEN COALESCE(NULLIF(l.title_ja,''), NULLIF(l.title,''), NULLIF(t.title_ja,''), NULLIF(t.title,''))
          ELSE COALESCE(NULLIF(t.title_ja,''), NULLIF(t.title,''), NULLIF(l.title_ja,''), NULLIF(l.title,''))
        END AS topic_title,
        CASE
          WHEN COALESCE(NULLIF(t.category,''),'') = 'news' THEN 'news'
          ELSE COALESCE(NULLIF(l.article_category,''), NULLIF(t.category,''), 'other')
        END AS category,
        l.kind AS kind,
        l.source AS source,
        l.url AS url,
        l.published_at AS published_at,
        l.fetched_at AS fetched_at,
        l.bucket AS bucket,
        t.score_48h AS importance_hint,
        COALESCE(NULLIF(l.content,''), NULLIF(l.title_ja,''), NULLIF(l.title,''), '') AS body,
        l.article_id AS src_article_id,
        ti.src_hash AS prev_src_hash,
        -- 内容ハッシュが同じでも作り直す必要がある「壊れた insight」の判定材料。
        -- summary は本文が長いので、空かどうかのフラグだけを持ち出す。
        COALESCE(ti.importance, 0) AS prev_importance,
        CASE WHEN COALESCE(ti.summary, '') = '' THEN 1 ELSE 0 END AS prev_summary_empty
      FROM topics t
      JOIN latest l ON l.topic_id = t.id AND l.rn = 1
      LEFT JOIN topic_insights ti ON ti.topic_id = t.id
      WHERE (
        ti.topic_id IS NULL
        OR (? = 1 AND (
              COALESCE(NULLIF(t.category,''), '') = 'news'
           OR COALESCE(NULLIF(l.kind,''), '') = 'news'
           OR COALESCE(ti.importance, 0) = 0
           OR COALESCE(ti.summary, '') = ''
           OR COALESCE(ti.src_hash, '') = ''
        ))
      )
    )
    SELECT
      topic_id, topic_title, category, kind, source, url,
      published_at, importance_hint, body, src_article_id, prev_src_hash,
      prev_importance, prev_summary_empty
    FROM (
      SELECT
        pending.*,
        ROW_NUMBER() OVER (
          PARTITION BY bucket
          ORDER BY datetime(COALESCE(NULLIF(published_at,''), fetched_at)) DESC, topic_id DESC
        ) AS bucket_rn
      FROM pending
    )
    -- bucket_rn ASC でバケット横断のラウンドロビン、同順位内は新しい順
    ORDER BY bucket_rn ASC, datetime(COALESCE(NULLIF(published_at,''), fetched_at)) DESC, topic_id DESC
    LIMIT ?
    """
    cur.execute(sql, (1 if rescue else 0, limit))
    return cur.fetchall()




def _row_get(row, key, default=""):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _clip_importance(v: int) -> int:
    return max(0, min(100, int(v)))


def _estimate_news_importance(row: dict) -> tuple[int, str]:
    source = str(_row_get(row, "source", "") or "").lower()
    category = str(_row_get(row, "category", "") or "").lower()

    high_trust = ("reuters", "nikkei", "bloomberg", "ap", "nhk", "wsj", "ft")
    medium_trust = ("cnet", "techcrunch", "the verge", "wired", "itmedia")
    if any(x in source for x in high_trust):
        source_score = 28
        source_label = "高"
    elif any(x in source for x in medium_trust):
        source_score = 18
        source_label = "中"
    else:
        source_score = 12
        source_label = "標準"

    freshness_score = 8
    published_at = (_row_get(row, "published_at", "") or "").strip()
    if published_at:
        try:
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_h = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)
            if age_h <= 6:
                freshness_score = 30
            elif age_h <= 24:
                freshness_score = 24
            elif age_h <= 72:
                freshness_score = 16
            else:
                freshness_score = 10
        except Exception:
            freshness_score = 8

    category_weight = {
        "security": 22,
        "policy": 19,
        "market": 17,
        "industry": 15,
        "company": 13,
        "news": 12,
        "other": 10,
    }.get(category, 11)

    try:
        related = int(_row_get(row, "importance_hint", 0) or 0)
    except Exception:
        related = 0
    related_score = min(20, max(0, related * 2))

    raw = source_score + freshness_score + category_weight + related_score
    importance = _clip_importance(raw)
    reason = (
        f"信頼度:{source_label}({source_score}) / 速報性:{freshness_score} / "
        f"カテゴリ重み:{category_weight} / 関連トピック:{related}件→{related_score}"
    )
    return importance, reason

def compute_src_hash(title: str, url: str, body: str) -> str:
    seed = (title or "") + "\n" + (url or "") + "\n" + (body or "")[:2000]
    return hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()


_PROMPT_SAMPLE_TEXTS = [
    "技術者目線のコメント（50字以内）",
    "経営者目線のコメント（50字以内）",
    "消費者目線のコメント（50字以内）",
    "技術/セキュリティ/運用の観点（必要なら推測: で）",
    "経営/法務/レピュテーションの観点（必要なら推測: で）",
    "利用者/生活者の観点（必要なら推測: で）",
    "利用者/生活者の観点(必要なら推測: で)",
]


def postprocess_insight(ins: dict, row: dict) -> dict:
    ins = dict(ins or {})
    is_news = ((_row_get(row, "category", "") or "").lower() == "news") or ((_row_get(row, "kind", "") or "").lower() == "news")

    try:
        ins["importance"] = int(ins.get("importance") or 0)
    except Exception:
        ins["importance"] = 0
    if ins["importance"] <= 0:
        if is_news:
            estimated_importance, reason = _estimate_news_importance(row)
            ins["importance"] = estimated_importance
            ins["importance_reason"] = reason
        else:
            ins["importance"] = 20
    ins["importance"] = _clip_importance(ins["importance"])

    if not ins.get("type"):
        ins["type"] = "other"

    if not (ins.get("summary") or "").strip():
        if is_news:
            raise RuntimeError("news summary is empty")
        ins["summary"] = "（要約を生成できませんでした）"

    if "evidence_urls" not in ins or not ins["evidence_urls"]:
        ins["evidence_urls"] = [_row_get(row, "url", "")]

    kps = [x for x in (ins.get("key_points") or []) if isinstance(x, str) and x.strip()]
    if is_news:
        fallback = [
            "推測: 詳細はリンク先の本文確認が必要",
            "推測: 背景や影響は本文で確認が必要",
            "推測: 続報で条件が変わる可能性がある",
        ]
        while len(kps) < 3:
            kps.append(fallback[len(kps)])
    else:
        while len(kps) < 3:
            kps.append("（本文中の明確な事実は限定的）")
    ins["key_points"] = kps[:3]

    p = ins.get("perspectives") if isinstance(ins.get("perspectives"), dict) else {}
    news_fb = {
        "engineer": "推測: 技術的な影響や背景はリンク先本文の確認が必要",
        "management": "推測: 社会的影響や対応要否は続報・一次情報で判断が必要",
        "consumer": "推測: 生活への影響は現時点では不明で、詳細確認が望ましい",
    }
    tech_fb = {
        "engineer": "（技術観点のコメントを生成できませんでした）",
        "management": "（経営観点のコメントを生成できませんでした）",
        "consumer": "（利用者観点のコメントを生成できませんでした）",
    }
    fb = news_fb if is_news else tech_fb
    for k in ("engineer", "management", "consumer"):
        val = p.get(k)
        # サンプルテキストがそのまま出力された場合はフォールバックに差し替え
        if isinstance(val, str) and any(sample in val for sample in _PROMPT_SAMPLE_TEXTS):
            val = ""
            p[k] = val
        if not isinstance(val, str) or not val.strip():
            p[k] = fb[k]
    ins["perspectives"] = p
    return ins


def upsert_insight(conn, topic_id, insight, src_article_id: int | None, src_hash: str):
    cur = conn.cursor()
    cur.execute(
        """
    INSERT INTO topic_insights(
        topic_id, importance, type, summary, key_points, evidence_urls, tags,
        perspectives, perspective_digest, updated_at, src_article_id, src_hash, inferred
      )
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(topic_id) DO UPDATE SET
        importance=excluded.importance,
        type=excluded.type,
        summary=excluded.summary,
        key_points=excluded.key_points,
        evidence_urls=excluded.evidence_urls,
        tags=excluded.tags,
        perspectives=excluded.perspectives,
        perspective_digest=excluded.perspective_digest,
        updated_at=excluded.updated_at,
        src_article_id=excluded.src_article_id,
        src_hash=excluded.src_hash,
        inferred=excluded.inferred
    """,
        (
            topic_id,
            int(insight.get("importance") or 0),
            (insight.get("type") or "other"),
            (insight.get("summary") or ""),
            json.dumps(insight.get("key_points") or [], ensure_ascii=False),
            json.dumps(insight.get("evidence_urls") or [], ensure_ascii=False),
            json.dumps(insight.get("tags") or [], ensure_ascii=False),
            json.dumps(insight.get("perspectives") or {}, ensure_ascii=False),
            json.dumps(insight.get("perspective_digest") or {}, ensure_ascii=False),
            _now(),
            src_article_id,
            src_hash,
            int(insight.get("inferred") or 0),
        ),
    )
