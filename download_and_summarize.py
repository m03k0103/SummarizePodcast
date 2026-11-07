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

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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


def get_podcast_output_dir(podcast_name: str | None = None) -> Path:
    """
    ポッドキャスト番組名に基づく保存先ディレクトリを返却（存在しない場合は自動作成）。
    既存のサブフォルダ（例: 'まぼろし会議', 'Howard Marks', 'Behind the Markets'）との柔軟な一致を優先。
    """
    if not podcast_name or not podcast_name.strip():
        folder = OUTPUT_DIR / "default"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    name = podcast_name.strip()
    safe_name = sanitize_filename(name)

    # 1. 完全一致する既存サブフォルダがあり、かつ中身がある場合は即座に再利用
    direct_match = OUTPUT_DIR / safe_name
    if direct_match.exists() and direct_match.is_dir() and any(direct_match.iterdir()):
        return direct_match

    # 2. 既存サブフォルダの部分一致探索（ファイルが存在するフォルダを優先）
    matching_dirs = []
    for d in OUTPUT_DIR.iterdir():
        if d.is_dir():
            d_name = d.name.lower()
            if d_name and (d_name in name.lower() or name.lower() in d_name):
                has_files = any(d.iterdir())
                matching_dirs.append((has_files, d))

    if matching_dirs:
        # ファイルが存在するフォルダを最優先、次いで名前の適合度
        matching_dirs.sort(key=lambda x: (not x[0], len(x[1].name)))
        return matching_dirs[0][1]

    folder = OUTPUT_DIR / safe_name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


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


def format_date_ymd(iso_str: str) -> str:
    """エピソードの公開日時から YYYYMMDD 文字列を抽出（取得不能時は空文字）"""
    if not iso_str:
        return ""
    try:
        import datetime
        dt = datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y%m%d")
    except Exception:
        import re
        m = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", iso_str)
        if m:
            return f"{m.group(1)}{m.group(2)}{m.group(3)}"
        return ""


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

    podcast_title = podcast_info.get("title", "")
    target_dir = get_podcast_output_dir(podcast_title)
    ep_published = latest_ep.get("published", "")
    ymd = format_date_ymd(ep_published)
    prefix = ymd if ymd else (ep_guid if ep_guid else "")
    safe_title = sanitize_filename(ep_title)
    output_base_name = f"{prefix}_{safe_title}" if prefix else safe_title

    audio_path = target_dir / f"{output_base_name}.mp3"
    # 旧直下ファイルの後方互換チェック
    legacy_audio = OUTPUT_DIR / f"{output_base_name}.mp3"
    if audio_path.exists() and audio_path.stat().st_size > 0:
        print(f"既存のローカル音声ファイルを使用します: {audio_path}")
    elif legacy_audio.exists() and legacy_audio.stat().st_size > 0:
        print(f"既存の旧ルート音声ファイルを使用します: {legacy_audio}")
        audio_path = legacy_audio
    else:
        print(f"音声ファイルをダウンロード中... ({ep_media_url})")
        req = urllib.request.Request(ep_media_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(audio_path, "wb") as out:
            shutil.copyfileobj(resp, out)
        print(f"ダウンロード完了: {audio_path}")

    # FFmpeg チェック
    ffmpeg_path = find_ffmpeg()
    if ffmpeg_path:
        os.environ["FFMPEG_BINARY"] = ffmpeg_path
        os.environ["PATH"] = str(Path(ffmpeg_path).parent) + os.pathsep + os.environ.get("PATH", "")

    # Whisper 文字起こし
    print(f"Whisper ({args.model}) で文字起こし実行中...")
    model = load_whisper_model(args.model)
    transcript_text, _ = transcribe_audio(model, audio_path, "auto")

    transcript_path = target_dir / f"{output_base_name}.txt"
    transcript_path.write_text(transcript_text, encoding="utf-8")
    print(f"文字起こしテキスト保存完了: {transcript_path}")

    # LLM 要約
    print("LLM要約生成中 (Gemini / OpenAI / Ollama)...")
    en_summary = generate_english_summary(transcript_text, "en")
    ja_summary = generate_japanese_summary(transcript_text, "en")

    en_path = target_dir / f"{output_base_name}.summary.en.md"
    ja_path = target_dir / f"{output_base_name}.summary.ja.md"

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
