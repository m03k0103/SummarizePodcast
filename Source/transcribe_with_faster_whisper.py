import argparse
from pathlib import Path
from faster_whisper import WhisperModel


def main():
    parser = argparse.ArgumentParser(description='Transcribe an MP3 using faster-whisper')
    parser.add_argument('mp3_path', help='Path to the MP3 file')
    parser.add_argument('-o', '--output', help='Output text file path')
    parser.add_argument('--model', default='tiny', help='Model size: tiny, base, small, medium, large')
    parser.add_argument('--language', default='en', help='Language code, e.g. en, ja')
    parser.add_argument('--device', default='cpu', help='Device: cpu, cuda, auto')
    args = parser.parse_args()

    mp3_path = Path(args.mp3_path)
    if not mp3_path.exists():
        raise SystemExit(f'File not found: {mp3_path}')

    output_path = Path(args.output) if args.output else mp3_path.with_suffix('.txt')

    model = WhisperModel(args.model, device=args.device, compute_type='int8')
    segments, info = model.transcribe(str(mp3_path), language=args.language)

    text = '\n'.join(segment.text for segment in segments if segment.text)
    output_path.write_text(text, encoding='utf-8')
    print(f'Saved transcription to: {output_path}')


if __name__ == '__main__':
    main()
