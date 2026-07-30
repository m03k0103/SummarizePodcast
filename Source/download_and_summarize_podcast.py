import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote_plus, urlparse

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def extract_download_url(page_html: str) -> str | None:
    page_html = html.unescape(page_html)
    patterns = [
        r'https://feeds\.soundcloud\.com/stream/[^"\'\s<>]+\.mp3[^"\'\s<>]*',
        r'https://[^"\'\s<>]+/stream/[^"\'\s<>]+\.mp3[^"\'\s<>]*',
        r'href=["\']([^"\']+)["\']',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, page_html, re.IGNORECASE)
        if not matches:
            continue
        if pattern.startswith("href"):
            for href in matches:
                href = html.unescape(href)
                if ".mp3" in href.lower() or "stream" in href.lower():
                    if href.startswith("http"):
                        return href
        else:
            for candidate in matches:
                candidate = html.unescape(candidate)
                if candidate.lower().endswith(".mp3") or ".mp3" in candidate.lower():
                    return candidate

    for match in re.finditer(r'Download file', page_html, re.IGNORECASE):
        start = max(0, match.start() - 300)
        end = min(len(page_html), match.end() + 600)
        snippet = page_html[start:end]
        hrefs = re.findall(r'href=["\']([^"\']+)["\']', snippet, re.IGNORECASE)
        for href in hrefs:
            if href.startswith("http") and (href.lower().endswith(".mp3") or ".mp3" in href.lower()):
                return href

    return None


def choose_output_name(url: str, download_url: str | None, explicit: str | None) -> str:
    if explicit:
        return explicit
    if download_url:
        parsed = urlparse(download_url)
        base = os.path.basename(parsed.path)
        if base:
            return base
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if path_parts:
        return f"{path_parts[-1]}.mp3"
    return "downloaded_audio.mp3"


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_output_base(output_arg: str | None, default_base: str) -> tuple[Path, str]:
    output_dir = get_project_root() / "Output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir, default_base


def download_file(url: str, output_path: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp, open(output_path, "wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return output_path


def find_ffmpeg() -> str | None:
    for exe in ["ffmpeg", "ffmpeg.exe"]:
        path = shutil.which(exe)
        if path:
            return path

    home = Path.home()
    extra_search_dirs = [
        home / "OneDrive" / "dev" / "ffmpeg",
        Path(r"C:\Users\m03k0\OneDrive\dev\ffmpeg"),
    ]

    start_dir = Path(__file__).resolve().parent
    search_roots = [start_dir, *start_dir.parents, *extra_search_dirs]

    for current in search_roots:
        if not current.exists():
            continue
        candidates = [
            current / "ffmpeg.exe",
            current / "bin" / "ffmpeg.exe",
            current / "ffmpeg-master-latest-win64-gpl-shared" / "bin" / "ffmpeg.exe",
            current / "ffmpeg" / "ffmpeg-master-latest-win64-gpl-shared" / "bin" / "ffmpeg.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

    return None


def install_package(package: str) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])


def load_whisper_model(model_name: str):
    try:
        import whisper
    except ImportError:
        print("Installing openai-whisper...")
        install_package("openai-whisper")
        import whisper

    return whisper.load_model(model_name)


def transcribe_audio(model, mp3_path: Path, language: str) -> tuple[str, list[dict]]:
    print(f"Transcribing: {mp3_path}")
    if language and language.lower() != "auto":
        result = model.transcribe(str(mp3_path), language=language, fp16=False)
    else:
        result = model.transcribe(str(mp3_path), fp16=False)
    text = result.get("text", "").strip()
    segments = result.get("segments", [])
    return text, segments


def translate_text_via_google_api(text: str, source_language: str = "en", target_language: str = "ja") -> str:
    if not text.strip():
        return text

    translated_parts: list[str] = []
    max_chunk = 2000
    position = 0

    while position < len(text):
        chunk = text[position:position + max_chunk]
        position += max_chunk
        url = (
            "https://translate.googleapis.com/translate_a/single?client=gtx"
            f"&sl={source_language}&tl={target_language}&dt=t&q={quote_plus(chunk)}"
        )
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://translate.google.com/",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw)
                translated = "".join([part[0] for part in parsed[0] if part])
                translated_parts.append(translated)
        except Exception as exc:
            print(f"Translation failed: {exc}", file=sys.stderr)
            # Return empty string to signal failure to caller (so caller can fallback)
            return ""

    return "".join(translated_parts)


def translate_text_via_mymemory(text: str, source_language: str = "en", target_language: str = "ja") -> str:
    if not text.strip():
        return ""
    try:
        url = (
            "https://api.mymemory.translated.net/get?"
            f"q={quote_plus(text)}&langpair={source_language}|{target_language}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw)
            return parsed.get("responseData", {}).get("translatedText", "") or ""
    except Exception as exc:
        print(f"MyMemory translation failed: {exc}", file=sys.stderr)
        return ""


def translate_text_via_libretranslate(text: str, source_language: str = "en", target_language: str = "ja") -> str:
    if not text.strip():
        return ""
    try:
        url = "https://libretranslate.de/translate"
        payload = json.dumps({
            "q": text,
            "source": source_language,
            "target": target_language,
            "format": "text",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw)
            return parsed.get("translatedText", "") or ""
    except Exception as exc:
        print(f"LibreTranslate failed: {exc}", file=sys.stderr)
        return ""


def translate_text(text: str, source_language: str = "en", target_language: str = "ja") -> str:
    """Try Google translate endpoint first, then fall back to MyMemory."""
    translated = translate_text_via_google_api(text, source_language=source_language, target_language=target_language)
    if translated and translated.strip():
        return translated
    # Try LibreTranslate (POST) to avoid URI length limits
    translated = translate_text_via_libretranslate(text, source_language=source_language, target_language=target_language)
    if translated and translated.strip():
        return translated
    # Try MyMemory for shorter texts or if others failed
    return translate_text_via_mymemory(text, source_language=source_language, target_language=target_language)


def translate_text_in_chunks(text: str, source_language: str = "en", target_language: str = "ja", max_chunk: int = 800) -> str:
    """Translate text by splitting into smaller pieces (sentences) to avoid URI-too-long errors.
    Falls back to per-sentence translation if chunk translation fails.
    """
    if not text.strip():
        return ""

    # Split by sentence-like boundaries; keep punctuation
    parts = re.split(r'(?<=[。！？!?\.\!\?])\s+', text)
    if not parts:
        parts = [text]

    translated_parts: list[str] = []
    buffer = ""
    for part in parts:
        if len(buffer) + len(part) + 1 <= max_chunk:
            buffer = (buffer + " " + part).strip() if buffer else part
            continue

        # translate buffer
        t = translate_text(buffer, source_language=source_language, target_language=target_language)
        if t and t.strip():
            translated_parts.append(t)
        else:
            # fallback to per-sentence
            for sent in re.split(r'(?<=[。！？!?\.\!\?])\s+', buffer):
                tt = translate_text(sent, source_language=source_language, target_language=target_language)
                translated_parts.append(tt if tt and tt.strip() else sent)

        buffer = part

    if buffer:
        t = translate_text(buffer, source_language=source_language, target_language=target_language)
        if t and t.strip():
            translated_parts.append(t)
        else:
            for sent in re.split(r'(?<=[。！？!?\.\!\?])\s+', buffer):
                tt = translate_text(sent, source_language=source_language, target_language=target_language)
                translated_parts.append(tt if tt and tt.strip() else sent)

    return "\n".join(part for part in translated_parts if part)


def remove_boilerplate(text: str) -> str:
    if not text:
        return text

    content = text

    # Prefer the actual TED Talk segment if present.
    start_match = re.search(r'(?i)and now our ted talk of the day[\.\!\?]*', content)
    if start_match:
        content = content[start_match.end():]

    end_match = re.search(
        r'(?i)(thank you[\.\!\?]*\s*that was .*|if you\'re curious about ted\'s curation.*|and that\'s it for today[\.\!\?]*)',
        content,
    )
    if end_match:
        content = content[:end_match.start()]

    # Remove promotional or fixed branding phrases inside the talk text.
    patterns = [
        r'(?i)ted(?: talks daily)?\s*(is|は) .*podcast',
        r'(?i)this episode was .*fact[- ]checked',
        r'(?i)produced and edited',
        r'(?i)produced.*edited',
        r'(?i)ted research team',
        r'(?i)curation guidelines',
        r'(?i)ted\.com.*curation',
        r'(?i)thank you\.?$',
        r'(?i)if you\'re curious about.*',
        r'(?i)and that\'s it for today\.?',
    ]
    for pattern in patterns:
        content = re.sub(pattern, "", content, flags=re.IGNORECASE)

    return content.strip()


def call_gemini_api(prompt: str, api_key: str, model: str = "gemini-2.0-flash") -> str:
    import time
    import urllib.error
    model_path = model if model.startswith("models/") else f"models/{model}"
    url = f"https://generativelanguage.googleapis.com/v1beta/{model_path}:generateContent?key={api_key}"
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")

    max_retries = 3
    backoff = 5
    for attempt in range(max_retries):
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "").strip()
                return ""
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < max_retries - 1:
                print(f"Rate limit hit for {model_path}. Retrying in {backoff} seconds... (attempt {attempt+1}/{max_retries})", file=sys.stderr)
                time.sleep(backoff)
                backoff *= 2
            else:
                print(f"Gemini API call failed ({model_path}): {exc}", file=sys.stderr)
                break
        except Exception as exc:
            print(f"Gemini API call failed ({model_path}): {exc}", file=sys.stderr)
            break
    return ""


def call_openai_api(prompt: str, api_key: str, model: str = "gpt-4o-mini", base_url: str = "https://api.openai.com/v1") -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant that summarizes podcasts accurately and clearly."},
            {"role": "user", "content": prompt}
        ]
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
    except Exception as exc:
        print(f"OpenAI API call failed: {exc}", file=sys.stderr)
    return ""


def call_ollama_api(prompt: str, model: str = "llama3", base_url: str = "http://localhost:11434") -> str:
    url = f"{base_url.rstrip('/')}/api/generate"
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", "").strip()
    except Exception as exc:
        print(f"Ollama API call failed: {exc}", file=sys.stderr)
    return ""


def summarize_with_llm(
    text: str,
    target_language: str,
    provider: str = "auto",
    gemini_key: str | None = None,
    openai_key: str | None = None,
    model: str | None = None,
    openai_base_url: str | None = None,
    ollama_base_url: str | None = None,
) -> str | None:
    cleaned = remove_boilerplate(text)
    if not cleaned.strip():
        return ""

    if provider.lower() == "textrank":
        return None

    gemini_key = gemini_key or os.environ.get("GEMINI_API_KEY")
    openai_key = openai_key or os.environ.get("OPENAI_API_KEY")

    if not gemini_key and sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg_key:
                val, _ = winreg.QueryValueEx(reg_key, "GEMINI_API_KEY")
                if val:
                    gemini_key = val
        except Exception:
            pass

    active_provider = provider.lower()
    if active_provider == "auto":
        if gemini_key:
            active_provider = "gemini"
        elif openai_key:
            active_provider = "openai"
        else:
            print("Notice: GEMINI_API_KEY / OPENAI_API_KEY not found. Falling back to TextRank summarizer.", file=sys.stderr)
            return None

    if target_language.lower().startswith("ja"):
        prompt = (
            "以下のポッドキャストの文字起こしテキストを読み、重要なポイントをわかりやすく要約してください。\n\n"
            "# 要約フォーマットの指定:\n"
            "## 概要\n"
            "(全体のトピックと主旨を2〜3文で簡潔にまとめる)\n\n"
            "## 主要ポイント\n"
            "- (トピックや重要発言・議論内容を3〜5つの箇条書きでまとめる)\n\n"
            "## 結論・まとめ\n"
            "(エピソードの結論やリスナーへの示唆を1〜2文でまとめる)\n\n"
            "--- 文字起こしテキスト ---\n"
            f"{cleaned}"
        )
    else:
        prompt = (
            "Please read the following podcast transcript and generate a clear, structured summary in English.\n\n"
            "# Required Format:\n"
            "## Overview\n"
            "(Summarize the main topic and purpose in 2-3 concise sentences)\n\n"
            "## Key Takeaways\n"
            "- (List 3-5 bullet points covering the key ideas, arguments, or discussions)\n\n"
            "## Conclusion\n"
            "(Summarize the conclusion or takeaway in 1-2 sentences)\n\n"
            "--- Transcript ---\n"
            f"{cleaned}"
        )

    if active_provider == "gemini":
        if not gemini_key:
            print("Gemini API key is required. Set GEMINI_API_KEY env var or pass --gemini-api-key.", file=sys.stderr)
            return None
        target_model = model or "gemini-flash-latest"
        print(f"Summarizing using Gemini API ({target_model})...")
        res = call_gemini_api(prompt, gemini_key, model=target_model)
        if res:
            return res
        import time
        for fallback_model in ["gemini-flash-latest", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemma-4-31b-it"]:
            if fallback_model == target_model:
                continue
            time.sleep(1)
            print(f"Retrying with {fallback_model}...")
            res = call_gemini_api(prompt, gemini_key, model=fallback_model)
            if res:
                return res

    elif active_provider == "openai":
        if not openai_key:
            print("OpenAI API key is required. Set OPENAI_API_KEY env var or pass --openai-api-key.", file=sys.stderr)
            return None
        target_model = model or "gpt-4o-mini"
        base_url = openai_base_url or "https://api.openai.com/v1"
        print(f"Summarizing using OpenAI API ({target_model})...")
        res = call_openai_api(prompt, openai_key, model=target_model, base_url=base_url)
        if res:
            return res

    elif active_provider == "ollama":
        target_model = model or "llama3"
        base_url = ollama_base_url or "http://localhost:11434"
        print(f"Summarizing using Ollama ({target_model})...")
        res = call_ollama_api(prompt, model=target_model, base_url=base_url)
        if res:
            return res

    return None


def generate_english_summary(
    text: str,
    language: str,
    max_sentences: int = 3,
    ratio: float = 0.25,
    llm_provider: str = "auto",
    gemini_key: str | None = None,
    openai_key: str | None = None,
    llm_model: str | None = None,
    openai_base_url: str | None = None,
    ollama_base_url: str | None = None,
) -> str:
    if not text.strip():
        return ""

    # Try LLM summarization first
    llm_res = summarize_with_llm(
        text=text,
        target_language="en",
        provider=llm_provider,
        gemini_key=gemini_key,
        openai_key=openai_key,
        model=llm_model,
        openai_base_url=openai_base_url,
        ollama_base_url=ollama_base_url,
    )
    if llm_res and llm_res.strip():
        return llm_res.strip()

    cleaned_text = remove_boilerplate(text)
    if not cleaned_text.strip():
        return ""

    source_language = language
    # If source is already English, summarize directly.
    if source_language.lower().startswith("en"):
        summary = summarize_text(cleaned_text, max_sentences=max_sentences, ratio=ratio)
        if not summary.strip():
            summary = summarize_text(cleaned_text, max_sentences=max_sentences, ratio=0.5)
        return summary

    # Try to translate full text to English first.
    english_text = translate_text(cleaned_text, source_language=source_language, target_language="en")
    if english_text.strip():
        summary = summarize_text(english_text, max_sentences=max_sentences, ratio=ratio)
        if not summary.strip():
            summary = summarize_text(english_text, max_sentences=max_sentences, ratio=0.5)
        return summary

    # Full-text translation failed: summarize in source language, then translate the shorter summary.
    source_summary = summarize_text(cleaned_text, max_sentences=max_sentences, ratio=ratio)
    if not source_summary.strip():
        source_summary = summarize_text(cleaned_text, max_sentences=max_sentences, ratio=0.5)

    translated_summary = translate_text(source_summary, source_language=source_language, target_language="en")
    if not translated_summary.strip():
        translated_summary = translate_text_in_chunks(source_summary, source_language=source_language, target_language="en")
    if translated_summary.strip():
        return translated_summary

    return source_summary


def generate_japanese_summary(
    text: str,
    language: str,
    max_sentences: int = 3,
    ratio: float = 0.25,
    llm_provider: str = "auto",
    gemini_key: str | None = None,
    openai_key: str | None = None,
    llm_model: str | None = None,
    openai_base_url: str | None = None,
    ollama_base_url: str | None = None,
) -> str:
    if not text.strip():
        return ""

    # Try LLM summarization first
    llm_res = summarize_with_llm(
        text=text,
        target_language="ja",
        provider=llm_provider,
        gemini_key=gemini_key,
        openai_key=openai_key,
        model=llm_model,
        openai_base_url=openai_base_url,
        ollama_base_url=ollama_base_url,
    )
    if llm_res and llm_res.strip():
        return llm_res.strip()

    cleaned_text = remove_boilerplate(text)
    if not cleaned_text.strip():
        return ""

    source_language = language
    if source_language.lower().startswith("ja"):
        japanese_text = cleaned_text
        summary = summarize_text(japanese_text, max_sentences=max_sentences, ratio=ratio)
        if not summary.strip():
            summary = summarize_text(japanese_text, max_sentences=max_sentences, ratio=0.5)
        return summary

    english_summary = generate_english_summary(text, language, max_sentences=max_sentences, ratio=ratio, llm_provider="textrank")
    if english_summary.strip():
        translated_summary = translate_text(english_summary, source_language="en", target_language="ja")
        if not translated_summary.strip():
            translated_summary = translate_text_in_chunks(english_summary, source_language="en", target_language="ja")
        if translated_summary.strip():
            return translated_summary

    japanese_text = translate_text(cleaned_text, source_language=source_language, target_language="ja")
    summary = summarize_text(japanese_text, max_sentences=max_sentences, ratio=ratio)
    if not summary.strip():
        summary = summarize_text(japanese_text, max_sentences=max_sentences, ratio=0.5)
    return summary


STOPWORDS = {
    'the', 'and', 'of', 'to', 'a', 'in', 'that', 'it', 'is', 'was', 'for', 'on', 'with',
    'as', 'are', 'this', 'but', 'be', 'at', 'by', 'or', 'an', 'from', 'they', 'not',
    'have', 'which', 'we', 'you', 'their', 'has', 'more', 'who', 'when', 'your',
    'will', 'what', 'about', 'could', 'than', 'should', 'been', 'also', 'one', 'all',
}


def tokenize(text: str) -> list[str]:
    return re.findall(r'[一-龥ぁ-んァ-ン]+|[A-Za-z0-9\']+', text)


def split_sentences(text: str) -> list[str]:
    if not text:
        return []
    # Split on sentence-ending punctuation; allow zero or more spaces after punctuation
    sentences = re.split(r'(?<=[。！？!?\.\!\?])\s*', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) == 0:
        return [text.strip()]

    # If the text didn't contain sentence punctuation and is very long, split into chunks
    if len(sentences) == 1 and len(sentences[0]) > 300:
        s = sentences[0]
        chunks: list[str] = []
        start = 0
        maxlen = 200
        while start < len(s):
            end = min(len(s), start + maxlen)
            segment = s[start:end]
            # find last sentence-ending punctuation within the segment
            last_punct = -1
            for p in ('。', '！', '？', '.', '!', '?'):
                i = segment.rfind(p)
                if i > last_punct:
                    last_punct = i
            if last_punct != -1:
                cut = start + last_punct + 1
            else:
                # fallback to last whitespace or fixed cut
                space_idx = segment.rfind(' ')
                if space_idx != -1:
                    cut = start + space_idx + 1
                else:
                    cut = end
            chunks.append(s[start:cut].strip())
            start = cut
        return [c for c in chunks if c]

    return sentences


def detect_language_from_text(text: str) -> str:
    if re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', text):
        return "ja"
    if re.search(r'[A-Za-z]', text):
        return "en"
    return "en"


def sentence_tokens(sentence: str) -> list[str]:
    tokens = [token.lower() for token in tokenize(sentence)]
    return [token for token in tokens if len(token) > 1 and token not in STOPWORDS]


def is_mostly_english(text: str) -> bool:
    if not text:
        return True
    eng = len(re.findall(r'[A-Za-z]', text))
    noneng = len(re.findall(r'[一-龥ぁ-んァ-ン]', text))
    return eng >= max(1, noneng)


def build_sentence_vector(tokens: list[str]) -> dict[str, float]:
    freq: dict[str, float] = {}
    for token in tokens:
        freq[token] = freq.get(token, 0.0) + 1.0
    return freq


def sentence_similarity(vec1: dict[str, float], vec2: dict[str, float]) -> float:
    if not vec1 or not vec2:
        return 0.0
    dot = sum(vec1[token] * vec2.get(token, 0.0) for token in vec1)
    norm1 = sum(value * value for value in vec1.values()) ** 0.5
    norm2 = sum(value * value for value in vec2.values()) ** 0.5
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


def summarize_text(text: str, max_sentences: int = 0, ratio: float = 0.25) -> str:
    sentences = split_sentences(text)
    if not sentences:
        return ""

    if max_sentences > 0 and len(sentences) <= max_sentences:
        return "\n".join(sentences)

    target_chars = max(100, int(len(text) * ratio))
    if target_chars > len(text):
        target_chars = len(text)

    vectors = [build_sentence_vector(sentence_tokens(sentence)) for sentence in sentences]
    n = len(sentences)
    similarity_matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            sim = sentence_similarity(vectors[i], vectors[j])
            similarity_matrix[i][j] = sim
            similarity_matrix[j][i] = sim

    pagerank = [1.0] * n
    d = 0.85
    for _ in range(30):
        new_scores = [0.0] * n
        for i in range(n):
            for j in range(n):
                if similarity_matrix[j][i] > 0:
                    denom = sum(similarity_matrix[j])
                    if denom > 0:
                        new_scores[i] += pagerank[j] * (similarity_matrix[j][i] / denom)
        for i in range(n):
            pagerank[i] = (1 - d) + d * new_scores[i]

    ranked = sorted(((score, idx) for idx, score in enumerate(pagerank)), reverse=True)
    selected_idx = []
    total_chars = 0
    for _, idx in ranked:
        if idx in selected_idx:
            continue
        selected_idx.append(idx)
        total_chars += len(sentences[idx])
        if total_chars >= target_chars:
            break
        if max_sentences > 0 and len(selected_idx) >= max_sentences:
            break

    if not selected_idx:
        selected_idx = [ranked[0][1]]

    selected = sorted(selected_idx)
    return "\n".join(sentences[idx] for idx in selected)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download a podcast episode, transcribe it, or summarize an existing transcript."
    )
    parser.add_argument("input", help="Podcast episode/page URL or path to existing transcript text file")
    parser.add_argument("-o", "--output", help="Base output name or output directory")
    parser.add_argument("--skip-download", action="store_true", help="Skip downloading audio and summarize an existing transcript file directly")
    parser.add_argument("--model", default="tiny", help="Whisper model size: tiny, base, small, medium, large")
    parser.add_argument(
        "--language",
        "--source-language",
        dest="source_language",
        default="auto",
        help="Original audio/transcript language code for transcription and summarization, e.g. en, ja (default: auto detect)",
    )
    parser.add_argument("--summary-sentences", type=int, default=0, help="Maximum number of sentences in the summary (0 = no maximum; use ratio only)")
    parser.add_argument("--summary-ratio", type=float, default=0.25, help="Target summary length ratio relative to input text by character length (0.2-0.3 recommended)")
    parser.add_argument("--llm-provider", choices=["auto", "gemini", "openai", "ollama", "textrank"], default="auto", help="Summarizer provider: auto, gemini, openai, ollama, textrank (default: auto)")
    parser.add_argument("--llm-model", default=None, help="LLM model name (e.g. gemini-2.5-flash, gpt-4o-mini, llama3)")
    parser.add_argument("--gemini-api-key", default=None, help="Gemini API Key (or set GEMINI_API_KEY env var)")
    parser.add_argument("--openai-api-key", default=None, help="OpenAI API Key (or set OPENAI_API_KEY env var)")
    parser.add_argument("--openai-base-url", default=None, help="OpenAI API Base URL (default: https://api.openai.com/v1)")
    parser.add_argument("--ollama-base-url", default=None, help="Ollama Base URL (default: http://localhost:11434)")
    args = parser.parse_args()

    llm_kwargs = {
        "llm_provider": args.llm_provider,
        "gemini_key": args.gemini_api_key,
        "openai_key": args.openai_api_key,
        "llm_model": args.llm_model,
        "openai_base_url": args.openai_base_url,
        "ollama_base_url": args.ollama_base_url,
    }

    if args.skip_download:
        transcript_path = Path(args.input)
        if not transcript_path.exists() or not transcript_path.is_file():
            print(f"Transcript file not found: {transcript_path}", file=sys.stderr)
            return 2

        transcript_text = transcript_path.read_text(encoding="utf-8")
        output_dir, output_base = resolve_output_base(args.output, transcript_path.stem)
        output_dir.mkdir(parents=True, exist_ok=True)

        english_summary = generate_english_summary(
            transcript_text, args.source_language, max_sentences=args.summary_sentences, ratio=args.summary_ratio, **llm_kwargs
        )
        if not english_summary:
            english_summary = "(Transcription is empty; summary could not be created.)"
        english_summary_path = output_dir / f"{output_base}.summary.en.txt"
        if not args.source_language.lower().startswith("en") and not is_mostly_english(english_summary):
            note = "(English translation unavailable; original summary in source language follows)\n\n"
            english_summary_path.write_text(note + english_summary, encoding="utf-8")
        else:
            english_summary_path.write_text(english_summary, encoding="utf-8")
        print(f"Saved English summary to: {english_summary_path}")

        japanese_summary = generate_japanese_summary(
            transcript_text, args.source_language, max_sentences=args.summary_sentences, ratio=args.summary_ratio, **llm_kwargs
        )
        if not japanese_summary:
            japanese_summary = "(文字起こしが空です。概要を作成できませんでした。)"
        japanese_summary_path = output_dir / f"{output_base}.summary.ja.txt"
        japanese_summary_path.write_text(japanese_summary, encoding="utf-8")
        print(f"Saved Japanese summary to: {japanese_summary_path}")
        return 0

    if not args.input.startswith("http"):
        print("Please provide a valid http(s) URL unless --skip-download is used.", file=sys.stderr)
        return 2

    print(f"Fetching episode page: {args.input}")
    page_html = fetch_text(args.input)
    download_url = extract_download_url(page_html)
    if not download_url:
        print("Could not find a downloadable MP3 URL in the page.", file=sys.stderr)
        return 1

    audio_base = Path(choose_output_name(args.input, download_url, None)).stem
    output_dir, output_base = resolve_output_base(args.output, audio_base)
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_path = output_dir / f"{output_base}.mp3"
    print(f"Resolved audio URL: {download_url}")
    print(f"Saving audio to: {audio_path}")
    download_file(download_url, audio_path)
    print("Download complete.")

    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        print("ffmpeg was not found. Please install FFmpeg and make sure 'ffmpeg' is on PATH.", file=sys.stderr)
        return 3

    os.environ["FFMPEG_BINARY"] = ffmpeg_path
    os.environ["PATH"] = str(Path(ffmpeg_path).parent) + os.pathsep + os.environ.get("PATH", "")

    model = load_whisper_model(args.model)
    transcript_text, segments = transcribe_audio(model, audio_path, args.source_language)

    transcript_path = output_dir / f"{output_base}.txt"
    transcript_path.write_text(transcript_text, encoding="utf-8")
    print(f"Saved transcription to: {transcript_path}")

    english_summary = generate_english_summary(
        transcript_text, args.source_language, max_sentences=args.summary_sentences, ratio=args.summary_ratio, **llm_kwargs
    )
    if not english_summary:
        english_summary = "(Transcription is empty; summary could not be created.)"
    english_summary_path = output_dir / f"{output_base}.summary.en.txt"
    if not args.source_language.lower().startswith("en") and not is_mostly_english(english_summary):
        note = "(English translation unavailable; original summary in source language follows)\n\n"
        english_summary_path.write_text(note + english_summary, encoding="utf-8")
    else:
        english_summary_path.write_text(english_summary, encoding="utf-8")
    print(f"Saved English summary to: {english_summary_path}")

    japanese_summary = generate_japanese_summary(
        transcript_text, args.source_language, max_sentences=args.summary_sentences, ratio=args.summary_ratio, **llm_kwargs
    )
    if not japanese_summary:
        japanese_summary = "(文字起こしが空です。概要を作成できませんでした。)"
    japanese_summary_path = output_dir / f"{output_base}.summary.ja.txt"
    japanese_summary_path.write_text(japanese_summary, encoding="utf-8")
    print(f"Saved Japanese summary to: {japanese_summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
