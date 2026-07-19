# download_and_summarize_podcast.py の使い方

## 1. ポッドキャスト URL からダウンロードして要約

```powershell
cd "C:\Users\m03k0\OneDrive\wsc\hexarys\ura"
.\.venv\Scripts\python.exe .\download_and_summarize_podcast.py "https://pocketcasts.com/podcast/..."
```

出力:
- `*.txt` : 文字起こし
- `*.summary.en.txt` : 英語要約
- `*.summary.ja.txt` : 日本語要約

## 2. 既存の transcript ファイルを直接要約する

```powershell
.\.venv\Scripts\python.exe .\download_and_summarize_podcast.py --skip-download media.txt
```

または出力先とファイル名を指定:

```powershell
.\.venv\Scripts\python.exe .\download_and_summarize_podcast.py --skip-download media.txt -o summary_output
```

出力:
- `summary_output.summary.en.txt`
- `summary_output.summary.ja.txt`

## 3. 生成する要約の長さを調整する

`--summary-sentences`:
- まとめに含める最大文数

`--summary-ratio`:
- 目安の文字数比率。例えば `0.25` は元テキストの約25% を目標

例:

```powershell
.\.venv\Scripts\python.exe .\download_and_summarize_podcast.py --skip-download media.txt --summary-sentences 4 --summary-ratio 0.3
```

## 4. 参考: コマンド一覧

- URL からダウンロードして要約
  - `python download_and_summarize_podcast.py "<URL>"`
- 既存 transcript を要約
  - `python download_and_summarize_podcast.py --skip-download <transcript_file>`
- 出力先を指定
  - `python download_and_summarize_podcast.py --skip-download <transcript_file> -o <output_base_or_dir>`
- Whisper モデル指定
  - `python download_and_summarize_podcast.py <URL> --model medium`
- 日本語音声を要約
  - `python download_and_summarize_podcast.py --skip-download media.txt --language ja`
