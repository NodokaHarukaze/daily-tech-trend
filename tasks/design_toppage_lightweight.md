# トップページ軽量化 設計書（2026-07-28 11:07 自律発案セッション）

## 背景・経緯

- `tasks/todo.md`564-580行目「残り（第2弾以降）」で唯一の未実装項目として残っている「トップページ軽量化（`docs/index.html` 964KB→初期表示絞り込み）」の設計。
- 2026-07-28 09:07セッションの計測: `.topic-row`が244件、先頭行以降で約970KB（全体の98.4%）、1行平均約3.97KB。「既存のクライアント側フィルタ/ソート/タグ機能（`common.js`）はDOM上の全`.topic-row`を対象に動くため、単純な行数カット/ページネーション化はフィルタ結果に『読み込んでいない行』がヒットしなくなる副作用がある」という懸念でJSON API化+クライアント側再構築の要検討のまま実装を見送っていた。
- 本セッションはこの懸念を解消できる、より低リスクな設計を提示する。

## 追加計測（本セッションで実施）

`docs/index.html`（2026-07-28時点、834,322文字）に対し `[regex]::Matches` で内訳を実測:

| 対象 | 件数 | 合計文字数 | ファイル全体比 |
|---|---|---|---|
| `<details class="insight">...</details>`（要約・key_points・perspectives・perspective_digest・evidence_urls、既定で折りたたみ非表示） | 200блок | 435,066 | **52.1%** |
| `data-summary="..."`属性（検索用の全文サマリー、`.topic-row`のトップレベル属性として既に存在） | 244 | 14,459 | 1.7% |

**重要な発見**: ファイル全体の過半（52.1%）が、初期表示では**常に非表示**（`<details>`のデフォルトclosed）の`.insight`ブロックに集中している。一方、検索・フィルタ機能（`common.js` `applyFilter()`）が実際に読んでいるのは `data-title`/`data-summary`/`data-imp`/`data-recent`/`data-date`/`data-tags` という軽量な属性群のみで、`data-summary`だけでも244件合計14.5KB（ファイル全体の1.7%）に過ぎない。**`.insight`の中身と、検索・フィルタが依存するデータは完全に独立している。**

## 設計方針（採用案）: `.insight` 中身のみ遅延ロード。行は間引かない

09-28セッションが懸念していた「行削除を伴うページネーション/JSON API化」ではなく、**既存の`.topic-row`要素は現状どおり全件を初期HTMLに残したまま、`<details class="insight">`の中身だけを外部JSONへ切り出し、ユーザーがそのdetailsを初めて開いたタイミングで`fetch`する**方式を採る。

根拠:
- 検索・フィルタ・ソートは `data-*` 属性だけで完結しており、`.topic-row`要素自体をDOMから削除・遅延生成する必要が一切ない → **既存JS（`applyFilter`/`applySort`/`toggleTag`等）への変更が不要**、フィルタ結果に「読み込んでいない行」が混じるリスクが構造的に発生しない
- 実装済みの「検索インデックス(1.9MB)遅延ロード」（`src/render_feeds.py` `SEARCH_HTML`、`q.addEventListener('focus', () => ensureIndex(), {once:true})`）と全く同型のパターンを流用できる → 新規の設計要素が少なく、`common.js`のレビュー済みコードスタイルを踏襲できる
- 対象が「初期表示で必ず非表示」な52.1%のブロックなので、**初回訪問者の大半（詳細を1件も開かない/数件だけ開くユーザー）にとって実質的な転送量削減効果が最大化される**。全件を開くヘビーユーザーだけが結果的に検索インデックスと同程度のJSONを追加取得する

### データ設計

- ページ単位（tech / news）で1個のJSONにまとめる（Optionを比較検討した結果、採用）:
  - `docs/assets/data/insights_tech.json`、`docs/assets/data/insights_news.json`
  - 構造: `{"<topic_id>": {"summary": "...", "key_points": [...], "perspectives": {...}, "perspective_digest": {...}, "evidence_urls": [...]}, ...}`
  - キーは既存の `id="topic-{{ t.id }}"` と同じ topic id を使う（`href="/daily-tech-trend/topic/{{ t.id }}/"`と一貫）
- **1ページ1ファイルにまとめ、topic単位の個別JSON（244ファイル）にはしない**。理由: 静的ホスティング（GitHub Pages）でリクエスト数を増やすとレイテンシオーバーヘッドの方が支配的になりやすく、検索インデックスも同じ「1ファイルへの遅延fetch」方式で既に実績がある。ブラウザキャッシュも1ファイルの方が効率的
- JSON自体のサイズは現状の`.insight`実HTML合計（435KB, tech単体）よりは小さくなる想定（HTMLタグ・属性のオーバーヘッドを含まない生データのみのため）。厳密なサイズは実装時に実測する

### レンダリング側の変更方針（`render_main.py` / `src/templates/tech.html` / `news.html`）

1. `render_main.py`側でtopics_by_catループ時に、`.insight`へ渡している辞書（summary/key_points/perspectives/perspective_digest/evidence_urls）を**そのままinsightsマップにも集約**して`insights_tech.json`として書き出す関数を追加する（既存の `_safe_json_list`/`_safe_json_obj` はそのまま再利用可能、二重に持つだけなので抽出ロジックの変更は不要）
2. テンプレート側（`tech.html`/`news.html`）の`<details class="insight">`内部は、**中身をレンダリングせず**プレースホルダのみにする:
   ```html
   <details class="insight" role="group" data-insight-topic="{{ t.id }}">
     <summary>要約・解説を表示</summary>
     <div class="insight-body" data-insight-pending>読み込み中…</div>
   </details>
   ```
3. `common.js`に検索インデックスと同型の遅延ロード関数を追加:
   ```js
   let insightsIndex = null, insightsPromise = null;
   function ensureInsights(pagePrefix) {
     if (insightsPromise) return insightsPromise;
     insightsPromise = fetch(`assets/data/insights_${pagePrefix}.json`)
       .then(r => r.json()).then(data => { insightsIndex = data; });
     return insightsPromise;
   }
   document.querySelectorAll('details.insight[data-insight-topic]').forEach(det => {
     det.addEventListener('toggle', () => {
       if (!det.open) return;
       ensureInsights(pagePrefix).then(() => renderInsightBody(det, insightsIndex[det.dataset.insightTopic]));
     }, { once: true }); // 初回openのみ、以降は既にDOMに描画済みのHTMLをそのまま使う
   });
   ```
   - `{once:true}`のリスナーで初回展開時のみfetch・DOM構築を行う。2回目以降の開閉はブラウザ標準の`<details>`挙動に任せる（追加のfetchなし）
   - `renderInsightBody()`は現行テンプレートの`<details>`内部（summary/key_points/perspectives/perspective_digest/evidence_urls のHTML化ロジック、`tech.html`230行目以降相当）をJS側に移植する必要がある。ここが今回の設計で唯一「テンプレートのロジックをJSに二重実装する」箇所であり、実装時の主な作業量・保守コストになる

### 既存機能への影響評価

| 機能 | 影響 |
|---|---|
| 検索（`applyFilter`, タイトル/サマリー一致） | **影響なし**。`data-summary`属性はレンダリング時のまま`.topic-row`に残す（今回の遅延ロード対象外） |
| タグフィルタ（`data-tags`） | 影響なし（`.topic-row`属性のまま） |
| ソート（`applySort`, `data-imp`/`data-recent`/`data-date`） | 影響なし（同上） |
| ハッシュリンクでの直接ジャンプ（`revealHashTarget`→`el.querySelector('details.insight'); det.open = true`） | **要対応**。現状 `scrollToHash()` が `details.insight` を強制的に `.open = true` にする箇所（`common.js` 187行目）があるため、そこで `toggle` イベントが発火し遅延ロードが動くことを確認する必要がある（プログラムによる`.open`代入でも`toggle`イベントは発火するため理論上は問題ないはずだが、実装時に実機検証が必須） |
| `content-visibility: auto`（既存の描画パフォーマンス対策） | 影響なし・併用可能 |
| forecast / forecast_hits ページ | 対象外（`.topic-row`+`details.insight`パターンを使っているのは`tech.html`/`news.html`のみとgrepで確認済み） |

### 検証方法（実装セッション向け）

- 第1〜3弾のテンプレート外部化セッションと同じ方式: `python src/render.py` 実行前後で `docs/`配下の差分を確認する。ただし今回は意図的にHTML構造を変える変更のため「完全一致」ではなく「`.insight`内部のプレースホルダ化」「新規`insights_*.json`の生成」を期待値として明示的に検証するテストを書く
- ブラウザでの実機確認が特に重要な変更（`details`のtoggleイベント発火・ハッシュリンクからの直接ジャンプ時の遅延ロード）。無人セッションでの実装時は、Node.jsの軽量DOM（例: 標準ライブラリのみで`<details>`の`toggle`エミュレートは難しい）またはPlaywright等が必要になる可能性があり、実装の見積もりに含めること
- 転送量の実測: 実装後に `docs/index.html`（新）+ 初回に開くdetails 1件分の`insights_tech.json`取得込みの合計と、旧`docs/index.html`単体を比較し、実際に初期表示コストが下がったことを数値で確認する

## リスクとスコープ外にしたもの

- **行のページネーション化・仮想スクロール化は採用しない**（09-28セッションが懸念した「読み込んでいない行が検索にヒットしない」副作用そのものが発生するため）。今回の設計は「行は全部残す・行の中身の一部だけ遅延ロードする」ことでこのリスクを構造的に回避している
- topic単位の個別JSON分割は不採用（前述の理由）
- `.insight`内部のレンダリングロジックをJSに二重実装するコストは残る。将来的にPythonとJSのロジックが乖離するリスクがあるため、実装時は「Jinja2テンプレート側のロジックをできる限り単純なデータ変換に留め、装飾的なHTML構造はJS側の1箇所にまとめる」ことを推奨する

## 次のステップ（実装セッション向け）

1. `render_main.py`に`insights_tech.json`/`insights_news.json`書き出し関数を追加（既存の辞書構築ロジックを流用）
2. `tech.html`/`news.html`の`<details class="insight">`内部をプレースホルダ化
3. `common.js`に`ensureInsights()`+`renderInsightBody()`+ `toggle`イベントの`{once:true}`リスナーを追加
4. `scrollToHash()`経由のハッシュリンク直接ジャンプで遅延ロードが正しく発火することを実機（ブラウザ）で確認
5. `render.py`前後の`docs/`差分比較＋新規テストの追加＋転送量の実測
