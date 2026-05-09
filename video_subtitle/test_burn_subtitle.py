"""Unit tests for burn_subtitle.py."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from burn_subtitle import (
    LANG_MAP,
    _debug_path,
    _format_time,
    _parse_time,
    _print_progress,
    build_mux_output_path,
    build_output_path,
    build_subtitle_path,
    build_translated_subtitle_path,
    burn_subtitle,
    display_subtitles,
    extract_subtitle,
    get_duration,
    get_subtitles,
    mux_subtitle,
    prompt_action,
    prompt_selection,
    strip_ass_tags,
    translate_ass_file,
    translate_lines,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

SAMPLE_FFPROBE_OUTPUT = {
    "streams": [
        {"index": 2, "codec_name": "ass",         "tags": {"language": "chi", "title": "诸神繁日"}},
        {"index": 3, "codec_name": "ass",         "tags": {"language": "chi", "title": "诸神简日"}},
        {"index": 4, "codec_name": "ass",         "tags": {"language": "chi", "title": "诸神繁体中文"}},
        {"index": 5, "codec_name": "ass",         "tags": {"language": "chi", "title": "诸神简体中文"}},
        {"index": 6, "codec_name": "dvd_subtitle","tags": {"language": "jpn", "title": "官方日文字幕"}},
        {"index": 7, "codec_name": "dvd_subtitle","tags": {"language": "eng", "title": "官方英文字幕"}},
    ]
}


def _make_run_result(returncode=0, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _make_popen_mock(stderr_chars: list[str], returncode: int = 0):
    process = MagicMock()
    process.stderr.read.side_effect = stderr_chars + [""]
    process.returncode = returncode
    return process


SAMPLE_PROGRESS = (
    "frame=  100 fps= 23 q=28.0 size=    1024kB "
    "time=00:00:04.17 bitrate=2009.8kbits/s speed=0.97x\r"
)


# ---------------------------------------------------------------------------
# get_subtitles
# ---------------------------------------------------------------------------

class TestGetSubtitles:
    def _ok(self):
        return _make_run_result(stdout=json.dumps(SAMPLE_FFPROBE_OUTPUT))

    @patch("burn_subtitle.subprocess.run")
    def test_returns_correct_count(self, mock_run):
        mock_run.return_value = self._ok()
        assert len(get_subtitles("video.mkv")) == 6

    @patch("burn_subtitle.subprocess.run")
    def test_subtitle_index_is_sequential(self, mock_run):
        mock_run.return_value = self._ok()
        assert [s["subtitle_index"] for s in get_subtitles("video.mkv")] == list(range(6))

    @patch("burn_subtitle.subprocess.run")
    def test_stream_index_preserved(self, mock_run):
        mock_run.return_value = self._ok()
        subs = get_subtitles("video.mkv")
        assert subs[0]["stream_index"] == 2
        assert subs[3]["stream_index"] == 5

    @patch("burn_subtitle.subprocess.run")
    def test_fields_populated(self, mock_run):
        mock_run.return_value = self._ok()
        s = get_subtitles("video.mkv")[3]
        assert s["codec"] == "ass"
        assert s["language"] == "chi"
        assert s["title"] == "诸神简体中文"

    @patch("burn_subtitle.subprocess.run")
    def test_empty_streams(self, mock_run):
        mock_run.return_value = _make_run_result(stdout=json.dumps({"streams": []}))
        assert get_subtitles("video.mkv") == []

    @patch("burn_subtitle.subprocess.run")
    def test_missing_tags_default_to_empty_string(self, mock_run):
        mock_run.return_value = _make_run_result(
            stdout=json.dumps({"streams": [{"index": 2, "codec_name": "ass"}]})
        )
        s = get_subtitles("video.mkv")[0]
        assert s["language"] == "" and s["title"] == ""

    @patch("burn_subtitle.subprocess.run")
    def test_raises_on_ffprobe_failure(self, mock_run):
        mock_run.return_value = _make_run_result(returncode=1, stderr="No such file")
        with pytest.raises(RuntimeError, match="ffprobe failed"):
            get_subtitles("missing.mkv")

    @patch("burn_subtitle.subprocess.run")
    def test_ffprobe_called_with_correct_args(self, mock_run):
        mock_run.return_value = self._ok()
        get_subtitles("/path/to/video.mkv")
        args = mock_run.call_args[0][0]
        assert args[0] == "ffprobe" and "/path/to/video.mkv" in args


# ---------------------------------------------------------------------------
# get_duration
# ---------------------------------------------------------------------------

class TestGetDuration:
    @patch("burn_subtitle.subprocess.run")
    def test_returns_float(self, mock_run):
        mock_run.return_value = _make_run_result(stdout="7590.293000\n")
        assert get_duration("video.mkv") == pytest.approx(7590.293)

    @patch("burn_subtitle.subprocess.run")
    def test_raises_on_failure(self, mock_run):
        mock_run.return_value = _make_run_result(returncode=1, stderr="error")
        with pytest.raises(RuntimeError, match="ffprobe failed"):
            get_duration("missing.mkv")

    @patch("burn_subtitle.subprocess.run")
    def test_ffprobe_called_with_correct_args(self, mock_run):
        mock_run.return_value = _make_run_result(stdout="100.0\n")
        get_duration("/path/video.mkv")
        args = mock_run.call_args[0][0]
        assert args[0] == "ffprobe" and "format=duration" in " ".join(args)


# ---------------------------------------------------------------------------
# _parse_time / _format_time
# ---------------------------------------------------------------------------

class TestParseTime:
    def test_hours_minutes_seconds(self):
        assert _parse_time("01:02:03.00") == pytest.approx(3723.0)

    def test_zero(self):
        assert _parse_time("00:00:00.00") == pytest.approx(0.0)

    def test_fractional_seconds(self):
        assert _parse_time("00:00:01.50") == pytest.approx(1.5)

    def test_large_value(self):
        assert _parse_time("02:06:30.00") == pytest.approx(7590.0)


class TestFormatTime:
    def test_zero(self):
        assert _format_time(0) == "00:00:00"

    def test_one_hour(self):
        assert _format_time(3600) == "01:00:00"

    def test_mixed(self):
        assert _format_time(7590) == "02:06:30"

    def test_sub_minute(self):
        assert _format_time(45) == "00:00:45"


# ---------------------------------------------------------------------------
# _print_progress
# ---------------------------------------------------------------------------

class TestPrintProgress:
    LINE = (
        "frame=  500 fps= 23 q=28.0 size=    5120kB "
        "time=00:00:20.87 bitrate=2009.8kbits/s speed=0.97x"
    )

    def test_prints_percentage(self, capsys):
        _print_progress(self.LINE, 100.0)
        assert "20.9%" in capsys.readouterr().out

    def test_prints_fps(self, capsys):
        _print_progress(self.LINE, 100.0)
        assert "23" in capsys.readouterr().out

    def test_prints_speed(self, capsys):
        _print_progress(self.LINE, 100.0)
        assert "0.97x" in capsys.readouterr().out

    def test_prints_size(self, capsys):
        _print_progress(self.LINE, 100.0)
        assert "5120kB" in capsys.readouterr().out

    def test_no_output_without_time_field(self, capsys):
        _print_progress("frame=100 fps=24", 100.0)
        assert capsys.readouterr().out == ""

    def test_percentage_capped_at_100(self, capsys):
        _print_progress(self.LINE, 1.0)
        assert "100.0%" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# display_subtitles
# ---------------------------------------------------------------------------

class TestDisplaySubtitles:
    SAMPLE = [
        {"subtitle_index": 0, "stream_index": 2, "codec": "ass", "language": "chi", "title": "诸神简体中文"},
        {"subtitle_index": 1, "stream_index": 6, "codec": "dvd_subtitle", "language": "eng", "title": "官方英文字幕"},
    ]

    def test_shows_all_entries(self, capsys):
        display_subtitles(self.SAMPLE)
        out = capsys.readouterr().out
        assert "诸神简体中文" in out and "官方英文字幕" in out

    def test_shows_codec_and_language(self, capsys):
        display_subtitles(self.SAMPLE)
        out = capsys.readouterr().out
        assert "ass" in out and "chi" in out and "eng" in out

    def test_shows_subtitle_indices(self, capsys):
        display_subtitles(self.SAMPLE)
        out = capsys.readouterr().out
        assert "0" in out and "1" in out

    def test_text_type_for_ass(self, capsys):
        display_subtitles(self.SAMPLE)
        out = capsys.readouterr().out
        assert "text" in out

    def test_image_type_for_dvd_subtitle(self, capsys):
        display_subtitles(self.SAMPLE)
        out = capsys.readouterr().out
        assert "image" in out

    def test_type_column_header_present(self, capsys):
        display_subtitles(self.SAMPLE)
        out = capsys.readouterr().out
        assert "Type" in out


# ---------------------------------------------------------------------------
# prompt_selection
# ---------------------------------------------------------------------------

class TestPromptSelection:
    SUBS = [
        {"subtitle_index": 0, "title": "Sub A"},
        {"subtitle_index": 1, "title": "Sub B"},
        {"subtitle_index": 2, "title": "Sub C"},
    ]

    def test_valid_first_input(self):
        with patch("builtins.input", return_value="1"):
            assert prompt_selection(self.SUBS) == self.SUBS[1]

    def test_boundary_zero(self):
        with patch("builtins.input", return_value="0"):
            assert prompt_selection(self.SUBS) == self.SUBS[0]

    def test_boundary_last(self):
        with patch("builtins.input", return_value="2"):
            assert prompt_selection(self.SUBS) == self.SUBS[2]

    def test_invalid_then_valid(self):
        with patch("builtins.input", side_effect=["99", "abc", "1"]):
            assert prompt_selection(self.SUBS) == self.SUBS[1]

    def test_negative_rejected(self):
        with patch("builtins.input", side_effect=["-1", "0"]):
            assert prompt_selection(self.SUBS) == self.SUBS[0]


# ---------------------------------------------------------------------------
# prompt_action
# ---------------------------------------------------------------------------

class TestPromptAction:
    def test_returns_burn_for_1(self):
        with patch("builtins.input", return_value="1"):
            assert prompt_action() == "burn"

    def test_returns_extract_for_2(self):
        with patch("builtins.input", return_value="2"):
            assert prompt_action() == "extract"

    def test_returns_translate_for_3(self):
        with patch("builtins.input", return_value="3"):
            assert prompt_action() == "translate"

    def test_invalid_then_valid(self):
        with patch("builtins.input", side_effect=["0", "x", "burn", "2"]):
            assert prompt_action() == "extract"


# ---------------------------------------------------------------------------
# build_output_path
# ---------------------------------------------------------------------------

class TestBuildOutputPath:
    def test_appends_subbed_suffix(self):
        assert build_output_path("/data/movie.mkv") == "/data/movie_subbed.mkv"

    def test_preserves_directory(self):
        assert build_output_path("/home/pi/film.mkv").startswith("/home/pi/")

    def test_preserves_extension(self):
        assert build_output_path("/data/film.mkv").endswith(".mkv")

    def test_stem_with_dots(self):
        assert build_output_path("/data/film.2024.mkv") == "/data/film.2024_subbed.mkv"


# ---------------------------------------------------------------------------
# build_subtitle_path
# ---------------------------------------------------------------------------

class TestBuildSubtitlePath:
    def test_ass_extension(self):
        sub = {"codec": "ass", "subtitle_index": 3, "title": "简体中文"}
        assert build_subtitle_path("/data/movie.mkv", sub).endswith(".ass")

    def test_srt_extension(self):
        sub = {"codec": "subrip", "subtitle_index": 0, "title": "English"}
        assert build_subtitle_path("/data/movie.mkv", sub).endswith(".srt")

    def test_dvd_gets_sup(self):
        sub = {"codec": "dvd_subtitle", "subtitle_index": 4, "title": "日本語"}
        assert build_subtitle_path("/data/movie.mkv", sub).endswith(".sup")

    def test_unknown_codec_defaults_to_ass(self):
        sub = {"codec": "unknown", "subtitle_index": 0, "title": "Test"}
        assert build_subtitle_path("/data/movie.mkv", sub).endswith(".ass")

    def test_title_used_as_label(self):
        sub = {"codec": "ass", "subtitle_index": 3, "title": "简体中文"}
        assert "简体中文" in build_subtitle_path("/data/movie.mkv", sub)

    def test_fallback_label_when_no_title(self):
        sub = {"codec": "ass", "subtitle_index": 3, "title": ""}
        assert "sub3" in build_subtitle_path("/data/movie.mkv", sub)

    def test_preserves_directory(self):
        sub = {"codec": "ass", "subtitle_index": 0, "title": "Sub"}
        assert build_subtitle_path("/home/pi/data/movie.mkv", sub).startswith("/home/pi/data/")

    def test_includes_video_stem(self):
        sub = {"codec": "ass", "subtitle_index": 0, "title": "Sub"}
        assert "movie" in build_subtitle_path("/data/movie.mkv", sub)


# ---------------------------------------------------------------------------
# build_translated_subtitle_path
# ---------------------------------------------------------------------------

class TestBuildTranslatedSubtitlePath:
    def test_includes_dest_lang(self):
        sub = {"codec": "ass", "subtitle_index": 3, "title": "简体中文"}
        result = build_translated_subtitle_path("/data/movie.mkv", sub, "en")
        assert ".en." in result

    def test_preserves_extension(self):
        sub = {"codec": "ass", "subtitle_index": 0, "title": "Sub"}
        assert build_translated_subtitle_path("/data/movie.mkv", sub, "en").endswith(".ass")

    def test_fallback_label_when_no_title(self):
        sub = {"codec": "ass", "subtitle_index": 2, "title": ""}
        assert "sub2" in build_translated_subtitle_path("/data/movie.mkv", sub, "en")

    def test_different_from_source_path(self):
        sub = {"codec": "ass", "subtitle_index": 0, "title": "Sub"}
        src = build_subtitle_path("/data/movie.mkv", sub)
        translated = build_translated_subtitle_path("/data/movie.mkv", sub, "en")
        assert src != translated


# ---------------------------------------------------------------------------
# build_mux_output_path
# ---------------------------------------------------------------------------

class TestBuildMuxOutputPath:
    def test_appends_translated_suffix(self):
        assert build_mux_output_path("/data/movie.mkv") == "/data/movie_translated.mkv"

    def test_preserves_directory(self):
        assert build_mux_output_path("/home/pi/film.mkv").startswith("/home/pi/")

    def test_preserves_extension(self):
        assert build_mux_output_path("/data/film.mkv").endswith(".mkv")


# ---------------------------------------------------------------------------
# strip_ass_tags
# ---------------------------------------------------------------------------

class TestStripAssTags:
    def test_removes_style_tags(self):
        assert strip_ass_tags(r"{\i1}Hello{\i0}") == "Hello"

    def test_removes_multiple_tags(self):
        assert strip_ass_tags(r"{\b1}Bold{\b0} and {\i1}italic{\i0}") == "Bold and italic"

    def test_replaces_hard_line_break(self):
        assert strip_ass_tags(r"Line one\NLine two") == "Line one Line two"

    def test_replaces_soft_line_break(self):
        assert strip_ass_tags(r"Line one\nLine two") == "Line one Line two"

    def test_replaces_hard_space(self):
        assert strip_ass_tags(r"Hello\hWorld") == "Hello World"

    def test_plain_text_unchanged(self):
        assert strip_ass_tags("Hello world") == "Hello world"

    def test_strips_surrounding_whitespace(self):
        assert strip_ass_tags("  Hello  ") == "Hello"

    def test_empty_string(self):
        assert strip_ass_tags("") == ""

    def test_complex_tag_with_args(self):
        assert strip_ass_tags(r"{\pos(320,50)}Text") == "Text"


# ---------------------------------------------------------------------------
# translate_lines
# ---------------------------------------------------------------------------

class TestTranslateLines:
    def _make_translator(self, return_value):
        mock = MagicMock()
        mock.return_value.translate.return_value = return_value
        return mock

    @patch("deep_translator.GoogleTranslator")
    def test_returns_same_count(self, mock_gt):
        mock_gt.return_value.translate.return_value = "Hello\nGoodbye"
        result = translate_lines(["你好", "再见"], src="zh-CN", dest="en")
        assert len(result) == 2

    @patch("deep_translator.GoogleTranslator")
    def test_translated_text_used(self, mock_gt):
        mock_gt.return_value.translate.return_value = "Hello\nGoodbye"
        result = translate_lines(["你好", "再见"])
        assert result == ["Hello", "Goodbye"]

    @patch("deep_translator.GoogleTranslator")
    def test_empty_lines_preserved(self, mock_gt):
        mock_gt.return_value.translate.return_value = "Hello"
        result = translate_lines(["你好", ""])
        assert result[1] == ""

    @patch("deep_translator.GoogleTranslator")
    def test_fallback_on_line_count_mismatch(self, mock_gt):
        # First batch call returns wrong count, fallback translates individually
        mock_gt.return_value.translate.side_effect = [
            "Hello World",  # merged — wrong count
            "Hello",        # individual fallback for line 0
            "Goodbye",      # individual fallback for line 1
        ]
        result = translate_lines(["你好", "再见"])
        assert len(result) == 2

    @patch("deep_translator.GoogleTranslator")
    def test_empty_input_returns_empty(self, mock_gt):
        result = translate_lines([])
        assert result == []
        mock_gt.assert_not_called()

    def test_raises_on_missing_dependency(self):
        with patch.dict("sys.modules", {"deep_translator": None}):
            with pytest.raises((RuntimeError, ImportError)):
                translate_lines(["你好"])


# ---------------------------------------------------------------------------
# translate_ass_file
# ---------------------------------------------------------------------------

class TestTranslateAssFile:
    MINIMAL_ASS = (
        "[Script Info]\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, Bold, Italic,"
        " Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline,"
        " Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,你好世界\n"
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,再见\n"
    )

    def test_translates_dialogue_text(self, tmp_path):
        input_file = tmp_path / "input.ass"
        output_file = tmp_path / "output.ass"
        input_file.write_text(self.MINIMAL_ASS, encoding="utf-8-sig")

        with patch("burn_subtitle.translate_lines", return_value=["Hello World", "Goodbye"]):
            translate_ass_file(str(input_file), str(output_file))

        content = output_file.read_text(encoding="utf-8-sig")
        assert "Hello World" in content
        assert "Goodbye" in content

    def test_output_is_valid_ass(self, tmp_path):
        import ass
        input_file = tmp_path / "input.ass"
        output_file = tmp_path / "output.ass"
        input_file.write_text(self.MINIMAL_ASS, encoding="utf-8-sig")

        with patch("burn_subtitle.translate_lines", return_value=["Hello", "Bye"]):
            translate_ass_file(str(input_file), str(output_file))

        with open(output_file, encoding="utf-8-sig") as f:
            doc = ass.parse(f)
        dialogues = [e for e in doc.events if isinstance(e, ass.Dialogue)]
        assert len(dialogues) == 2

    def test_translate_lines_called_with_stripped_texts(self, tmp_path):
        input_file = tmp_path / "input.ass"
        output_file = tmp_path / "output.ass"
        # Add ASS tags to dialogue text
        tagged_ass = self.MINIMAL_ASS.replace("你好世界", r"{\i1}你好世界{\i0}")
        input_file.write_text(tagged_ass, encoding="utf-8-sig")

        with patch("burn_subtitle.translate_lines", return_value=["Hello", "Bye"]) as mock_tl:
            translate_ass_file(str(input_file), str(output_file))

        passed_texts = mock_tl.call_args[0][0]
        assert all("{" not in t for t in passed_texts)

    def test_raises_on_missing_ass_dependency(self):
        with patch.dict("sys.modules", {"ass": None}):
            with pytest.raises((RuntimeError, ImportError)):
                translate_ass_file("in.ass", "out.ass")


# ---------------------------------------------------------------------------
# mux_subtitle
# ---------------------------------------------------------------------------

class TestMuxSubtitle:
    @patch("burn_subtitle.get_subtitles", return_value=[{}, {}, {}])  # 3 existing subs
    @patch("burn_subtitle.subprocess.run")
    def test_calls_ffmpeg(self, mock_run, _):
        mock_run.return_value = _make_run_result()
        mux_subtitle("/data/video.mkv", "/data/en.ass", "/data/out.mkv")
        assert mock_run.call_args[0][0][0] == "ffmpeg"

    @patch("burn_subtitle.get_subtitles", return_value=[])
    @patch("burn_subtitle.subprocess.run")
    def test_maps_all_original_streams(self, mock_run, _):
        mock_run.return_value = _make_run_result()
        mux_subtitle("/data/video.mkv", "/data/en.ass", "/data/out.mkv")
        args = mock_run.call_args[0][0]
        assert "-map" in args and "0" in args

    @patch("burn_subtitle.get_subtitles", return_value=[])
    @patch("burn_subtitle.subprocess.run")
    def test_maps_subtitle_file(self, mock_run, _):
        mock_run.return_value = _make_run_result()
        mux_subtitle("/data/video.mkv", "/data/en.ass", "/data/out.mkv")
        args = mock_run.call_args[0][0]
        assert "1:0" in args

    @patch("burn_subtitle.get_subtitles", return_value=[])
    @patch("burn_subtitle.subprocess.run")
    def test_stream_copy(self, mock_run, _):
        mock_run.return_value = _make_run_result()
        mux_subtitle("/data/video.mkv", "/data/en.ass", "/data/out.mkv")
        args = mock_run.call_args[0][0]
        assert "-c" in args and "copy" in args

    @patch("burn_subtitle.get_subtitles", return_value=[{}, {}])
    @patch("burn_subtitle.subprocess.run")
    def test_metadata_uses_correct_index(self, mock_run, _):
        mock_run.return_value = _make_run_result()
        mux_subtitle("/data/video.mkv", "/data/en.ass", "/data/out.mkv", title="English")
        args = mock_run.call_args[0][0]
        assert "-metadata:s:s:2" in args  # 2 existing subs → new one at index 2

    @patch("burn_subtitle.get_subtitles", return_value=[{}, {}])
    @patch("burn_subtitle.subprocess.run")
    def test_new_subtitle_set_as_default(self, mock_run, _):
        mock_run.return_value = _make_run_result()
        mux_subtitle("/data/video.mkv", "/data/en.ass", "/data/out.mkv")
        args = mock_run.call_args[0][0]
        # new subtitle is at index 2 (after 2 existing); must be marked default
        assert "-disposition:s:2" in args
        idx = args.index("-disposition:s:2")
        assert args[idx + 1] == "default"

    @patch("burn_subtitle.get_subtitles", return_value=[{}, {}])
    @patch("burn_subtitle.subprocess.run")
    def test_existing_subtitle_defaults_cleared(self, mock_run, _):
        mock_run.return_value = _make_run_result()
        mux_subtitle("/data/video.mkv", "/data/en.ass", "/data/out.mkv")
        args = mock_run.call_args[0][0]
        # Both existing streams (0 and 1) must have their defaults cleared
        assert "-disposition:s:0" in args
        assert args[args.index("-disposition:s:0") + 1] == "none"
        assert "-disposition:s:1" in args
        assert args[args.index("-disposition:s:1") + 1] == "none"

    @patch("burn_subtitle.get_subtitles", return_value=[])
    @patch("burn_subtitle.subprocess.run")
    def test_overwrite_flag_set(self, mock_run, _):
        mock_run.return_value = _make_run_result()
        mux_subtitle("/data/video.mkv", "/data/en.ass", "/data/out.mkv")
        assert "-y" in mock_run.call_args[0][0]

    @patch("burn_subtitle.get_subtitles", return_value=[])
    @patch("burn_subtitle.subprocess.run")
    def test_raises_on_failure(self, mock_run, _):
        mock_run.return_value = _make_run_result(returncode=1, stderr="error")
        with pytest.raises(RuntimeError, match="ffmpeg mux failed"):
            mux_subtitle("/data/video.mkv", "/data/en.ass", "/data/out.mkv")


# ---------------------------------------------------------------------------
# burn_subtitle
# ---------------------------------------------------------------------------

class TestBurnSubtitle:
    @patch("burn_subtitle.get_duration", return_value=100.0)
    @patch("burn_subtitle.subprocess.Popen")
    def test_calls_ffmpeg(self, mock_popen, _):
        mock_popen.return_value = _make_popen_mock([])
        burn_subtitle("/data/video.mkv", 3, "/data/out.mkv")
        assert mock_popen.call_args[0][0][0] == "ffmpeg"

    @patch("burn_subtitle.get_duration", return_value=100.0)
    @patch("burn_subtitle.subprocess.Popen")
    def test_subtitle_index_in_vf(self, mock_popen, _):
        mock_popen.return_value = _make_popen_mock([])
        burn_subtitle("/data/video.mkv", 3, "/data/out.mkv")
        args = mock_popen.call_args[0][0]
        assert "si=3" in args[args.index("-vf") + 1]

    @patch("burn_subtitle.get_duration", return_value=100.0)
    @patch("burn_subtitle.subprocess.Popen")
    def test_audio_copied(self, mock_popen, _):
        mock_popen.return_value = _make_popen_mock([])
        burn_subtitle("/data/video.mkv", 0, "/data/out.mkv")
        args = mock_popen.call_args[0][0]
        assert args[args.index("-c:a") + 1] == "copy"

    @patch("burn_subtitle.get_duration", return_value=100.0)
    @patch("burn_subtitle.subprocess.Popen")
    def test_raises_on_failure(self, mock_popen, _):
        mock_popen.return_value = _make_popen_mock([], returncode=1)
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            burn_subtitle("/data/video.mkv", 0, "/data/out.mkv")

    @patch("burn_subtitle.get_duration", return_value=100.0)
    @patch("burn_subtitle.subprocess.Popen")
    def test_progress_printed(self, mock_popen, _, capsys):
        mock_popen.return_value = _make_popen_mock(list(SAMPLE_PROGRESS))
        burn_subtitle("/data/video.mkv", 0, "/data/out.mkv")
        assert "%" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# extract_subtitle
# ---------------------------------------------------------------------------

class TestExtractSubtitle:
    @patch("burn_subtitle.get_duration", return_value=100.0)
    @patch("burn_subtitle.subprocess.Popen")
    def test_calls_ffmpeg(self, mock_popen, _):
        mock_popen.return_value = _make_popen_mock([])
        extract_subtitle("/data/video.mkv", 3, "/data/out.ass")
        assert mock_popen.call_args[0][0][0] == "ffmpeg"

    @patch("burn_subtitle.get_duration", return_value=100.0)
    @patch("burn_subtitle.subprocess.Popen")
    def test_maps_correct_subtitle_stream(self, mock_popen, _):
        mock_popen.return_value = _make_popen_mock([])
        extract_subtitle("/data/video.mkv", 3, "/data/out.ass")
        assert "0:s:3" in mock_popen.call_args[0][0]

    @patch("burn_subtitle.get_duration", return_value=100.0)
    @patch("burn_subtitle.subprocess.Popen")
    def test_raises_on_failure(self, mock_popen, _):
        mock_popen.return_value = _make_popen_mock([], returncode=1)
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            extract_subtitle("/data/video.mkv", 0, "/data/out.ass")


# ---------------------------------------------------------------------------
# LANG_MAP
# ---------------------------------------------------------------------------

class TestLangMap:
    def test_chi_maps_to_zh_cn(self):
        assert LANG_MAP["chi"] == "zh-CN"

    def test_jpn_maps_to_ja(self):
        assert LANG_MAP["jpn"] == "ja"

    def test_eng_maps_to_en(self):
        assert LANG_MAP["eng"] == "en"


# ---------------------------------------------------------------------------
# _debug_path
# ---------------------------------------------------------------------------

class TestDebugPath:
    def test_appends_debug_suffix(self):
        assert _debug_path("/data/movie_subbed.mkv") == "/data/movie_subbed_debug.mkv"

    def test_preserves_directory(self):
        assert _debug_path("/home/pi/out.mkv").startswith("/home/pi/")

    def test_preserves_extension(self):
        assert _debug_path("/data/file.ass").endswith(".ass")

    def test_works_on_ass_files(self):
        assert _debug_path("/data/movie.Sub.en.ass") == "/data/movie.Sub.en_debug.ass"


# ---------------------------------------------------------------------------
# debug_seconds in burn_subtitle / extract_subtitle / mux_subtitle
# ---------------------------------------------------------------------------

class TestBurnSubtitleDebug:
    @patch("burn_subtitle.get_duration", return_value=7590.0)
    @patch("burn_subtitle.subprocess.Popen")
    def test_t_flag_added_when_debug(self, mock_popen, _):
        mock_popen.return_value = _make_popen_mock([])
        burn_subtitle("/data/video.mkv", 0, "/data/out.mkv", debug_seconds=600)
        args = mock_popen.call_args[0][0]
        assert "-t" in args and "600" in args

    @patch("burn_subtitle.get_duration", return_value=7590.0)
    @patch("burn_subtitle.subprocess.Popen")
    def test_no_t_flag_without_debug(self, mock_popen, _):
        mock_popen.return_value = _make_popen_mock([])
        burn_subtitle("/data/video.mkv", 0, "/data/out.mkv")
        assert "-t" not in mock_popen.call_args[0][0]

    @patch("burn_subtitle.get_duration", return_value=7590.0)
    @patch("burn_subtitle.subprocess.Popen")
    def test_duration_capped_at_debug_seconds(self, mock_popen, mock_dur):
        mock_popen.return_value = _make_popen_mock([])
        # duration used for progress should be min(7590, 600) = 600
        burn_subtitle("/data/video.mkv", 0, "/data/out.mkv", debug_seconds=600)
        # If duration were 7590, progress % for time=00:00:10 would be tiny;
        # with cap at 600 it would be ~1.7%. We just verify -t is present.
        args = mock_popen.call_args[0][0]
        assert args[args.index("-t") + 1] == "600"


class TestExtractSubtitleDebug:
    @patch("burn_subtitle.get_duration", return_value=7590.0)
    @patch("burn_subtitle.subprocess.Popen")
    def test_t_flag_added_when_debug(self, mock_popen, _):
        mock_popen.return_value = _make_popen_mock([])
        extract_subtitle("/data/video.mkv", 0, "/data/out.ass", debug_seconds=600)
        args = mock_popen.call_args[0][0]
        assert "-t" in args and "600" in args

    @patch("burn_subtitle.get_duration", return_value=7590.0)
    @patch("burn_subtitle.subprocess.Popen")
    def test_no_t_flag_without_debug(self, mock_popen, _):
        mock_popen.return_value = _make_popen_mock([])
        extract_subtitle("/data/video.mkv", 0, "/data/out.ass")
        assert "-t" not in mock_popen.call_args[0][0]


class TestMuxSubtitleDebug:
    @patch("burn_subtitle.get_subtitles", return_value=[])
    @patch("burn_subtitle.subprocess.run")
    def test_t_flag_added_when_debug(self, mock_run, _):
        mock_run.return_value = _make_run_result()
        mux_subtitle("/data/video.mkv", "/data/en.ass", "/data/out.mkv", debug_seconds=600)
        args = mock_run.call_args[0][0]
        assert "-t" in args and "600" in args

    @patch("burn_subtitle.get_subtitles", return_value=[])
    @patch("burn_subtitle.subprocess.run")
    def test_no_t_flag_without_debug(self, mock_run, _):
        mock_run.return_value = _make_run_result()
        mux_subtitle("/data/video.mkv", "/data/en.ass", "/data/out.mkv")
        assert "-t" not in mock_run.call_args[0][0]
