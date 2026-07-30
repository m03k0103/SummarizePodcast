# download_and_summarize_podcast.py の使い方

## 1. ポッドキャスト URL からダウンロードして要約

```powershell
python Source/download_and_summarize_podcast.py "https://pocketcasts.com/podcast/..."
```

出力:
- `*.txt` : 文字起こし
- `*.summary.en.txt` : 英語要約
- `*.summary.ja.txt` : 日本語要約

---

## 2. LLM（Gemini / OpenAI / Ollama）を用いた高品質要約

LLMを使用して高品質な要約を生成する場合、APIキーを環境変数に設定するか、コマンド引数で直接渡します。

### (A) Gemini API を使用する場合（推奨）

環境変数 `GEMINI_API_KEY` が設定されていれば、自動的に Gemini API (`gemini-2.5-flash`) が優先使用されます。

```powershell
# 環境変数を設定する場合
$env:GEMINI_API_KEY="your-gemini-api-key"
python Source/download_and_summarize_podcast.py --skip-download media.txt

# 引数で直接指定する場合
python Source/download_and_summarize_podcast.py --skip-download media.txt --llm-provider gemini --gemini-api-key "your-gemini-api-key"
```

### (B) OpenAI API を使用する場合

```powershell
# 環境変数を設定する場合
$env:OPENAI_API_KEY="sk-proj-..."
python Source/download_and_summarize_podcast.py --skip-download media.txt

# 引数で指定・モデル変更する場合
python Source/download_and_summarize_podcast.py --skip-download media.txt --llm-provider openai --llm-model gpt-4o-mini --openai-api-key "sk-proj-..."
```

### (C) Ollama（ローカルLLM）を使用する場合

```powershell
python Source/download_and_summarize_podcast.py --skip-download media.txt --llm-provider ollama --llm-model llama3
```

---

## 3. 既存の transcript ファイルを直接要約する

```powershell
python Source/download_and_summarize_podcast.py --skip-download media.txt
```

出力先とファイル名を指定:

```powershell
python Source/download_and_summarize_podcast.py --skip-download media.txt -o summary_output
```

出力:
- `Output/summary_output.summary.en.txt`
- `Output/summary_output.summary.ja.txt`

---

## 4. 参考: コマンド・オプション一覧

- **LLMプロバイダー指定**:
  - `--llm-provider auto`: キーが設定されていればGemini/OpenAI、無ければTextRankに自動フォールバック (デフォルト)
  - `--llm-provider gemini`: Gemini API を強制指定
  - `--llm-provider openai`: OpenAI API を強制指定
  - `--llm-provider ollama`: Ollama (ローカルLLM) を指定
  - `--llm-provider textrank`: 従来のルールベース要約を強制指定

- **APIキー・URLオプション**:
  - `--gemini-api-key <KEY>`
  - `--openai-api-key <KEY>`
  - `--llm-model <MODEL_NAME>` (例: `gemini-2.5-flash`, `gpt-4o-mini`, `llama3`)

- **Whisper モデル指定**:
  - `python Source/download_and_summarize_podcast.py <URL> --model medium`
