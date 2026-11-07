import datetime
import gzip
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import streamlit as st

# Source フォルダからモジュールインポート
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
    download_file,
    fetch_episodes_from_podcast_url,
    extract_download_url,
    fetch_text,
)
from audio_chunker import (
    get_audio_duration,
    split_audio_into_chunks,
    extract_speaker_profile_and_tail,
    adjust_timestamps,
    build_chunk_prompt,
)

# 保存先・管理ファイル
OUTPUT_DIR = PROJECT_ROOT / "Output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


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

    # 3. なければ safe_name で新規作成
    folder = OUTPUT_DIR / safe_name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def find_output_file(output_base: str, suffix: str, podcast_name: str | None = None) -> Path | None:
    """
    指定エピソードのファイルを探索して Path を返却。
    1. 完全一致: {output_base}{suffix}
    2. タイトル後方一致: *_{output_base}{suffix} (プレフィックスの違いを吸収)
    """
    filename = f"{output_base}{suffix}"

    # 探索先ディレクトリ（指定ポッドキャストフォルダ、および Output ルート）
    dirs_to_check = []
    if podcast_name:
        dirs_to_check.append(get_podcast_output_dir(podcast_name))
    dirs_to_check.append(OUTPUT_DIR)

    # 1. 完全一致探索
    for d in dirs_to_check:
        p = d / filename
        if p.exists() and p.is_file():
            return p

    # 2. 全サブフォルダ再帰探索（完全一致）
    matched = [f for f in OUTPUT_DIR.rglob(filename) if f.is_file()]
    if matched:
        return matched[0]

    # 3. プレフィックスの違いを吸収するタイトル後方一致探索
    # 例: output_base が 'タイトル' の場合、'*_タイトル{suffix}' を探索
    for d in dirs_to_check:
        matched_wild = [f for f in d.glob(f"*_{filename}") if f.is_file()]
        if matched_wild:
            return matched_wild[0]

    matched_wild_all = [f for f in OUTPUT_DIR.rglob(f"*_{filename}") if f.is_file()]
    if matched_wild_all:
        return matched_wild_all[0]

    return None
PODCAST_LIST_FILE = PROJECT_ROOT / "saved_podcasts.json"
PROCESSED_GUIDS_FILE = PROJECT_ROOT / "processed_guids.json"
LEGACY_GUID_FILE = PROJECT_ROOT / "last_processed_guid.txt"

# ページ基本設定
st.set_page_config(
    page_title="Podcast AI Summarizer",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# スタイル
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #10B981 0%, #3B82F6 50%, #8B5CF6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 0.95rem;
        color: #6B7280;
        margin-bottom: 1.5rem;
    }
    .podcast-card {
        border-radius: 12px;
        padding: 1.25rem 1.5rem;
        margin-bottom: 1.25rem;
        border: 1px solid #E5E7EB;
        background-color: #F9FAFB;
    }
    @media (prefers-color-scheme: dark) {
        .podcast-card {
            border: 1px solid #374151;
            background-color: #1F2937;
        }
        .sub-header {
            color: #9CA3AF;
        }
    }
    .badge-new {
        background-color: #EF4444;
        color: white;
        padding: 0.25rem 0.75rem;
        border-radius: 9999px;
        font-size: 0.85rem;
        font-weight: 700;
        display: inline-block;
    }
    .badge-done {
        background-color: #10B981;
        color: white;
        padding: 0.25rem 0.75rem;
        border-radius: 9999px;
        font-size: 0.85rem;
        font-weight: 700;
        display: inline-block;
    }
</style>
""", unsafe_allow_html=True)


# --- ユーティリティ・管理関数 ---
def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", "_", name)
    return name.strip("_")


def format_duration(seconds: int | float | None) -> str:
    if not seconds:
        return "不明"
    total_sec = int(seconds)
    m, s = divmod(total_sec, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}時間{m:02d}分{s:02d}秒"
    return f"{m}分{s:02d}秒"


def format_date_ymd(iso_str: str) -> str:
    """エピソードの公開日時から YYYYMMDD 文字列を抽出（取得不能時は空文字）"""
    if not iso_str:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y%m%d")
    except Exception:
        import re
        m = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", iso_str)
        if m:
            return f"{m.group(1)}{m.group(2)}{m.group(3)}"
        return ""


def format_date(iso_str: str) -> str:
    if not iso_str:
        return "不明"
    try:
        dt = datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y年%m月%d日 %H:%M")
    except Exception:
        return iso_str


def load_saved_podcasts() -> list[dict]:
    """登録済みポッドキャスト一覧を取得"""
    podcasts = []
    if PODCAST_LIST_FILE.exists():
        try:
            podcasts = json.loads(PODCAST_LIST_FILE.read_text(encoding="utf-8"))
        except Exception:
            podcasts = []
    if not podcasts:
        # デフォルト候補
        podcasts = [
            {
                "name": "Behind the Markets Podcast",
                "url": "https://pocketcasts.com/podcast/behind-the-markets-podcast/13110350-be7f-0134-10a8-25324e2a541d",
                "default_is_monologue": False
            }
        ]
        save_saved_podcasts(podcasts)
    else:
        for p in podcasts:
            if "default_is_monologue" not in p:
                p["default_is_monologue"] = False
    return podcasts


def save_saved_podcasts(podcasts: list[dict]) -> None:
    """登録済みポッドキャスト一覧を保存"""
    PODCAST_LIST_FILE.write_text(json.dumps(podcasts, ensure_ascii=False, indent=2), encoding="utf-8")


def load_processed_guids() -> dict[str, str]:
    """番組別またはGUID別の処理済み記録を取得"""
    records = {}
    if PROCESSED_GUIDS_FILE.exists():
        try:
            records = json.loads(PROCESSED_GUIDS_FILE.read_text(encoding="utf-8"))
        except Exception:
            records = {}
    
    # 既存の legacy txt があれば統合
    if LEGACY_GUID_FILE.exists():
        legacy_val = LEGACY_GUID_FILE.read_text(encoding="utf-8").strip()
        if legacy_val and "legacy" not in records:
            records["legacy"] = legacy_val
    return records


def save_processed_guid(podcast_key: str, guid: str) -> None:
    """処理済みGUIDを保存"""
    records = load_processed_guids()
    records[podcast_key] = guid.strip()
    PROCESSED_GUIDS_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    # レガシーファイルも互換性のため更新
    LEGACY_GUID_FILE.write_text(guid.strip(), encoding="utf-8")


def extract_podcast_uuid(url_or_uuid: str) -> str | None:
    """URLまたは文字列から Pocket Casts の UUID を抽出"""
    m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", url_or_uuid, re.I)
    return m.group(0).lower() if m else None


def is_podcast_already_saved(url_or_uuid: str, podcasts: list[dict]) -> bool:
    """URLまたはUUIDがすでに登録済み番組リストに存在するか判定"""
    target_uuid = extract_podcast_uuid(url_or_uuid)
    clean_target = url_or_uuid.strip().lower()
    for p in podcasts:
        p_url = p.get("url", "").strip().lower()
        if p_url == clean_target:
            return True
        p_uuid = extract_podcast_uuid(p_url)
        if target_uuid and p_uuid and target_uuid == p_uuid:
            return True
    return False



@st.cache_data(ttl=180, show_spinner=False)
def fetch_feed_data(url_or_uuid: str) -> tuple[dict, list[dict]]:
    """Pocket Casts Cache API または URL から番組情報とエピソード一覧を取得"""
    uuid = extract_podcast_uuid(url_or_uuid)
    if uuid:
        cache_url = f"https://cache.pocketcasts.com/podcast/full/{uuid}"
        req = urllib.request.Request(
            cache_url,
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

    # UUID が見つからない場合は汎用パーサー
    episodes = fetch_episodes_from_podcast_url(url_or_uuid, count=30)
    podcast_info = {
        "title": "カスタムポッドキャスト",
        "author": "不明",
        "description": url_or_uuid,
        "uuid": "",
    }
    return podcast_info, episodes


def is_episode_processed(ep_guid: str, ep_title: str, podcast_key: str = "", podcast_name: str = "", ep_published: str = "") -> bool:
    """エピソードが処理済みかどうかを判定（YYYYMMDD または GUID プレフィックスのファイルの存在、または GUID 記録）"""
    safe_title = sanitize_filename(ep_title)

    # 1. YYYYMMDD 形式での探索
    ymd = format_date_ymd(ep_published)
    if ymd:
        output_base_ymd = f"{ymd}_{safe_title}"
        ja_file = find_output_file(output_base_ymd, ".summary.ja.txt", podcast_name)
        if ja_file and ja_file.exists():
            return True

    # 2. GUID 形式（旧形式）での探索
    if ep_guid:
        output_base_guid = f"{ep_guid}_{safe_title}"
        ja_file = find_output_file(output_base_guid, ".summary.ja.txt", podcast_name)
        if ja_file and ja_file.exists():
            return True

    # 3. プレフィックスなしでの探索
    ja_file = find_output_file(safe_title, ".summary.ja.txt", podcast_name)
    if ja_file and ja_file.exists():
        return True

    # 4. GUID チェック
    records = load_processed_guids()
    last_guid = records.get(podcast_key) or records.get("legacy", "")
    if ep_guid and ep_guid == last_guid:
        return True

    return False


def _transcribe_single_audio_file_with_gemini(
    audio_path: Path,
    api_key: str,
    model_name: str,
    prompt: str,
    caption_prefix: str = ""
) -> str:
    """単一の音声ファイルを Gemini File API 経由でアップロードし文字起こしを実行"""
    import json
    import time
    import urllib.request
    import urllib.error

    clean_key = api_key.strip()
    file_size = audio_path.stat().st_size
    display_name = audio_path.name

    prefix_str = f"[{caption_prefix}] " if caption_prefix else ""
    st.caption(f"{prefix_str}☁️ Gemini File API へ音声ファイル ({display_name}, {file_size / (1024*1024):.1f} MB) をアップロード中...")

    # 1. Resumable Upload の初期化
    init_url = "https://generativelanguage.googleapis.com/upload/v1beta/files"
    init_headers = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(file_size),
        "X-Goog-Upload-Header-Content-Type": "audio/mp3",
        "Content-Type": "application/json",
        "x-goog-api-key": clean_key
    }
    init_body = json.dumps({"file": {"display_name": display_name}}).encode("utf-8")
    init_req = urllib.request.Request(init_url, data=init_body, headers=init_headers, method="POST")

    try:
        with urllib.request.urlopen(init_req, timeout=60) as resp:
            upload_url = resp.headers.get("X-Goog-Upload-URL")
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        raise ValueError(f"Gemini 音声アップロード初期化エラー ({e.code}): {err_msg}")

    if not upload_url:
        raise ValueError("Gemini Upload URL の取得に失敗しました。")

    file_name = None
    file_uri = None

    try:
        # 2. 音声バイナリのアップロード
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        upload_headers = {
            "Content-Length": str(file_size),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize"
        }
        upload_req = urllib.request.Request(upload_url, data=audio_bytes, headers=upload_headers, method="POST")

        with urllib.request.urlopen(upload_req, timeout=300) as resp:
            file_info = json.loads(resp.read().decode("utf-8")).get("file", {})

        file_name = file_info.get("name")
        file_uri = file_info.get("uri")

        if not file_uri or not file_name:
            raise ValueError(f"アップロード後のファイル情報取得に失敗しました: {file_info}")

        # 3. サーバー側での音声ファイル前処理待ち
        with st.spinner(f"{prefix_str}Gemini クラウド側で音声ファイルを解析・前処理中..."):
            max_wait = 180
            elapsed = 0
            while file_info.get("state") == "PROCESSING" and elapsed < max_wait:
                time.sleep(3)
                elapsed += 3
                get_url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}"
                with urllib.request.urlopen(urllib.request.Request(get_url, headers={"x-goog-api-key": clean_key}), timeout=30) as resp:
                    file_info = json.loads(resp.read().decode("utf-8"))

        if file_info.get("state") == "FAILED":
            raise ValueError("Gemini サーバー側での音声ファイル処理に失敗しました。")

        # 4. generateContent による文字起こし＆話者分離 (フォールバック機構付き)
        clean_model = model_name
        if clean_model.startswith("models/"):
            clean_model = clean_model[len("models/"):]

        candidate_models = [clean_model]
        for fallback in ["gemini-3.6-flash", "gemini-3.1-flash-lite", "gemini-3.5-flash"]:
            if fallback not in candidate_models:
                candidate_models.append(fallback)

        req_body = {
            "contents": [
                {
                    "parts": [
                        {
                            "file_data": {
                                "mime_type": "audio/mp3",
                                "file_uri": file_uri
                            }
                        },
                        {
                            "text": prompt
                        }
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.2
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"}
            ]
        }

        last_error = None
        for try_model in candidate_models:
            st.caption(f"{prefix_str}🧠 Gemini ({try_model}) で音声認識と話者分離を実行中...")
            gen_url = f"https://generativelanguage.googleapis.com/v1beta/models/{try_model}:generateContent"
            gen_req = urllib.request.Request(
                gen_url,
                data=json.dumps(req_body).encode("utf-8"),
                headers={"Content-Type": "application/json", "x-goog-api-key": clean_key},
                method="POST"
            )

            max_attempts = 2
            for attempt in range(max_attempts):
                try:
                    with urllib.request.urlopen(gen_req, timeout=300) as resp:
                        gen_resp = json.loads(resp.read().decode("utf-8"))

                    candidates = gen_resp.get("candidates", [])
                    if candidates:
                        cand = candidates[0]
                        finish_reason = cand.get("finishReason", "UNKNOWN")
                        parts = cand.get("content", {}).get("parts", [])

                        text_chunks = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text") and not p.get("thought")]
                        if not text_chunks:
                            text_chunks = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]

                        full_transcript = "".join(text_chunks).strip()

                        # リピートループ（異常な繰り返し）の検出
                        has_loop = bool(re.search(r'(.{2,30}?)\1{8,}', full_transcript))
                        if full_transcript and not has_loop:
                            return full_transcript
                        elif has_loop:
                            last_error = f"{try_model}: 異常な繰り返し（ループ）を検出しました"
                        else:
                            last_error = f"{try_model}: 生成テキストが空でした (finishReason: {finish_reason})"
                    else:
                        prompt_fb = gen_resp.get("promptFeedback", {})
                        last_error = f"{try_model}: 候補が返されませんでした (promptFeedback: {prompt_fb})"

                    st.caption(f"⚠️ {last_error}。代替モデルへフォールバックします...")
                    break

                except urllib.error.HTTPError as e:
                    err_body = e.read().decode("utf-8", errors="ignore")
                    last_error = f"{try_model} (HTTP {e.code}): {err_body}"
                    if e.code in (429, 503) and attempt < max_attempts - 1:
                        st.caption(f"⚠️ {try_model} が混雑中 (HTTP {e.code})。3秒待機後に再試行します...")
                        time.sleep(3)
                        continue
                    else:
                        st.caption(f"⚠️ {last_error}。代替モデルへフォールバックします...")
                        break
                except Exception as e:
                    last_error = f"{try_model} 通信エラー: {e}"
                    st.caption(f"⚠️ {last_error}。代替モデルへフォールバックします...")
                    break

        raise ValueError(f"すべてのGeminiモデルで文字起こしに失敗しました。最新エラー: {last_error}")

    finally:
        # 5. クリーンアップ (Googleサーバー上の一時ファイル削除)
        if file_name:
            try:
                del_url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}"
                del_req = urllib.request.Request(del_url, headers={"x-goog-api-key": clean_key}, method="DELETE")
                with urllib.request.urlopen(del_req, timeout=30):
                    pass
            except Exception:
                pass


def transcribe_audio_with_gemini(
    audio_path: Path,
    api_key: str,
    model_name: str = "gemini-3.6-flash",
    podcast_title: str = "",
    chunk_minutes: int | None = None,
    is_monologue: bool = False
) -> str:
    """
    Gemini Files REST API を用いて音声を文字起こし。
    長尺音声（一人語り時は45分超、対談時は15分超）は自動でチャンク分割し、前パートの話者情報・会話コンテキストを引き継ぎながら順次文字起こし・結合する。
    """
    if chunk_minutes is None:
        chunk_minutes = 45 if is_monologue else 15

    ffmpeg_path = find_ffmpeg()
    duration_sec = 0.0
    if ffmpeg_path:
        try:
            duration_sec = get_audio_duration(audio_path, ffmpeg_path)
        except Exception:
            duration_sec = 0.0

    # 15分 (900秒) 以下、または ffmpeg 検出不能な場合は単一処理
    threshold_sec = chunk_minutes * 60
    if not ffmpeg_path or duration_sec <= threshold_sec:
        prompt = build_chunk_prompt(
            podcast_title=podcast_title,
            chunk_index=1,
            total_chunks=1,
            is_monologue=is_monologue
        )
        return _transcribe_single_audio_file_with_gemini(
            audio_path=audio_path,
            api_key=api_key,
            model_name=model_name,
            prompt=prompt
        )

    # 15分超の場合はチャンク分割処理
    chunks = split_audio_into_chunks(audio_path, ffmpeg_path, chunk_minutes=chunk_minutes)
    total_chunks = len(chunks)
    split_reason = "一人語りの文脈維持" if is_monologue else "話者分離の精度維持"
    st.info(f"⏱️ 長尺音声（{format_duration(duration_sec)}）を検出しました。{split_reason}のため {total_chunks} 個のパート（各 {chunk_minutes} 分）に自動分割して順次文字起こしを実行します。")

    transcripts = []
    accumulated_speakers = []
    last_tail = ""
    temp_dir_to_clean = None

    try:
        progress_bar = st.progress(0, text="音声チャンクの文字起こしを開始します...")
        for idx, (chunk_file, start_sec, dur_sec) in enumerate(chunks):
            if chunk_file != audio_path and chunk_file.parent.name.startswith("audio_chunks_"):
                temp_dir_to_clean = chunk_file.parent

            part_num = idx + 1
            start_str = format_duration(start_sec)
            end_str = format_duration(start_sec + dur_sec)
            caption_prefix = f"パート {part_num}/{total_chunks} ({start_str}〜{end_str})"
            progress_bar.progress(int((idx / total_chunks) * 100), text=f"🎙️ {caption_prefix} を処理中...")

            # コンテキスト引き継ぎプロンプトの構築
            prompt = build_chunk_prompt(
                podcast_title=podcast_title,
                chunk_index=part_num,
                total_chunks=total_chunks,
                previous_speakers=accumulated_speakers,
                previous_tail=last_tail,
                is_monologue=is_monologue
            )

            # チャンク文字起こし実行
            raw_chunk_text = _transcribe_single_audio_file_with_gemini(
                audio_path=chunk_file,
                api_key=api_key,
                model_name=model_name,
                prompt=prompt,
                caption_prefix=caption_prefix
            )

            # 相対タイムスタンプに開始秒数を加算
            adjusted_text = adjust_timestamps(raw_chunk_text, start_sec)
            transcripts.append(adjusted_text)

            # 次パートへの引き継ぎコンテキストを抽出
            found_spks, tail = extract_speaker_profile_and_tail(raw_chunk_text)
            for spk in found_spks:
                if spk not in accumulated_speakers:
                    accumulated_speakers.append(spk)
            last_tail = tail

            # チャンク一時ファイルの削除
            if chunk_file != audio_path and chunk_file.exists():
                try:
                    chunk_file.unlink()
                except Exception:
                    pass

        progress_bar.progress(100, text=f"✅ 全 {total_chunks} パートの文字起こしが完了しました！結合中...")

    finally:
        if temp_dir_to_clean and temp_dir_to_clean.exists():
            try:
                shutil.rmtree(temp_dir_to_clean, ignore_errors=True)
            except Exception:
                pass

    # シームレスに結合
    full_transcript = "\n\n".join(transcripts).strip()
    return full_transcript



def execute_episode_pipeline(
    ep_title: str,
    ep_guid: str,
    ep_media_url: str,
    model_size: str,
    llm_prov: str,
    podcast_key: str | None = None,
    is_latest: bool = False,
    asr_engine: str = "gemini",
    gemini_key: str | None = None,
    gemini_model: str = "gemini-3.6-flash",
    podcast_name: str = "",
    is_monologue: bool = False,
    ep_published: str = ""
):
    """1エピソードをダウンロード -> 文字起こし(Whisper or Gemini) -> 要約 -> 保存 -> GUID更新"""
    safe_title = sanitize_filename(ep_title)
    ymd = format_date_ymd(ep_published)
    prefix = ymd if ymd else (ep_guid if ep_guid else "")
    output_base = f"{prefix}_{safe_title}" if prefix else safe_title

    # ポッドキャストごとの保存先ディレクトリを取得
    target_dir = get_podcast_output_dir(podcast_name)

    txt_path = target_dir / f"{output_base}.txt"
    ja_path = target_dir / f"{output_base}.summary.ja.txt"
    en_path = target_dir / f"{output_base}.summary.en.txt"
    audio_path = target_dir / f"{output_base}.mp3"

    # 既存の音声ファイル探索（新YYYYMMDD優先、旧GUIDフォールバック）
    existing_audio = find_output_file(output_base, ".mp3", podcast_name)
    if not existing_audio and ep_guid:
        legacy_base = f"{ep_guid}_{safe_title}"
        existing_audio = find_output_file(legacy_base, ".mp3", podcast_name)

    if existing_audio and existing_audio.exists() and existing_audio.stat().st_size > 0:
        st.write(f"📁 **既存のローカル音声ファイルを使用します:** `{existing_audio.name}`")
        audio_path = existing_audio
    else:
        st.write(f"📥 **ステップ 1/3: 音声をダウンロード中... ({target_dir.name} フォルダに保存)**")
        st.caption(f"URL: `{ep_media_url}`")
        req = urllib.request.Request(ep_media_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(audio_path, "wb") as out:
            shutil.copyfileobj(resp, out)
        st.write(f"✅ 音声ファイル保存完了: `{audio_path.name}`")

    # 文字起こし実行
    if asr_engine == "gemini":
        if not gemini_key:
            raise ValueError("Gemini 文字起こしを利用するには Gemini API Key の設定が必要です。サイドバーで入力してください。")
        mode_label = "（一人語りモード）" if is_monologue else "＆話者分離"
        st.write(f"🎙️ **ステップ 2/3: Gemini ({gemini_model}) マルチモーダルで文字起こし{mode_label}を実行中...**")
        full_title = f"{podcast_name} - {ep_title}" if podcast_name else ep_title
        transcript_text = transcribe_audio_with_gemini(
            audio_path=audio_path,
            api_key=gemini_key,
            model_name=gemini_model,
            podcast_title=full_title,
            is_monologue=is_monologue
        )
    else:
        # FFmpeg 設定
        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path:
            os.environ["FFMPEG_BINARY"] = ffmpeg_path
            os.environ["PATH"] = str(Path(ffmpeg_path).parent) + os.pathsep + os.environ.get("PATH", "")

        # Whisper 文字起こし
        st.write(f"🎙️ **ステップ 2/3: Whisper ({model_size}) で文字起こし中...** (長尺音声の場合は数分かかります)")
        model = load_whisper_model(model_size)
        transcript_text, _ = transcribe_audio(model, audio_path, "auto")

    txt_path.write_text(transcript_text, encoding="utf-8")
    st.write(f"✅ 文字起こし完了: `{txt_path.name}` ({len(transcript_text):,} 文字)")

    # LLM 要約
    st.write(f"🧠 **ステップ 3/3: LLM ({llm_prov}) で日英要約を生成中...**")
    ja_sum = generate_japanese_summary(transcript_text, "en", llm_provider=llm_prov)
    en_sum = generate_english_summary(transcript_text, "en", llm_provider=llm_prov)

    ja_path.write_text(ja_sum or "(要約生成失敗)", encoding="utf-8")
    en_path.write_text(en_sum or "(要約生成失敗)", encoding="utf-8")
    st.write("✅ 日本語・英語の要約保存完了")

    # GUID を更新
    if ep_guid:
        save_key = podcast_key if podcast_key else "default"
        save_processed_guid(save_key, ep_guid)
        st.write(f"🔖 処理済み GUID を更新しました: `{ep_guid}`")

    return transcript_text, ja_sum, en_sum



# --- サイドバー設定 ---
st.sidebar.title("⚙️ 設定 & 環境")

st.sidebar.subheader("🤖 AI 要約設定")
llm_provider = st.sidebar.selectbox(
    "LLM プロバイダー",
    ["auto", "gemini", "openai", "ollama", "textrank"],
    index=0,
    help="auto: APIキーがあればGemini/OpenAI、無ければTextRankに自動フォールバック"
)

default_gemini_key = os.environ.get("GEMINI_API_KEY", "")
gemini_key_input = st.sidebar.text_input(
    "🔑 Gemini API Key",
    value=default_gemini_key,
    type="password",
    help="GEMINI_API_KEY 環境変数がセットされている場合は自動読込されます"
)
if gemini_key_input:
    os.environ["GEMINI_API_KEY"] = gemini_key_input

default_openai_key = os.environ.get("OPENAI_API_KEY", "")
openai_key_input = st.sidebar.text_input(
    "🔑 OpenAI API Key",
    value=default_openai_key,
    type="password",
    help="OPENAI_API_KEY 環境変数がセットされている場合は自動読込されます"
)
if openai_key_input:
    os.environ["OPENAI_API_KEY"] = openai_key_input

st.sidebar.subheader("🎙️ 文字起こし設定")
asr_engine = st.sidebar.selectbox(
    "文字起こしエンジン",
    [
        "Gemini (マルチモーダル / 話者分離対応)",
        "Whisper (ローカルモデル)"
    ],
    index=0,
    help="Gemini: 音声を直接Geminiに渡して話者分離付きで高速文字起こし / Whisper: PC上でローカル実行"
)

whisper_model = "base"
gemini_asr_model = "gemini-3.6-flash"
if asr_engine.startswith("Whisper"):
    whisper_model = st.sidebar.selectbox(
        "Whisper モデルサイズ",
        ["tiny", "base", "small", "medium", "large"],
        index=0,
        help="tiny: 最速・省メモリ / base: 標準 / small・medium: 高精度"
    )
else:
    model_choice = st.sidebar.selectbox(
        "Gemini モデル",
        [
            "gemini-3.6-flash (推奨・高精度・安定)",
            "gemini-3.1-flash-lite (高速・軽量・安定)",
            "gemini-3.5-flash",
            "gemini-3.8-flash (⚠️混雑のため503失敗多め)"
        ],
        index=0,
        help="音声認識＆話者分離に使用するGeminiモデル (3.6-flash推奨。3.8-flashはGoogle側混雑のため現在503エラーが多発します)"
    )
    gemini_asr_model = model_choice.split()[0]
    if not gemini_key_input:
        st.sidebar.warning("⚠️ Gemini API Key を設定してください")





st.sidebar.markdown("---")
st.sidebar.subheader("💡 システム状態")
ffmpeg_path = find_ffmpeg()
if ffmpeg_path:
    st.sidebar.success("✅ FFmpeg 検出済み")
else:
    st.sidebar.error("❌ FFmpeg 未検出 (音声変換に必要です)")

# --- メインエリア ---
st.markdown('<div class="main-header">🎙️ Podcast AI Summarizer</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">任意のポッドキャストを自動監視 ➔ ダウンロード ➔ Whisper 文字起こし ➔ AI 日英要約</div>', unsafe_allow_html=True)

# ポッドキャスト選択・入力バー
saved_podcasts = load_saved_podcasts()
st.markdown("### 📻 対象ポッドキャストの指定")

podcast_options = [p["name"] for p in saved_podcasts] + ["✏️ URLを直接指定"]
default_idx = 0
current_selected = st.session_state.get("selected_podcast_name")
if current_selected in podcast_options:
    default_idx = podcast_options.index(current_selected)

col_sel, col_del, col_custom = st.columns([5, 1, 6])

with col_sel:
    selected_option = st.selectbox("登録済み番組から選択", podcast_options, index=default_idx)
    st.session_state["selected_podcast_name"] = selected_option

with col_del:
    st.write("")
    st.write("")
    if selected_option != "✏️ URLを直接指定":
        if st.button("🗑️", key="del_selected_pod", help=f"「{selected_option}」を登録から削除"):
            saved_podcasts = [p for p in saved_podcasts if p["name"] != selected_option]
            save_saved_podcasts(saved_podcasts)
            st.session_state["selected_podcast_name"] = saved_podcasts[0]["name"] if saved_podcasts else "✏️ URLを直接指定"
            st.toast(f"🗑️ 「{selected_option}」の登録を解除しました", icon="ℹ️")
            st.rerun()

active_entry = next((p for p in saved_podcasts if p["name"] == selected_option), None)
current_podcast_is_monologue = active_entry.get("default_is_monologue", False) if active_entry else False

with col_custom:
    if selected_option == "✏️ URLを直接指定":
        active_url = st.text_input("Pocket Casts 番組 URL または UUID (入力すると自動保存されます)", placeholder="https://pocketcasts.com/podcast/...")
    else:
        active_url = active_entry["url"] if active_entry else ""
        selected_default_mode = st.radio(
            "⚙️ 番組の標準構成（デフォルト）",
            ["👥 複数人（対談）", "🎙️ 単独（一人語り）"],
            index=1 if current_podcast_is_monologue else 0,
            horizontal=True,
            key=f"default_speaker_mode_{selected_option}",
            help="この番組のエピソード要約時に初期選択される構成です。エピソード実行時に個別に切り替えることも可能です。"
        )
        new_default_mono = selected_default_mode.startswith("🎙️")
        if active_entry and new_default_mono != current_podcast_is_monologue:
            active_entry["default_is_monologue"] = new_default_mono
            save_saved_podcasts(saved_podcasts)
            st.toast(f"💾 「{selected_option}」の標準構成を【{'単独（一人語り）' if new_default_mono else '複数人（対談）'}】に更新しました", icon="✅")
            st.rerun()

# ポッドキャストデータ取得
podcast_info, episodes = {}, []
podcast_key = extract_podcast_uuid(active_url) or sanitize_filename(selected_option)

if active_url.strip():
    try:
        podcast_info, episodes = fetch_feed_data(active_url.strip())
    except Exception as e:
        st.error(f"ポッドキャスト情報の取得に失敗しました: {e}")

# URL直接指定時の自動登録処理
if selected_option == "✏️ URLを直接指定" and active_url.strip() and episodes:
    if not is_podcast_already_saved(active_url.strip(), saved_podcasts):
        p_title = podcast_info.get("title") or "新規ポッドキャスト"
        existing_names = [p["name"] for p in saved_podcasts]
        final_name = p_title
        counter = 2
        while final_name in existing_names:
            final_name = f"{p_title} ({counter})"
            counter += 1

        saved_podcasts.append({
            "name": final_name,
            "url": active_url.strip(),
            "default_is_monologue": False
        })
        save_saved_podcasts(saved_podcasts)
        st.session_state["selected_podcast_name"] = final_name
        st.toast(f"📌 「{final_name}」を登録済み番組として自動保存しました！", icon="✅")
        st.rerun()

# 番組メタデータの表示
if podcast_info and episodes:
    p_title = podcast_info.get("title") or selected_option
    p_author = podcast_info.get("author") or "不明"
    p_desc = podcast_info.get("description") or ""
    
    st.markdown(f"""
<div class="podcast-card">
    <div style="font-size: 1.25rem; font-weight: bold; margin-bottom: 0.3rem;">{p_title}</div>
    <div style="font-size: 0.88rem; color: #6B7280; margin-bottom: 0.5rem;">👤 <b>配信者:</b> {p_author} &nbsp;&nbsp;|&nbsp;&nbsp; 📦 <b>取得エピソード数:</b> {len(episodes)} 件</div>
    <div style="font-size: 0.85rem; color: #4B5563;">{p_desc[:250] + ('...' if len(p_desc) > 250 else '')}</div>
</div>
""", unsafe_allow_html=True)


# タブ構成
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📻 最新エピソード監視 & 要約",
    "📋 エピソード一覧から要約",
    "⚡ 複数エピソード一括要約",
    "📄 テキストファイル要約",
    "📚 要約アーカイブ閲覧"
])


# ==========================================
# タブ 1: 📻 最新エピソード監視 & 要約
# ==========================================
with tab1:
    st.subheader("最新エピソード監視ステータス")

    col_btn, col_blank = st.columns([1, 4])
    with col_btn:
        if st.button("🔄 最新フィード再取得", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    if not episodes:
        st.info("ポッドキャストのURLを指定すると、最新エピソードの監視ステータスが表示されます。")
    else:
        latest_ep = episodes[0]
        ep_guid = latest_ep.get("uuid") or latest_ep.get("id") or latest_ep.get("url") or ""
        ep_title = latest_ep.get("title") or "Untitled Episode"
        ep_media_url = latest_ep.get("url") or latest_ep.get("media_url") or ""
        ep_published = latest_ep.get("published") or ""
        ep_duration = latest_ep.get("duration")

        is_processed = is_episode_processed(ep_guid, ep_title, podcast_key, p_title, ep_published)

        status_badge = '<span class="badge-done">✅ 処理済み (最新状態)</span>' if is_processed else '<span class="badge-new">🆕 新着エピソードあり（未処理）</span>'
        
        st.markdown(f"""
<div class="podcast-card">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.8rem;">
        <span style="font-size: 1.15rem; font-weight: bold;">{ep_title}</span>
        {status_badge}
    </div>
    <div style="font-size: 0.9rem; color: #6B7280; line-height: 1.6;">
        📅 <b>公開日時:</b> {format_date(ep_published)} &nbsp;&nbsp;|&nbsp;&nbsp; 
        ⏱️ <b>再生時間:</b> {format_duration(ep_duration)} &nbsp;&nbsp;|&nbsp;&nbsp; 
        🆔 <b>エピソード GUID:</b> <code>{ep_guid if ep_guid else '自動生成'}</code>
    </div>
</div>
""", unsafe_allow_html=True)

        if ep_media_url:
            with st.expander("🎧 音声をブラウザでプレビュー再生", expanded=False):
                st.audio(ep_media_url)

        tab1_dialogue_mode = st.radio(
            "出演者構成（話者分離設定）",
            ["👥 対談・複数人（話者分離あり）", "🎙️ 一人語り・単独（話者分離なし）"],
            index=1 if current_podcast_is_monologue else 0,
            horizontal=True,
            key=f"tab1_dialogue_mode_{podcast_key}",
            help="「対談・複数人」では話者ごとにラベルや名前を識別します。「一人語り」では話者ラベルを付与せず自然な文章として書き起こします。このエピソード固有の構成に合わせて個別に切り替え可能です。"
        )
        tab1_is_monologue = tab1_dialogue_mode.startswith("🎙️")

        col_exec1, col_exec2 = st.columns([2, 2])
        with col_exec1:
            force_run = st.checkbox("差分チェックを無視して強制的に再処理", value=False, key="force_run_tab1")
        with col_exec2:
            can_run = not is_processed or force_run
            btn_label = "🚀 最新エピソードを要約実行" if can_run else "✅ 最新エピソードは処理済みです（強制再処理で実行可能）"
            run_btn = st.button(btn_label, type="primary" if can_run else "secondary", disabled=not can_run, use_container_width=True)

        if run_btn:
            if not ep_media_url:
                st.error("エピソードの音声 URL が取得できませんでした。")
            else:
                with st.status("エピソードのダウンロード・要約処理を実行中...", expanded=True) as status:
                    try:
                        transcript, ja_sum, en_sum = execute_episode_pipeline(
                            ep_title=ep_title,
                            ep_guid=ep_guid,
                            ep_media_url=ep_media_url,
                            model_size=whisper_model,
                            llm_prov=llm_provider,
                            podcast_key=podcast_key,
                            is_latest=True,
                            asr_engine="gemini" if "Gemini" in asr_engine else "whisper",
                            gemini_key=gemini_key_input,
                            gemini_model=gemini_asr_model,
                            podcast_name=p_title,
                            is_monologue=tab1_is_monologue,
                            ep_published=ep_published
                        )

                        status.update(label="🎉 要約処理がすべて完了しました！", state="complete")
                        st.balloons()

                        st.session_state["last_result"] = {
                            "title": ep_title,
                            "ja": ja_sum,
                            "en": en_sum,
                            "transcript": transcript,
                            "base_name": f"{format_date_ymd(ep_published) or ep_guid}_{sanitize_filename(ep_title)}" if (ep_published or ep_guid) else sanitize_filename(ep_title),
                            "podcast_name": p_title
                        }
                    except Exception as e:
                        status.update(label=f"❌ エラーが発生しました: {e}", state="error")
                        st.exception(e)

        # 直前または最新の要約結果表示
        result_data = st.session_state.get("last_result")
        if not result_data and is_processed:
            safe_title = sanitize_filename(ep_title)
            ymd = format_date_ymd(ep_published)
            output_base = f"{ymd}_{safe_title}" if ymd else f"{ep_guid}_{safe_title}"
            ja_file = find_output_file(output_base, ".summary.ja.txt", p_title)
            if not ja_file and ep_guid:
                output_base = f"{ep_guid}_{safe_title}"
                ja_file = find_output_file(output_base, ".summary.ja.txt", p_title)
            en_file = find_output_file(output_base, ".summary.en.txt", p_title)
            txt_file = find_output_file(output_base, ".txt", p_title)
            if ja_file and ja_file.exists():
                result_data = {
                    "title": ep_title,
                    "ja": ja_file.read_text(encoding="utf-8"),
                    "en": en_file.read_text(encoding="utf-8", errors="ignore") if en_file and en_file.exists() else "",
                    "transcript": txt_file.read_text(encoding="utf-8", errors="ignore") if txt_file and txt_file.exists() else "",
                    "base_name": output_base,
                    "podcast_name": p_title
                }

        if result_data:
            st.markdown("---")
            st.subheader(f"📄 要約・文字起こし結果: {result_data['title']}")
            
            res_tab1, res_tab2, res_tab3, res_tab4 = st.tabs(["🇯🇵 日本語要約", "🇬🇧 英語要約", "📝 全文文字起こし", "🎧 音声 (MP3)"])
            with res_tab1:
                st.download_button(
                    "📥 日本語要約 (.txt) をダウンロード",
                    data=result_data["ja"],
                    file_name=f"{result_data['base_name']}.summary.ja.txt",
                    mime="text/plain"
                )
                st.markdown(result_data["ja"])

            with res_tab2:
                st.download_button(
                    "📥 英語要約 (.txt) をダウンロード",
                    data=result_data["en"],
                    file_name=f"{result_data['base_name']}.summary.en.txt",
                    mime="text/plain"
                )
                st.markdown(result_data["en"])

            with res_tab3:
                st.download_button(
                    "📥 文字起こしテキスト (.txt) をダウンロード",
                    data=result_data["transcript"],
                    file_name=f"{result_data['base_name']}.txt",
                    mime="text/plain"
                )
                st.text_area("文字起こし本文", value=result_data["transcript"], height=400)

            with res_tab4:
                p_name_for_audio = result_data.get("podcast_name", p_title)
                saved_audio = find_output_file(result_data["base_name"], ".mp3", p_name_for_audio)
                if saved_audio and saved_audio.exists():
                    st.audio(str(saved_audio))
                    with open(saved_audio, "rb") as af:
                        st.download_button(
                            "📥 音声ファイル (.mp3) をダウンロード",
                            data=af.read(),
                            file_name=saved_audio.name,
                            mime="audio/mpeg"
                        )
                else:
                    st.caption("ローカル音声ファイルはありません")



# ==========================================
# タブ 2: 📋 エピソード一覧から要約
# ==========================================
with tab2:
    st.subheader("エピソード一覧から選択して要約")
    st.caption("選択中ポッドキャストの過去エピソードから任意の回を選んで、個別にダウンロード・文字起こし・要約を実行できます。")

    if not episodes:
        st.info("ポッドキャストを指定してください。")
    else:
        ep_options = []
        for i, ep in enumerate(episodes[:50]):
            title = ep.get("title", "Untitled")
            pub = format_date(ep.get("published", ""))
            ep_options.append(f"{i+1}. {title} ({pub})")

        selected_idx = st.selectbox("処理対象のエピソードを選択", range(len(ep_options)), format_func=lambda x: ep_options[x])
        target_ep = episodes[selected_idx]
        t_guid = target_ep.get("uuid") or target_ep.get("id") or ""
        t_title = target_ep.get("title") or "Untitled"
        t_url = target_ep.get("url") or target_ep.get("media_url") or ""
        t_pub = target_ep.get("published") or ""
        t_dur = target_ep.get("duration")

        has_existing = is_episode_processed(t_guid, t_title, podcast_key, p_title, t_pub)

        col_t1, col_t2 = st.columns([3, 1])
        with col_t1:
            st.markdown(f"**エピソード名:** {t_title}")
            st.caption(f"公開日: {format_date(t_pub)} | 再生時間: {format_duration(t_dur)} | GUID: `{t_guid}`")
        with col_t2:
            if has_existing:
                st.success("✅ 処理済みデータあり")
            else:
                st.warning("⏳ 未処理")

        if t_url:
            st.audio(t_url)

        tab2_dialogue_mode = st.radio(
            "出演者構成（話者分離設定）",
            ["👥 対談・複数人（話者分離あり）", "🎙️ 一人語り・単独（話者分離なし）"],
            index=1 if current_podcast_is_monologue else 0,
            horizontal=True,
            key=f"tab2_dialogue_mode_{podcast_key}_{selected_idx}",
            help="「対談・複数人」では話者ごとにラベルや名前を識別します。「一人語り」では話者ラベルを付与せず自然な文章として書き起こします。このエピソード固有の構成に合わせて個別に切り替え可能です。"
        )
        tab2_is_monologue = tab2_dialogue_mode.startswith("🎙️")

        if st.button(f"🚀 このエピソード ({t_title[:25]}...) を要約実行", key=f"run_ep_{selected_idx}", type="primary"):
            with st.status("エピソード処理を実行中...", expanded=True) as status:
                try:
                    transcript, ja_sum, en_sum = execute_episode_pipeline(
                        ep_title=t_title,
                        ep_guid=t_guid,
                        ep_media_url=t_url,
                        model_size=whisper_model,
                        llm_prov=llm_provider,
                        podcast_key=podcast_key,
                        is_latest=False,
                        asr_engine="gemini" if "Gemini" in asr_engine else "whisper",
                        gemini_key=gemini_key_input,
                        gemini_model=gemini_asr_model,
                        podcast_name=p_title,
                        is_monologue=tab2_is_monologue,
                        ep_published=t_pub
                    )
                    status.update(label="🎉 処理完了！", state="complete")
                    st.balloons()
                    
                    st.markdown("### 🇯🇵 日本語要約")
                    st.markdown(ja_sum)
                    st.markdown("### 🇬🇧 英語要約")
                    st.markdown(en_sum)
                except Exception as e:
                    status.update(label=f"❌ エラー: {e}", state="error")
                    st.exception(e)


# ==========================================
# タブ 3: ⚡ 複数エピソード一括要約
# ==========================================
with tab3:
    st.subheader("複数エピソードの一括（バッチ）ダウンロード＆要約")
    col_batch_cnt, col_batch_skip = st.columns([2, 2])
    with col_batch_cnt:
        batch_count = st.number_input("処理する最新エピソード件数", min_value=1, max_value=30, value=3, step=1)
    with col_batch_skip:
        skip_already_processed = st.checkbox("処理済みのエピソードはスキップする", value=True)

    tab3_dialogue_mode = st.radio(
        "一括処理の出演者構成（話者分離設定）",
        ["👥 対談・複数人（話者分離あり）", "🎙️ 一人語り・単独（話者分離なし）"],
        index=1 if current_podcast_is_monologue else 0,
        horizontal=True,
        key=f"tab3_dialogue_mode_{podcast_key}",
        help="一括処理するエピソード群の出演者構成を指定します（デフォルト: この番組の標準設定）。"
    )
    tab3_is_monologue = tab3_dialogue_mode.startswith("🎙️")

    if st.button("⚡ 一括要約を開始", type="primary", key="batch_btn"):
        if not episodes:
            st.warning("処理対象のエピソードがありません。ポッドキャストURLを確認してください。")
        else:
            targets = episodes[:batch_count]
            with st.status(f"最新 {len(targets)} 件のバッチ要約を実行中...", expanded=True) as status:
                for idx, ep in enumerate(targets, start=1):
                    ep_t = ep.get("title") or "Untitled"
                    ep_u = ep.get("url") or ep.get("media_url") or ""
                    ep_g = ep.get("uuid") or ep.get("id") or ""
                    ep_pub = ep.get("published") or ""
                    
                    st.markdown(f"--- **[{idx}/{len(targets)}] {ep_t}** ---")
                    if not ep_u:
                        st.caption("音声 URL が取得できませんでした。スキップします。")
                        continue

                    if skip_already_processed and is_episode_processed(ep_g, ep_t, podcast_key, p_title, ep_pub):
                        st.caption("処理済みのためスキップしました。")
                        continue

                    try:
                        execute_episode_pipeline(
                            ep_title=ep_t,
                            ep_guid=ep_g,
                            ep_media_url=ep_u,
                            model_size=whisper_model,
                            llm_prov=llm_provider,
                            podcast_key=podcast_key,
                            is_latest=(idx == 1),
                            asr_engine="gemini" if "Gemini" in asr_engine else "whisper",
                            gemini_key=gemini_key_input,
                            gemini_model=gemini_asr_model,
                            podcast_name=p_title,
                            is_monologue=tab3_is_monologue,
                            ep_published=ep_pub
                        )
                    except Exception as e:
                        st.error(f"エラーが発生しました: {e}")
                
                status.update(label="🎉 バッチ処理が完了しました！", state="complete")
                st.balloons()


# ==========================================
# タブ 4: 📄 テキストファイル要約
# ==========================================
with tab4:
    st.subheader("既存の文字起こしテキストから要約")
    uploaded_file = st.file_uploader("文字起こしテキストファイル (.txt) をアップロード", type=["txt"])
    text_direct = st.text_area("またはテキストを直接貼り付け", height=200)

    if st.button("📝 テキストを要約", key="txt_btn", type="primary"):
        target_text = ""
        base_name = "custom_transcript"
        if uploaded_file:
            target_text = uploaded_file.read().decode("utf-8", errors="ignore")
            base_name = Path(uploaded_file.name).stem
        elif text_direct.strip():
            target_text = text_direct.strip()

        if not target_text:
            st.warning("テキストファイルまたはテキスト本文を入力してください。")
        else:
            with st.spinner("要約を生成中..."):
                ja_sum = generate_japanese_summary(target_text, "en", llm_provider=llm_provider)
                en_sum = generate_english_summary(target_text, "en", llm_provider=llm_provider)

                text_summary_dir = get_podcast_output_dir("テキスト要約")
                (text_summary_dir / f"{base_name}.summary.ja.txt").write_text(ja_sum, encoding="utf-8")
                (text_summary_dir / f"{base_name}.summary.en.txt").write_text(en_sum, encoding="utf-8")

                st.success("✅ 要約が生成されました！")
                col_ja, col_en = st.columns(2)
                with col_ja:
                    st.markdown("### 🇯🇵 日本語要約")
                    st.markdown(ja_sum)
                with col_en:
                    st.markdown("### 🇬🇧 英語要約")
                    st.markdown(en_sum)


# ==========================================
# タブ 5: 📚 要約アーカイブ閲覧
# ==========================================
with tab5:
    st.subheader("📚 保存済み要約・文字起こし・音声アーカイブ")
    all_files = [f for f in OUTPUT_DIR.rglob("*") if f.is_file()]

    if not all_files:
        st.info("まだ Output フォルダに出力ファイルがありません。")
    else:
        grouped_records = {}
        for f in all_files:
            name = f.name
            rel_dir = f.parent.relative_to(OUTPUT_DIR)
            category = str(rel_dir).replace("\\", "/") if str(rel_dir) != "." else "（未分類）"

            if name.endswith(".summary.ja.txt"):
                base = name[:-len(".summary.ja.txt")]
                grouped_records.setdefault((category, base), {})["ja"] = f
            elif name.endswith(".summary.en.txt"):
                base = name[:-len(".summary.en.txt")]
                grouped_records.setdefault((category, base), {})["en"] = f
            elif name.endswith(".txt"):
                base = name[:-len(".txt")]
                grouped_records.setdefault((category, base), {})["txt"] = f
            elif name.endswith(".mp3"):
                base = name[:-len(".mp3")]
                grouped_records.setdefault((category, base), {})["mp3"] = f

        # ポッドキャスト番組一覧で絞り込み
        all_categories = sorted(list({k[0] for k in grouped_records.keys()}))
        filter_options = ["すべて表示"] + all_categories
        col_cat_filter, col_srch = st.columns([1, 2])
        with col_cat_filter:
            selected_cat = st.selectbox("🎙️ ポッドキャストで絞り込み", filter_options, index=0)

        # 候補一覧の生成
        items = []
        for (cat, base), rec in grouped_records.items():
            if selected_cat != "すべて表示" and cat != selected_cat:
                continue
            display_label = f"[{cat}] {base}" if selected_cat == "すべて表示" else base
            items.append((display_label, cat, base, rec))

        items.sort(key=lambda x: x[0], reverse=True)

        with col_srch:
            search_query = st.text_input("🔍 アーカイブを検索（タイトルやGUID、キーワード）", "")
        if search_query.strip():
            items = [it for it in items if search_query.lower() in it[0].lower()]

        if not items:
            st.info("検索条件に一致するアーカイブが見つかりませんでした。")
        else:
            choice_labels = [it[0] for it in items]
            selected_choice = st.selectbox("表示するアーカイブを選択", choice_labels)
            matched_item = next((it for it in items if it[0] == selected_choice), None)

            if matched_item:
                display_label, cat, selected_base, rec = matched_item
                st.markdown(f"#### 📂 {selected_base}  `({cat})`")

                arc_tab1, arc_tab2, arc_tab3, arc_tab4 = st.tabs(["🇯🇵 日本語要約", "🇬🇧 英語要約", "📝 文字起こし全文", "🎧 音声 (MP3)"])
                with arc_tab1:
                    if "ja" in rec:
                        ja_c = rec["ja"].read_text(encoding="utf-8", errors="ignore")
                        st.download_button("📥 日本語要約をダウンロード", ja_c, file_name=rec["ja"].name, mime="text/plain")
                        st.markdown(ja_c)
                    else:
                        st.caption("日本語要約ファイルがありません")

                with arc_tab2:
                    if "en" in rec:
                        en_c = rec["en"].read_text(encoding="utf-8", errors="ignore")
                        st.download_button("📥 英語要約をダウンロード", en_c, file_name=rec["en"].name, mime="text/plain")
                        st.markdown(en_c)
                    else:
                        st.caption("英語要約ファイルがありません")

                with arc_tab3:
                    if "txt" in rec:
                        txt_c = rec["txt"].read_text(encoding="utf-8", errors="ignore")
                        st.download_button("📥 文字起こしテキストをダウンロード", txt_c, file_name=rec["txt"].name, mime="text/plain")
                        st.text_area("全文テキスト", txt_c, height=450)
                    else:
                        st.caption("文字起こしファイルがありません")

                with arc_tab4:
                    if "mp3" in rec:
                        mp3_f = rec["mp3"]
                        st.audio(str(mp3_f))
                        with open(mp3_f, "rb") as af:
                            st.download_button("📥 音声ファイル (.mp3) をダウンロード", data=af.read(), file_name=mp3_f.name, mime="audio/mpeg")
                    else:
                        st.caption("ローカル音声ファイルがありません")

