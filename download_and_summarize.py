#!/usr/bin/env python3
"""
Behind the Markets Podcast 自動要約・差分監視スクリプト
Pocket Casts API を用いて新着エピソードを監視し、
未処理のエピソードがある場合にダウンロード -> Whisper文字起こし -> Gemini API要約を実行します。
"""

import argparse
import gzip
import json
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

# Source フォルダから既存のコア機能をインポート
PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_DIR = PROJECT_ROOT / "Source"
if str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from download_and_summarize_podcast import (
    find_ffmpeg,
    generate_english_summary,
    generate_japanese_summary,
    load_whisper_model,
    transcribe_audio,
)

PODCAST_PAGE_URL = "https://pocketcasts.com/podcast/behind-the-markets-podcast/13110350-be7f-0134-10a8-25324e2a541d"
PODCAST_UUID = "13110350-be7f-0134-10a8-25324e2a541d"
CACHE_API_URL = f"https://cache.pocketcasts.com/podcast/full/{PODCAST_UUID}"
LAST_GUID_FILE = PROJECT_ROOT / "last_processed_guid.txt"
OUTPUT_DIR = PROJECT_ROOT / "Output"


def fetch_podcast_feed_info() -> tuple[dict, list[dict]]:
    """Pocket Casts Cache API からポッドキャスト情報とエピソード一覧を取得"""
    req = urllib.request.Request(
        CACHE_API_URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        content = resp.read()

    try:
        data_str = gzip.decompress(content).decode("utf-8")
    except Exception:
        data_str = content.decode("utf-8", errors="ignore")

    data = json.loads(data_str)
    podcast_info = data.get("podcast", {})
    episodes = podcast_info.get("episodes", [])
    return podcast_info, episodes


def get_last_processed_guid() -> str:
    if LAST_GUID_FILE.exists():
        return LAST_GUID_FILE.read_text(encoding="utf-8").strip()
    return ""


def save_last_processed_guid(guid: str) -> None:
    LAST_GUID_FILE.write_text(guid.strip(), encoding="utf-8")


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", "_", name)
    return name.strip("_")


def main() -> int:
    parser = argparse.ArgumentParser(description="Behind the Markets Podcast 新着自動要約スクリプト")
    parser.add_argument("--force", action="store_true", help="差分チェックをスキップして強制的に最新エピソードを再処理")
    parser.add_argument("--model", default="tiny", help="Whisper モデルサイズ (tiny, base, small, medium, large)")
    args = parser.parse_args()

    print("=== Behind the Markets Podcast 新着チェック開始 ===")
    try:
        podcast_info, episodes = fetch_podcast_feed_info()
    except Exception as e:
        print(f"ERROR: ポッドキャスト情報の取得に失敗しました: {e}", file=sys.stderr)
        return 1

    if not episodes:
        print("エピソードが見つかりませんでした。")
        return 0

    latest_ep = episodes[0]
    ep_guid = latest_ep.get("uuid") or latest_ep.get("id") or latest_ep.get("url") or ""
    ep_title = latest_ep.get("title") or "Untitled Episode"
    ep_media_url = latest_ep.get("url") or ""

    print(f"最新エピソード: '{ep_title}'")
    print(f"GUID: {ep_guid}")

    last_guid = get_last_processed_guid()
    if not args.force and ep_guid and ep_guid == last_guid:
        print(f"処理済み GUID ('{last_guid}') と一致しました。新着エピソードはありません。スキップします。")
        return 0

    if not ep_media_url:
        print("ERROR: エピソードの MP3 ダウンロード URL が取得できませんでした。", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_title = sanitize_filename(ep_title)
    output_base_name = f"{ep_guid}_{safe_title}" if ep_guid else safe_title

    # 一時音声ファイルへのダウンロードと要約
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_audio_path = Path(temp_dir) / "temp_audio.mp3"
        print(f"音声ファイルを一時ダウンロード中... ({ep_media_url})")

        req = urllib.request.Request(ep_media_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(temp_audio_path, "wb") as out:
            shutil.copyfileobj(resp, out)
        print("ダウンロード完了。")

        # FFmpeg チェック
        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path:
            os.environ["FFMPEG_BINARY"] = ffmpeg_path
            os.environ["PATH"] = str(Path(ffmpeg_path).parent) + os.pathsep + os.environ.get("PATH", "")

        # Whisper 文字起こし
        print(f"Whisper ({args.model}) で文字起こし実行中...")
        model = load_whisper_model(args.model)
        transcript_text, _ = transcribe_audio(model, temp_audio_path, "auto")

        transcript_path = OUTPUT_DIR / f"{output_base_name}.txt"
        transcript_path.write_text(transcript_text, encoding="utf-8")
        print(f"文字起こしテキスト保存完了: {transcript_path}")

        # LLM 要約
        print("LLM要約生成中 (Gemini / OpenAI / Ollama)...")
        en_summary = generate_english_summary(transcript_text, "en")
        ja_summary = generate_japanese_summary(transcript_text, "en")

        en_path = OUTPUT_DIR / f"{output_base_name}.summary.en.txt"
        ja_path = OUTPUT_DIR / f"{output_base_name}.summary.ja.txt"

        en_path.write_text(en_summary or "(要約生成失敗)", encoding="utf-8")
        ja_path.write_text(ja_summary or "(要約生成失敗)", encoding="utf-8")

        print(f"Saved English summary to: {en_path}")
        print(f"Saved Japanese summary to: {ja_path}")

    # 一時ファイル削除後に GUID を保存
    if ep_guid:
        save_last_processed_guid(ep_guid)
        print(f"処理済み GUID を更新しました: {ep_guid}")

    print("=== 全処理が正常終了しました ===")
    return 0


if __name__ == "__main__":
    import shutil
    sys.exit(main())
