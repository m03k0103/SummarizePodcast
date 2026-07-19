import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def install_package(package: str) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])


def find_ffmpeg() -> str | None:
    for exe in ["ffmpeg", "ffmpeg.exe"]:
        path = shutil.which(exe)
        if path:
            return path

    local_bin = Path(__file__).resolve().parent / "ffmpeg" / "ffmpeg-master-latest-win64-gpl-shared" / "bin"
    candidate = local_bin / "ffmpeg.exe"
    if candidate.exists():
        return str(candidate)

    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe an MP3 file to text")
    parser.add_argument("mp3_path", help="Path to the MP3 file")
    parser.add_argument("-o", "--output", help="Output text file path")
    parser.add_argument("--model", default="tiny", help="Whisper model size: tiny, base, small, medium, large")
    parser.add_argument("--language", default="en", help="Audio language code, e.g. en, ja")
    args = parser.parse_args()

    mp3_path = Path(args.mp3_path)
    if not mp3_path.exists():
        print(f"File not found: {mp3_path}", file=sys.stderr)
        return 2

    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        print("ffmpeg was not found. Please install FFmpeg and make sure 'ffmpeg' is on PATH.", file=sys.stderr)
        return 3

    os.environ["FFMPEG_BINARY"] = ffmpeg_path
    os.environ["PATH"] = str(Path(ffmpeg_path).parent) + os.pathsep + os.environ.get("PATH", "")

    try:
        import whisper
    except ImportError:
        print("Installing openai-whisper...")
        install_package("openai-whisper")
        import whisper

    output_path = Path(args.output) if args.output else mp3_path.with_suffix(".txt")

    print(f"Loading model: {args.model}")
    model = whisper.load_model(args.model)

    print(f"Transcribing: {mp3_path}")
    result = model.transcribe(str(mp3_path), language=args.language, fp16=False)
    text = result.get("text", "").strip()

    output_path.write_text(text, encoding="utf-8")
    print(f"Saved transcription to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
