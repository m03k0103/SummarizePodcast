import os
import sys
import tempfile
import urllib.request
import gzip
import json
import re
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
    fetch_text,
    extract_download_url,
    choose_output_name,
    download_file,
    resolve_output_base,
)

OUTPUT_DIR = PROJECT_ROOT / "Output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ページ基本設定とカスタムCSS (Glassmorphism & Modern Dark Theme)
st.set_page_config(
    page_title="SummarizePodcast - ポッドキャスト自動要約",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        background: linear-gradient(135deg, #6EE7B7 0%, #3B82F6 50%, #9333EA 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-size: 1.0rem;
        color: #9CA3AF;
        margin-bottom: 2rem;
    }
    .stCard {
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 12px;
        padding: 1.5rem;
        backdrop-filter: blur(10px);
        margin-bottom: 1rem;
    }
    .success-box {
        background: rgba(16, 185, 129, 0.1);
        border: 1px solid rgba(16, 185, 129, 0.3);
        border-radius: 8px;
        padding: 1rem;
        color: #34D399;
    }
</style>
""", unsafe_allow_html=True)


# --- サイドバー設定 ---
st.sidebar.title("⚙️ 設定 & API環境")

llm_provider = st.sidebar.selectbox(
    "🤖 LLMプロバイダー",
    ["auto", "gemini", "openai", "ollama", "textrank"],
    index=0,
    help="auto: APIキーがあればGemini/OpenAI、無ければTextRankにフォールバック"
)

# APIキー設定
default_gemini_key = os.environ.get("GEMINI_API_KEY", "")
gemini_key_input = st.sidebar.text_input(
    "🔑 Gemini API Key",
    value=default_gemini_key,
    type="password",
    help="GEMINI_API_KEY 環境変数がセットされている場合は自動入力されます"
)
if gemini_key_input:
    os.environ["GEMINI_API_KEY"] = gemini_key_input

default_openai_key = os.environ.get("OPENAI_API_KEY", "")
openai_key_input = st.sidebar.text_input(
    "🔑 OpenAI API Key",
    value=default_openai_key,
    type="password",
    help="OPENAI_API_KEY 環境変数がセットされている場合は自動入力されます"
)
if openai_key_input:
    os.environ["OPENAI_API_KEY"] = openai_key_input

whisper_model = st.sidebar.selectbox(
    "🎙️ Whisper モデルサイズ",
    ["tiny", "base", "small", "medium", "large"],
    index=0,
    help="文字起こしの認識精度。tinyが最も高速、medium/largeが高精度です"
)

st.sidebar.markdown("---")
st.sidebar.markdown("💡 **ステータス情報**")
ffmpeg_path = find_ffmpeg()
if ffmpeg_path:
    st.sidebar.success("✅ FFmpeg 検出済み")
else:
    st.sidebar.error("❌ FFmpeg 未検出")


# --- メインエリア ---
st.markdown('<div class="main-header">🎙️ SummarizePodcast Web Dashboard</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Behind the Markets Podcast や各種ポッドキャストの自動文字起こし＆AI要約システム</div>', unsafe_allow_html=True)

tab1, tab2, tab3, tab4 = st.tabs([
    "📻 Behind the Markets 自動監視",
    "🌐 任意URLの要約",
    "📄 テキストファイル要約",
    "📚 過去の要約・文字起こし閲覧"
])


# --- タブ 1: Behind the Markets 自動監視 ---
with tab1:
    st.subheader("Behind the Markets Podcast 新着自動要約")
    st.write("対象ポッドキャスト: `https://pocketcasts.com/podcast/behind-the-markets-podcast/13110350-be7f-0134-10a8-25324e2a541d`")
    
    col1, col2 = st.columns([2, 1])
    with col1:
        force_run = st.checkbox("差分チェックを無視して強制再処理", value=False)
    with col2:
        run_btn = st.button("🚀 新着エピソードを要約実行", type="primary", use_container_width=True)

    if run_btn:
        with st.status("新着エピソードの処理を実行中...", expanded=True) as status:
            import subprocess
            cmd = [sys.executable, "download_and_summarize.py", "--model", whisper_model]
            if force_run:
                cmd.append("--force")
            
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=PROJECT_ROOT)
            log_area = st.empty()
            full_log = ""
            for line in iter(proc.stdout.readline, ''):
                full_log += line
                log_area.code(full_log[-2000:])
            proc.wait()

            if proc.returncode == 0:
                status.update(label="✅ 要約処理が正常に完了しました！", state="complete")
                st.balloons()
            else:
                status.update(label="❌ 処理中にエラーが発生しました", state="error")


# --- タブ 2: 任意URLの要約 ---
with tab2:
    st.subheader("任意のポッドキャスト URL からダウンロード＆要約")
    col_url, col_cnt = st.columns([3, 1])
    with col_url:
        url_input = st.text_input("ポッドキャストのエピソード/番組 URL (Pocket Casts 等)", placeholder="https://pocketcasts.com/podcast/...")
    with col_cnt:
        ep_count = st.number_input("最新要約エピソード数", min_value=1, max_value=50, value=10, step=1, help="指定した件数分の最新エピソードを順次ダウンロードして要約します")
    
    if st.button("📥 ダウンロード＆要約を開始", key="url_btn", type="primary"):
        if not url_input.strip():
            st.warning("URL を入力してください。")
        else:
            with st.status("エピソード情報を取得中...", expanded=True) as status:
                from download_and_summarize_podcast import fetch_episodes_from_podcast_url
                st.write(f"1. `{url_input}` から最新 {ep_count} 件のエピソード情報を取得中...")
                episodes = fetch_episodes_from_podcast_url(url_input, count=ep_count)
                
                if not episodes:
                    status.update(label="❌ エピソード情報が見つかりませんでした", state="error")
                else:
                    st.write(f"2. {len(episodes)} 件のエピソードが見つかりました。処理を開始します...")
                    model = load_whisper_model(whisper_model)
                    
                    for idx, ep in enumerate(episodes, start=1):
                        ep_title = ep["title"]
                        download_url = ep["media_url"]
                        ep_uuid = ep["uuid"]
                        
                        if not download_url:
                            st.write(f"[{idx}/{len(episodes)}] スキップ: {ep_title} (音声URLなし)")
                            continue
                            
                        st.markdown(f"--- **[{idx}/{len(episodes)}] 1ファイル個別処理開始: {ep_title}** ---")
                        safe_title = re.sub(r'[\\/*?:"<>|]', "", ep_title).strip()
                        safe_title = re.sub(r"\s+", "_", safe_title)
                        audio_base = f"{ep_uuid}_{safe_title}" if ep_uuid else safe_title
                        
                        temp_audio_path = OUTPUT_DIR / f"{audio_base}.mp3"
                        try:
                            st.write(f"1/4. 該当ファイル（1件）の音声ダウンロード中: `{download_url}`")
                            download_file(download_url, temp_audio_path)
                            
                            st.write(f"2/4. Whisper ({whisper_model}) 文字起こし中...")
                            transcript_text, _ = transcribe_audio(model, temp_audio_path, "auto")
                            
                            txt_path = OUTPUT_DIR / f"{audio_base}.txt"
                            txt_path.write_text(transcript_text, encoding="utf-8")
                        finally:
                            if temp_audio_path.exists():
                                temp_audio_path.unlink()
                                st.write("3/4. 音声ファイル（1件）の即時削除完了 (ストレージ解放)")
                            
                        st.write("4/4. LLM 要約生成中...")
                        ja_sum = generate_japanese_summary(transcript_text, "en", llm_provider=llm_provider)
                        en_sum = generate_english_summary(transcript_text, "en", llm_provider=llm_provider)
                        
                        ja_path = OUTPUT_DIR / f"{audio_base}.summary.ja.txt"
                        en_path = OUTPUT_DIR / f"{audio_base}.summary.en.txt"
                        ja_path.write_text(ja_sum, encoding="utf-8")
                        en_path.write_text(en_sum, encoding="utf-8")
                        
                        st.success(f"✅ [{idx}/{len(episodes)}] {ep_title} の全処理完了。次のファイルへ進みます。")
                        
                    status.update(label=f"🎉 全 {len(episodes)} 件のエピソード要約が正常完了しました！", state="complete")
                    st.balloons()


# --- タブ 3: テキストファイル要約 ---
with tab3:
    st.subheader("既存の文字起こしテキストから要約")
    uploaded_file = st.file_uploader("文字起こしテキストファイル (.txt) をアップロード", type=["txt"])
    text_direct = st.text_area("またはテキストを直接入力", height=200)
    
    if st.button("📝 テキストを要約", key="txt_btn", type="primary"):
        target_text = ""
        base_name = "custom_transcript"
        if uploaded_file:
            target_text = uploaded_file.read().decode("utf-8")
            base_name = Path(uploaded_file.name).stem
        elif text_direct.strip():
            target_text = text_direct.strip()
            
        if not target_text:
            st.warning("テキストファイルまたはテキスト本文を入力してください。")
        else:
            with st.spinner("要約を生成中..."):
                ja_sum = generate_japanese_summary(target_text, "en", llm_provider=llm_provider)
                en_sum = generate_english_summary(target_text, "en", llm_provider=llm_provider)
                
                (OUTPUT_DIR / f"{base_name}.summary.ja.txt").write_text(ja_sum, encoding="utf-8")
                (OUTPUT_DIR / f"{base_name}.summary.en.txt").write_text(en_sum, encoding="utf-8")
                
                st.success("✅ 要約が生成されました！")
                
                col_ja, col_en = st.columns(2)
                with col_ja:
                    st.markdown("### 🇯🇵 日本語要約")
                    st.markdown(ja_sum)
                with col_en:
                    st.markdown("### 🇬🇧 英語要約")
                    st.markdown(en_sum)


# --- タブ 4: 過去の要約・文字起こし閲覧 ---
with tab4:
    st.subheader("📚 出力済み要約・文字起こしアーカイブ")
    files = list(OUTPUT_DIR.glob("*.txt"))
    if not files:
        st.info("まだ出力ファイルがありません。")
    else:
        file_names = sorted([f.name for f in files], reverse=True)
        selected_file = st.selectbox("表示するファイルを選択", file_names)
        
        if selected_file:
            file_path = OUTPUT_DIR / selected_file
            content = file_path.read_text(encoding="utf-8")
            
            st.download_button(
                label="📥 このファイルをダウンロード",
                data=content,
                file_name=selected_file,
                mime="text/plain"
            )
            st.text_area("ファイル内容", value=content, height=400)
