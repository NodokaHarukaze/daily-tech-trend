# 技術ナレッジベース

<!-- 過去プロジェクトで得た技術的知見を蓄積するファイル -->
<!-- 新規プロジェクト開始前・実装中に参照すること -->
<!-- 形式: 問題 → 解決策 → 適用条件 -->

---

## LLM 関連

### LLM出力の思考タグ除去（ストリーミング対応）

**問題**: Qwen/DeepSeek等の推論モデルは `<think>...</think>` で思考過程を出力する。ストリーミング時にタグがトークン境界をまたぐ場合がある（例: `<thi` で切れて次チャンクで `nk>` が来る）

**解決策**:
- 非ストリーミング: 正規表現で段階的に除去（閉じタグありブロック → 未閉じブロック → 孤立タグ → ヘッダー形式）
- ストリーミング: `<think>` の部分一致をバッファリングし、次トークンまで待機。`</think>` も同様
- `"Thinking Process:"` ヘッダー形式にも対応する

**適用条件**: 推論モデル（DeepSeek-R1, Qwen等）使用時

**出典**: LocalNotebookLM `backend/core/llm_client.py`

---

### LLM出力の JSON パース（コードブロック対応）

**問題**: LLMが JSON を返す際、` ```json ... ``` ` でラップする場合がある。直接 `json.loads()` するとパース失敗

**解決策**:
```python
raw = raw.strip()
if "```" in raw:
    start = raw.find("[")
    end = raw.rfind("]") + 1
    if start != -1 and end > start:
        raw = raw[start:end]
suggestions = json.loads(raw)
```
- パース失敗時は空リストなどのフォールバックを返す

**適用条件**: LLMにJSON形式の出力を要求する全てのケース

---

### 言語別システムプロンプト（日本語出力強制）

**問題**: LLMが英語で回答してしまう場合がある（特に入力が英語の場合）

**解決策**:
- 「必ず日本語で回答してください。日本語以外の言語で回答してはいけません。」と明示的に指示
- 「思考過程は出力せず、回答のみを直接出力してください。」も追加
- 言語設定をDBの `Setting` テーブルで管理し、プロンプトを動的に選択する

**適用条件**: 日本語アプリケーション全般

---

### 要約の Map-Reduce パターン（長文対応）

**問題**: 複数の大型ドキュメントのテキスト全体がコンテキストウィンドウを超える

**解決策**:
1. テキストが閾値以内なら直接要約
2. 超える場合は Map: チャンク分割 → 各チャンクを個別要約
3. Reduce: チャンク要約を統合 → 最終要約を生成

**注意点**:
- チャンク分割ロジックは chunker モジュールと統一する（重複実装を避ける）
- テキスト長はトークン数ベースで判定する（文字数ではない）

**適用条件**: 長文要約、大量ドキュメントの統合処理

---

## SSE ストリーミング

### バックエンド: queue + スレッド分離パターン

**問題**: 重い処理（OCR、LLM生成）をメインスレッドで実行するとUIが応答しない

**解決策**:
```python
progress_queue: queue.Queue[dict] = queue.Queue()

# バックグラウンドスレッドで処理
thread = threading.Thread(target=run_task, daemon=True)
thread.start()

def event_stream():
    while True:
        try:
            event = progress_queue.get(timeout=300)
        except queue.Empty:
            yield 'data: {"type": "error", "detail": "タイムアウト"}\n\n'
            break
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        if event["type"] in ("complete", "error"):
            break
    thread.join(timeout=5)

return StreamingResponse(
    event_stream(),
    media_type="text/event-stream",
    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
)
```

**ポイント**:
- `X-Accel-Buffering: no` で Nginx のバッファリングを回避
- `timeout=300` でハング防止
- `daemon=True` でプロセス終了時にスレッドも終了

**適用条件**: FastAPI + 重い処理の進捗配信

---

### フロントエンド: AsyncGenerator + バッファリングパターン

**問題**: SSE形式のデータがネットワークチャンク単位で到着し、イベント境界が不規則

**解決策**:
```typescript
async function* streamEvents(res: Response): AsyncGenerator<Event> {
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';  // 不完全な行をバッファに残す

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith('data: ')) {
        try {
          yield JSON.parse(trimmed.slice(6));
        } catch { /* 不完全なデータは無視 */ }
      }
    }
  }
  reader.releaseLock();
}
```

**ポイント**:
- `lines.pop()` で最後の不完全な行をバッファに残す
- `decoder.decode(value, { stream: true })` でマルチバイト文字の分断に対応
- `finally` で `releaseLock()` を呼ぶ

**適用条件**: ブラウザからSSEを手動パースする場合（EventSourceが使えないPOSTリクエスト等）

---

## React パターン

### ストリーミング時の段階的 State 更新

**問題**: LLMの逐次応答をリアルタイムで表示しつつ、最終的に sources や score も設定したい

**解決策**:
1. ユーザーメッセージを即座に state に追加
2. 空のアシスタントメッセージを仮追加
3. チャンク受信ごとに `setMessages` で最後の要素を更新
4. 完了イベントで sources と confidence_score を最終設定
5. エラー時は空のアシスタントメッセージを除去

**注意点**:
- 仮ID（`Date.now()`）はユニーク性が保証されない。UUID推奨
- チャンク更新ごとに配列全体をコピーする（`[...prev]`）ため、大量メッセージ時は性能低下の可能性

**適用条件**: チャットUIでのストリーミング表示

---

### AbortController による非同期処理の中断

**問題**: 画面切り替え時、前回の非同期処理がまだ走っていると古いデータが state に混入する

**解決策**:
```typescript
const abortRef = useRef<AbortController | null>(null);

useEffect(() => {
  abortRef.current?.abort();  // 前回を中断
  let cancelled = false;

  async function load() {
    const data = await fetchData(id);
    if (!cancelled) setData(data);
  }
  void load();

  return () => { cancelled = true; };
}, [id]);
```

**ポイント**:
- `cancelled` フラグで stale closure を防ぐ
- ストリーミング中の abort は `AbortController` の signal を fetch に渡す

**適用条件**: ノートブック/ページ切り替え時のデータロード全般

---

### インライン引用マーカーのレンダリング

**問題**: LLM出力の `[1]`, `[2]` をクリック可能なバッジに変換したい

**解決策**:
1. sources から citation_index のマップを構築
2. 正規表現 `/\[(\d+)\]/g` でテキストを分割
3. マッチ部分をボタンコンポーネントに置換、それ以外はテキストのまま
4. マップにない番号は無視

**適用条件**: RAGチャットでソース引用を表示する場合

---

## 日本語対応

### テキストチャンク分割での日本語文境界

**問題**: 英語は `. ` で文を分割できるが、日本語は `。` が文の終わり

**解決策**:
- 分割階層: 段落（`\n\n`）→ 文（`。` or `. `）→ トークン単位（強制分割）
- `re.split(r"(。|(?<=\.)\s)", text)` で日本語・英語両対応
- トークン数計算に tiktoken を使用（文字数ではない）
- チャンク間オーバーラップ（200トークン程度）で検索精度を維持

**適用条件**: RAG、要約など、テキスト分割が必要な処理全般

---

### テキストファイルのエンコーディング対応

**問題**: アップロードされたテキストファイルのエンコーディングが不確定

**解決策**:
```python
try:
    text = file_bytes.decode("utf-8")
except UnicodeDecodeError:
    try:
        text = file_bytes.decode("cp932")  # Shift-JIS
    except UnicodeDecodeError:
        raise HTTPException(422, "デコード失敗")
```

**改善案**: `chardet` や `charset-normalizer` で自動判定する方が堅牢（EUC-JP等にも対応）

**適用条件**: 日本語テキストファイルのアップロード処理

---

## Python / FastAPI パターン

### PyTorch meta tensor 問題の回避

**問題**: PyTorch 2.10+ と safetensors の組み合わせで、SentenceTransformers ロード時に CUDA 初期化が走り OOM/ハングする

**解決策**:
```python
original_cuda = os.environ.get("CUDA_VISIBLE_DEVICES")
original_is_available = torch.cuda.is_available
try:
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    torch.cuda.is_available = lambda: False
    model = SentenceTransformer(model_name, device="cpu")
finally:
    # 環境を復元
    if original_cuda is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = original_cuda
    torch.cuda.is_available = original_is_available
```

**適用条件**: PyTorch 2.10以降 + SentenceTransformers + CPU推論環境

---

### PDF テキスト抽出（埋め込みテキスト優先 + OCR フォールバック）

**問題**: PDFの種類（テキスト埋め込み vs スキャン画像）で最適な抽出方法が異なる

**解決策**:
1. まず `page.get_text()` で埋め込みテキストを抽出
2. 50文字以上あればそのまま使用（OCRスキップで高速）
3. テキストが少ない場合のみ OCR にフォールバック
4. OCR時は低解像度（150dpi）で軽量化
5. ページ数上限（max_pdf_pages）で計算量を制御

**適用条件**: PDF アップロード処理

---

### FastAPI lifespan（起動時初期化）

**問題**: データディレクトリやDBテーブルを起動時に自動作成したい

**解決策**:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 起動時の初期化処理
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    init_db()
    yield
    # 終了時のクリーンアップ（必要なら）

app = FastAPI(lifespan=lifespan)
```

**注意**: `on_event("startup")` / `on_event("shutdown")` は非推奨。lifespan を使う

**適用条件**: FastAPI プロジェクト全般

---

### FastAPI グローバル例外ハンドラ

**問題**: 外部サービス未接続やバリデーションエラーを統一的にハンドリングしたい

**解決策**:
```python
@app.exception_handler(ConnectionError)  # 外部サービス接続失敗 → 503
@app.exception_handler(ValueError)       # バリデーション失敗 → 400
@app.exception_handler(Exception)        # 予期しないエラー → 500（詳細は隠す）
```

**ポイント**:
- 500エラーは `logger.exception()` でスタックトレースを記録
- ユーザーには内部詳細を返さない（「内部サーバーエラーが発生しました」）

**適用条件**: FastAPI プロジェクト全般

---

### SQLite マルチスレッド設定

**問題**: FastAPI の複数スレッドから SQLite にアクセスするとエラー

**解決策**:
```python
engine = create_engine(
    f"sqlite:///{path}",
    connect_args={"check_same_thread": False},
)
```

**注意**: 書き込み競合の可能性あり。本番は PostgreSQL 推奨

**適用条件**: SQLite + FastAPI（開発・個人ツール向け）

---

### Pydantic BaseSettings での .env 対応

**解決策**:
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    llm_model: str = "default-model"
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()
```

**適用条件**: 環境変数管理が必要な Python プロジェクト全般

---

## Vite / フロントエンドビルド

### Vite プロキシ設定（API統合）

**問題**: React開発サーバーとAPIサーバーが別ポートでCORSエラーが発生

**解決策**:
```typescript
export default defineConfig({
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
```

**適用条件**: フロントエンド + バックエンド分離の開発環境

---

## EasyOCR

### 遅延初期化 + スレッドセーフ

**問題**: EasyOCR の Reader 初期化に1-2分かかる。起動時に初期化するとサーバー起動が遅延する

**解決策**:
- ダブルチェックロッキングで遅延初期化（初回OCRリクエスト時に初期化）
- GPU/CPU は `torch.cuda.is_available()` で自動判定
- 信頼度閾値は 0.3 以上を推奨（0.1だと低品質テキストが混入する）

**適用条件**: EasyOCR 使用時

---

## 実行環境情報の参照先

**自宅 LAN PC ハードウェア構成（PC1-3）および Ollama ローカル LLM クラスタ運用は `C:\work\★Template\ENVIRONMENT.md`（プロジェクト直下の `ENVIRONMENT.md`）に分離した。**

実行環境・ノード配置・モデル選定の検討時はそちらを参照すること。

---

## マルチエージェント LLM 対話 / ゲームAI

### 複数AIエージェントの発言ログ管理

**問題**: 各AIが共有コンテキスト(全員が見るチャット履歴)を参照する一方で、個別の秘匿情報(占い結果・内心メモ等)も管理したい

**解決策**:
- 共有ログ (全員可視) と 個別メモ (エージェントごとの state) を別変数で保持
- LLM呼び出し時は system prompt に役職/立場を埋め込み、user prompt に共有ログ + 個別メモを結合
- 終了後のリプレイ/開示機能のため個別メモは時系列 (day, phase) で蓄積する

**適用条件**: 複数AIが役割を持って対話するゲーム・シミュレーション・多視点ディベート等

---

### Ollama `keep_alive` で初回レイテンシを緩和

**問題**: モデルロード初回呼び出しで p95 が 10 秒超に跳ねる (2回目以降はキャッシュで 1 秒前後)。リアルタイムUIでは初回の遅延が致命的

**解決策**:
```python
ollama.chat(..., keep_alive="10m")  # 10分メモリ保持
```
- セッション開始時にウォームアップ呼び出しを1回入れると体感が改善
- 複数モデル同時使用は VRAM 圧迫するため、単一モデルを全エージェントで共有し system prompt で人格分離するのが効率的

**適用条件**: Ollama を UI の裏で使うリアルタイム用途全般

---

### LLM の「選択肢から1つ返す」出力を安定化: JSON強制

**問題**: 「候補から1つの名前だけを返せ」と指示しても、説明文混入・候補外の名前・自分自身を返す等の不正出力が一定割合で発生する

**解決策**:
- Ollama は `format="json"` オプションで構造化出力を強制できる
- プロンプトで `{"target": "名前"}` のスキーマを明示 + 候補を箇条書きで強調
- パース失敗時のフォールバック: 再試行1回 → それでも失敗なら候補からランダム選択
```python
resp = ollama.chat(model=m, messages=[...], format="json", options={"temperature": 0.4})
obj = json.loads(resp["message"]["content"])
target = obj.get("target")
if target not in candidates:
    # フォールバック
```

**適用条件**: LLM に選択肢から1つ選ばせる判断タスク全般 (投票/分類/ルーティング等)

---

### 日本語LLM対話タスクのモデル選定 (2026-04 時点)

**知見** (5人構成の人狼ゲームで 6 ゲーム実測):
| モデル | 日本語自然さ | レイテンシ (warm p95) | 役職遵守 |
|---|---|---|---|
| gemma2:9b | ◎ 自然な対話 | ~1.1s | ◎ |
| llama3.1:8b | ○ 翻訳調が混入 | ~0.9s | ◎ |
| qwen2.5:7b | ◎ 自然 | - | - |

**推奨**: 日本語対話が主のアプリは **gemma2:9b を第一候補**、llama3.1:8b を代替

**適用条件**: 日本語LLM対話アプリのローカルモデル選定

---

### gpt-oss:20b に文字数指定（「◯◯字程度」）を課すと reasoning が文字カウントで肥大化し空応答になる

**問題**: Ollama の `gpt-oss:20b`（`/v1/chat/completions` OpenAI互換エンドポイント）に「170〜230字程度」のような**文字数の目標**を含むプロンプトを与えると、`reasoning` フィールドで文字を一つずつ数える思考ループに入り、`max_tokens`（900）を使い切って `finish_reason: "length"`・`content` 空文字で返ってくることがある（実測で再現）。同じ内容でも「2〜3文」という文数指定に変えると即座に解消した

**解決策**:
- プロンプトで**文字数の目標を出さない**。「◯文字程度」ではなく「2〜3文」など文数・構成（考え方→推奨行動→注意点の順）で長さを制御する
- `/v1/chat/completions` ペイロードに `"reasoning_effort": "low"` を追加する（Ollama の OpenAI互換エンドポイントでも有効。reasoning が短くなり本文生成に確実にトークンが残る）
- 上記2つを組み合わせた場合、`finish_reason: "stop"` で 3〜4秒以内に安定して返るようになった（実測）

**適用条件**: Ollama + gpt-oss系モデルで自由記述の長文（100〜300字程度）をJSON構造化出力させる場合全般。既存の `think: false`（qwen3）・`think: "low"`（gpt-oss, `/api/chat`使用時）の知見の延長で、`/v1/chat/completions`（OpenAI互換）使用時は `reasoning_effort` パラメータを使う

**出典**: daily-tech-trend「立場別200文字サマリー」機能 Phase 1 実装時の実機検証（2026-07-12, 隙間時間有効活用 自律発案セッション）

---

### 夜間パイプラインの所要時間が日ごとに伸びる場合、原因は「GPU の空き VRAM 不足によるモデルの CPU 退避」

**問題**: `run_daily.bat` の所要時間が 8/1 は約40分だったのが 8/2 は約8時間、8/3 は約12.7時間と日を追って悪化し、
8/4 には RAM 28.8GB を枯渇させて PC が約14時間フリーズした。ログには
`HTTPConnectionPool(host='127.0.0.1', port=11434): Read timed out. (read timeout=180)` が大量に出る。

**原因の連鎖**: 他の GPU ジョブ（ComfyUI 常駐 / OneTrainer の LoRA 学習など）が VRAM を占有している
→ `gpt-oss:20b`（13.9GB）が VRAM 16GB に収まらずレイヤーの大半が CPU 実行に落ちる（`ENVIRONMENT.md` の既知現象）
→ 1リクエストが 180 秒でも返らずタイムアウト
→ `post_ollama` が `_pick_model_candidates()` の**全モデル総当たり**で次々に別モデルをロード
→ 失敗モデルが解放されないまま積み上がり RAM 枯渇 → OS ごとフリーズ

**解決策**:
1. **実行前に空き VRAM を確認する**。`nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader` で
   対象モデルのサイズ + コンテキスト分の空きがあることを確かめる。無ければ ComfyUI は
   `POST http://127.0.0.1:8188/free {"unload_models":true,"free_memory":true}` でプロセスを落とさずに VRAM だけ解放できる（可逆）
2. **実行中は `/api/ps` の `size` と `size_vram` が一致していることを確認する**。
   `size=13.9GB / size_vram=13.9GB` なら完全 GPU 常駐で健全。乖離していたら即中断してよい
3. 失敗モデルの即アンロード（`160f8a0e1` で修正済み）は連鎖を緩和するが万能ではない。
   `_unload_model()` の POST タイムアウトは 10 秒で、RAM スラッシング中はこのアンロード要求自体が失敗しうる（未対応）
4. ~~タイムアウト失敗は `_FAILED_MODELS` に登録されない（HTTP 400 系のみ）ため、同じモデルがリトライで再ロードされる点にも注意~~
   → `258e6a787` で修正済み（2026-08-09 11:07 自律発案）。例外パスでも`_FAILED_MODELS`に登録するようにした
5. **`OLLAMA_EXCLUDE_MODELS`環境変数**（`258e6a787`で新設）で、VRAMに収まらないと判明済みのモデル（例:
   PC2の`qwen3:30b-a3b`、17.3GB）を「全モデル総当たり」の自動収集候補から明示的に除外できる。ただし
   2026-08-09時点では`run_daily.bat`にはまだ設定していない（デフォルト動作は変化なし、有効化は次回の判断）

**実測（2026-08-09 再開テスト）**: 空き VRAM 15.3GB / 空き RAM 20.1GB の状態で手動実行したところ、
`size_vram=13.9GB` の完全 GPU 常駐を維持し、**タイムアウト 0 件・所要 32 分**（09:47:16→10:19:13）で完走した。
最小空き RAM は 17.29GB で、枯渇の兆候は皆無だった。所要時間そのものが健全性の指標として使える。

**適用条件**: VRAM 16GB のノード（PC1/PC2）で、他の GPU 常駐プロセスと同居しながら Ollama バッチを回す全ケース
