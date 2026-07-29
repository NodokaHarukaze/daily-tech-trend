"""トップページ軽量化（details.insight 遅延ロード）のテスト。

`_tech_has_insight` / `_news_has_insight` はテンプレート側の表示条件
（tech.html の details.insight / news.html の details.insight）と一致させる
ためのヘルパーで、`_insight_payload` / `write_insights_json` は
insights_tech.json / insights_news.json の書き出しを担う。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import render_main


# --- _tech_has_insight -------------------------------------------------------
def test_tech_has_insight_true_when_summary_present():
    assert render_main._tech_has_insight({"summary": "要約あり"}) is True


def test_tech_has_insight_true_when_only_evidence_urls_present():
    assert render_main._tech_has_insight({"evidence_urls": ["https://example.com"]}) is True


def test_tech_has_insight_false_when_all_fields_empty():
    t = {
        "summary": "",
        "key_points": [],
        "perspectives": {},
        "perspective_digest": {},
        "evidence_urls": [],
    }
    assert render_main._tech_has_insight(t) is False


# --- _news_has_insight --------------------------------------------------------
def test_news_has_insight_true_when_importance_basis_present():
    # importance_basis は _news_importance_basis_simple() が常に非空文字列を返すため、
    # news.html の details.insight は実質的に常に表示される（既存挙動どおり）
    assert render_main._news_has_insight({"importance_basis": "通常"}) is True


def test_news_has_insight_true_when_perspectives_subfield_present():
    it = {"perspectives": {"engineer": "技術者コメント"}}
    assert render_main._news_has_insight(it) is True


def test_news_has_insight_false_when_perspectives_all_blank():
    # perspectives 自体は非空dictでも中身が全部空文字ならnews側は非表示（tech側とは条件が異なる）
    it = {"perspectives": {"engineer": "", "management": "", "consumer": ""}}
    assert render_main._news_has_insight(it) is False


def test_news_has_insight_false_when_completely_empty():
    assert render_main._news_has_insight({}) is False


# --- _insight_payload ---------------------------------------------------------
def test_insight_payload_includes_only_truthy_fields():
    t = {
        "summary": "要約テキスト",
        "key_points": ["ポイント1"],
        "perspectives": {},
        "perspective_digest": None,
        "evidence_urls": [],
    }
    payload = render_main._insight_payload(t)
    assert payload == {"summary": "要約テキスト", "key_points": ["ポイント1"]}


def test_insight_payload_includes_importance_basis_for_news():
    it = {"summary": "s", "importance_basis": "算出根拠テキスト"}
    payload = render_main._insight_payload(it)
    assert payload["importance_basis"] == "算出根拠テキスト"


def test_insight_payload_empty_dict_when_no_content():
    assert render_main._insight_payload({}) == {}


# --- write_insights_json -------------------------------------------------------
def test_write_insights_json_creates_file_under_assets_data(tmp_path):
    items = {"123": {"summary": "テスト要約"}}
    path = render_main.write_insights_json(tmp_path, "tech", items)

    assert path == tmp_path / "assets" / "data" / "insights_tech.json"
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == items


def test_write_insights_json_overwrites_existing_file(tmp_path):
    render_main.write_insights_json(tmp_path, "news", {"1": {"summary": "旧"}})
    render_main.write_insights_json(tmp_path, "news", {"2": {"summary": "新"}})

    path = tmp_path / "assets" / "data" / "insights_news.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == {"2": {"summary": "新"}}
