# 世界ニュース欠落対策（2026-04-24）

## 現状の問題
- `docs/news/index.html` の「🌍 世界ニュース」表示が **3件のみ**
- DB 実態: BBC World の global/news 記事 298件（直近14日）、insight 未生成 545件
- 今日の insight 生成数 48件（通常 100前後）

## 根本原因
1. `pick_topic_inputs` (llm_insights_pipeline.py:17) が `published_at DESC` 単純順
   → jp/news が上位を占め、global/news が後回しに
2. 既定 `--max-sec 300` で途中打ち切り → 未処理が累積
3. insight 不足を検知する仕組み無し

## 恒久対策

### A. `pick_topic_inputs` フェアネス改善 ✅
- region (jp/global/other) × kind (news/tech) のバケット単位で `ROW_NUMBER() OVER`
- 新しい順は維持しつつバケット間をラウンドロビンで優先度付け
- 既存テスト (test_llm_pick_topic_inputs.py) を壊さない設計（PRAGMA で region 列有無検査）

### B. `pipeline_report.py` に未生成バケット別メトリクス ✅
- `topics_without_insight_by_bucket` を出力 (region × kind)
- 閾値 100 超で WARN 表示

### C. テスト追加 ✅
- `tests/test_llm_pick_topic_inputs.py` にフェアネス検証テスト（4件 green）
- `tests/test_pipeline_report.py` 新規（2件 green）

## 現状復旧
1. A 適用後、`python src/llm_insights_local.py 400 --max-sec 1800` を実行（進行中）
2. `python src/render.py` で再生成（未着手）
3. `docs/news/index.html` の 🌍 カウントを確認（未着手）

## 検証
- `python -m pytest tests/` 全グリーン（pre-existing 12件の失敗は無関係と確認済み）
- 旧 insight の上書きは発生しない（ti IS NULL 条件は維持）
- render 後、global セクションに複数件表示

## 記録
- `tasks/lessons.md` に教訓追記 ✅

---

# 未来予測パイプライン全面オーバーホール（2026-05-20）

## 背景
data/forecasts/report_*.md の品質に複数の構造的欠陥を観測:
- タイトル空欄（5/19で4件、5/20で1件）
- forecast_verifications 67件中 verdict_json 空配列40件（accuracy_score None率97%）
- 「予測」が既報の言い換えに退化／horizon跨ぎ重複／3視点見出し階層崩壊／数値捏造

## 実装した対策（Plan: ~/.claude/plans/lexical-percolating-puddle.md）

### Phase 1: 即効修正 ✅
- T1: `_safe_translate` ラッパー追加、`build_markdown_report` で title 空時に prediction 先頭40字フォールバック、空ならアイテムスキップ
- T2: PERSPECTIVES_USER_TMPL を ### 強制、後処理で `^## ` を `### ` に降格
- T3: `_extract_body_head` 追加で title 空アイテムを LLM に渡す際 body 先頭を代替ラベル化、`_item_key` で carry-over も同様

### Phase 2: 予測品質強化 ✅
- T4: SYSTEM_PROMPT 全面書き換え（未来時制必須／[推定]ラベル必須／impact閾値／confidence閾値）、subjects/numeric_claims フィールド追加
- T5: previous_context に主体+prediction先頭、`_dedupe_across_horizons` (rapidfuzz token_set_ratio≥85) 後処理、requirements.txt に rapidfuzz 追記
- T6: HORIZON_CONFIG["1週間後"].focus 強化、`_SHORT_TERM_LEAK_RE` で「2027年/中期的/長期的/数年」混入を除去

### Phase 3: 構造改善 ✅
- T7: `_aggregate_topic_perspectives` で topic_insights.perspectives (engineer/management/consumer JSON) を集約し、generate_perspectives の主要素材として注入。LLM の役割を「自由推論」から「集約と構造化」に格下げ
- T9: `_call_verify_llm` をリトライ化 (max_retries=2)、JSON-mode `response_format` 試行、解析失敗時は None 返却で空配列上書きを停止、main で既存 verdict 保持

### Phase 4: 信頼性可視化 ✅
- T10: `_validate_numeric_claims` で digest 裏付けのない数値を `unverified_numerics` メタデータに記録、build_markdown_report に ⚠ バッジ＋脚注（本文改変なし）
- T11: 既存 accuracy UI (render_main.py:3044-3049) はコード変更なし。verify 修正で自動的に活性化

### 追加: 破損データ自動回復 ✅
- `_find_verification_targets` を拡張: 過去の verdict_json="[]" + accuracy=None レコードを再検証対象に含め、DELETE して再 INSERT

## 検証
- `tests/test_forecast_generate.py` 38→52テスト（新規14件追加）✅
- `tests/test_forecast_verify.py` 12→18テスト（新規6件追加）✅
- `tests/test_forecast_backward_compat.py` 新設 2テスト（過去30レポートの parse 互換性）✅
- 既存全テスト regression なし（pre-existing 12件の失敗は無関係）

## 記録
- `tasks/lessons.md` に5教訓追記 ✅

## 追補（同日）: verify 実行が極端に遅い問題の修正

### 観測（E2E 実行ログ）
- `forecast_verify.py --limit 1` で 1 件処理に **1237秒**
- ログに `bge-m3:latest failed / nomic-embed-text:latest failed` という想定外の警告
- JSON 解析が3回連続で失敗

### 原因
1. `llm_insights_api._pick_model_candidates` (llm_insights_api.py:71) が Ollama にある
   全モデル ID を区別なくチャット候補に追加し、埋め込み専用モデル (`bge-m3`,
   `nomic-embed-text`) もチャット完了エンドポイントに POST されて 400 で失敗
2. `_call_verify_llm` が試行的に付けていた `response_format={"type":"json_object"}` が
   gpt-oss:20b の応答品質を逆に悪化させていた
3. 解析失敗時に raw 応答の中身が見えず根本原因がわからなかった

### 修正
- **F1**: `_call_verify_llm` から JSON-mode (`response_format`) 試行を完全除去
- **F2**: `_is_embedding_model` 追加 + `_pick_model_candidates` の自動候補から埋め込み除外。
  ユーザー明示指定 (`OLLAMA_MODEL` / `OLLAMA_FALLBACK_MODEL`) は尊重（pinned model は通す）
- **F3**: JSON 解析失敗時に raw 応答の先頭200字を WARN ログに出す

### 効果
- `forecast_verify.py --limit 1` の実行時間: **1237秒 → 8.9秒** (約138倍高速化)
- 埋め込みモデルへのフォールバック試行が消滅
- JSON 解析成功

### 追加テスト
- `tests/test_llm_embedding_filter.py` 新設 8件 ✅
- 全テスト 183件 pass (pre-existing 失敗を除く)

---

# Phase 5 品質チューニング（2026-05-27）

## 背景
オーバーホール後1週間の生成レポートを目視レビューしたところ、新たに見えた品質問題:
- 5/25, 5/27 で **3視点分析が予測と無関係な「発砲事件」「軍事輸送」を分析** している
- 5/27 1週間後 horizon の**根拠 evidence が4件すべて空欄**
- タイトルが途中切れ（「日本のO」「電気SUVを発売し」）・冗長（title=prediction の丸写し）
- `[推定]` ラベルゼロ、⚠ バッジ 12件と過剰警告（5/25）

## 修正内容

### P1: 3視点分析のカテゴリフィルタ ✅
- `_aggregate_topic_perspectives` の SQL に `a.category IN (_TECH_CATEGORIES)` を追加
- allowlist は IT/製造業/テクノロジー系16カテゴリ（ai, dev, system, manufacturing, security, security_ot, policy, market, environment 等）
- 事件・テロ・スポーツ系トピックが3視点分析を乗っ取る問題を根絶
- prefix に category 名を含めて、LLM に「どのカテゴリの立場別コメントか」を可視化

### P2: 根拠空欄アイテムの除去 ✅
- `build_markdown_report` で evidence が空のアイテムを除外
- SYSTEM_PROMPT に「evidence は実質的に必須、元タイトル丸写し禁止、先行事例・市場動向・統計を引用」を強化

### P3: タイトル冗長/切れ防止 ✅
- `_smart_truncate_for_title`: 句読点（。.!?）優先で切り、なければ 70% 以降の読点（、,）で切る、それもなければ … 付与
- `_is_title_redundant`: title が prediction の prefix にある場合の冗長検知
- build_markdown_report で title が空・50字超・冗長のいずれかなら整形

### P4: ⚠ バッジニュートラル化 ✅
- `⚠[出典未確認数値あり]` → `📊 [推定値あり]`（読者を不安にさせない語感）
- 脚注 `⚠ 出典未確認の数値:` → `📊 推定値（ニュース原文での明示なし）:`
- `_validate_numeric_claims` でパーセンテージ (`\d+%`) を検証対象外に。「200社」「90ドル」「1000万」など根拠が問われるべきカウント・金額のみ対象

## 検証
- `tests/test_forecast_generate.py` に新規 17件追加（smart truncate 5, redundant 4, evidence filter 1, badge 1, category filter 1, percentage 1 + 既存修正4）
- 既存テスト regression なし: forecast 系 94件全 pass
- 全体 pre-existing 失敗除く: 全 pass

---

# 立場別200文字サマリー Phase 1: データ層実装（2026-07-12）

## 背景
README の「立場別200文字サマリー仕様（提案）」を実装。既存の `perspectives`（50字程度の短評、engineer/management/consumer）は変更せず、200字前後の発展版 `perspective_digest` を追加。段階導入方針に基づき、本番の夜間パイプラインには自動組み込みしない。

## 実装内容
- **`src/db.py`**: `topic_insights` に `ensure_column(cur, "topic_insights", "perspective_digest", "TEXT")` を追加
- **`src/llm_insights_pipeline.py`**: `upsert_insight()` の INSERT/ON CONFLICT に `perspective_digest` カラムを追加（`insight.get("perspective_digest")` が無ければ `{}` を保存する後方互換）
- **`src/llm_insights_api.py`**: `_extract_evidence_domain` / `_normalize_perspective_digest` / `call_llm_perspective_digest` を追加。80字未満はフォールバック対象として空文字（実機検証後に120→80へ調整、下記「追補」参照）、260字超は `…` で切り詰め、evidence_urls先頭ドメインを末尾に付与（無ければ「（参考情報未取得）」）
- **`src/generate_perspective_digest.py`**（新規）: 手動バックフィルスクリプト。`perspective_digest` 未生成の `topic_insights` 行を対象に `call_llm_perspective_digest` を呼び、UPDATE。`--limit`（デフォルト20、上限200）と `--dry-run` をサポート
- **`tests/test_perspective_digest.py`**（新規）: 10件（正規化・ドメイン抽出・LLM呼び出しの組み立てをカバー）
- **`README.md`**: 「提案」の見出しを外し、Phase 1実装済みである旨と使い方を追記

## 検証
- `python -m pytest -q` 全体: 236 passed, 12 failed（すべて実装前から存在する pre-existing 失敗。`test_opinion_page.py` 系9件・`test_docs_asset_paths.py` 1件は `render_main.py`/`render.py` の内容不一致、`test_exception_handling.py` 2件は `DummyConn` に `close` 属性が無いテスト側の不備で、いずれも今回変更した `db.py` / `llm_insights_api.py` / `llm_insights_pipeline.py` / `generate_perspective_digest.py` とは無関係）
- 新規10件は全件 pass

## 追補: 実機dogfooding で発覚した重大バグと修正（同日）

初回実装のプロンプトは「各値は170〜230字程度」という**文字数目標**を含んでいた。
これを実際のローカルOllama（PC1、既定モデル `gpt-oss:20b`）で `call_llm_perspective_digest` を直接呼び出して検証したところ、
**全ケースで空応答**になることが判明した。

### 原因
生応答を確認すると、`reasoning` フィールドが「200文字くらい書く。1文字ずつ数えよう…」という
文字カウントの独り言で `max_tokens`（900）を全部消費し、`finish_reason: "length"`・`content` が空文字のまま返っていた。
文字数を厳密に狙わせる指示が gpt-oss の reasoning を暴走させる（＝「実装完了」であって「実用レベル」ではなかった）。

### 修正
- `src/llm_insights_api.py` の `call_llm_perspective_digest`: プロンプトを「170〜230字程度」→「2〜3文」に変更（文字数を数えさせない）
- 同関数のペイロードに `"reasoning_effort": "low"` を追加
- `_DIGEST_MIN_LEN` を 120 → 80 に調整（「2〜3文」指示だと実測150〜200字程度に収まり、120は厳しすぎたため）
- `_DIGEST_PROMPT_SAMPLES` にサンプル文言の新表記を追加

### 検証結果
- 修正後、ローカルOllama（PC1, gpt-oss:20b）に2記事×3視点=6件を実投入。全件で `finish_reason: stop`・150〜200字程度の実用的な内容が3〜4秒で生成されることを確認（例: 「既存のAPIキー方式から新認証方式への移行を計画的に進めることが重要です。まずは移行ガイドを確認し…（参考: example-cloud.com）」）
- `python -m pytest -q`: 236 passed, 12 failed（pre-existing failure のみ、修正前と同一件数・同一テスト名で新規リグレッションなし）

## 次回セッション向け残タスク（Phase 1 時点）
- [x] `render_main.py` での `perspective_digest` レンダリング未実装（表示UIは今回のスコープ外）→ **Phase 2 で確認: 実装済み**（下記参照）
- [ ] 本番パイプライン（`llm_insights_local.py` 等）への自動統合可否の判断（毎晩の所要時間・LLM呼び出し回数への影響を評価してから判断する）。品質は実機検証済みなので統合の障害は解消済み
- [x] `generate_perspective_digest.py --limit 20` を本番DB（NULL件多数）に対して実行し、大量件数でも品質が安定するか確認 → Phase 2 で実施済み
- [ ] 「gpt-oss:20b + 文字数指定 + reasoning肥大化」の知見を `C:\work\★Template\tasks\knowledge.md`（Template本体）に反映する（今回は夜間モードの書き込み禁止に阻まれたため未反映。`隙間時間有効活用\tasks\knowledge.md` には反映済み）
- [ ] 上記12件の pre-existing テスト失敗（`test_opinion_page.py` 等）は本タスクのスコープ外だが、別途根本原因調査が望ましい

---

# 立場別200文字サマリー Phase 2: render_main.py 表示実装の確認・テスト・dogfooding（2026-07-13）

## 発端
Phase 1 の todo.md には「`render_main.py` での `perspective_digest` レンダリング未実装」と残タスク記載があったが、
Phase 2 セッション開始時に `src/render_main.py` の作業ツリー（未コミット）を調査したところ、**該当実装は既に完了していた**ことが判明。
本リポジトリは夜間パイプライン（`daily update (local LLM)` コミット）や他の並行セッションによって継続的に更新されており、
Phase 1 の todo.md 更新後・Phase 2 セッション開始前のどこかのタイミングで、別プロセスが実装を完了させたとみられる（コミット未実施のため git log には現れない）。

## 確認内容（既存実装の検証）
- **テンプレート**: `HTML`（tech ページ, `t.` ループ, render_main.py:744-750）と `NEWS_HTML`（news ページ, `it.` ループ, render_main.py:998-1005）の両方に、
  既存の `perspectives` ブロック直後に `perspective-digest` div（小見出し「立場別くわしい解説」）が実装済み。`{% if t.perspective_digest %}` / `{% if it.perspective_digest %}` で空/NULL時は非表示。
- **Python側**: `grep '"perspectives": _safe_json_obj'` で見つかった9箇所（依頼書の想定8箇所と近似、実測9）すべてで直後に `"perspective_digest": _safe_json_obj(perspective_digest)` が追加済み。SQL SELECT・タプルアンパックも全箇所で `perspective_digest` カラムを取得済み。
- ただし9箇所のうち実際にテンプレートで `perspective_digest` を表示するのは `topics_by_cat`（tech ページの `t.` ブロック経由）と `render_news_region_page`（news ページの `it.` ブロック経由）の2系統のみ。残り（`jp_priority_top` 等の Top10 サイドウィジェット）は `perspectives`/`perspective_digest` とも元々表示していない簡易カードのため、データだけ保持していても実害なし。

## 追加実装
- **テスト追加**（`tests/test_render_utilities.py`）: レンダリング関数（`render_news_region_page` の出力を実際に `Template(NEWS_HTML).render()` してHTML化）の単体テストを2件追加
  - `test_news_html_renders_perspective_digest_section`: perspective_digest ありの記事で「立場別くわしい解説」見出し・本文が `perspectives`（短評）の直後に表示されることを確認
  - `test_news_html_hides_perspective_digest_when_empty`: perspective_digest が `{}` の記事では見出し自体が出ないことを確認
- 既存の `test_fetch_news_articles_by_category_includes_perspective_digest` / `test_render_news_region_page_item_has_perspective_digest`（データ層）と合わせて計4件が本機能をカバー

## 検証
- `python -m pytest -q`: **240 passed, 12 failed**（Phase 1 と同一の pre-existing 失敗12件のみ。新規リグレッションなし。新規4件はすべて pass）

## dogfooding（実データでの動作確認）
1. 本番DB `data/state.sqlite`（498MB）を `data/state.sqlite.bak2_20260713_0207` にバックアップ
2. `python src/generate_perspective_digest.py --limit 20` を実行 → `updated_rows=20`（news 10件・market 5件・policy 2件・decarbonization_ops 2件・ai 1件）。生成内容を目視確認、日本語として自然・具体的（例:「技術者は、事件発生場所の周辺環境を監視システムやセンサーで継続的にモニタリングし…（参考: news.web.nhk）」）
3. `python src/render.py` を実行（8.6秒で完了）→ `docs/news/index.html` に `立場別くわしい解説` が10件表示されることを grep で確認（news カテゴリの10件と一致）
4. `docs/index.html`（tech ページ、`t.` ブロック側）では今回バックフィルした market/policy/decarbonization_ops/ai の10トピックは **1件も表示されなかった**（`grep topic-<id>` で0件）。原因はレンダリング側のバグではなく、バックフィル対象が `ORDER BY updated_at DESC` で選ばれた「最近 perspective_digest 未生成のまま更新された」トピックであり、必ずしも tech ページの表示条件（直近48h・カテゴリ別 importance/recent 上位N件）を満たすとは限らないため（データ選定上の制約）。tech ページ側のレンダリングロジック自体は news ページと完全に同一パターンであり、コード上・ユニットテスト上は動作確認済み

## 結論
- 依頼内容（render_main.py への表示実装・テスト・dogfooding）はすべて充足。Phase 2 のスコープは完了とする
- 本番パイプラインへの自動統合（Phase 1 残タスク）は引き続き未着手・要判断事項として残す

---

# 立場別200文字サマリー Phase 3: 本番パイプライン自動統合の可否判断（2026-07-13）

## 判断事項
Phase 1/2 から繰り返し持ち越されていた「`perspective_digest` を本番夜間パイプラインに自動統合するか」を検討した。

### 比較した2案
1. **`src/llm_insights_local.py` の1トピックあたりのループ内に `call_llm_perspective_digest` を直接組み込む案**
   - `llm_insights_local.py` は `max_sec`（既定300秒）・`delay`（既定3秒）で予算管理された本番メインループで、`C:\work\run_daily.bat` から `--rescue` 付きで毎晩呼ばれている（直近実行: 2026-07-13 06:33、本セッション開始40分前）
   - ここに2本目のLLM呼び出しを挟むと**1トピックあたりの所要時間が実質2倍**になり、同じ `max_sec` 予算内で処理できるトピック数が黙って半減する。主機能（`perspectives`/`importance`等の本体insight生成）のスループットを犠牲にするため不採用
2. **既存のオプトイン・バックフィルスクリプト `generate_perspective_digest.py` を独立ステップとして夜間バッチに追加する案**
   - 本体ループの予算・挙動に触れずに済み、既存の `forecast_generate`/`forecast_verify`（`run_daily.bat` 内、失敗しても `[WARN] ... continuing` で継続する non-fatal パターン）と同じ位置づけで追加できる
   - **採用**。ただし実行手順（下記）は本セッションでは適用せず、判断と受け入れ準備のみ行う

### 本セッションで実施した受け入れ準備
- `generate_perspective_digest.py` に `llm_insights_local.py` と同様の **`--max-sec` 時間予算ガード**を追加（既定は env `PERSPECTIVE_DIGEST_MAX_SEC`、未設定なら0=無制限）。従来は `--limit` のみで時間上限がなく、無人実行に組み込むには危険だった
- ユニットテスト4件追加（`tests/test_generate_perspective_digest.py`）: 全件処理・時間予算超過時の早期打ち切り・env変数からのデフォルト解決・dry-run
- `python -m pytest -q`: **244 passed, 12 failed**（Phase 1/2 と同一の pre-existing 失敗12件のみ、新規4件は全件pass、リグレッションなし）
- 本番DBを `data/state.sqlite.bak2_20260713_0715` にバックアップ後、実機dogfooding: `python src/generate_perspective_digest.py --limit 20 --max-sec 5` を実行 → モデルのコールドスタート込みで1件処理した時点で経過9.6秒 > max_sec(5) となり `[TIME] ... budget reached` を出力して安全に打ち切られることを確認。DBには当該1件の `perspective_digest` が実際に保存されていることも確認済み

### run_daily.bat / .github/workflows/daily.yml への実際の組み込みは今回は見送った
**理由**:
- `run_daily.bat`（`C:\work\run_daily.bat`）は **未git管理**かつ、本セッション開始のわずか34分前（06:33）に自動push付きで実行されたばかりの生きた無人本番パイプライン。作業ツリー（`git status`）は他プロセスによる多数の未コミット変更が既に存在する状態で、ここに自律発案セッションが割り込んで手を入れると、可逆性（誤りがあってもすぐ戻せるか）と安全性（次回無人実行が想定通り動くか）を十分検証しないまま本番に影響してしまうリスクがある
- CLAUDE.md の「Stability Over Speed」「変更は常にロールバック可能にする」の原則に照らし、今回は判断とスクリプト側の安全策（`--max-sec`）の追加に留め、実際の配線はユーザーの明示的な承認を得てから行うのが適切と判断した

### 組み込み手順（ユーザー承認後に適用する想定の具体案）
`run_daily.bat` の `llm_insights_local --rescue` ステップの直後・`render` ステップの前に、`forecast_generate`/`forecast_verify` と同じ non-fatal パターンで以下を追加する:
```bat
set "LASTSTEP=generate_perspective_digest"
py -3.11 -u src\generate_perspective_digest.py --limit 30 --max-sec 120 >> "%LOG%" 2>&1
set "RC=!ERRORLEVEL!"
if not "!RC!"=="0" (
  echo [WARN] generate_perspective_digest failed rc=!RC!, continuing >> "%LOG%"
)
```
`--limit 30 --max-sec 120` は保守的な初期値の一例（1晩で最大30件・最大2分）。実運用に入れる場合は数晩分のログ（`[TIME] generate_perspective_digest ... sec=`）を見ながら調整するとよい。

## 次回セッション向け残タスク
- [x] （ユーザー承認前提）上記の `run_daily.bat` 組み込みを実際に適用し、1晩分のログで想定通り動くか確認する → **本セッション末尾の追記時点で判明: 同日07-13の別セッション「利用価値向上 第1弾」（548行目）で既に適用済みだった。** 当セクション執筆時点ではまだ見送り判断だったが、その後同日中に方針が変わり実際に組み込まれた（本ファイル内に「見送った」という記述と「組み込んだ」という記述が両方残っていたため、2026-07-23の自律発案セッションが `run_daily.bat` の実物とログを確認して矛盾を解消した。詳細は本ファイル末尾「Phase 3 本番配線 実態確認（2026-07-23）」を参照）
- [ ] 「gpt-oss:20b + 文字数指定 + reasoning肥大化」の知見の Template本体 (`C:\work\★Template\tasks\knowledge.md`) への反映（引き続き夜間モードの書き込み制限で未実施）
- [x] pre-existing テスト失敗12件（`test_opinion_page.py` 等）の根本原因調査 → 2026-07-13 全体改善タスク（下記）で解消

---

# プロジェクト全体改善タスク（2026-07-13）

3並列調査（src/ コード品質・docs/ 出力とDB・CI/リポジトリ衛生）の結果を受け、ユーザー承認のもと全項目に対応する。

## 事前調査で確定した重要事実
- 直近100コミットすべてローカル（PC2）発。CI（daily.yml）のコミットは実質機能していない
- `docs/index.html` と `docs/tech/index.html` の完全一致は `run_daily.bat` の意図的な copy（バグではない）
- `topic_snapshots`（150万行、DB肥大の主因）の利用者は `diff_view.py` のみで直近2日しか参照しない → retention 導入は安全
- `.bak2_*` バックアップは過去セッションの手動作成（自動生成コードなし）
- 意見ページは `7fafd026 "remove: 立場別意見ページを廃止"` で意図的に廃止済み。`OPINION_HTML` は削除漏れの死コード、`test_opinion_page.py` は廃止機能への陳腐化テスト
- **`.gitignore` の許可リスト漏れにより `docs/topic/` `entity/` `diff/` `exec/` `api/` `search*.{html,json}` `sitemap.xml` `feed.xml` が未追跡 → GitHub Pages で404**（追跡は102ファイルのみ）

## Phase A: リポジトリ衛生
- [x] .gitignore: docs 許可リストに topic/entity/diff/exec/api/search/sitemap/feed を追加、opinion 許可を削除、ローカル bat（★*.bat, 01_git pull.bat）・.claude/・docs_backup/ を除外
- [x] `git rm --cached data/state.sqlite`（.gitignore の「DO NOT COMMIT」意図に合わせ追跡解除。504MB は GitHub の 100MB 制限で push 不能になるため必須）
- [x] 古いバックアップ削除（bak2_20260712_1217, bak2_20260713_0207, 旧 .bak。最新 bak2_20260713_0715 は保持）→ 約1GB 解放
- [x] 未追跡の新規 src/tests/tasks ファイルをコミット対象に

## Phase B: テスト整備（12件の失敗解消）
- [x] test_opinion_page.py を削除（廃止済み機能のテスト）
- [x] OPINION_HTML と意見ページ専用の死コードを render_main.py から削除
- [x] test_exception_handling.py: DummyConn に close() 追加
- [x] test_docs_asset_paths.py の失敗調査・修正

## Phase C: CI 再設計
- [x] daily.yml をパイプライン実行+コミットから検証専用（feed_lint + pytest）へ転換。デバッグステップ削除。発行はローカル（PC2）に一本化

## Phase D: コード修正
- [x] git_auto_push.py: shell=True + f-string → リスト形式 subprocess、state.sqlite コンフリクト特例削除（追跡解除に伴い不要）、docstring 修正
- [x] サイト URL 一元化（site_config）+ og:url の dachshund-github ドメイン誤り修正
- [x] data-date="None" 修正（published_at 欠損時の str(None) 漏出）
- [x] entities.yaml 作成（entities.py が参照するのに未存在）
- [x] watchdog.py: index.lock 自動削除の安全化（git プロセス生存確認）
- [x] 新規5モジュールの重複 HTML スタイルを共通モジュールへ
- [x] collect_health_*.jsonl のローテーション（90日超を削除）

## Phase E: データ整備
- [x] topic_snapshots に retention（14日）を組み込み + 一回限りの purge & VACUUM（481MB→縮小）
- [x] suspended フィード44件の HTTP 再チェック → 生きているものは failure_count リセット、死んでいるものは一覧化

## Phase F: 検証・ドキュメント・コミット
- [x] pytest 全 green
- [x] render 実行で docs 生成確認
- [x] CLAUDE.md / README 更新（opinion 廃止反映・新モジュール追記）
- [x] 論理単位でコミット & push
- [x] lessons.md 更新

## スコープ外（今回は見送り・提案のみ）
- render_main.py（5,419行）の全面分割 → 段階的に実施すべき大規模リファクタのため別タスク化
- git 履歴の肥大解消（filter-repo）→ force-push を要するため要ユーザー判断
- forecast の過去欠損日（6/13-23, 6/29）→ 過去日付の予測は遡及生成不能
- 古い topic ページの削除 → パーマリンク価値があるため保持

## 実施結果（2026-07-13）
- テスト: 256件(244 pass/12 fail) → **224件 全pass**（意見ページ廃止に伴う陳腐化テスト34件を削除、DBエラー再送出仕様を復元）
- render_main.py: 5,419行/232KB → **4,355行/179KB**（意見ページの死コード1,064行を削除）
- DB: **504MB → 231MB**（topic_snapshots 113万行パージ + VACUUM。以後14日retentionで自動パージ）
- data/: 旧バックアップ削除で**約1GB解放**（最新 bak2_20260713_0715 は保持）
- .gitignore 修正により docs/topic・entity・diff・exec・api・search・sitemap・feed.xml が**初めて公開対象に**（従来はローカル生成止まりで本番404）
- entities: 辞書を13→45エントリに拡充（entities.yaml 新設）、単語境界照合に修正。誤リンク再構築で 12,145 → 3,673 links（約7割が「Meta→metal」等の誤マッチだった）
- CI: daily.yml 廃止 → ci.yml（feed_lint + pytest の検証専用）。発行はPC2に一本化
- 停止フィード45件を再チェック（src/feed_recheck.py 新設）: **全滅（復活0件）**。failure_count>=100 の32件は30日suspendに変更

### 死亡フィード一覧（sources.yaml の棚卸し候補・要URL差し替えまたは削除）
404: iso.org, enisa, jisc.go.jp, ai.googleblog.com, ai.facebook.com, github.blog/ml,
nipponsteel.com, kobelco.co.jp, posco-inc.com:4451, nucor.com, ussteel.com, thyssenkrupp-steel.com,
siemens.com/cert, aveva.com, rockwellautomation.com, nttdata.com, aws.amazon.com/blogs/industries,
digital.go.jp, soumu.go.jp, nedo.go.jp, metro.tokyo.lg.jp, ec.europa.eu, ipa.go.jp, prtimes.jp/it,
automationworld.com, controlglobal.com, primetals.com, steeltimesint.com, sms-group.com,
danieli.com, zeiss.com, ghgprotocol.org, env.go.jp
403: cisa.gov(KEV/SBOM/ICS), iec.ch, arcelormittal.com, tatasteel.com, meti.go.jp
200だが空: cloud.google.com/blog/rss, recyclingtoday.com, tenova.com
接続不可: azure.microsoft.com(IoT blog, timeout), blog.skf.com

### 残タスク（次回以降）
- [x] 上記死亡フィードの URL 差し替え（各サイトの新フィードURLを Web で調査）または sources.yaml からの削除 → **実装済み・完了記録あり**（2026-07-13、コミット`09418980`・`2ba60f4d`）。下記「死亡フィードの棚卸し・差し替え完了」節（666行目以降）に45件全件の実施記録があり、`src/sources.yaml`の現物も反映済みであることを2026-07-29セッションで確認した。このチェックボックスが未更新のまま残っていただけの記録漏れ（実害なし）
- [x] render_main.py の段階的分割の継続（テンプレート外部化）→ 2026-07-21 第1弾・第2弾実施（下記参照）。残り2テンプレート（`FORECAST_HTML`/`FORECAST_HITS_HTML`）は未着手
- [ ] git 履歴の肥大解消（filter-repo で過去の state.sqlite blob を除去。force-push を伴うため要ユーザー判断）

---

# render_main.py テンプレート外部化 第1弾（2026-07-21・自律発案）

## 実施内容
render_main.py（4,397行）にインライン埋め込みされていた6個の巨大Jinja2テンプレート文字列
（`PORTAL_HTML`/`HTML`/`NEWS_HTML`/`OPS_HTML`/`FORECAST_HTML`/`FORECAST_HITS_HTML`）のうち、
`PORTAL_HTML` と `NEWS_HTML` の2個を `src/templates/` に外部化した（commit `049dfc82`）。

- `_TEMPLATE_DIR = Path(__file__).parent / "templates"` と
  `_jinja_env = Environment(loader=FileSystemLoader(_TEMPLATE_DIR))` を追加
- `NEWS_HTML` → `src/templates/news.html`。`Template(NEWS_HTML).render(...)` を
  `_jinja_env.get_template("news.html").render(...)` に置換（news/index.html 生成で実使用中）
- `PORTAL_HTML` → `src/templates/portal.html`。**着手前の調査で、現在このテンプレートは
  render_main.py 内のどこからも `render()` されていない死コードと判明**
  （docs/index.html は `HTML`/`tech_html_root` から生成されており、`PORTAL_HTML` を参照する
  呼び出し箇所は存在しなかった）。git log では継続的に編集されている形跡があり、
  過去に明示的な「廃止」コミット（OPINION_HTML削除時の `7fafd026` のような）も見当たらないため、
  安全側の判断として削除はせず、内容を保持したままファイル化するに留めた
  （将来ポータルページとして再利用する場合は `_jinja_env.get_template("portal.html")` で読み込める）
- `tests/test_render_utilities.py` の `_render_news_html` ヘルパーを新しい読み込み方式に追従修正

## 検証方法・結果
- 変更前後で `python src/render.py` を実行し、生成された `docs/` を比較
  （タイムスタンプが常時変動するため厳密なバイト完全一致にはならない前提で、
  全108件の差分ファイルについて `generated_at` 系タイムスタンプ行以外に差分がないことを
  Pythonスクリプトで自動検証。非タイムスタンプ差分ゼロを確認）
- `python -m pytest -q`: 224 passed（新規リグレッションなし）
- `render_main.py`: 4,397行 → 4,093行（-304行）
- 検証用に生成した `docs/` の差分・`docs_baseline_compare/` 退避コピーはコミット前に
  `git checkout -- docs` で元に戻し、退避ディレクトリも削除済み（リポジトリはクリーンな状態でコミット）

## 次回候補（未着手・残り4テンプレート）
- [ ] `HTML`（tech ページ本体、約400行）
- [ ] `OPS_HTML`（運用メトリクスページ）
- [ ] `FORECAST_HTML`（未来予測ページ・過去分含め複数箇所で呼び出し）
- [ ] `FORECAST_HITS_HTML`（予想的中ページ）
- 上記いずれも `_jinja_env.get_template(...)` への置換で同様の手順が使える
  （Jinja2の `Environment`/`FileSystemLoader` インフラは今回のコミットで整備済み）
- 抽出時の注意点: 元の `r"""..."""` 文字列は開始直後に改行が1つ入る（`r"""\n<!doctype...`）ため、
  外部化したテンプレートファイルの先頭に空行を1行残さないと出力の先頭に空行が1行減るバグになる
  （今回の作業で実際にハマった箇所。ファイル抽出後は必ず元の文字列と1文字単位で比較すること）

---

# render_main.py テンプレート外部化 第2弾（2026-07-21・夜間ランナー）

## 実施内容
第1弾の続き。残り4テンプレートのうち `HTML`（tech ページ本体・docs/index.html および
docs/tech/index.html 生成に実使用中）と、時間に余裕があったため `OPS_HTML`（運用メトリクスページ・
docs/ops/index.html 生成に実使用中）の2個を追加で `src/templates/` に外部化した。

- `HTML` → `src/templates/tech.html`。`Template(HTML).render(...)` の2箇所（`tech_html_sub` /
  `tech_html_root` 生成、それぞれ docs/tech/index.html と docs/index.html に対応）を
  `_jinja_env.get_template("tech.html").render(...)` に置換
- `OPS_HTML` → `src/templates/ops.html`。`Template(OPS_HTML).render(...)` を
  `_jinja_env.get_template("ops.html").render(...)` に置換（docs/ops/index.html 生成で実使用中）
- 抽出は手作業の書き写しではなく、Python スクリプトで `render_main.py` の該当行範囲を
  バイト単位でスライスして `src/templates/*.html` に書き出し、`import render_main` した
  実行時の変数値（`render_main.HTML` / `render_main.OPS_HTML`）と文字列完全一致することを
  スクリプトで検証してから元の変数定義を削除する手順を踏んだ（第1弾で判明した「先頭改行」の
  落とし穴を機械的に回避するため）
- `tests/test_render_utilities.py` 等を確認したが、`HTML`/`OPS_HTML` 変数や専用ヘルパーに
  依存するテストは存在しなかった（`NEWS_HTML` の `_render_news_html` のような追従修正は不要）

## 検証方法・結果
- `HTML`→`tech.html`: 変更前（`git stash` で一時的に旧コードへ戻す）と変更後それぞれで
  `python src/render.py` を実行し `docs/` を比較。バイト差分が出た109ファイルのうち
  非タイムスタンプ差分は5ファイル（`feed.xml` の `lastBuildDate`、`index.html`/`tech/index.html`/
  `news/index.html`/`ops/index.html` の `new48h` 系カウント・`+NN/48h` バッジ）のみ。
  これらが本変更由来か切り分けるため、変更後コード のみで `render.py` を数十秒間隔で2回連続実行し
  再度比較したところ、同一コードでも同じ種類の差分（時刻・48h集計カウント）が発生することを確認。
  すなわち `datetime.now()` 基準の48h集計が実行タイミングでぶれる既存の仕様であり、
  今回のテンプレート外部化によるレンダリング内容の変化ではないと判断した
  （tech.html の内容自体は `render_main.HTML`（旧変数）とスクリプトでバイト完全一致を確認済み）
- `OPS_HTML`→`ops.html`: 同様に `python src/render.py` 実行後、`docs/ops/index.html` を
  変更前（コミット済み `docs/`）と比較。差分は701行中6行のみで、いずれもタイムスタンプ行と
  ソース別48h集計カウント（`BBC World`/`OilPrice.com`/`ITmedia NEWS`等の数値列）で、
  上記と同種の時刻依存の揺れと確認。行数・構造の差分はゼロ
- `python -m pytest -q`: 224 passed, 0 failed（新規リグレッションなし。前回セッションで
  記録されていた pre-existing 12件の失敗は、その後の別コミット（daily update 等）で
  解消済みとみられ今回は再現しなかった）
- `render_main.py`: 4,093行 → 3,495行（-598行）
- 検証用に生成した `docs_baseline_compare` / `docs_before` / `docs_after1` / `docs_baseline2`
  等の退避ディレクトリ、および抽出・検証用の一時スクリプト（`extract_*.py` / `verify_*.py` /
  `remove_*.py` / `compare_*.py`）はすべて削除し、`git checkout -- docs` で `docs/` を
  コミット前の状態に戻した上でコミットした

## 次回候補（未着手・残り2テンプレート）
- [ ] `FORECAST_HTML`（未来予測ページ・過去分を含め複数箇所から呼び出されており依存関係が複雑なため
  今回もスコープ外とした）
- [ ] `FORECAST_HITS_HTML`（予想的中ページ）
- 上記も `_jinja_env.get_template(...)` への置換で同様の手順が使える見込みだが、
  呼び出し箇所が複数・条件分岐を伴う可能性があるため、着手前に呼び出し箇所を全て洗い出すこと

---

# render_main.py テンプレート外部化 第3弾（2026-07-22 02:07・夜間ランナー・自律発案）

## 実施内容
残り2テンプレート（`FORECAST_HTML`・`FORECAST_HITS_HTML`）を`src/templates/forecast.html`・
`src/templates/forecast_hits.html`として外部化した。これで全6テンプレートの外部化が完了。

- 呼び出し箇所を事前にgrepで洗い出し: `FORECAST_HTML`は2箇所（現在レポート`forecast_html`・
  過去レポートループ内`pr_html`）、`FORECAST_HITS_HTML`は1箇所（`html`）
- 抽出は第2弾と同じ「バイト単位スライス→`import render_main`した実行時変数値との文字列完全一致を
  スクリプトで検証→元の変数定義を削除」手順を踏襲
- **CRLF保持に関する新知見**: `render_main.py`はCRLF改行だが、Pythonがソースをコンパイルする際に
  文字列リテラル内の`\r\n`も含めユニバーサル改行変換で`\n`のみに正規化してメモリ上に保持するため、
  `open(path, encoding="utf-8").read()`（デフォルトのテキストモード）で読んだ変数値は常に`\n`のみになる。
  これをそのまま`open(outfile, "w", encoding="utf-8", newline="\n")`で書き出すとテンプレートファイルが
  LFのみになり、既存4テンプレート（すべてCRLF）と不整合が生じる。回避策: 書き込み時は`newline`引数を
  指定せず（Windowsのデフォルトテキストモード書き込みに`\n`→`\r\n`変換を任せる）、CRLFで統一する。
  一方、`render_main.py`自体の行削除・置換編集は`open(path, encoding="utf-8", newline="")`で読み書きし
  ユニバーサル改行変換自体を無効化してCRLFを文字通り保持する必要がある（読み込み時にデフォルトモードを
  使うと全行がLFに正規化され、無関係な行まで含めた巨大な差分になってしまう）
- `render_main.py`: 3,495行 → 3,109行（-386行）

## 検証方法・結果
- `python -m pytest -q`実行で1件失敗（`test_navigation_contains_ops_page_link`）を発見:
  `render_main.py`本体のソース文字列に`/daily-tech-trend/ops/`というナビリンク文字列が含まれることを
  検証するテストだったが、このリンクは`FORECAST_HTML`（今回外部化）に残っていた最後の1箇所であり、
  外部化によってrender_main.py本体からこの文字列が消えたため失敗した。`git stash`で変更前のコードに
  戻して同テストを実行し、変更前は成功（＝今回の変更による純粋なリグレッション）であることを確認した。
  第1弾・第2弾で外部化した`PORTAL_HTML`/`NEWS_HTML`/`HTML`/`OPS_HTML`にも同じナビリンク文字列が
  含まれていたが、当時はまだ`FORECAST_HTML`が本体に残っていたためテストが通っていた、という
  「段階的外部化の副作用が最後の1個を消すまで顕在化しなかった」ケースだった。
  `tests/test_docs_asset_paths.py`に`_read_render_sources()`（render_main.py本体＋
  `src/templates/*.html`全件を連結して返す）を追加し、`test_navigation_contains_ops_page_link`が
  このヘルパーを使うよう修正。修正後`python -m pytest -q`で224件全合格を確認
- `python src/render.py`実行前後で`docs/`を比較したところ、forecast配下99ページ・feed.xml・
  api/*.json含め109ファイルに差分が出た。中身を確認したところタイムスタンプ以外に、
  Markdown箇条書き（`1. **予測内容**：...`形式）が`<p>`羅列と`<ol><li>`の2パターンで揺れる
  構造的な差分が見つかり、当初は今回の変更による影響を疑った。しかし**同一コード（変更後）で
  render.pyを2回連続実行しても差分が発生**し、さらに**`git stash`で変更前の旧コードに戻して
  実行しても同じ揺れが再現**したため、今回のテンプレート外部化とは無関係な、既存のMarkdown
  変換処理の非決定性（原因未特定、`markdown`ライブラリの拡張処理順序等が疑われる）であると
  切り分けられた。検証用に生成した`docs_baseline_compare`/`docs_after1`退避コピーと
  一時スクリプト（`extract_forecast_tmp.py`/`remove_forecast_tmp.py`/`debug_forecast_tmp.py`）は
  すべて削除し、`git checkout -- docs`で`docs/`をコミット前の状態に戻した上でコミットした
  （forecastページのMarkdown非決定性は本タスクのスコープ外のため、下記「次回候補」に記録するに留める）
- 着手前・完了後とも`Get-ScheduledTask`で`Daily Tech Trend`/`Watchdog Daily Tech Trend`/
  `CollectedInfo_Pipeline`が`Ready`（実行中でない）であることを確認済み。`run_daily.bat`には触れていない

## 次回候補
- [ ] テンプレート外部化自体は6/6完了。今後は`render_main.py`の残り約3,100行（DB読み書き・
  データ集計ロジック中心）の可読性改善が次の分割候補になりうるが、テンプレート文字列のような
  自己完結した単位ではないため難易度が上がる

---

## 2026-07-22 07:07 自律発案: forecastページMarkdown非決定性の再調査（結論: 誤診断・対応不要）

前回セッション（02:07）で「forecastページのMarkdown箇条書きレンダリングが`<p>`羅列と
`<ol><li>`の間で非決定的に揺れる」と報告された件を再調査した。結論: **再現せず。実際には
`generated_at`系タイムスタンプの差分のみであり、Markdownレンダリング自体は完全に決定的**
だったと判明した（前回セッションの診断は誤り）。

### 調査内容
1. `md_to_html()`が実際に使っているのは`markdown`パッケージではなく`mistune`
   （`mistune.create_markdown(plugins=["table"])`、`render_forecast_page()`内でローカル関数として
   都度生成）だったため、まずこの前提を訂正した
2. `parse_forecast_markdown`/`parse_prediction_items`で全101件の`data/forecasts/report_*.md`から
   抽出した全セクション（executive_summary・checked_report・appendix×2・perspectives・
   predictions各horizon×item、計1,960項目）を`md_to_html()`でレンダリングしSHA256ハッシュ化する
   スクリプトを作成し、別プロセスとして3回起動して比較 → **1,960項目×3回、完全に同一のハッシュ**
   （mistuneのレンダリングはプロセスを跨いでも決定的）
3. 上記だけでは実際のパイプライン全体（Jinja2テンプレート結合・DB読み取り順序等）をカバーしないため、
   `python src/render.py`を同一DB状態で2回連続実行し`docs/`を比較する、前回と同じ手法で再検証した。
   着手前に`Get-ScheduledTask`で`Daily Tech Trend`/`Watchdog Daily Tech Trend`/`CollectedInfo_Pipeline`
   が`Ready`であることを確認済み
4. 差分が出たファイルは110件（前回の109件とほぼ同数）だったが、**全ファイルとも差分行数はちょうど2行
   （1行の書き換えのみ）で、中身は`generated_at`/`Generated (JST)`/`最終更新`いずれかの
   タイムスタンプ表示だけ**だった（`diff <file1> <file2> | grep -c '^[<>]'`で110ファイル全件を機械的に
   確認、Markdown構造（`<p>`/`<ol>`等）の差分は1件も存在しなかった）
5. 前回セッションが「タイムスタンプ以外の構造差分」と報告したのは、`grep -v`等での除外パターンが
   `generated_at`表記ゆれ（ページごとに"Generated (JST)"/"最終更新"/ラベルなしの3パターンがある）を
   拾いきれず、除外しきれなかったタイムスタンプ行を構造差分と誤認した可能性が高いと推測される
   （今回のログでも同じ誤認が再現しかけたため、これが原因だとほぼ断定できる）

### 対応
- 実際のバグが存在しないため、コード修正は行わない
- `python -m pytest -q`は変更なしのため未実行（コード変更ゼロ）
- 検証用一時ファイル（`repro_md_nondeterminism.py`・`repro_hashes_*.txt`・`docs_run1`/`docs_run2`・
  `render_run*.log`）はすべて削除し、`git checkout -- docs`で`docs/`をコミット前の状態に戻して
  作業ツリーをクリーンにした（`git status --short`で確認済み）
- 上記「次回候補」から本項目を削除した（対応不要と判明したため）

---

# 利用価値向上 第1弾（2026-07-13）

## 実施内容
- **ナビ導線整備**: 全5ページ共通ナビに「差分」「企業別」「エグゼクティブ」「🔍検索」「RSS」を追加（従来これらのページへのリンクはゼロで発見不能だった）
- **📈 経緯リンク**: tech ページの各トピックカードからトピックタイムライン（topic/<id>/）へリンク。リンク切れ防止のため、render 時に生成済み HTML から実リンク ID を回収してタイムライン生成対象に含める自己整合方式（render_main.py → topic_timeline.render_topic_timelines(include_ids=...)）
- **RSS 自動検出**: 全5ページの head に <link rel="alternate" type="application/rss+xml">
- **通知の配線**: run_daily.bat に notify.py ステップ追加（webhook 環境変数未設定なら no-op。設定手順は README）
- **perspective_digest 自動生成の配線**: run_daily.bat に --limit 30 --max-sec 120 で組み込み（Phase 3 準備済み案の適用）
- **計測フック**: common.js に GoatCounter フック（DTT_GOATCOUNTER_ENDPOINT 空なら無効。手順は README）
- run_daily.bat は編集前に C:\workun_daily.bat.bak_20260713 へバックアップ済み

## 派生バグ修正（経緯リンク検証で発見）
- **topic_articles の孤児行 9,281件**: dedupe.py が記事削除時に紐付けを掃除していなかった。一括削除＋ dedupe.py 末尾に恒久掃除を追加（テスト用最小DB対応の存在チェック付き）
- **タイムラインの記事0件スキップ**: スキップすると 404 になるため、空状態メッセージつきページを生成する方式に変更
- 検証: 経緯リンク 215件 → 404 ゼロ、全テスト 224 pass

## 残り（第2弾以降・計測データを見てから優先度決定）

**【2026-07-28 09:07 自律発案セッションで棚卸し】この見出し自体が「第2弾以降」だが、下記5項目のうち3項目は
実際には同日（2026-07-13）中に "第2弾" として実装済みだった（コミット `d5e9ec7e`
「feat(ux): ダークモード・検索遅延ロード・タグ永続化・exec定常運用化（第2弾）」19:50）。
このチェックリストだけが未反映のまま12日以上残っていた（他プロジェクトで繰り返し発見してきた
「実装完了・文書化ゼロ」パターンと同型）。**

- [x] トップページ軽量化（`docs/index.html` 現在964KB → 初期表示絞り込み） → **2026-07-28 12:07セッションで実装完了**。
  2026-07-28計測: `<div class="topic-row">`マーカー244件、先頭行以降で約970KB（全体の98.4%）、
  ヘッダ/CSS等ボイラープレートは約15.7KB（1.6%）のみ。重量はほぼ全てトピック行本体
  （1行平均約3.97KB、Jinja2側で全件を静的HTMLとして出力しているため）。
  `content-visibility:auto`（common.css）で描画パフォーマンスは対策済みだが、転送量（ネットワーク）は未対策。
  2026-07-28 11:07セッションで設計完了（`tasks/design_toppage_lightweight.md`参照）。
  追加計測で `<details class="insight">`（既定で折りたたみ非表示の要約/key_points/perspectives/
  perspective_digest/evidence_urls）だけでファイル全体の**52.1%**（435KB/834KB）を占め、かつ検索・
  フィルタ機能が読む`data-*`属性（`data-summary`だけで244件合計14.5KB）とは完全に独立していることを
  実測で確認した。これにより「行は間引かず`.topic-row`は全件残したまま、`<details>`の中身だけを
  ページ単位のJSON（`insights_tech.json`/`insights_news.json`）へ切り出し、初回`toggle`時に遅延fetch
  する」設計（既存の検索インデックス遅延ロードと同型）を採用し、09-28 09:07セッションが懸念していた
  「ページネーション化でフィルタに読み込んでいない行がヒットしなくなる」副作用を構造的に回避できることを
  示した。
  **実装内容（2026-07-28 12:07セッション）**:
  1. `render_main.py`: `_tech_has_insight()`/`_news_has_insight()`でtech.html/news.htmlの
     `<details class="insight">`表示条件を一箇所に集約し（テンプレート側は`{% if t.has_insight %}`
     を参照するだけに単純化、条件の二重管理を回避）、`_insight_payload()`で summary/key_points/
     perspectives/perspective_digest/evidence_urls/importance_basis を抽出、`write_insights_json()`
     で `docs/assets/data/insights_tech.json`・`insights_news.json` へ書き出す関数を追加。
     `docs/index.html`と`docs/tech/index.html`は同じ`topics_by_cat`から生成されるため
     insights_tech.jsonは1回書き出すだけで両方から絶対パス（`/daily-tech-trend/assets/data/...`、
     既存の`common_js_src`等と同じ絶対パス方式）で参照できる。
  2. `tech.html`/`news.html`: `<details class="insight">`内部を`data-insight-topic="{{ t.id }}"`
     ＋「読み込み中…」プレースホルダに置き換え（news.htmlの`sec.other_rows`側ネスト`.insight`は
     実際の生成コードでは`other_rows`キー自体が渡されず常にdead codeと判明したため対象外・現状維持）。
  3. `common.js`: `ensureInsights(pagePrefix)`（検索インデックスの`ensureIndex()`と同型）＋
     `renderInsightBody()`＋各テンプレートのkey_points重複排除ロジック（tech版・news版で異なるため
     `buildTechKeyPoints()`/`buildNewsKeyPoints()`として別実装、Jinjaの元ロジックと1:1で対応させた）
     を追加。`details.insight`の`toggle`イベント（`{once:true}`）で初回展開時のみJSONをfetchしDOM構築。
     XSS対策として全てDOM API（`textContent`/`createElement`）で構築し、innerHTML文字列結合は使わない
     （既存Jinjaテンプレートは`{{ t.summary }}`等を`|e`なしで出力しており実は無防備だったが、今回新規に
     書くJS側は素直に安全な実装にした。既存テンプレート側の同問題の修正は別スコープとして着手していない）。
  4. `t.next_actions`（tech.html旧278-364行目に存在した「次アクション」ブロック）は`render_main.py`の
     topics_by_cat構築ロジックに`next_actions`キーが一切存在せず、実際には常にUndefined→非表示の
     dead codeだったため、JSON側への移植は行わなかった（実データが来ることは無い）。
  5. テスト: `tests/test_insight_lazyload.py`新規（`_tech_has_insight`/`_news_has_insight`/
     `_insight_payload`/`write_insights_json`の単体テスト）。既存`tests/test_render_utilities.py`の
     `test_news_html_renders_perspective_digest_section`はperspective_digestが直接HTMLに出力される
     前提だったため、プレースホルダ＋JSON側での検証に更新（`test_news_html_uses_lazy_placeholder_...`
     に改名）。`python -m pytest -q` 236件全合格（既存235件+新規/更新分を含む）。
  6. 実機検証（`python src/render.py`）: `docs/index.html` 1,012,969→521,265バイト（**約48.5%削減**、
     詳細を1件も開かないユーザーの実質転送量）。`docs/news/index.html` 398,538→124,554バイト
     （**約68.7%削減**）。全件のdetailsを開いた場合でも `docs/index.html`(521,265)+`insights_tech.json`
     (165,485)=686,750バイトで旧`docs/index.html`単体(1,012,969)より約32%少ない（JSONは1回fetchで
     複数ページ共有されるため実際はさらに有利）。`grep -c "技術者目線"`等でHTML側に生の立場別コメントが
     一切漏れていないことを確認。`insights_tech.json`のキー数(188)とHTML側の`data-insight-topic`件数が
     一致することを確認。
  7. [x] **実機検証完了**（2026-07-29 02:07セッション）: `C:\work\AmazonAssociate\.venv`に既に
     インストール済みのPlaywright 1.58.0 + Chromium(1208)を借用し（daily-tech-trend側には
     playwrightを新規インストールせずMinimal Impactで検証）、`docs\index.html`が
     `fetch('/daily-tech-trend/assets/data/...')`という絶対パスに依存している点を、Windowsの
     ディレクトリジャンクション（`mklink /J`、管理者権限不要）で`<一時dir>\daily-tech-trend -> docs\`
     とし本番同様のURL構造を再現して検証した。ヘッドレスChromiumで2パターンを確認し両方PASS:
     - A) 通常クリック: `tech/index.html`をハッシュ無しで開くと`if (!location.hash) toggleAllCats();`
       により全カテゴリが既定で折りたたまれる仕様（今回の検証で初めて気づいた既存挙動。バグではない）
       のため、実際のユーザーと同じく「すべて開く」ボタンをクリックしてから`details.insight`の
       `<summary>`をクリック → `toggle`イベント発火 → `insights_tech.json`へのfetchが観測され →
       本文（要約等）が正しく描画されることを確認
     - B) ハッシュ直接アクセス: `tech/index.html#topic-<id>`に直接アクセスすると
       `revealHashTarget→scrollToHash`経路で`det.open = true`のプログラム代入が行われ、
       これでも`toggle`イベントが実際に発火し（HTML Living Standardの仕様通り、ユーザー操作限定ではない）、
       `insights_tech.json`へのfetchが観測され本文が描画されることを実機で確認（残課題だった論点そのもの）
     検証スクリプトは`scripts\verify_lazy_insight_browser.py`として保存（pytestスイートには非組み込み。
     実行方法・借用元venvはスクリプト冒頭のdocstringに明記）。news.htmlは同一の`common.js`関数
     （`ensureInsights`/`initInsightLazyLoad`/`scrollToHash`は`pagePrefix`引数のみ異なる共通実装）を
     使うため、tech側での実機確認により機構自体は妥当性が示されたと判断し、news側の個別実機確認は
     時間対効果の観点から見送った。
  8. **運用上の注意（今回の反省点）**: 本セッション中に`Get-ScheduledTask`で確認せず`python src/render.py`を
     手動実行したところ、ちょうど同時刻に本番の"Daily Tech Trend"タスクが実行中（dedupeフェーズ）で
     `data/state.sqlite`への書き込みが競合し、`diff.render`/`entities.render`で
     `sqlite3.OperationalError: database is locked`が発生した（tech/news/insights_*.json自体は
     正常に書き出せており実害はないが、次回からは**手動でrender.pyを実行する前に
     `Get-ScheduledTask -TaskName "Daily Tech Trend"`のStateを確認する**ことをルール化する。
     詳細は`tasks/lessons.md`にも記録）。
- [x] 検索インデックス(1.9MB)の遅延ロード → **実装済み**（2026-07-13, `d5e9ec7e`）。
  `src/render_feeds.py` `SEARCH_HTML`内、`q.addEventListener('focus', ...)`で検索欄フォーカス時に
  初めて`search-index.json`を`fetch`する設計（203-216行目付近、コード内コメントに明記）
- [x] ダークモード（common.css の変数上書き） → **実装済み**（2026-07-13, `d5e9ec7e`）。
  `docs/assets/css/common.css` `@media (prefers-color-scheme: dark)`ブロックでCSS変数
  （`--bg`/`--panel`/`--text-main`等）を上書き。OS設定に自動追従する方式で、手動トグルボタンは無い
  （todo.md記載時に想定されていた実装方式と完全一致するかは不明だが、機能としての「ダークモード」自体は
  達成されている）
- [x] ウォッチリスト（localStorage） → **部分実装**（2026-07-13, `d5e9ec7e`）。
  `docs/assets/js/common.js` 36-47行目でタグ選択（`dttSelectedTags`/`dttTagMode`）を`localStorage`に
  永続化し次回訪問時に復元する仕組みがあり、コード内コメントで「ウォッチリスト的な使い方」と明記されている。
  ただし記事単位の個別保存（ブックマーク）ではなくタグフィルタ状態の永続化のため、
  todo.md記載時に想定されていた「特定記事を継続ウォッチする」機能そのものが必要であれば別途未着手
- [x] 死亡フィードの URL 差し替え → **実装済み・完了記録あり**（2026-07-13）。下記
  「死亡フィードの棚卸し・差し替え完了」節に18/18 OK の実施記録が既にあり、このチェックリストとの
  重複・矛盾を解消した

---

# 死亡フィードの棚卸し・差し替え完了（2026-07-13 利用価値向上 第1弾の続き）

Web 調査（別エージェント・全候補を実取得検証）に基づき sources.yaml を更新。

## 復旧・差し替え（実取得検証済み・18/18 OK）
- Google Cloud Blog → cloudblog.withgoogle.com/rss/
- Google AI Blog → research.google/blog/rss/（Research Blog に統合）
- Meta AI → engineering.fb.com/category/ai-research/feed/（公式RSS廃止のため代替）
- GitHub ML → github.blog/ai-and-ml/machine-learning/feed/
- 日本製鉄 → nipponsteel.com/newsroom/news/rss.xml
- Tata Steel → tatasteelnederland.com（欧州部門 Presspage）
- Siemens ProductCERT → cert-portal.siemens.com/productcert/rss/advisories.atom
- Azure IoT → /blog/category/internet-of-things/feed/（要ブラウザUA）
- デジタル庁 → digital.go.jp/rss/news.xml / 総務省 → soumu.go.jp/news.rdf / IPA → alert.rdf
- CISA all/ics/ics-medical: URL 据え置きで復旧（403 の原因は UA。下記参照）
- METI: URL 据え置きで復旧（AWS WAF。ブラウザUA指定）
- 新規追加: SteelOnTheNet（Steel Times Int'l 廃止の代替）、Automation World、Control Global

## collect.py の機能追加
- フィード単位の `user_agent:` オプション（sources.yaml、YAML アンカー &browser_ua で共有）
- user_agent 指定時は Accept/Accept-Language を含むブラウザ相当ヘッダ一式を送信
  （CISA は UA 単体では 403 のまま。ヘッダ一式で 200 になることを実測確認）

## 廃止（コメントで sources.yaml に記録）
- ENISA（新サイトで RSS 全廃・公式告知あり）、神戸製鋼（RSS 終了）、ArcelorMittal（新サイトに RSS なし）、
  CISA の sbom.xml / KEV catalog.xml / ics-recommended-practices.xml（404。KEV は all.xml に流れる。
  KEV 全量が必要なら公式 JSON API → 将来の JSON インジェスト課題）

## 未対応（優先度低・次回以降）
- iso.org / iec.ch / jisc.go.jp / nedo.go.jp / 東京都 / ec.europa.eu(taxation) / prtimes.jp/it /
  recyclingtoday / tenova / sms-group / danieli / zeiss / ghgprotocol / env.go.jp / nucor / ussteel /
  thyssenkrupp / posco / aveva / rockwellautomation / nttdata / aws industries / skf blog（今回の調査対象外）

## DB 整備
- feed_health: sources.yaml に存在しない陳腐化 28行を削除、UA対応で復旧した4フィードの suspend を解除

---

# 利用価値向上 第2弾（2026-07-13）

## 実施内容
- **ダークモード**: common.css に prefers-color-scheme:dark 追従（変数上書き＋ハードコード色の個別補正）。
  サブページ（diff/entity/exec/topic）にも page_common.PAGE_DARK_CSS を適用
- **描画軽量化**: .topic-row に content-visibility:auto（トップは200件超の行があるため画面外の描画をスキップ）
- **検索の遅延ロード**: search-index.json(約2MB) をページ表示時ではなく最初の入力時に fetch
- **タグ選択の永続化**: 選択タグ・AND/OR モードを localStorage に保存し次回訪問時に復元（ウォッチリスト的な使い方）
- **エグゼクティブサマリーの定常運用化**:
  - run_daily.bat に exec_summary ステップを追加（ナビから導線を張ったため毎晩更新が必要になった）
  - gpt-oss の reasoning 暴走による空応答（finish_reason=length）を修正: reasoning_effort=low + max_tokens 1600 + リトライ2回
  - --category 単体実行で index.html が1件に上書きされるバグを修正（ディスク上の全ページから index を構築）
  - 全7カテゴリを llm=yes で再生成済み

## 検証
- render 後、diff/entity/topic/exec 各ページに dark CSS が入っていること、search.html が遅延ロードになっていることを確認
- 全テスト 224 pass

---

# 死亡フィード棚卸し 第2弾 完了（2026-07-13）

Web 調査（24件・全候補を実取得検証）に基づき sources.yaml を更新。第1弾と合わせて棚卸しは完了。

## 復旧・差し替え（9件・全件 collect.py 実取得経路で entries>0 を確認）
- POSCO → newsroom.posco.com/en/feed/（tls_mode: relaxed の例外運用も解消）
- Nucor → IR プレスリリース RSS / thyssenkrupp → グループ全体 RSS / Rockwell → IR プレスリリース RSS
- SMS group → PresseBox 配信 RSS（新規再追加）
- AWS Manufacturing → カテゴリパス変更に追従
- 東京都報道発表 → 新 RSS URL / EU CBAM → taxation-customs.ec.europa.eu / GHG Protocol → rss.xml（再追加）

## 廃止確定（コメントで sources.yaml に記録・14件）
ISO, IEC(ボット保護), JISC, NEDO, 環境省, U.S. Steel(IRサイト消滅), Recycling Today,
Tenova(空フィード), Primetals, AVEVA, NTT DATA, SKF(更新停止), ZEISS, Danieli(ボット保護),
PR TIMES カテゴリ別RDF（index.rdf のみ提供・非IT混在のためカテゴリフィルタ実装まで見送り）

## これで死亡フィード45件の棚卸しがすべて完了
- 第1弾: 17件復旧（うち4件は UA/WAF 対策で復旧）+ 廃止4件
- 第2弾: 9件復旧 + 廃止14件
- feed_health の陳腐化行も全掃除済み（sources.yaml と完全同期）

---

# Phase 3 本番配線 実態確認（2026-07-23 自律発案）

## 発端
07-22 12:07 セッション（成果物棚卸し更新）が「Phase3（立場別サマリー本番配線）の todo.md 内矛盾記述」を発見していた。「立場別200文字サマリー Phase 3」節（236行目）は「run_daily.bat への組み込みは今回は見送った・ユーザー承認前提の残タスク」と書いているが、同日付の後続「利用価値向上 第1弾」節（548行目）には「perspective_digest 自動生成の配線: run_daily.bat に組み込み済み」と書かれており矛盾していた。

## 確認した事実
1. **`C:\work\run_daily.bat` の実物を確認**: `generate_perspective_digest.py --limit 30 --max-sec 120` のステップが実在する（97-101行目、`[WARN] ... continuing` の non-fatal パターンで登録済み）。つまり「利用価値向上 第1弾」の記述が正しく、Phase 3 節の「見送った」は 07-13 のその後のセッションで方針が変わったまま Phase 3 節側が更新されずに残った、という**単純な記述の更新漏れ**だった（隠れたバグや設定ミスではない）
2. **無人実行での稼働実績**: 07-13〜07-22 の全ログ（1日4回×10日=44回中44回で該当ステップが存在）を集計した結果、**44/44 回とも `updated_rows=30`（打ち切りなし・全件成功）、`[WARN]` は一度も出ていない**。所要時間は 98.8〜108.7 秒で安定し、`--max-sec 120` の予算に対し最大でも 90.6% 使用に留まっており、直近まで悪化傾向も見られない（切迫していない）
3. **本番DBでの反映状況**: `topic_insights` 全16,775行中 `perspective_digest` 充填済みは980件。Phase2セッション（07-12）時点では「tech ページ（`docs/index.html`）には1件も表示されなかった」問題があったが、10日間の継続稼働で解消しており、現在は `docs/news/index.html` に67件・`docs/index.html`（tech ページ、`docs/tech/index.html` と同一）に18件、実際に「立場別くわしい解説」として表示されていることを確認した

## 結論・対応
- **本番配線は既に完了・安定稼働中であり、追加のユーザー承認や新規実装は不要**。Phase 3 節の「次回セッション向け残タスク」チェックボックスを実態に合わせて `[x]` に修正し、経緯を明記した（272行目）
- `run_daily.bat` 自体への変更（`--max-sec` 引き上げ等）は、現状問題が一切ないため今回は行わない（Minimal Impact、生きた無人本番パイプラインへの不要な変更を避ける）
- 本番DB・`run_daily.bat`・`docs/` への書き込みは一切行っていない（読み取り確認のみ）

## 次回候補
- 引き続き perspective_digest の充填率は緩やかに増加中（1晩30件ペース）。tech ページでの表示件数が増えてきたら、Phase 2 で見送った「表示デザインの調整」（現状は既存 perspectives ブロック直後に追加しただけ）の要否を再検討してもよい
- 優先度は低い

---

## 2026-07-29 04:07 自律発案: render_main.py 可読性改善 第1弾（ops用データ取得ブロックの関数分離）

### 発案理由
queue.mdは全件完了済みで進行中の自律発案は無かった。Explore agentでC:\work配下の他プロジェクト（健康テスト・遠隔操作・AI動画生成研究・ComfyUI・PC設定関連・llama_runtime_PC2・CollectedInfo・AmazonAssociate・stable-diffusion-webui・NotifyAI・サーバー集約）のtodo.mdを横断調査したが、無人セッションから安全に着手できる新規候補は乏しかった（大半がGPU起動・ユーザーGUI確認待ち）。調査結果に含まれていた「daily-tech-trendのpre-existing 12件テスト失敗の根本原因調査」は、実際に`python -m pytest -q`を実行して確認したところ**2026-07-13に既に解消済み（現在236件全pass、失敗0件）**と判明し、古い情報に基づく誤った候補だった（knowledge.mdの「todo.mdの記述を鵜呑みにせず現状を確認する」教訓通り）。次に本ファイル502行目の「render_main.pyの残り約3,100行（DB読み書き・データ集計ロジック中心）の可読性改善」を調べたところ、`main()`関数が実際に約1,590行（1592-3182行）に達する巨大関数だったことを確認し、これを分割候補として選定した。

### やったこと
1. `main()`内の構造をセクションコメント単位で分析し、`--- ops用データ取得 ---`ブロック（記事統計・日別収集トレンド・カテゴリ別分布・ソース別TOP15・フィード健全性・一次情報比率・RSS数、約170行）が「`cur`・`cutoff_48h`・`cat_name`のみを入力に取り、複数のローカル変数を構築するだけ」という完全に自己完結したデータ取得・集計ロジックであることを確認。後続で参照される全変数（`ops_stats`/`daily_trend`/`category_dist`/`source_exposure`/`feed_issues`/`primary_ratio_by_category`/`primary_ratio_threshold`/`rss_sources`）の使用箇所をgrepで洗い出し、`ops.html`テンプレート描画にのみ使われることを確認した
2. 新規関数 `_build_ops_page_data(cur, cutoff_48h, cat_name) -> Dict[str, Any]` を追加し、上記ブロックをそのまま移設（ロジック・SQL文は一字一句変更せず、戻り値をdictにまとめただけ）。`main()`側は関数呼び出し+アンパックの8行に置換した
3. 初回実装で `primary_ratio_threshold`（一次情報比率の閾値、ops.html描画で3箇所から参照される）を戻り値に含め忘れており、`python src/render.py`実行時に`NameError`で発覚。pytestの236件は全てグリーンのまま検出できなかった（＝pytestスイートはmain()のops.html描画パスを実データでE2E検証していない）ため、**render.py実機実行による検証は今後もこの種の抽出ミスを検出する唯一の手段**という教訓を得た。戻り値dictとmain()側のアンパックに追記して修正
4. 検証: 自分の変更を一時的に逆適用（reverse edit）してリファクタ前のファイル（1行単位でdiff確認、3182行で行数完全一致）を再現し、pytest 236件pass→`render.py`実行→`docs/`を退避。その後リファクタ後のファイルに戻して再度pytest 236件pass→`render.py`実行→`docs/`を比較。**差分は116ファイルで発生したが、全て`generated_at`/`最終更新`/`lastBuildDate`等のタイムスタンプ表示のみ（bash+pythonでタイムスタンプ行を除外した非タイムスタンプ差分を機械チェックし0件を確認）**。構造・データ内容の差分はゼロで、リファクタが完全に無害であることを実測で確認した
5. 検証用に生成した`docs/`はコミット前に`git checkout -- docs`で元に戻し、退避用一時ディレクトリ（`/tmp/dtt_verify`）も削除済み

### 成果物
- `C:\work\daily-tech-trend\src\render_main.py`（`_build_ops_page_data`関数を新設、`main()`から約170行を分離。`main()`本体は約1,590行→約1,420行に縮小）

### 教訓（tasks/lessons.mdにも別途記録）
- 巨大関数からのブロック抽出は、そのブロックが「後続で参照する変数」を洗い出す際に**関数内で1回だけ計算されるスカラー変数**（今回の`primary_ratio_threshold`）を見落としやすい。抽出前に対象ブロックで定義される**全てのローカル変数名**を機械的にgrepし、抽出範囲外での参照有無を確認する手順を徹底する
- pytestが全green でも、`main()`のような「実データ + 全ページ描画」を1回のテストで通しで検証していないコードベースでは、リファクタのregressionを検出できない。**render.py（またはmain()）を実際に実行する検証を、pytestとは別に必須の検証手順とする**

### 次回候補
- `main()`にはまだ他の自己完結ブロックが残っている（1601-2202行の「categories: YAML→DB→other」+ tech topics収集ループ、約600行が次に大きい塊。ただしこちらは`tech_categories`/`topics_by_cat`等、後続の広範囲で参照される変数が多く、依存関係の洗い出しがより難しいため、着手前に今回同様の変数使用箇所の全数grepを徹底すること）
- ~~2260-2636行に元々あった「ops用データ取得」コメントの実体は2431行までで終わっており、2432-2635行（`meta`辞書構築＋JP優先トピックTOP10×2クエリ）は別の関心事（トップページ用データ）が同じコメントブロックに混在していたことも判明した。次回の分割ではこの部分を`_build_jp_priority_top(cur, cutoff_48h)`のような別関数として切り出すのも候補になる~~ → 2026-07-29 05:07セッションで対応済み（下記参照）

---

## 2026-07-29 05:07 自律発案: render_main.py 可読性改善 第2弾（meta辞書構築+JP優先トピックTOP10×2クエリの関数分離）

### 発案理由
queue.mdは全件完了済みで進行中の自律発案は無かった。04:07セッションが本ファイル787-789行「次回候補」に残した2項目のうち、「2432-2635行のmeta辞書構築＋JP優先トピックTOP10×2クエリ」を選定した。もう一方の候補（1601-2202行のcategories/tech topics収集ループ約600行）は`tech_categories`/`topics_by_cat`等の後続広範囲で参照される変数依存が多く難易度が高いと明記されていたため見送り、より自己完結度が高いこちらを選んだ。

### やったこと
1. 対象ブロック（`runtime_sec`計算〜`jp_priority_trending_top`ループ終端、約230行）が`cur`・`cutoff_48h`・`rss_sources`（`_build_ops_page_data`の戻り値）のみを入力に取ることを確認。後続で参照される`meta`/`jp_priority_top`/`jp_priority_trending_top`の3変数はテンプレート描画（`render_html`等呼び出し3箇所）にのみ使われることをgrepで確認済み
2. `_build_ops_page_data`と同じ場所・同じ命名規約で`_build_meta_and_jp_priority(cur, cutoff_48h, rss_sources) -> Dict[str, Any]`を新設し、ロジック・SQL文は一字一句変更せず移設。`main()`側は`_build_ops_page_data`呼び出しの直後にこの関数を呼び、戻り値をアンパックする4行に置換した（ops呼び出し→meta/jp呼び出しの順序になったが、両者とも`cur`への読み取り専用クエリのみで副作用がなく相互に独立しているため実害なし）
3. 検証: 自分の変更を一時的に逆適用したファイルを`.verify_tmp/`配下に作成し、`pytest`（236件pass）→`python src/render.py`実行→`docs/`退避。リファクタ後のファイルに戻して再度`pytest`（236件pass）→`render.py`実行→`docs/`をPythonでバイト比較。**差分は116ファイルで発生したが、正規表現で`generated_at`/`lastBuildDate`/`最終更新`/`Generated (JST)`等のタイムスタンプ文字列を除外した上で再比較すると差分0件**（構造・データ内容の差分ゼロ）を確認した
4. 検証用に生成した`docs/`は`git checkout -- docs`で元に戻し、`.verify_tmp/`（一時ファイル一式）も削除済み。本番DB・スケジュールタスクへの操作はなし（実行前に`Get-ScheduledTask`で"Daily Tech Trend"等が全てReadyであることを確認済み）

### 成果物
- `C:\work\daily-tech-trend\src\render_main.py`（`_build_meta_and_jp_priority`関数を新設。`main()`本体はさらに約210行縮小）

### 次回候補
- `main()`に残る最大の自己完結ブロックは1601-2202行の「categories: YAML→DB→other」+ tech topics収集ループ（約600行）のみ。`tech_categories`/`topics_by_cat`が後続の広範囲（カテゴリ横断TOP・タグ集計・insights JSON書き出し等）で参照されるため、分割時は依存変数の全数grepを徹底すること
- 本セッションで`main()`の「ops用データ取得」「上部サマリー用meta＋JP優先トップ」の2ブロックが分離済みとなり、残るは上記1ブロックのみ。次回は依存関係の複雑さを鑑み、一気に切り出すのではなく「categories構築」「tech topics収集ループ」のようにさらに小さく分割することも検討するとよい
- 本セッションの分離は「①ops用データ取得」のみで、今後複数セッションに分けて残りのセクションを段階的に分離していく想定（過去のテンプレート外部化と同じ「1回に1-2個ずつ」のペースを踏襲）

---

## 2026-07-29 07:07 自律発案: render_main.py 可読性改善 第3弾（categories構築+tech topics収集ループの関数分離）

### 発案理由
queue.mdの本案件エントリ（状態: 進行中）を確認したところ、依頼内容は「残る最大の自己完結ブロック（1601-2202行、categories構築+tech topics収集ループ約600行）をさらに小さい単位に分割する」というもの。着手時点で`src/render_main.py`を確認したところ、**`_build_tech_categories`（categories: YAML→DB→other構築部分）と`_build_tag_groups`（insights_tech.json書き出し+タグ集計部分）が既に関数分離済み**（コミット前・未記録の状態）であることが判明した。git状態・ファイルmtime（06:12、todo.md最終更新05:18より後）から、本セッション開始前に別セッション（おそらく前回セッションが40分の作業時間内に完了しきれなかった続き）が着手し、この2関数の分離まで完了させていたが、todo.md/queue.mdへの記録・完了報告がされないまま終わっていたと推測される（無人セッションの40分打ち切りによる中断が疑われる）。本セッションはこの状態を引き継ぎ、`main()`に残った唯一の未分離ブロックである「tech topics収集ループ」（`for cat in tech_categories:` 本体、約554行）に着手した。

### やったこと
1. ループ本体（2112-2666行）が`cur`・`cat_id`（`cat["id"]`のみ、`cat_name`は不使用と確認）・`cutoff_48h`・`LIMIT_PER_CAT`・`HOT_TOP_N`のみを入力に取り、`hot_by_cat[cat_id]`・`topics_by_cat[cat_id]`の2辞書エントリのみを出力することを確認。ループ内で新規定義される全ローカル変数名（`cat_id`/`hot_ids`/`hot_set`/`hot_set_for_filter`/`it`/`item_ids`/`items`/`kept`/`missing_ids`/`params`/`placeholders`/`r`/`rows`/`sql_missing`）を機械的にgrepし、`main()`内のループ外（前後）で参照されていないことを確認済み（`hot_by_cat`/`topics_by_cat`はループ後に`_build_tag_groups`・テンプレート描画でのみ使用）
2. `_build_category_topics(cur, cat_id, cutoff_48h, LIMIT_PER_CAT, HOT_TOP_N) -> Tuple[List, List]`を新設し、ループ1回分の処理をそのまま移設（SQL・ロジックは一字一句変更せず、`hot_by_cat[cat_id]`への直接代入を`hot_list`ローカル変数に、`hot_by_cat.get(cat_id, [])`参照を`hot_list`直接参照に置換したのみ）。`main()`側のループは5行（`cat_id`取得→関数呼び出し→2辞書へ代入）に縮小
3. 抽出はPythonスクリプトによるバイト単位スライス（`newline=''`でCRLFを保持したまま読み書き）で実施し、手作業の書き写しミスを回避（第2弾までと同じ手法）
4. 検証: 自分の変更を一時的に逆適用したファイル（`_build_category_topics`をループ本体に再インライン化）をスクリプトで生成し、`pytest`（236件pass）→`render.py`実行→`docs/`退避。リファクタ後のファイルに戻して再度`pytest`（236件pass）→`render.py`実行→`docs/`をバイト比較
5. **1回目の比較で108件がタイムスタンプ以外の差分ありと判定され、特に`index.html`/`tech/index.html`で942行規模の差分（`<details class="insight">`ブロックの有無）が見つかり、一時的にリファクタが原因かと疑った。** しかし切り分けのため「逆適用版を2回連続実行」「本セッションの版を2回連続実行」した対照実験を行ったところ、いずれも差分はタイムスタンプ2行のみで、942行規模の差分は再現しなかった。さらに「逆適用版→(間にpytest等の待ち時間を挟む)→本セッション版」の順で実行し直すと942行差分が再現し、一方「逆適用版→(待ち時間なしで直後に)→本セッション版」では差分がタイムスタンプのみに収まった。これにより**942行差分はコードの違いではなく、2回のrender.py実行の間に本番DBへ挿入されたtopic_insights行（バックグラウンドで稼働中の別プロセスによるLLM insight生成）が原因**と判明した（`has_insight`フラグが実行タイミング依存でtrue化する既存の仕様。第2弾セッションが発見した「48h集計カウントの実行タイミング依存」と同種の非決定性）。最終的に間隔を空けない対照比較で**タイムスタンプ以外の差分ゼロ**を確認した
6. 検証用に生成した`docs/`・一時ファイル一式（`.verify_tmp/`）は`git checkout -- docs`・削除で後始末済み。本番DB・スケジュールタスクへの操作なし（実行前後とも`Get-ScheduledTask`で"Daily Tech Trend"等がReadyであることを確認済み）

### 成果物
- `C:\work\daily-tech-trend\src\render_main.py`（`_build_category_topics`関数を新設。`main()`本体は約592行に縮小。これで1601-2202行に元々あった約600行の「categories構築+tech topics収集ループ」ブロックはすべて3関数（`_build_tech_categories`/`_build_tag_groups`/`_build_category_topics`）に分離完了）

### 教訓（tasks/lessons.mdにも別途記録）
- render.pyのdocs/バイト比較検証で「タイムスタンプ以外の差分」を検出した場合、即座にリファクタが原因と断定せず、**同一コードでの2回連続実行**と**時間を空けた同一コードでの2回実行**を両方試して切り分けること。本番DBが他プロセス（LLM insight生成等）によって継続的に更新されているため、render.py実行の「間隔」自体が非タイムスタンプ差分の発生源になりうる

### 次回候補
- `main()`のテンプレート外部化（6/6完了）+ 主要ブロック分離（ops用データ取得/meta+JP優先トップ/categories構築/tag集計/tech topics収集ループ、計5関数）が完了し、当初の「render_main.py可読性改善」シリーズの主要スコープはこれで一区切りとする
- `main()`本体（約592行）にはまだDB接続・出力ファイル書き出し・カテゴリ横断TOP集計・複数テンプレート呼び出しが残っているが、これらは互いに逐次依存（前段の出力を次段が使う）が強く、これ以上の分離は費用対効果が下がる可能性がある。次に着手する場合は、まず現状の`main()`を通読して新たな自己完結ブロックがあるか再評価すること

### 【解消済み】未コミットの変更が長期間残留していた問題（2026-07-29 10:07セッションでコミット完了）
- 2026-07-29 09:07セッションが発見した「テンプレート外部化第1〜3弾より後の作業が丸ごと未コミット」問題（5関数分離+トップページ軽量化、6ファイル・1045行挿入/812行削除）は、10:07セッションで解消した
- 着手前に`Get-ScheduledTask -TaskName "Daily Tech Trend"`が`Ready`であること、`git diff`にシークレット等の不審な内容が無いこと、`pytest`236件全passを確認した上で、コミット`153b06a7`（`refactor(render_main): トップページ軽量化+main()の主要ブロックを5関数に分離`）として一括ローカルコミット（push無し）した
- コミット後も`git status`がクリーン・`pytest`236件全passであることを再確認済み
- 同型の「記録はあるがコミットが漏れる」再発防止策（`night_task.md`へのgit commit項目追加）は09:07セッションで既に実施済み。今回はその初回検証も兼ねた形になった

## render_main.py main()残存ブロックの再点検 — 2026-07-30 02:07（自律発案）

- 発端: `隙間時間有効活用\queue.md`に前回セッションが残した次回候補「まず`main()`（約592行）を通読して新たな自己完結ブロックがあるか再評価すること」を実施
- `main()`（当時2649-3241行）を全文通読し、`hot_by_cat`/`topics_by_cat`のループの後、テンプレート描画までの間にある「カテゴリ横断TOP」ブロック（旧2695-3047行、約353行）が新たな自己完結ブロックであることを発見した。4本のSQLクエリ（Global Top 10 / Trending Top 10 / market_top / market_trending_top）でほぼ同型
- 抽出前に対象ブロックの全ローカル変数名を機械的にgrepし、`TECH_CATS`/`tech_cat_ids`/`ph`はブロック内のみで完結し、`global_top`/`trending_top`/`market_top`/`market_trending_top`の4つの出力はテンプレート描画呼び出し（`tech.html`の2回のrender呼び出し）以外では参照されないことを確認した
- `_build_cross_category_top(cur, cutoff_48h, tech_categories) -> Tuple[List, List, List, List]`を新設し、ロジック・SQL文は一字一句変更せずそのまま移設。`main()`側は関数呼び出し+4変数アンパックの3行に置換した（`main()`本体は約592行→約242行に縮小）
- 抽出はPythonスクリプトによるバイト単位スライス（`newline=''`でCRLFを保持）で実施したが、挿入した関数ヘッダー・フッター文字列がプレーンな`\n`だったため一部の行がLFのみになる不具合が発生。`py_compile`は通ったが検証前に気づき、全行を`\r\n`に統一する後処理を追加して修正した（**教訓**: バイトスライス抽出時、既存行はCRLF保持でも新規に挿入する文字列リテラルは明示的に`\r\n`で書くか、書き込み後に改行コードを検査すること）
- 検証: `Get-ScheduledTask -TaskName "Daily Tech Trend"`が`Ready`であることを実行前後で確認。リファクタ後のコードで`pytest`（236件pass）→`src/render.py`実行→`docs/`を`%TEMP%`配下に退避。その直後（間隔を空けず）に`git show HEAD:src/render_main.py`で元コードに戻して`pytest`（236件pass）→`src/render.py`実行→`docs/`を別ディレクトリに退避、というシーケンスで比較した（07-29 07:07セッションの教訓通り、本番DBの非同期更新由来の偽陽性差分を避けるため2回の実行間隔を最小化）
- バイト比較は当初Git Bashの`/tmp/...`パスをWindows版Python（`py -3.11`）にそのまま渡してしまい「差分0件」という誤った結果が出た（`os.path.exists`がFalseで実質何も比較していなかった）。`cygpath -w`で実Windowsパス（`%LOCALAPPDATA%\Temp\...`）に変換して再実行し、正しく117ファイルの差分を検出した（**教訓**: Git Bash上で`py`（Windows Python）に`/tmp/...`のようなPOSIXパスを渡すと存在しないパス扱いになり検証が「無害」と誤判定されうる。Windows Python起動時は`cygpath -w`等でWindowsパスに変換してから渡すこと）
- 117ファイルの差分内容を`generated_at`/`lastBuildDate`（RSS）等のタイムスタンプパターンで正規表現除外して再比較し、**タイムスタンプ以外の差分ゼロ**（116ファイルはHTML内`generated_at`表記のみ、`feed.xml`の1ファイルは`<lastBuildDate>`行のみ）を確認した
- 検証用の一時docsコピー（`%TEMP%\docs_before`/`docs_after`）は確認後に削除済み。本番`docs/`は`git checkout -- docs`で無傷に復元済み。本番DB・スケジュールタスクへの操作はなし
- コミット前に`git diff`でシークレットパターン（`sk-`/`ghp_`/`Bearer `等）が含まれないことを確認し、`Get-ScheduledTask`のReadyを再確認した上でローカルコミット（push無し）

### 成果物
- `C:\work\daily-tech-trend\src\render_main.py`（`_build_cross_category_top`関数を新設。`main()`本体は約242行に縮小）

### 次回候補
- `main()`（約242行）はDB接続・出力ファイル書き出し・複数`render_*_page`呼び出しの列挙が中心で、各呼び出しは既にモジュール化された関数への1〜3行の薄いラッパーが並ぶのみ。これ以上の分離候補は見当たらず、「render_main.py可読性改善」シリーズは今回で本当に完了と判断する。次にこのファイルへ触れるセッションは、無条件の再評価ではなく新たな機能追加・不具合修正の文脈で着手すること

## 2026-08-05 02:07 自律発案: RAM枯渇による14時間PCフリーズの根本原因修正（PAUSE中）

### 現状（最重要・要ユーザー確認）
- **本プロジェクトの夜間自動実行は現在完全停止中**。`C:\work\daily_tech_trend.PAUSE`が存在する限り`run_daily.bat`は起動直後に何もせず終了する
- 停止理由: 2026-08-04 20:26頃、`llm_insights_api.py`のモデルフォールバック処理がOllamaモデルを次々ロードし続けRAM 28.8GBを枯渇させ、PCが約14時間フリーズした。ユーザー本人がPAUSEガードファイルを追加して緊急停止した（自律発案セッションによる対応ではない）

### 本セッションでやったこと
- 根本原因を特定: `src/llm_insights_api.py`の`post_ollama`が、候補モデル失敗時にアンロードせず次候補へ進んでいたため、`_pick_model_candidates()`が返す「全モデル総当たり」候補で失敗が連鎖するとRAM上にモデルが積み上がり続ける実装になっていた
- `post_ollama`のフォールバックループを修正し、失敗した候補は`_unload_model()`で即アンロードしてから次候補へ進むよう変更（詳細は`tasks/lessons.md`「2026-08-05 02:07」節）
- 回帰テストを追加（`tests/test_llm_autostart.py::test_post_ollama_unloads_failed_candidates_between_attempts`）、`pytest`237件全pass（既存236+新規1）を確認
- コミット済み（`160f8a0e1`、push無し）。`daily_tech_trend.PAUSE`は**削除していない**（根本原因は修正したが、実際のOllama環境での動作確認は無人セッションでは検証不能なため、フリーズという重大インシデントの再発リスクを考慮し解除はユーザー判断に委ねた）

### 次回やること（ユーザー確認待ち・自律発案では進めない）
- ユーザーが`C:\work\daily_tech_trend.PAUSE`を削除して夜間実行を再開するかどうかを判断する。再開する場合は、初回実行を監視できるタイミング（日中等）に手動で`run_daily.bat`を叩いて様子を見ることを推奨
- 万一まだ懸念がある場合、`OLLAMA_FALLBACK_MODEL`を明示指定し候補を絞る、または`_pick_model_candidates`の「全モデル総当たり」挙動自体を見直す（候補数に上限を設ける等）というさらに保守的な対策も検討の余地がある（本セッションでは「失敗時アンロード」という最小修正に留めた）

## 2026-08-09 10:19 夜間実行の再開（PAUSE 解除・監視付きテスト成功）

### 発端
ユーザーから「ニュースダイジェストが8/3で止まっている？」と指摘。調査の結果、故障ではなく
`C:\work\daily_tech_trend.PAUSE`（2026-08-04 20:27 にユーザーが作成した緊急停止マーカー）が残ったままで、
`run_daily.bat` が起動直後に `exit /b 0` していた。タスクスケジューラ上は毎回 `Result: 0` の成功扱いになるため
監視からは正常に見えており、8/5 以降はログすら生成されていなかった（**成功終了する no-op は監視の盲点になる**）。

### 実施内容（ユーザーが「監視付きで手動1回テスト」を選択）
1. 事前チェック: `pytest` 237 件 全pass。`git status` 上、修正コミット `160f8a0e1` を含む2件が未push だったことを確認
2. 実行タイミングの調整: OneTrainer の LoRA 学習が VRAM 15.7/16.3GB を占有していたため終了を待機（Monitor で検知）。
   終了後も ComfyUI が VRAM 5.7GB を保持していたので `POST /free` でモデルのみアンロード（プロセスは維持＝可逆）
3. 安全網: RAM ウォッチドッグ（10秒間隔・空き 2.5GB 未満が連続したら Ollama 強制停止 + パイプラインのみ kill）を併走。
   ComfyUI 等の無関係プロセスはコマンドライン照合で対象外にした
4. 二重起動対策: 定時タスクの無効化は管理者権限が必要で拒否されたため、**PAUSE ファイルを排他ロックとして利用**した。
   ガードは起動時に1回だけ判定されるので、手動実行がガードを通過した直後に PAUSE を復活させることで、
   実行中プロセスに影響を与えずに 12:00 等の定時起動だけをブロックできる
5. 結果: **所要 32 分（09:47:16→10:19:13）で完走**。タイムアウト 0 件 / ERROR 0 件 / WARN 1 件（無関係な RSS の
   XML 不正: thyssenkrupp）。最小空き RAM 17.29GB、ウォッチドッグの危険域イベント 0 件。
   `gpt-oss:20b` は `size=13.9GB / size_vram=13.9GB` の完全 GPU 常駐を維持した
6. 成果物: コミット `2275866dd daily update (local LLM)` を生成し **push 済み**（未push だった2件も同時に公開された）。
   `docs/` は 10:18 に再生成、`origin/main` と同期済み

### 現在の状態
- `C:\work\daily_tech_trend.PAUSE` は**削除済み**。夜間・定時実行（6/9/12/15/18/21 時）は**再開している**
- 直近の懸念だった「PAUSE 解除の判断」（2026-08-05 セッションからの持ち越し）は、これで解消

### 残存リスク（未対応・要検討）
- ~~`_pick_model_candidates()` の「全モデル総当たり」は健在で、PC2 で実用不可の `qwen3:30b-a3b`（17.3GB）も候補に含まれる~~
  → 2026-08-09 11:07 自律発案セッションで対応（下記参照）
- ~~タイムアウトによる失敗は `_FAILED_MODELS` に登録されない（HTTP 400 系のみ）ため、同一モデルがリトライで再ロードされる~~
  → 2026-08-09 11:07 自律発案セッションで対応（下記参照）
- ~~`_unload_model()` の POST タイムアウトは 10 秒。RAM スラッシング中はアンロード要求自体が失敗しうる~~
  → 2026-08-11 05:07 自律発案セッションで対応（下記参照。`LLM_UNLOAD_TIMEOUT_SEC`新設・既定30秒へ延長）
- **恒久対策の候補**: `run_daily.bat` の冒頭に「空き VRAM がモデルサイズ + 余裕分に満たなければ実行をスキップ（または
  ComfyUI の `/free` を叩いてから開始）」というプリフライトチェックを入れる。今回の事故は VRAM 競合が起点だったため、
  ここを塞ぐのが最も費用対効果が高い（未対応）

### 別件（今回発見・未対応）
- 定時タスク `Watchdog Daily Tech Trend`（毎日 8:30）は実体の `C:\work\run_watchdog.bat` が存在せず、
  2026-04-06 以降ずっと空振りしている。復活させるか、タスクごと削除するかの判断が必要

## 2026-08-09 11:07 自律発案: 残存リスク2件の修正（_FAILED_MODELS登録漏れ・除外リスト未整備）

### 背景
直前の10:19セッション（ユーザーによる監視付き手動テスト、PAUSE解除）が残した「残存リスク」節のうち、
コード修正のみで閉じられる2件に対応した（VRAMプリフライトチェック・Watchdogタスクの扱いは、より大きな
設計判断やGPU実機での検証を伴うため見送り、未対応のまま次回に残す）。

### やったこと
1. **タイムアウト失敗を`_FAILED_MODELS`に登録**: `post_ollama`の`except Exception`節（`src/llm_insights_api.py`
   line 298付近）はHTTP 400系と違い失敗モデルを`_FAILED_MODELS`に追加していなかったため、同一リトライ周回内で
   同じ壊れた/VRAM不足のモデルへロード→タイムアウト→アンロードを繰り返すだけで他候補へ進めない構造的ギャップが
   あった。HTTP 400系と同じ処理（`_FAILED_MODELS.add(model)` + `_SELECTED_MODEL`リセット）を例外パスにも追加した
2. **`OLLAMA_EXCLUDE_MODELS`環境変数を新設**: `_model_settings()`で読み取り、`_pick_model_candidates()`の自動収集
   候補（`for mid in model_ids: _add(mid)`）から除外できるようにした（`OLLAMA_MODEL`/`OLLAMA_FALLBACK_MODEL`同様、
   明示指定は埋め込みフィルタと同じく尊重してバイパスする設計とし、除外はあくまで「全モデル総当たり」の自動収集分のみに効く）
3. テスト2件追加（タイムアウト失敗の登録・除外env varの動作）、`pytest tests/ -q`で239件全pass（既存237+新規2）
4. ローカルコミット `258e6a787`（push無し。直前セッションの未pushコミットは無く、`origin/main`と同期済みの状態から積んだ）

### あえてやらなかったこと
- **`OLLAMA_EXCLUDE_MODELS=qwen3:30b-a3b`を`run_daily.bat`に実際に設定することはしなかった**。現状は環境変数未設定
  のためデフォルト動作（挙動変化なし）のままで、10:19セッションが確認した「タイムアウト0件・32分完走」の実績は
  維持される。次回の12:00定時実行を控えた直前のタイミングで、監視なしに本番の起動設定を変える判断はリスクが見合わないと
  判断した。除外リストを有効化するかどうか（対象モデル名を含む）は、ユーザーまたは次回の監視可能なセッションの判断に委ねる
- VRAMプリフライトチェック（`run_daily.bat`冒頭でのチェック）・`_unload_model()`タイムアウト延長・Watchdogタスクの
  復活/削除判断は、いずれも上記と同様の理由、またはGPU実機検証が要るため見送った（残存リスク節に残したまま）

### 次回やること
- `OLLAMA_EXCLUDE_MODELS`を実際に`run_daily.bat`で設定するか判断する（ユーザー、または日中/監視可能なセッション）
- 上記「残存リスク」「別件」に残る3項目（`_unload_model`タイムアウト・VRAMプリフライト・Watchdogタスク）は未着手のまま

## 2026-08-10 12:07 自律発案: VRAMプリフライトチェックの実装（`run_daily.bat`への組み込みは見送り）

### 背景
`隙間時間有効活用\tasks\candidate_pool.md`の「未着手・着手可能な候補」にあった「VRAMプリフライトチェックの実装検討」に着手。上記「残存リスク」節の恒久対策候補（`run_daily.bat`冒頭で空きVRAM確認）をコードとして実装した。

### やったこと
- `src/vram_preflight.py`を新規作成: `get_free_vram_mb()`（`nvidia-smi --query-gpu=memory.free`で取得、非搭載機はNoneでfail-open）、`try_free_ollama_models()`（`/api/ps`でロード中モデルを列挙し`keep_alive=0`で即時アンロード、ベストエフォート）、`check_preflight(min_free_mb, ...)`（不足時はアンロードを試み再判定）、CLI(`main()`、exit 0=実行可/1=不足)を実装
- 既定閾値`DEFAULT_MIN_FREE_MB=14000`は`gpt-oss:20b`のVRAM常駐実測(13.9GB、上記2026-08-09 10:19節)に余裕を加えた値
- `tests/test_vram_preflight.py`新規16件（nvidia-smi成功/非搭載/異常終了/不正出力、アンロード成功/接続失敗/個別失敗耐性、preflight判定の各分岐、CLI）追加。`pytest tests/ -q`で255件全pass（既存239+新規16）
- 実機(PC2, GPUアイドル・空きVRAM 15170MB)でCLI実行し、閾値14000MBでは`exit 0`、閾値999999MBでは`exit 1`（Ollamaに現在ロード中モデル無しのため即NG）と、想定通りの分岐を確認
- `git add`+コミット（push無し）

### あえてやらなかったこと
- **`run_daily.bat`（`C:\work\run_daily.bat`、daily-tech-trendのgit管理外・本番の生きた無人パイプライン）への実際の組み込みは行っていない**。候補プールの記載通り「本番の無人パイプラインに触れるため、テスト・ドライラン必須で通常以上の慎重さが必要」な変更であり、`OLLAMA_EXCLUDE_MODELS`（2026-08-09 11:07節）と同じ理由（監視なしでの本番起動設定変更はリスクが見合わない）で、実配線の判断はユーザーまたは監視可能なセッションに委ねる
- 実配線する場合の組み込み案（`(A) Precheck disabled`セクション、collectの直前に追加する想定）:
  ```bat
  set "LASTSTEP=vram_preflight"
  py -3.11 -u "%ROOT%\src\vram_preflight.py" --min-free-mb 14000 >> "%LOG%" 2>&1
  if not "!ERRORLEVEL!"=="0" (
    echo [SKIP] insufficient VRAM, skip run >> "%LOG%"
    exit /b 0
  )
  ```
  正常終了(`exit /b 0`)扱いでスキップする設計は、2026-08-09 10:19節の教訓「PAUSE時のexit /b 0はタスクスケジューラ上成功に見えて監視の盲点になる」と同じ死角を持つため、実配線時はログの`[SKIP]`行を監視するか、専用の通知を追加することを推奨する
- system RAM枯渇（事故の実測値は28.8GB）そのものの直接チェックは実装していない（候補プール記載のスコープが「VRAM」だったため）。GPU VRAMが十分な状態を保てればOllamaがCPUオフロードに頼らずRAM消費が抑えられる想定だが、厳密な再発防止にはRAM側の空き容量チェックも将来的に検討の余地がある

### 次回やること
- 実配線の承認判断（ユーザー、または日中/監視可能なセッション）
- 上記の他、`_unload_model`タイムアウト延長・Watchdogタスクの扱い・`OLLAMA_EXCLUDE_MODELS`実運用判断は未着手のまま

## 2026-08-11 05:07 自律発案: `_unload_model()`のPOSTタイムアウト延長

### 背景
`隙間時間有効活用\tasks\candidate_pool.md`の「未着手・着手可能な候補」にあった「`_unload_model()`のPOSTタイムアウト延長」に着手した（既存プロジェクトフォルダを再利用、新規Templateコピーなし）。上記「残存リスク」節に残っていた最後の1件（RAMスラッシング中はアンロード要求自体が旧10秒では失敗しうる）。

### やったこと
- `src/llm_insights_api.py`にタイムアウト定数群（`LLM_LONG/SHORT/HEALTH_TIMEOUT_SEC`）と同じパターンで`LLM_UNLOAD_TIMEOUT_SEC`(環境変数で上書き可能、既定30秒)を新設し、`_unload_model()`のデフォルト引数をハードコードの`10.0`からこの定数へ変更した
- `tests/test_llm_autostart.py`にテスト2件追加（デフォルトタイムアウトが定数経由で30秒になること・明示指定時は従来通り上書きされること）、`pytest tests/ -q`で257件全pass（既存255+新規2）
- ローカルコミット`02a9ed8f5`（push無し）

### 他候補との違い（VRAMプリフライト・OLLAMA_EXCLUDE_MODELSとの区別）
今回は`run_daily.bat`（git管理外の本番無人パイプライン）への新規ステップ組み込みではなく、既存の呼び出し経路（`_ensure_model_prepared()`・`post_ollama`の失敗時アンロード）が使うデフォルト値の変更のみ。タイムアウトを延長する変更は「失敗までに待つ時間が伸びる」という一方向の効果しかなく、通常時の正常な動作フロー自体は変えない（例外パスは従来通りWARNログのみでcatchされる）ため、VRAMプリフライト/OLLAMA_EXCLUDE_MODELSのような「本番の起動設定を監視なしで変える」ケースには当たらないと判断し、承認待ちにせずそのまま実装・コミットした。

### 次回やること
- Watchdogタスク(`Watchdog Daily Tech Trend`)の復活/削除判断は未着手のまま（ユーザー判断待ち）
- `OLLAMA_EXCLUDE_MODELS`実運用判断・VRAMプリフライトの`run_daily.bat`実配線判断も引き続きユーザー/監視可能なセッション待ち
- これで「残存リスク」節の3項目は全てコード対応済み。残る論点は全て「本番設定の有効化判断」のみ（ユーザー判断待ち）

---

# パイプライン更新効率化（2026-08-13）

## 背景（実測）
1回の実行 38分 / 1日6回（6,9,12,15,18,21時）= 1日3.8時間。
`logs/run_20260813_150002.log` および本番DBコピーでのプロファイル結果:

| ステップ | 実測 | 備考 |
|---|---:|---|
| collect | 44s | 正常 |
| normalize | 10s | 正常 |
| dedupe | 847s | 全32,319件を毎回総当たり。削除は43件のみ |
| thread | 855s | うち491sが `mark_news_representative_articles` |
| translate | 41s | 正常 |
| LLM(rescue) | 300s | 毎回 max_sec で打ち切り |
| render | 15s | 正常 |
| その他 | ~170s | backfill/forecast/exec/push |

dedupe + thread = 1702s = 全体の74%。

## 根本原因
1. `thread.py:78` `mark_news_representative_articles`
   CTE `ranked` を相関サブクエリ `EXISTS(...)` 内で参照 → SQLiteが対象行ごとに
   ウィンドウ関数クエリを再計算。実測491s。（edges再構築は実測0.03sで無関係）
2. `dedupe.py`
   純Pythonの `SequenceMatcher` を874万回呼ぶ（実測換算514s）。
   判定済みの過去記事3万件を毎回再判定しており、新規は1日314件のみ＝99%が無駄。
3. LLM未生成トピック 16,655件。300s×6回=30分/日では消化不能。

## 方針（ユーザー承認済み 2026-08-13）
- 範囲: 高速化 + スケジュール再設計
- 制約: **既存の判定結果と完全一致を維持**（検証必須）

### 設計判断: rapidfuzz置換は採用しない
`difflib.SequenceMatcher`(Ratcliff/Obershelp) と `rapidfuzz.fuzz.ratio`(Indel距離ベース)は
計算式が異なり同値を返さない。「完全一致維持」と両立しないため、
**増分化のみ**で高速化する。比較回数が874万→数万に落ちるためSequenceMatcherのままで足りる。

## タスク

### Phase 1: thread.py の1クエリ書き換え … 完了
- [x] `mark_news_representative_articles` を一時テーブル経由の2段UPDATEに書き換え
- [x] 本番DBコピーで旧実装/新実装の `is_representative` 全行一致を検証
      → **32,319行 完全一致**（`is_representative=1` は 15,870行で内訳も一致）
      → **592.06s → 0.21s（2,768倍）**
      （旧592sはdedupe検証との並行実行によるもの。単独実測は491s）
- [x] `pytest tests/` 全pass（257件）

### Phase 2: dedupe.py の増分化 … 完了
- [x] `articles.dedupe_checked INTEGER DEFAULT 0` を `ensure_column` で追加
- [x] 判定済み記事は「候補インデックスへの登録」のみ行い、類似比較をスキップ
      （exact URL判定は安価なので従来通り実施し、取りこぼしを防ぐ）
- [x] 判定した記事に `dedupe_checked=1` を立てる
- [x] 本番DBコピーで旧実装/新実装の削除article_id集合と dedupe_judgments 一致を検証
      → 残存article_id **完全一致**、dedupe_judgments **18,115行 完全一致**
      → 定常状態 **598.8s → 0.87s（691倍）**
- [x] `pytest tests/` 全pass（257件）

**注意: 新コードでの初回実行のみ、全記事に判定済みフラグを立てるため
従来通り約9分かかる（実測517s）。2回目以降が0.87s。**

### Phase 3: LLM予算の拡大（本番設定変更・ユーザー承認済み 2026-08-13）
浮いた28分をLLM insight生成に回す。light/full分離は今回は採用せず、
`LLM_MAX_SEC` の引き上げのみという最小変更を選択（案は
`scratchpad/run_daily_proposed.bat` に保存済み。将来必要になれば流用可）。

判断根拠（DB実測）: 新規トピック数に対し insight 生成が慢性的に1/3しかなく、
未生成16,655件は「古い取りこぼし」ではなく毎日積み上がる赤字だった。

| 日付 | 新規topic | insight生成 |
|---|---:|---:|
| 08-13 | 154 | 60 |
| 08-12 | 227 | 85 |
| 08-11 | 169 | 53 |
| 08-10 | 257 | 50 |
| 08-09 | 454 | 94 |

- [x] `C:\work\run_daily.bat` に `LLM_MAX_SEC=1200` を追加
      バックアップ: `C:\work\run_daily.bat.bak_20260813_195026`
      バッチ構文と環境変数のPythonへの伝搬をテストバッチで実行確認済み
- [x] タスクスケジューラは6回（6/9/12/15/18/21時）のまま据え置き（再登録不要）
  - **訂正（2026-08-14 02:07セッション）**: この確認は誤りだった。`Get-ScheduledTask -TaskName "Daily Tech Trend"`で実際のトリガーを確認したところ、6:00/9:00/12:00/15:00の4トリガーは`Enabled: True`だが、**18:00・21:00の2トリガーは`Enabled: False`**だった（`StartBoundary`が2025-12-23で他4件の2025-12-14〜12-18より後に追加されており、追加時に有効化し忘れた可能性がある。`NumberOfMissedRuns=2`とも整合）。つまり実際は1日4回（6/9/12/15時）しか動いておらず、21:00実行自体が発生しないため下記の検証は今晩できない。詳細は下記「2026-08-14 02:07セッション」節参照
- [ ] ~~次回実行（21:00）のログで所要時間と insight 生成数を確認~~ → 21:00トリガーが無効化されているため実行自体が発生しない。次に確認できるのは翌日06:00の実行（2回目、`step=dedupe`が0.87秒になるかの本命確認）

### 次回セッションで確認すること
- **上記の通り21:00実行は発生しない**。以下は21:00トリガーが有効化された後、または翌06:00実行を対象に読み替えること
- 実行ログ（`logs/run_YYYYMMDD_HHmmss.log`）で:
  - `step=dedupe end` … 初回のため約9分（517s）の見込み。**2回目（翌6:00）以降が0.87秒になるかが本命**
  - `step=thread end` … 0.5秒程度になっているか
  - `[TIME] llm budget reached sec=... max_sec=1200` … 予算が1200秒で効いているか
  - 全体の `SUCCESS_FROM_BAT` までの所要時間
- 数日後に「新規topic数 vs insight生成数」を再測し、赤字が解消したか確認する
  （解消しない場合はさらに `LLM_MAX_SEC` を引き上げるか、light/full分離
   `scratchpad/run_daily_proposed.bat` の採用を検討する）

### 今回スコープ外にした既知の問題
- **`Watchdog Daily Tech Trend` タスクが 2026/04/06 以降 一度も実行されていない**
  （State=Ready、30分間隔トリガーがあるにもかかわらず最終実行が4ヶ月前）。
  監視が事実上死んでいる。過去セッションから「ユーザー判断待ち」で持ち越されている案件
- **トピック統合がほぼ機能していない**: topics 34,572件に対し topic_articles 32,319件、
  edges 195本。ほぼ1記事1トピックの状態で、`thread.py` の統合閾値
  （TOPIC_SIM=88 / NEWS_TOPIC_SIM=94）が厳しすぎる可能性がある。
  これはLLM未生成トピックが積み上がる一因でもある（トピックが分散するほど生成対象が増える）

### 検証手順
1. 本番DBを scratchpad にコピー
2. 旧実装（git stash / 別コピー）と新実装をそれぞれコピーDBに対して実行
3. 出力テーブルを全行比較して一致を確認
4. `pytest tests/` 全pass
5. 実際の1回実行で所要時間を実測し、38分→目標10分以内を確認

## 目標
1回 38分 → 10分以内。1日3.8時間 → 1時間以内。
浮いた時間をLLM insight のバックログ消化に回す。

---

## 2026-08-14 02:07 自律発案セッション: `Daily Tech Trend`タスクの18時・21時トリガーが無効化されていた発見

### 発端
`隙間時間有効活用`のExploreエージェントによる全プロジェクト横断フルスキャンで、「08-13 15:00の実行ログ以降、18:00・21:00分のログが存在しない」ことを発見。08-13セッションが前提としていた「タスクスケジューラは6回（6/9/12/15/18/21時）のまま」（上記1079行目）と矛盾するため詳細調査した。

### 確認した事実（PowerShell `Get-ScheduledTask`実機確認）
```
Daily Tech Trend のトリガー:
  06:00 Enabled=True （StartBoundary 2025-12-14）
  09:00 Enabled=True （StartBoundary 2025-12-18）
  12:00 Enabled=True （StartBoundary 2025-12-18）
  15:00 Enabled=True （StartBoundary 2025-12-23）
  18:00 Enabled=False（StartBoundary 2025-12-23）
  21:00 Enabled=False（StartBoundary 2025-12-23）
Get-ScheduledTaskInfo: LastRunTime=2026/08/13 15:00:01, NumberOfMissedRuns=2
```
- 過去ログ（`logs/run_*.log`、2026-08-09〜08-13の全ログファイル名）を確認したところ、この期間**一度も18時・21時台の実行ログが存在しない**。つまり今回に限らず、少なくともここ数日間は恒常的に1日4回（6/9/12/15時）しか動いていなかった
- `NumberOfMissedRuns=2`は08-13の18:00・21:00分と一致する
- いつ・誰が18時/21時トリガーを無効化したかの記録は`tasks/`配下のどこにも見当たらない（`apply_task_scheduler_fixes.ps1`は`Watchdog Daily Tech Trend`タスクのトリガー再有効化のみを扱っており、`Daily Tech Trend`本体のトリガーには触れていない別問題）
- この状態のまま昨日（08-13 19:50時点でユーザー承認済み）`LLM_MAX_SEC=1200`を`run_daily.bat`に追加し「21:00実行で効果確認」を予定していたが、21:00トリガーが無効なため**その検証は今後も自然には発生しない**

### 対応方針（今回は変更を実施していない）
`Set-ScheduledTask`/`Enable-ScheduledTaskTrigger`によるトリガー有効化そのものは`.claude\hooks\block-dangerous-commands.sh`の夜間厳格モードの明示的なブロック対象（`Unregister-ScheduledTask`）ではなく技術的には実行可能だったが、以下の理由から無人セッションでの実施を見送った:
- このタスクは2026-08-04に**RAM枯渇で14時間PCフリーズ**という実害事故を起こし緊急停止(`daily_tech_trend.PAUSE`)された経緯があるプロジェクトである（08-09にユーザーが監視付きで解除済み）
- 無効化されている2トリガーがどちらもユーザーが在席している可能性が高い夕方〜夜間（18時・21時）であり、「17時以降は自動実行しない」という`隙間時間有効活用`自身の夜間ガード方針とも重なる時間帯
- 無効化が意図的な過去の安全策なのか単なる設定漏れなのか確証が持てない
- 1日4回→6回への変更は本番の実行頻度・リソース消費に関わる設定変更であり、`OLLAMA_EXCLUDE_MODELS`実運用判断・VRAMプリフライトのrun_daily.bat配線と同じ「監視可能なセッションでの承認が必要」方針に該当すると判断した

したがって`隙間時間有効活用\tasks\candidate_pool.md`の「ブロック中の候補」表に追加し、ユーザー判断待ちとした。

### 次回セッションへの申し送り
- [ ] ユーザーが18時・21時トリガーを意図的に無効化した理由に心当たりがあれば、そのまま4回/日を継続するか、有効化して6回/日に戻すかを判断してほしい
- [ ] 有効化する場合のコマンド例（管理者PowerShell、日中の対話セッションで実行想定）:
  ```powershell
  $t = Get-ScheduledTask -TaskName "Daily Tech Trend"
  foreach ($tr in $t.Triggers) { if ($tr.StartBoundary -match "T(18|21):00:00") { $tr.Enabled = $true } }
  Set-ScheduledTask -TaskName "Daily Tech Trend" -Trigger $t.Triggers
  ```
- [ ] 有効化された後、上記「次回セッションで確認すること」節のLLM_MAX_SEC=1200検証（`step=dedupe`所要時間・insight生成数）を実施する

---

## 2026-08-14 rescue の空回り解消（LLM予算の実効化）

### 背景（実測）
`LLM_MAX_SEC=1200` により1回のLLM処理件数は 22〜27件 → 110〜115件（4.5倍）になったが、
そのうち約半分が「内容が1文字も変わっていないトピックの再生成」だった。

`llm_insights_local.py:88`（旧）:
```python
if (not rescue) and prev_hash and (prev_hash == src_hash):
    continue
```
`--rescue` は設計上ハッシュ比較を無効化していた（help: "Reprocess rows even when
source hash is unchanged"）。一方 `pick_topic_inputs` の rescue 条件は
`t.category='news' OR l.kind='news'` で news を無条件に候補へ入れるため、
news トピックが毎回そのまま作り直されていた。

本番DB実測（limit=120, rescue=True）:
- 候補 120件 = 未生成59 + 内容変化0 + **内容変化なし61（50%）**
- 「内容変化あり」は0件。つまり61件は完全な空回り

### 対応
1. `llm_insights_pipeline.pick_topic_inputs`: 壊れた insight の判定材料として
   `prev_importance` と `prev_summary_empty` を返すよう追加
   （summary は本文が長いので空フラグだけを持ち出す）
2. `llm_insights_local`: `_needs_repair()` を追加し、rescue でも
   「ハッシュ未変更 かつ 壊れていない」行はスキップ。
   壊れた insight（importance=0 / 要約が空）は従来通り作り直す
3. `run_daily.bat`: 候補件数を既定120 → **500** に引き上げ
   （これをしないと有効候補59件で予算1200秒＝約110件分を使い切れず**逆効果**になる）

### 効果（本番DB実測）
| limit | 候補 | LLMに投げる | スキップ |
|---:|---:|---:|---:|
| 120（旧既定） | 120 | 59 | 61 |
| **500（新設定）** | 500 | **344**（未生成331+変化13） | 156 |

`pick_topic_inputs` のSQLコストは limit 120/500 とも 0.70秒で差なし。
スキップ判定コストは 0.00秒。予算1200秒がすべて実のある生成に使われる見込み。

### テスト
`tests/test_llm_rescue_skip.py` 新規9件（_needs_repair の4分岐、新カラムの返却、
内容未変更でスキップ／続報で再生成／壊れた行は修復／未生成は常に処理）。
`pytest tests/` 266件全pass（既存257 + 新規9）。

### 次回確認すること
- 8/15 06:00 以降のログで:
  - `[TIME] llm candidates=500 limit=500 rescue=1`
  - `[TIME] step=llm end sec=... processed=... skipped_unchanged=...`
    → processed が110件前後、skipped_unchanged が出ていること
  - `insight未生成トピック総数` の減少ペースが上がったか（8/14時点 16,556件）

### 未対応（指摘のみ）
- **`--delay` 既定3秒が予算の約27%を消費している**。1件あたり LLM 8秒 + sleep 3秒で、
  110件なら330秒が待ち時間。0にすれば同じ1200秒で約150件処理できる計算（+36%）。
  ただし LLM サーバーへの負荷軽減が目的の設定であり、2026-08-04 のRAM枯渇事故の
  経緯があるため無断では変更していない
- 18時・21時トリガー無効（1日4回）の有効化はユーザー判断待ちのまま

---

## 2026-08-15 技術動向ダイジェスト（tech）を LLM 生成対象から除外

### 背景
ユーザーが技術動向ダイジェストを参照しなくなったため、関連機能を止めて
処理時間を短くできないかという相談。調査した結果:

- **「技術動向ダイジェスト」は `src/templates/tech.html` の h1 で、生成先は
  `docs/tech/index.html`。夜間バッチがこれを `docs/index.html` へコピーしているため
  サイトのトップページそのもの**（CLAUDE.md にも「両者は同一」と記載）
- **処理時間はほとんど短くならない**。LLM が `LLM_MAX_SEC=1200` の予算で
  打ち切られており、tech を外しても news の未生成が9,934件残っていて候補が尽きず、
  1200秒をそのまま使い切るため。短縮は30〜60秒（全体の2〜4%）程度

8/15 15:00 実行（26分40秒=1600秒）の内訳:
| ステップ | 秒 | tech停止で |
|---|---:|---|
| LLM insight | 1208 | 変わらない（予算固定） |
| generate_perspective_digest | 107 | 対象の64%がtech |
| collect | 34 | 約半分 |
| render | 16 | 少し減る |
| translate | 11 | 約半分 |
| dedupe / thread | 1.6 | 誤差 |
| その他(exec_summary/forecast/backfill/push) | 約212 | exec_summaryは7カテゴリ中6つがtech系 |

### 対応（ユーザー承認: 「LLM要約だけ止める」）
tech ページと収集は残し、LLM の insight 生成対象から tech を外した。
得られるのは時間短縮ではなく**予算の集中**（news の消化が約2倍）。

1. `pick_topic_inputs(conn, ..., skip_kinds=())` を追加。
   `COALESCE(NULLIF(t.kind,''), NULLIF(l.kind,''), '')` で判定し、
   topics.kind を優先。topics.kind が無い旧DB/テストDBでは記事側の kind に
   フォールバック（`_topics_has_kind()` で検査、既存の `_articles_has_region()` と同じ方式）
2. `llm_insights_local` に `--skip-kinds`（既定は env `LLM_SKIP_KINDS`）を追加
3. `run_daily.bat` に `set "LLM_SKIP_KINDS=tech"` を追加

### 効果（本番DB実測, limit=500 rescue=True）
| 設定 | 候補の内訳 | LLMに投げる | スキップ |
|---|---|---:|---:|
| 除外なし | news 250 / tech 250 | 305 | 195 |
| **tech除外** | **news 490 / tech 10** | **250** | 250 |

tech が10件残るのは topics.kind='news' だが最新記事が tech のケース。
topics.kind 基準では news 側のコンテンツなので除外しないのが正しい（安全側）。
有効候補250件は予算（約110件分）を上回るので予算は使い切れる。

### テスト
`tests/test_llm_skip_kinds.py` 新規6件（tech除外／除外なし／大文字小文字と空白/
複数除外／topics.kind非搭載DBでのフォールバック／rescue下でも効くこと）。
`pytest tests/` 272件全pass（既存266 + 新規6）。
バッチの環境変数伝搬もテストバッチで実行確認済み。

### 残っている tech 向け LLM 処理（ユーザー判断待ち）
insight 生成以外にも tech を対象にした LLM 処理が残っている。
**これらは固定予算ではないため、止めれば実際に処理時間が減る**:
- `generate_perspective_digest`: 実測107秒/回。未生成は tech 10,446件 / news 5,778件で
  **対象の64%が tech**。kind の区別なく `topic_insights` 全体を対象にしている
- `exec_summary`: 対象カテゴリは ai/security/manufacturing/system/policy/market/news の
  7つで、**news以外の6つが tech 系**。1カテゴリごとにLLMを呼ぶ
- `forecast_generate` / `forecast_verify`: tech 記事も予測の材料にしている
