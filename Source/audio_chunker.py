"""
音声チャンク分割 & 話者コンテキスト引き継ぎ & タイムスタンプ調整モジュール
Track [Diarization] (Drop 5): 長尺音声の自動チャンク分割処理
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows文字コード安全保護
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def find_ffprobe(ffmpeg_path: str | None = None) -> str | None:
    """ffmpeg パスまたは環境から ffprobe の実行可能パスを取得"""
    for exe in ["ffprobe", "ffprobe.exe"]:
        p = shutil.which(exe)
        if p:
            return p

    if ffmpeg_path:
        candidate = Path(ffmpeg_path).with_name("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        if candidate.exists():
            return str(candidate)

    # 共通探索ディレクトリ
    home = Path.home()
    extra_dirs = [
        home / "OneDrive" / "dev" / "ffmpeg" / "ffmpeg-master-latest-win64-gpl-shared" / "bin",
        Path(r"C:\Users\m03k0\OneDrive\dev\ffmpeg\ffmpeg-master-latest-win64-gpl-shared\bin"),
    ]
    for d in extra_dirs:
        probe = d / "ffprobe.exe"
        if probe.exists():
            return str(probe)

    return None


def get_audio_duration(audio_path: Path, ffmpeg_path: str | None = None) -> float:
    """ffprobe を用いて音声ファイルの再生時間（秒）を正確に取得"""
    ffprobe = find_ffprobe(ffmpeg_path)
    if not ffprobe:
        raise FileNotFoundError("ffprobe が見つかりませんでした。FFmpeg のインストールおよび PATH を確認してください。")

    cmd = [
        str(ffprobe),
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    out = res.stdout.strip()
    return float(out) if out else 0.0


def split_audio_into_chunks(
    audio_path: Path,
    ffmpeg_path: str,
    chunk_minutes: int = 15,
    min_chunk_minutes: int = 3
) -> list[tuple[Path, int, int]]:
    """
    音声を指定分数（デフォルト15分）ごとのチャンクにロスレス高速分割する。
    戻り値: [(chunk_file_path, start_offset_seconds, duration_seconds), ...]
    ※ 最後のチャンクが極端に短い（min_chunk_minutes 未満）場合は直前のチャンクと統合する。
    """
    total_seconds = int(get_audio_duration(audio_path, ffmpeg_path))
    chunk_size_sec = chunk_minutes * 60
    min_chunk_sec = min_chunk_minutes * 60

    if total_seconds <= chunk_size_sec:
        # 分割不要
        return [(audio_path, 0, total_seconds)]

    # 分割計画の算出
    ranges = []
    current_start = 0
    while current_start < total_seconds:
        remaining = total_seconds - current_start
        if remaining <= chunk_size_sec:
            ranges.append((current_start, remaining))
            break
        # 次の残りが極端に短い場合は延長してマージ
        if (remaining - chunk_size_sec) < min_chunk_sec:
            ranges.append((current_start, remaining))
            break
        ranges.append((current_start, chunk_size_sec))
        current_start += chunk_size_sec

    # ffmpeg でロスレス分割 (-acodec copy)
    temp_dir = Path(tempfile.mkdtemp(prefix="audio_chunks_"))
    results = []

    for idx, (start_sec, dur_sec) in enumerate(ranges, start=1):
        chunk_file = temp_dir / f"chunk_{idx:02d}_{start_sec}s_{dur_sec}s.mp3"
        cmd = [
            str(ffmpeg_path),
            "-y",
            "-ss", str(start_sec),
            "-t", str(dur_sec),
            "-i", str(audio_path),
            "-acodec", "copy",
            str(chunk_file)
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        results.append((chunk_file, start_sec, dur_sec))

    return results


def extract_speaker_profile_and_tail(transcript: str, tail_lines_count: int = 4) -> tuple[list[str], str]:
    """
    直前チャンクの文字起こし結果から、出現した話者ラベル一覧と、直前数行の会話テキストを抽出する。
    """
    if not transcript.strip():
        return [], ""

    lines = [line.strip() for line in transcript.splitlines() if line.strip()]
    
    # 話者ラベルの検出（例: [話者1 (深井)], [話者2 (けんすう)], [話者1] 等）
    speaker_pattern = re.compile(r'\[(話者\s*\d+[^\]]*)\]')
    found_speakers = []
    for line in lines:
        for m in speaker_pattern.finditer(line):
            spk = m.group(1).strip()
            if spk and spk not in found_speakers:
                found_speakers.append(spk)

    # 見つからない場合は汎用的なラベル（[深井] 等）をフォールバック探索
    if not found_speakers:
        fallback_pattern = re.compile(r'\[(?:\d{1,2}:\d{2}(?::\d{2})?)?\]?\s*\[(.*?)\]')
        for line in lines:
            for m in fallback_pattern.finditer(line):
                spk = m.group(1).strip()
                if spk and not re.match(r'^\d{1,2}:\d{2}', spk) and spk not in found_speakers:
                    found_speakers.append(spk)

    # 末尾の数行
    tail_lines = lines[-tail_lines_count:] if len(lines) >= tail_lines_count else lines
    tail_text = "\n".join(tail_lines)

    return found_speakers, tail_text


def adjust_timestamps(transcript: str, offset_seconds: int) -> str:
    """
    チャンクごとの文字起こし結果に含まれる相対タイムスタンプ [MM:SS] に、
    そのチャンクの開始秒数（offset_seconds）を加算して絶対タイムスタンプに変換する。
    """
    if offset_seconds <= 0:
        return transcript

    def replace_ts(match):
        ts_str = match.group(1)
        parts = ts_str.split(":")
        try:
            if len(parts) == 2:
                m, s = int(parts[0]), int(parts[1])
                sec = m * 60 + s
            elif len(parts) == 3:
                h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                sec = h * 3600 + m * 60 + s
            else:
                return match.group(0)
        except ValueError:
            return match.group(0)

        # モデルが相対時間で出力している場合にオフセットを加算
        # （すでにオフセット以上の絶対時間を出力している場合は二重加算を防止）
        if sec < offset_seconds:
            new_sec = sec + offset_seconds
        else:
            new_sec = sec

        nm, ns = divmod(new_sec, 60)
        nh, nm = divmod(nm, 60)
        if nh > 0:
            return f"[{nh:02d}:{nm:02d}:{ns:02d}]"
        return f"[{nm:02d}:{ns:02d}]"

    # [00:15] や [01:23:45] を置換
    return re.sub(r'\[(\d{1,2}:\d{2}(?::\d{2})?)\]', replace_ts, transcript)


def build_chunk_prompt(
    podcast_title: str,
    chunk_index: int,
    total_chunks: int,
    previous_speakers: list[str] | None = None,
    previous_tail: str | None = None
) -> str:
    """
    チャンク分割時のコンテキスト引き継ぎ用プロンプトを構築する。
    話者の名前を同定し、[話者1 (深井)] のような形式で出力させる。
    """
    title_context = f"ポッドキャスト/エピソード名: {podcast_title}\n" if podcast_title else ""
    part_info = f"【パート {chunk_index}/{total_chunks}】\n" if total_chunks > 1 else ""

    # パーソナリティ・ゲストに関するヒントの推測
    hints = []
    if podcast_title:
        if any(k in podcast_title for k in ["幻会議", "深井", "件数", "けんすう"]):
            hints.append("本番組（深井件数の幻会議）のレギュラー出演者は「深井龍之介（深井）」と「けんすう（古川健介）」の2名です。発言者の声や役割に応じて [話者1 (深井)]、[話者2 (けんすう)] のように名前を明記してください。")
        m_guest = re.search(r"【初?ゲスト回?】?([^\sさん]+(?:さん|氏|様)?)", podcast_title)
        if m_guest and not any(k in m_guest.group(1) for k in ["深井", "件数", "けんすう"]):
            hints.append(f"ゲストとして「{m_guest.group(1)}」が参加している可能性があります。登場時は [話者3 (名前)] 等として識別してください。")

    hint_context = ""
    if hints:
        hint_context = "【出演者ヒント】\n" + "\n".join([f"- {h}" for h in hints]) + "\n"

    context_injection = ""
    if chunk_index > 1 and previous_speakers:
        speakers_str = "、".join([f"[{s}]" for s in previous_speakers])
        context_injection = (
            f"【直前パートからの継続コンテキスト】\n"
            f"直前のパートから続く対談音声です。登場人物の話者番号および名前ラベルを前パートと完全に一致させてください。\n"
            f"- 前パートで確認された話者ラベル: {speakers_str}\n"
        )
        if previous_tail:
            context_injection += (
                f"- 前パート直前の会話内容（この直後から始まります）:\n"
                f"```\n{previous_tail}\n```\n"
            )

    prompt = (
        f"このポッドキャストの音声を詳細かつ忠実に文字起こししてください。\n"
        f"{title_context}"
        f"{hint_context}"
        f"{part_info}"
        f"{context_injection}"
        "【要件】\n"
        "1. 【最重要：話者分離と話者名の明記】\n"
        "   - 本音声は複数名による対話・対談ポッドキャストです（1人の独白ではありません）。\n"
        "   - 各発言行の先頭は必ず `[MM:SS] [話者X (名前)]: 発言内容` のフォーマットで統一してください。\n"
        "     （例: `[00:00] [話者1 (深井)]: 幻会議の幻会議。`、`[00:04] [話者2 (けんすう)]: はい、...`、`[00:30] [話者3 (川邊)]: よろしくお願いします。`）\n"
        "   - タイトルコール、自己紹介、会話内の呼びかけ（「〇〇さん」等）から話者の名前を特定し、必ず【話者番号＋括弧内に名前】を付けてください。名前が判明しない場合のみ `[話者1]` や `[話者2]` とし、会話途中で名前が判明した場合は以降その名前付きラベルを使用してください。\n"
        "   - 【話者膠着・偏りの禁止】会話の中で一方の話者が長く語る場合でも、相手からの相槌（「うん」「はい」「なるほど」「確かに」など）や短い質問が必ず挟まれます。相槌や短いリアクションであっても声が変わっていれば必ず相手側の話者ラベルに切り替えてください。直前の話者ラベルの慣性に引きずられて、1人の話者ラベル（例: [話者2] のみ）が何分間も連続して出力される状態は絶対に避けてください。\n"
        "2. おおよそのタイムスタンプ（例: [00:15]）を各発言行に付与してください（この音声区間の開始位置からの相対時間で記述してください）。\n"
        "3. 会話内容を要約・省略せず、発言内容を忠実に漏れなく書き起こしてください。\n"
        "4. 【リピートループ禁止】同一単語やフレーズの異常な繰り返し（リピートループ・幻覚）は絶対に避けてください。言い淀みは自然に整形してください。\n"
    )
    return prompt

