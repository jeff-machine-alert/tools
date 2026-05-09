# video_subtitle — Burn, Extract, and Translate MKV Subtitles

## Goal

MKV files often contain multiple embedded subtitle tracks that web-based players (such as PiGallery2) cannot render, because the HTML5 `<video>` element only supports WebVTT subtitles and browser-side ASS/DVD image rendering is not available. This script solves that with three actions:

- **Burn** — hardcode a subtitle track into the video pixels (universal playback)
- **Extract** — save a subtitle track to a standalone file for editing or inspection
- **Translate** — translate a subtitle track to English and mux it back into the video as a new stream (no re-encode)

## Implementation

The script is built around two CLI tools that must be available on the system:

- **ffprobe** — inspects the video file and returns subtitle stream metadata in JSON format.
- **ffmpeg** — re-encodes (burn), demuxes (extract), or stream-copies (mux) the video.

Translation uses **Google Translate** via the `deep-translator` Python package, and ASS file parsing uses the `ass` Python package.

### Key design decisions

| Decision | Reason |
|---|---|
| Re-encode with `libx264 -crf 20 -preset medium` | Balances output quality and encoding speed on ARM hardware |
| Copy audio with `-c:a copy` | Preserves all audio tracks without quality loss |
| Use ffmpeg `si=` (subtitle stream index) not global stream index | ffmpeg's `subtitles` filter counts only subtitle streams, starting from 0 |
| Mux translated subtitle with `-c copy` (stream copy) | No re-encode needed — adds the new track in seconds |
| Strip ASS tags before translating | Prevents the translator from corrupting or misreading style codes |
| Batch translation (30 lines/request) | Stays within Google Translate rate limits; falls back to line-by-line on count mismatch |
| Lazy import of `deep-translator` and `ass` | Burn and extract actions work without translation dependencies installed |
| Output files named next to the source | Keeps the original intact and makes outputs easy to locate |
| `-y` flag on all ffmpeg calls | Allows re-running without being blocked by an existing output file |

### File structure

```
video_subtitle/
├── burn_subtitle.py       # Main script
├── test_burn_subtitle.py  # Unit tests (93 tests)
└── README.md
```

### Functions

#### ffprobe helpers

| Function | Description |
|---|---|
| `get_subtitles(video_path)` | Calls `ffprobe` and returns a list of subtitle stream dicts (index, codec, language, title) |
| `get_duration(video_path)` | Calls `ffprobe` and returns the video duration in seconds |

#### Prompts

| Function | Description |
|---|---|
| `display_subtitles(subtitles)` | Prints a formatted table of available subtitle tracks |
| `prompt_selection(subtitles)` | Interactively asks the user to pick a subtitle by number |
| `prompt_action()` | Asks the user to choose burn, extract, or translate; returns `'burn'`, `'extract'`, or `'translate'` |

#### Output path builders

| Function | Output example |
|---|---|
| `build_output_path(video_path)` | `movie_subbed.mkv` |
| `build_subtitle_path(video_path, subtitle)` | `movie.诸神简体中文.ass` |
| `build_translated_subtitle_path(video_path, subtitle, dest_lang)` | `movie.诸神简体中文.en.ass` |
| `build_mux_output_path(video_path)` | `movie_translated.mkv` |

#### Core actions

| Function | Description |
|---|---|
| `burn_subtitle(video_path, subtitle_index, output_path)` | Re-encodes video with subtitle burned in; streams live ffmpeg progress |
| `extract_subtitle(video_path, subtitle_index, output_path)` | Extracts one subtitle stream to a file; streams live ffmpeg progress |
| `mux_subtitle(video_path, subtitle_path, output_path, title, language)` | Adds a subtitle file to the video as a new stream via stream copy (fast, no re-encode) |

#### Translation helpers

| Function | Description |
|---|---|
| `strip_ass_tags(text)` | Removes `{...}` inline style tags and normalises `\N`/`\n`/`\h` to spaces |
| `translate_lines(lines, src, dest, batch_size)` | Translates a list of strings via Google Translate, batching requests to avoid rate limits |
| `translate_ass_file(input_path, output_path, src, dest)` | Parses an ASS file, strips tags, translates all dialogue lines, and writes a new ASS file |

#### Supported languages (`LANG_MAP`)

Maps ISO 639-2 codes (as stored in MKV streams) to Google Translate language codes. Key entries:

| MKV code | Google code | Language |
|---|---|---|
| `chi` / `zho` | `zh-CN` | Chinese (Simplified) |
| `jpn` | `ja` | Japanese |
| `kor` | `ko` | Korean |
| `fre` / `fra` | `fr` | French |
| `eng` | `en` | English |

## Requirements

### Python

Python 3.10 or later is required (uses the `type | None` union syntax and `list[dict]` generics).

```bash
python3 --version   # must be 3.10+
```

Standard library modules used (`json`, `re`, `subprocess`, `sys`, `pathlib`, `time`) require no installation.

### System dependencies

Both tools must be installed and available in `PATH`.

| Tool | Purpose | Install (Debian/Ubuntu/Raspberry Pi OS) |
|---|---|---|
| `ffmpeg` | Burn, extract, and mux subtitle streams | `sudo apt install ffmpeg` |
| `ffprobe` | Reads subtitle/duration metadata from the video | included with `ffmpeg` package |

Verify they are present:

```bash
ffmpeg -version
ffprobe -version
```

### Python packages

| Package | Required for | Install |
|---|---|---|
| `deep-translator` | Translate action | `pip3 install deep-translator` |
| `ass` | Translate action (ASS file parsing) | `pip3 install ass` |
| `pytest` | Running the test suite | `pip3 install pytest` |

Install all at once:

```bash
pip3 install deep-translator ass pytest
```

Burn and extract work without `deep-translator` and `ass` installed. These are imported lazily and only raise an error if the translate action is actually used.

## Usage

```bash
python3 burn_subtitle.py /path/to/movie.mkv
```

The script scans subtitle tracks, lets you pick one, then asks what to do:

```
What would you like to do?
  1. Burn subtitle into video
  2. Extract subtitle to file
  3. Translate subtitle and add to video
```

### Example — burn

```
Selected: [3] 诸神简体中文 (chi)
Output:   /home/pi/data/2013-The_Wind_Rises_subbed.mkv

Encoding — total duration: 02:06:30

   45.2% | 00:57:01 / 02:06:30 | FPS:     23 | Speed:  0.97x | Size:   3072kB
```

The original MKV is not modified.

### Example — extract

```
Selected: [3] 诸神简体中文 (chi)
Output:   /home/pi/data/2013-The_Wind_Rises.诸神简体中文.ass
```

Produces a standalone `.ass` file that can be opened in a text editor or subtitle tool.

### Example — translate

```
Selected: [3] 诸神简体中文 (chi)

Extracting to: /home/pi/data/2013-The_Wind_Rises.诸神简体中文.ass
Translating to: /home/pi/data/2013-The_Wind_Rises.诸神简体中文.en.ass
Translating 1240 lines (zh-CN → en)...
  Batch 3/42 (90/1240 lines)...

Muxing into video → /home/pi/data/2013-The_Wind_Rises_translated.mkv

Done. Output saved to: /home/pi/data/2013-The_Wind_Rises_translated.mkv
```

The output MKV contains all original tracks plus the new English subtitle stream. No video or audio re-encoding occurs.

> **Note:** Image-based subtitle formats (`dvd_subtitle`, `hdmv_pgs_subtitle`) cannot be translated — the script will exit with an error if selected for the translate action.

## Running tests

```bash
cd /home/pi/machine.alert.repository/download/video_subtitle
python3 -m pytest test_burn_subtitle.py -v
```
