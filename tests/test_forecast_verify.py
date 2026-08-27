"""forecast_verify の検証タイミング・スコア集約のユニットテスト"""

import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _make_db():
    """テスト用のインメモリDBを作成"""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE forecast_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT,
            file_path TEXT,
            executive_summary TEXT,
            created_at TEXT,
            accuracy_score REAL,
            verified_at TEXT,
            model_id TEXT,
            temperature_config TEXT
        )
    """)
    cur.execute("""
        CREATE UNIQUE INDEX idx_forecast_date ON forecast_reports(report_date)
    """)
    cur.execute("""
        CREATE TABLE forecast_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL,
            horizon TEXT NOT NULL,
            verification_round INTEGER NOT NULL DEFAULT 1,
            verdict_json TEXT,
            accuracy_score REAL,
            undetermined_count INTEGER DEFAULT 0,
            verified_at TEXT NOT NULL,
            digest_hours INTEGER
        )
    """)
    cur.execute("""
        CREATE UNIQUE INDEX idx_fv_date_horizon_round
        ON forecast_verifications(report_date, horizon, verification_round)
    """)
    return conn, cur


class TestFindVerificationTargets:
    """_find_verification_targets のテスト"""

    def test_short_term_after_7_days(self):
        """1週間後予測は7日経過で検証対象になる"""
        from forecast_verify import _find_verification_targets
        conn, cur = _make_db()
        cur.execute(
            "INSERT INTO forecast_reports (report_date, file_path) VALUES (?, ?)",
            ("2026-03-01", "data/forecasts/report_2026-03-01.md"),
        )
        conn.commit()

        today = datetime(2026, 3, 9, tzinfo=timezone.utc)  # 8日後
        targets = _find_verification_targets(cur, today)

        horizons = [t["horizon"] for t in targets]
        assert "1週間後" in horizons
        # 中期・長期はまだ対象外
        assert "1〜6ヶ月後" not in horizons
        assert "1年後" not in horizons
        conn.close()

    def test_short_term_before_7_days(self):
        """1週間後予測は7日未満では検証対象にならない"""
        from forecast_verify import _find_verification_targets
        conn, cur = _make_db()
        cur.execute(
            "INSERT INTO forecast_reports (report_date, file_path) VALUES (?, ?)",
            ("2026-03-01", "data/forecasts/report_2026-03-01.md"),
        )
        conn.commit()

        today = datetime(2026, 3, 6, tzinfo=timezone.utc)  # 5日後
        targets = _find_verification_targets(cur, today)
        assert len(targets) == 0
        conn.close()

    def test_midterm_after_30_days(self):
        """1〜6ヶ月後予測は30日経過でラウンド1が検証対象になる"""
        from forecast_verify import _find_verification_targets
        conn, cur = _make_db()
        cur.execute(
            "INSERT INTO forecast_reports (report_date, file_path) VALUES (?, ?)",
            ("2026-01-01", "data/forecasts/report_2026-01-01.md"),
        )
        conn.commit()

        today = datetime(2026, 2, 1, tzinfo=timezone.utc)  # 31日後
        targets = _find_verification_targets(cur, today)

        midterm = [t for t in targets if t["horizon"] == "1〜6ヶ月後"]
        assert len(midterm) == 1
        assert midterm[0]["round"] == 1
        conn.close()

    def test_skip_already_verified_round(self):
        """既に検証済みのラウンドはスキップされる"""
        from forecast_verify import _find_verification_targets
        conn, cur = _make_db()
        cur.execute(
            "INSERT INTO forecast_reports (report_date, file_path) VALUES (?, ?)",
            ("2026-03-01", "data/forecasts/report_2026-03-01.md"),
        )
        # ラウンド1は検証済み
        cur.execute("""
            INSERT INTO forecast_verifications
            (report_date, horizon, verification_round, accuracy_score, undetermined_count, verified_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("2026-03-01", "1週間後", 1, 0.5, 0, "2026-03-09T00:00:00+00:00"))
        conn.commit()

        today = datetime(2026, 3, 10, tzinfo=timezone.utc)
        targets = _find_verification_targets(cur, today)
        short_targets = [t for t in targets if t["horizon"] == "1週間後"]
        assert len(short_targets) == 0
        conn.close()

    def test_round2_requires_undetermined(self):
        """ラウンド2は前ラウンドに未確定がある場合のみ対象"""
        from forecast_verify import _find_verification_targets
        conn, cur = _make_db()
        cur.execute(
            "INSERT INTO forecast_reports (report_date, file_path) VALUES (?, ?)",
            ("2026-01-01", "data/forecasts/report_2026-01-01.md"),
        )
        # ラウンド1実施済み、未確定あり
        cur.execute("""
            INSERT INTO forecast_verifications
            (report_date, horizon, verification_round, accuracy_score, undetermined_count, verified_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("2026-01-01", "1〜6ヶ月後", 1, None, 3, "2026-02-01T00:00:00+00:00"))
        conn.commit()

        today = datetime(2026, 4, 2, tzinfo=timezone.utc)  # 91日後
        targets = _find_verification_targets(cur, today)
        midterm_r2 = [t for t in targets if t["horizon"] == "1〜6ヶ月後" and t["round"] == 2]
        assert len(midterm_r2) == 1
        conn.close()

    def test_round2_skipped_if_all_determined(self):
        """前ラウンドが全て確定済みならラウンド2はスキップ"""
        from forecast_verify import _find_verification_targets
        conn, cur = _make_db()
        cur.execute(
            "INSERT INTO forecast_reports (report_date, file_path) VALUES (?, ?)",
            ("2026-01-01", "data/forecasts/report_2026-01-01.md"),
        )
        # ラウンド1実施済み、未確定なし
        cur.execute("""
            INSERT INTO forecast_verifications
            (report_date, horizon, verification_round, accuracy_score, undetermined_count, verified_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("2026-01-01", "1〜6ヶ月後", 1, 0.8, 0, "2026-02-01T00:00:00+00:00"))
        conn.commit()

        today = datetime(2026, 4, 2, tzinfo=timezone.utc)  # 91日後
        targets = _find_verification_targets(cur, today)
        midterm_r2 = [t for t in targets if t["horizon"] == "1〜6ヶ月後" and t["round"] == 2]
        assert len(midterm_r2) == 0
        conn.close()


class TestVerifyHorizon:
    """verify_horizon のテスト"""

    def _make_item(self, title="テスト予測", body="テスト本文"):
        from forecast_parser import PredictionItem
        return PredictionItem(impact="中", confidence="中", title=title, body=body)

    @patch("forecast_verify._call_verify_llm")
    def test_first_round_calls_llm(self, mock_llm):
        """初回検証では全項目をLLMに渡す"""
        from forecast_verify import verify_horizon
        mock_llm.return_value = [
            {"title": "予測A", "verdict": "的中", "accuracy": 1.0, "reason": "当たった"},
            {"title": "予測B", "verdict": "未確定", "accuracy": None, "reason": "不明"},
        ]
        items = [self._make_item("予測A"), self._make_item("予測B")]
        verdicts, accuracy, undetermined = verify_horizon(
            "2026-03-01", "1週間後", items, "ダイジェスト"
        )
        assert len(verdicts) == 2
        assert accuracy == 1.0
        assert undetermined == 1
        mock_llm.assert_called_once()

    @patch("forecast_verify._call_verify_llm")
    def test_recheck_only_undetermined(self, mock_llm):
        """再検証では未確定の項目のみLLMに渡す"""
        from forecast_verify import verify_horizon
        prev = [
            {"title": "予測A", "verdict": "的中", "accuracy": 1.0, "reason": "当たった"},
            {"title": "予測B", "verdict": "未確定", "accuracy": None, "reason": "不明"},
        ]
        mock_llm.return_value = [
            {"title": "予測B", "verdict": "外れ", "accuracy": 0.0, "reason": "外れた"},
        ]
        items = [self._make_item("予測A"), self._make_item("予測B")]
        verdicts, accuracy, undetermined = verify_horizon(
            "2026-03-01", "1〜6ヶ月後", items, "ダイジェスト", prev_verdicts=prev
        )
        # 的中(1.0) + 外れ(0.0) の平均 = 0.5
        assert accuracy == 0.5
        assert undetermined == 0
        # LLMには未確定の予測Bだけ渡される
        call_args = mock_llm.call_args[0][1]
        assert "予測B" in call_args
        assert "予測A" not in call_args

    @patch("forecast_verify._call_verify_llm")
    def test_all_undetermined_returns_none(self, mock_llm):
        """全て未確定の場合 accuracy は None"""
        from forecast_verify import verify_horizon
        mock_llm.return_value = [
            {"title": "予測A", "verdict": "未確定", "accuracy": None, "reason": "不明"},
        ]
        items = [self._make_item("予測A")]
        verdicts, accuracy, undetermined = verify_horizon(
            "2026-03-01", "1週間後", items, "ダイジェスト"
        )
        assert accuracy is None
        assert undetermined == 1

    @patch("forecast_verify._call_verify_llm")
    def test_all_previously_determined_no_llm_call(self, mock_llm):
        """全項目が確定済みならLLMは呼ばれない"""
        from forecast_verify import verify_horizon
        prev = [
            {"title": "予測A", "verdict": "的中", "accuracy": 1.0, "reason": "OK"},
        ]
        items = [self._make_item("予測A")]
        verdicts, accuracy, undetermined = verify_horizon(
            "2026-03-01", "1〜6ヶ月後", items, "ダイジェスト", prev_verdicts=prev
        )
        assert accuracy == 1.0
        assert undetermined == 0
        mock_llm.assert_not_called()


class TestUpdateReportAccuracy:
    """_update_report_accuracy のテスト"""

    def test_aggregate_from_verifications(self):
        """時間軸別の最新ラウンドからの集約が正しい"""
        from forecast_verify import _update_report_accuracy
        conn, cur = _make_db()
        cur.execute(
            "INSERT INTO forecast_reports (report_date, file_path) VALUES (?, ?)",
            ("2026-03-01", "test.md"),
        )
        # 1週間後: accuracy=0.8
        cur.execute("""
            INSERT INTO forecast_verifications
            (report_date, horizon, verification_round, accuracy_score, undetermined_count, verified_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("2026-03-01", "1週間後", 1, 0.8, 0, "2026-03-09T00:00:00+00:00"))
        # 1〜6ヶ月後: ラウンド1=None, ラウンド2=0.6（最新のラウンド2を使う）
        cur.execute("""
            INSERT INTO forecast_verifications
            (report_date, horizon, verification_round, accuracy_score, undetermined_count, verified_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("2026-03-01", "1〜6ヶ月後", 1, None, 3, "2026-04-01T00:00:00+00:00"))
        cur.execute("""
            INSERT INTO forecast_verifications
            (report_date, horizon, verification_round, accuracy_score, undetermined_count, verified_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("2026-03-01", "1〜6ヶ月後", 2, 0.6, 0, "2026-06-01T00:00:00+00:00"))
        conn.commit()

        _update_report_accuracy(cur, "2026-03-01")
        conn.commit()

        cur.execute("SELECT accuracy_score FROM forecast_reports WHERE report_date = ?", ("2026-03-01",))
        score = cur.fetchone()[0]
        # (0.8 + 0.6) / 2 = 0.7
        assert abs(score - 0.7) < 0.01
        conn.close()

    def test_no_verifications(self):
        """検証結果がない場合は更新しない"""
        from forecast_verify import _update_report_accuracy
        conn, cur = _make_db()
        cur.execute(
            "INSERT INTO forecast_reports (report_date, file_path) VALUES (?, ?)",
            ("2026-03-01", "test.md"),
        )
        conn.commit()

        _update_report_accuracy(cur, "2026-03-01")
        conn.commit()

        cur.execute("SELECT accuracy_score FROM forecast_reports WHERE report_date = ?", ("2026-03-01",))
        score = cur.fetchone()[0]
        assert score is None
        conn.close()


class TestExtractBodyHead:
    """_extract_body_head: title 空アイテムを verify LLM に渡すための代替ラベル抽出"""

    def test_picks_prediction_content_line(self):
        from forecast_verify import _extract_body_head
        body = (
            "**影響度: 大 / 確信度: 高**\n\n"
            "- **予測内容**：Microsoft Azure IoT がアジア市場で40%シェアを獲得する\n\n"
            "> **根拠**: 元ニュース見出し\n"
        )
        head = _extract_body_head(body, n=40)
        assert "Microsoft" in head and "Azure" in head

    def test_falls_back_to_first_meaningful_line(self):
        from forecast_verify import _extract_body_head
        body = "本文先頭ライン\n別の行\n"
        head = _extract_body_head(body, n=20)
        assert head.startswith("本文先頭ライン")

    def test_empty_body_returns_empty(self):
        from forecast_verify import _extract_body_head
        assert _extract_body_head("") == ""
        assert _extract_body_head(None) == ""


class TestVerifyHorizonEmptyTitle:
    """title 空アイテムでも verify LLM に意味のあるラベルが渡るかを検証する"""

    @patch("forecast_verify._call_verify_llm")
    def test_empty_title_uses_body_head_label(self, mock_llm):
        from forecast_verify import verify_horizon
        from forecast_parser import PredictionItem
        mock_llm.return_value = [
            {"title": "ラベル代替", "verdict": "未確定", "accuracy": None, "reason": "保留"},
        ]
        item = PredictionItem(
            impact="中", confidence="中",
            title="",  # ★ title 空
            body=(
                "**影響度: 中 / 確信度: 中**\n\n"
                "- **予測内容**：Azure IoT 12%成長と見込まれる\n\n"
                "> **根拠**: 公式発表\n"
            ),
        )
        verify_horizon("2026-05-20", "1週間後", [item], "ダミーダイジェスト")
        # LLM 入力に空ラベルではなく本文先頭が渡っていることを確認
        # （実バグでは `- : 本文...` となって LLM が解析放棄していた）
        user_input = mock_llm.call_args[0][1]
        assert "- : " not in user_input
        assert "Azure IoT" in user_input or "12%成長" in user_input


class TestCallVerifyLLMRetry:
    """_call_verify_llm のリトライ動作: 解析失敗時は None、空配列での上書きを防ぐ"""

    @patch("forecast_verify.post_ollama")
    def test_returns_none_after_all_retries_fail(self, mock_post):
        from forecast_verify import _call_verify_llm
        from unittest.mock import MagicMock
        # 全試行で JSON でない文字列が返るケース
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "choices": [{"message": {"content": "ごめんなさい、JSON を出せませんでした"}}]
        }
        mock_post.return_value = fake_resp
        result = _call_verify_llm("sys", "user", max_retries=1)
        assert result is None  # 空配列ではなく None
        # 2回試行されたこと
        assert mock_post.call_count == 2

    @patch("forecast_verify.post_ollama")
    def test_returns_parsed_list_on_first_success(self, mock_post):
        from forecast_verify import _call_verify_llm
        from unittest.mock import MagicMock
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "choices": [{"message": {"content": '[{"title": "x", "verdict": "的中", "accuracy": 1.0, "reason": "ok"}]'}}]
        }
        mock_post.return_value = fake_resp
        result = _call_verify_llm("sys", "user", max_retries=2)
        assert isinstance(result, list)
        assert len(result) == 1
        assert mock_post.call_count == 1


class TestExtractJsonArray:
    """_extract_json_array: 深さ追跡による JSON 配列抽出（2026-08-28 追加）。

    本番ログ(2026-08-24〜08-27)で実際に観測された `verify JSON 解析失敗` の原因調査で、
    旧実装(`text.find("[")`/`text.rfind("]")`)は配列の外側に別の `]` があると
    誤った終端を掴んで json.loads が失敗することが分かった。
    """

    def test_plain_array(self):
        from forecast_verify import _extract_json_array
        text = '[{"title": "x", "verdict": "的中"}]'
        assert _extract_json_array(text) == text

    def test_trailing_prose_with_bracket_after_array(self):
        """配列の後にモデルが雑談文を続け、その中に `]` を含めるケース。

        旧実装は rfind("]") で末尾の雑談文中の `]` を終端と誤認し、
        余計な文字列を含んだ壊れた候補文字列を json.loads に渡していた。
        """
        from forecast_verify import _extract_json_array
        text = '[{"title": "x", "verdict": "的中"}]\n上記の予測[的中]について説明します。'
        assert _extract_json_array(text) == '[{"title": "x", "verdict": "的中"}]'

    def test_bracket_inside_string_value(self):
        """文字列値の中に `[`/`]` が含まれていても、配列自体の終端は正しく検出する。"""
        from forecast_verify import _extract_json_array
        text = '[{"title": "決算[Q3]速報", "verdict": "外れ"}]'
        assert _extract_json_array(text) == text

    def test_code_fence_stripped(self):
        from forecast_verify import _extract_json_array
        text = '```json\n[{"title": "x", "verdict": "未確定"}]\n```'
        assert _extract_json_array(text) == '[{"title": "x", "verdict": "未確定"}]'

    def test_truncated_array_returns_none(self):
        """max_tokens超過等で配列が閉じずに切れた場合は None のまま（既存挙動を維持）。"""
        from forecast_verify import _extract_json_array
        text = '[{"title": "x", "verdict": "的中", "reason": "本文が途中で切'
        assert _extract_json_array(text) is None

    def test_no_array_returns_none(self):
        from forecast_verify import _extract_json_array
        assert _extract_json_array('{"perspective_digest": {"engineer": "..."}}') is None

    def test_empty_text_returns_none(self):
        from forecast_verify import _extract_json_array
        assert _extract_json_array("") is None
        assert _extract_json_array(None) is None
