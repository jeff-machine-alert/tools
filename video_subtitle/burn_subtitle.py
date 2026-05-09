#!/usr/bin/env python3
"""Burn, extract, or translate-and-mux subtitle tracks from an MKV video file."""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

# Maps ffmpeg codec names to the file extension used when extracting.
# Image-based formats (dvd_subtitle, hdmv_pgs_subtitle) are exported as
# .sup (binary PGS), which cannot be edited as plain text.
CODEC_TO_EXT: dict[str, str] = {
    "ass":                ".ass",
    "ssa":                ".ass",
    "subrip":             ".srt",
    "mov_text":           ".srt",
    "webvtt":             ".vtt",
    "dvd_subtitle":       ".sup",
    "hdmv_pgs_subtitle":  ".sup",
}
IMAGE_BASED_CODECS = {"dvd_subtitle", "hdmv_pgs_subtitle"}

# ISO 639-2 language codes → Google Translate language codes
LANG_MAP: dict[str, str] = {
    "chi": "zh-CN", "zho": "zh-CN",
    "jpn": "ja",
    "kor": "ko",
    "fre": "fr", "fra": "fr",
    "ger": "de", "deu": "de",
    "spa": "es",
    "por": "pt",
    "ita": "it",
    "rus": "ru",
    "eng": "en",
}


# ---------------------------------------------------------------------------
# ffprobe helpers
# ---------------------------------------------------------------------------

def get_subtitles(video_path: str) -> list[dict]:
    """Return a list of subtitle streams from the video file."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "s",
        "-show_entries", "stream=index,codec_name:stream_tags=language,title",
        "-of", "json",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")

    streams = json.loads(result.stdout).get("streams", [])
    subtitles = []
    for i, s in enumerate(streams):
        tags = s.get("tags", {})
        subtitles.append({
            "stream_index": s["index"],
            "subtitle_index": i,
            "codec": s.get("codec_name", "unknown"),
            "language": tags.get("language", ""),
            "title": tags.get("title", ""),
        })
    return subtitles


def get_duration(video_path: str) -> float:
    """Return the video duration in seconds."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")
    return float(result.stdout.strip())


# ---------------------------------------------------------------------------
# Display / prompts
# ---------------------------------------------------------------------------

def display_subtitles(subtitles: list[dict]) -> None:
    print(f"\n{'#':<4} {'Stream':<8} {'Codec':<22} {'Lang':<6} {'Type':<7} {'Title'}")
    print("-" * 72)
    for sub in subtitles:
        sub_type = "image" if sub["codec"] in IMAGE_BASED_CODECS else "text"
        print(
            f"{sub['subtitle_index']:<4} "
            f"{sub['stream_index']:<8} "
            f"{sub['codec']:<22} "
            f"{sub['language']:<6} "
            f"{sub_type:<7} "
            f"{sub['title']}"
        )
    print()


def prompt_selection(subtitles: list[dict]) -> dict:
    """Ask the user which subtitle to use. Returns the chosen subtitle dict."""
    while True:
        raw = input(f"Enter subtitle # (0–{len(subtitles) - 1}): ").strip()
        if raw.isdigit():
            idx = int(raw)
            if 0 <= idx < len(subtitles):
                return subtitles[idx]
        print(f"  Invalid input. Please enter a number between 0 and {len(subtitles) - 1}.")


def prompt_action() -> str:
    """Ask the user what to do. Returns 'burn', 'extract', or 'translate'."""
    while True:
        print("What would you like to do?")
        print("  1. Burn subtitle into video")
        print("  2. Extract subtitle to file")
        print("  3. Translate subtitle and add to video")
        choice = input("Enter 1, 2, or 3: ").strip()
        if choice == "1":
            return "burn"
        if choice == "2":
            return "extract"
        if choice == "3":
            return "translate"
        print("  Invalid input. Please enter 1, 2, or 3.")


# ---------------------------------------------------------------------------
# Output path builders
# ---------------------------------------------------------------------------

def build_output_path(input_path: str) -> str:
    """<dir>/<stem>_subbed.mkv — for burn."""
    p = Path(input_path)
    return str(p.with_stem(p.stem + "_subbed"))


def build_subtitle_path(video_path: str, subtitle: dict) -> str:
    """<dir>/<stem>.<title or subN>.<ext> — for extract."""
    p = Path(video_path)
    ext = CODEC_TO_EXT.get(subtitle["codec"], ".ass")
    label = subtitle["title"] or f"sub{subtitle['subtitle_index']}"
    return str(p.parent / f"{p.stem}.{label}{ext}")


def build_translated_subtitle_path(video_path: str, subtitle: dict, dest_lang: str) -> str:
    """<dir>/<stem>.<title or subN>.<dest_lang>.<ext> — for translated subtitle."""
    p = Path(video_path)
    ext = CODEC_TO_EXT.get(subtitle["codec"], ".ass")
    label = subtitle["title"] or f"sub{subtitle['subtitle_index']}"
    return str(p.parent / f"{p.stem}.{label}.{dest_lang}{ext}")


def build_mux_output_path(video_path: str) -> str:
    """<dir>/<stem>_translated.mkv — for mux output."""
    p = Path(video_path)
    return str(p.with_stem(p.stem + "_translated"))


def _debug_path(path: str) -> str:
    """Insert '_debug' before the extension so debug outputs are clearly labelled."""
    p = Path(path)
    return str(p.with_stem(p.stem + "_debug"))


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def _parse_time(time_str: str) -> float:
    """Convert HH:MM:SS.ss string to total seconds."""
    h, m, s = time_str.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _format_time(seconds: float) -> str:
    """Format total seconds as HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _print_progress(line: str, duration: float) -> None:
    """Parse an ffmpeg progress line and print a real-time status update."""
    time_match = re.search(r"time=(\d+:\d+:\d+\.\d+)", line)
    if not time_match:
        return
    fps_match = re.search(r"fps=\s*(\S+)", line)
    speed_match = re.search(r"speed=\s*(\S+)", line)
    size_match = re.search(r"size=\s*(\S+)", line)

    current = _parse_time(time_match.group(1))
    pct = min(100.0, current / duration * 100) if duration else 0.0
    fps = fps_match.group(1) if fps_match else "?"
    speed = speed_match.group(1) if speed_match else "?"
    size = size_match.group(1) if size_match else "?"

    print(
        f"\r  {pct:5.1f}% | {time_match.group(1)} / {_format_time(duration)}"
        f" | FPS: {fps:>6} | Speed: {speed:>6} | Size: {size:>10}",
        end="",
        flush=True,
    )


def _run_ffmpeg(cmd: list[str], duration: float) -> None:
    """Run an ffmpeg command and stream live progress to stdout."""
    process = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)
    buf = ""
    while True:
        ch = process.stderr.read(1)
        if not ch:
            break
        if ch in ("\r", "\n"):
            if buf.strip():
                _print_progress(buf.strip(), duration)
            buf = ""
        else:
            buf += ch
    process.wait()
    print()
    if process.returncode != 0:
        raise RuntimeError("ffmpeg failed.")


# ---------------------------------------------------------------------------
# Core actions
# ---------------------------------------------------------------------------

def burn_subtitle(
    video_path: str,
    subtitle_index: int,
    output_path: str,
    debug_seconds: int | None = None,
) -> None:
    """Re-encode video with the chosen subtitle burned in, showing live progress."""
    duration = min(get_duration(video_path), debug_seconds) if debug_seconds else get_duration(video_path)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"subtitles={video_path}:si={subtitle_index}",
        "-c:v", "libx264", "-crf", "20", "-preset", "medium",
        "-c:a", "copy",
        "-map", "0:v:0",
        "-map", "0:a",
    ]
    if debug_seconds:
        cmd += ["-t", str(debug_seconds)]
    cmd.append(output_path)
    print(f"\nEncoding — duration: {_format_time(duration)}\n")
    _run_ffmpeg(cmd, duration)


def extract_subtitle(
    video_path: str,
    subtitle_index: int,
    output_path: str,
    debug_seconds: int | None = None,
) -> None:
    """Extract a single subtitle stream to a file, showing live progress."""
    duration = min(get_duration(video_path), debug_seconds) if debug_seconds else get_duration(video_path)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-map", f"0:s:{subtitle_index}",
    ]
    if debug_seconds:
        cmd += ["-t", str(debug_seconds)]
    cmd.append(output_path)
    print(f"\nExtracting subtitle — duration: {_format_time(duration)}\n")
    _run_ffmpeg(cmd, duration)


def strip_ass_tags(text: str) -> str:
    """Remove ASS inline style tags and normalize line break escapes to spaces."""
    text = re.sub(r"\{[^}]*\}", "", text)
    return text.replace(r"\N", " ").replace(r"\n", " ").replace(r"\h", " ").strip()


def translate_lines(
    lines: list[str],
    src: str = "zh-CN",
    dest: str = "en",
    batch_size: int = 30,
) -> list[str]:
    """Translate a list of strings via Google Translate, batching to avoid rate limits.

    Requires: pip install deep-translator
    """
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        raise RuntimeError("Missing dependency: pip install deep-translator")

    results = list(lines)
    non_empty = [(i, line) for i, line in enumerate(lines) if line.strip()]
    total = len(non_empty)
    n_batches = (total + batch_size - 1) // batch_size if total else 0

    for batch_num, start in enumerate(range(0, total, batch_size), 1):
        chunk = non_empty[start : start + batch_size]
        indices = [i for i, _ in chunk]
        texts = [t for _, t in chunk]

        print(
            f"\r  Batch {batch_num}/{n_batches} "
            f"({min(start + batch_size, total)}/{total} lines)...",
            end="",
            flush=True,
        )

        try:
            translated = GoogleTranslator(source=src, target=dest).translate("\n".join(texts))
            parts = [p.strip() for p in translated.split("\n")]
            if len(parts) == len(texts):
                for i, part in zip(indices, parts):
                    results[i] = part
            else:
                # Line count mismatch — fall back to one-by-one
                for i, text in zip(indices, texts):
                    results[i] = GoogleTranslator(source=src, target=dest).translate(text)
                    time.sleep(0.05)
        except Exception as e:
            print(f"\n  [warn] Batch {batch_num} failed ({e}), retrying line by line...")
            for i, text in zip(indices, texts):
                try:
                    results[i] = GoogleTranslator(source=src, target=dest).translate(text)
                except Exception as e2:
                    print(f"\n  [warn] Line {i} failed ({e2}), keeping original.")
                time.sleep(0.05)

        if start + batch_size < total:
            time.sleep(0.5)

    if n_batches:
        print()
    return results


def translate_ass_file(
    input_path: str,
    output_path: str,
    src: str = "zh-CN",
    dest: str = "en",
) -> None:
    """Parse an ASS file, translate all dialogue lines, and write a new ASS file.

    Requires: pip install ass
    """
    try:
        import ass
    except ImportError:
        raise RuntimeError("Missing dependency: pip install ass")

    with open(input_path, encoding="utf-8-sig") as f:
        doc = ass.parse(f)

    dialogues = [e for e in doc.events if isinstance(e, ass.Dialogue)]
    raw_texts = [strip_ass_tags(d.text) for d in dialogues]

    print(f"Translating {len(dialogues)} lines ({src} → {dest})...")
    translated = translate_lines(raw_texts, src=src, dest=dest)

    for d, text in zip(dialogues, translated):
        d.text = text

    with open(output_path, "w", encoding="utf-8-sig") as f:
        doc.dump_file(f)


def mux_subtitle(
    video_path: str,
    subtitle_path: str,
    output_path: str,
    title: str = "",
    language: str = "eng",
    debug_seconds: int | None = None,
) -> None:
    """Add a subtitle file to the video as a new stream without re-encoding.

    The new subtitle is set as the default; all existing subtitle defaults are cleared
    so the player auto-selects the newly added track.
    """
    existing_subs = get_subtitles(video_path)
    new_idx = len(existing_subs)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", subtitle_path,
        "-map", "0",
        "-map", "1:0",
        "-c", "copy",
    ]
    if title:
        cmd += [f"-metadata:s:s:{new_idx}", f"title={title}"]
    if language:
        cmd += [f"-metadata:s:s:{new_idx}", f"language={language}"]
    # Clear default from every existing subtitle stream, then mark only the new one.
    for i in range(len(existing_subs)):
        cmd += [f"-disposition:s:{i}", "none"]
    cmd += [f"-disposition:s:{new_idx}", "default"]
    if debug_seconds:
        cmd += ["-t", str(debug_seconds)]
    cmd.append(output_path)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed: {result.stderr.strip()}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Burn, extract, or translate subtitle tracks from an MKV file."
    )
    parser.add_argument("video", help="Input MKV file")
    parser.add_argument(
        "--debug",
        type=float,
        metavar="MINUTES",
        help="Process only the first N minutes (for testing). Adds '_debug' to output filenames.",
    )
    parsed = parser.parse_args(argv)

    video_path = parsed.video
    debug_seconds = int(parsed.debug * 60) if parsed.debug else None

    if not Path(video_path).exists():
        print(f"Error: file not found: {video_path}")
        sys.exit(1)

    if debug_seconds:
        print(f"[DEBUG] Processing first {_format_time(debug_seconds)} only.\n")

    print(f"Scanning subtitles in: {video_path}")
    subtitles = get_subtitles(video_path)

    if not subtitles:
        print("No subtitle tracks found in this file.")
        sys.exit(0)

    display_subtitles(subtitles)
    chosen = prompt_selection(subtitles)
    action = prompt_action()

    print(f"\nSelected: [{chosen['subtitle_index']}] {chosen['title']} ({chosen['language']})")

    if action == "burn":
        output_path = build_output_path(video_path)
        if debug_seconds:
            output_path = _debug_path(output_path)
        print(f"Output:   {output_path}\n")
        burn_subtitle(video_path, chosen["subtitle_index"], output_path, debug_seconds)

    elif action == "extract":
        output_path = build_subtitle_path(video_path, chosen)
        if debug_seconds:
            output_path = _debug_path(output_path)
        if chosen["codec"] in IMAGE_BASED_CODECS:
            print(
                f"  Note: '{chosen['codec']}' is image-based. "
                "The .sup file cannot be edited as plain text."
            )
        print(f"Output:   {output_path}\n")
        extract_subtitle(video_path, chosen["subtitle_index"], output_path, debug_seconds)

    elif action == "translate":
        if chosen["codec"] in IMAGE_BASED_CODECS:
            text_tracks = [s for s in subtitles if s["codec"] not in IMAGE_BASED_CODECS]
            hint = ""
            if text_tracks:
                nums = ", ".join(str(s["subtitle_index"]) for s in text_tracks)
                hint = f" Text-based tracks (marked 'text'): #{nums}."
            print(f"Error: '{chosen['codec']}' is image-based and cannot be translated as text.{hint}")
            sys.exit(1)

        src_lang = LANG_MAP.get(chosen["language"]) or "auto"
        dest_lang = "en"

        # Step 1 — extract
        extracted_path = build_subtitle_path(video_path, chosen)
        if debug_seconds:
            extracted_path = _debug_path(extracted_path)
        print(f"Extracting to: {extracted_path}\n")
        extract_subtitle(video_path, chosen["subtitle_index"], extracted_path, debug_seconds)

        # Step 2 — translate
        translated_path = build_translated_subtitle_path(video_path, chosen, dest_lang)
        if debug_seconds:
            translated_path = _debug_path(translated_path)
        print(f"Translating to: {translated_path}")
        translate_ass_file(extracted_path, translated_path, src=src_lang, dest=dest_lang)
        print(f"Saved: {translated_path}")

        # Step 3 — mux back into video
        output_path = build_mux_output_path(video_path)
        if debug_seconds:
            output_path = _debug_path(output_path)
        print(f"\nMuxing into video → {output_path}")
        mux_subtitle(
            video_path, translated_path, output_path,
            title="English (Translated)", language="eng",
            debug_seconds=debug_seconds,
        )

    print(f"\nDone. Output saved to: {output_path}")


if __name__ == "__main__":
    main()
