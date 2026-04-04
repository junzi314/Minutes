"""Unit tests for src/vtt_parser.py (Zoom VTT parser)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.transcriber import Segment
from src.vtt_parser import parse_vtt, parse_vtt_file


# ---------------------------------------------------------------------------
# Sample VTT content
# ---------------------------------------------------------------------------

BASIC_VTT = """\
WEBVTT

1
00:00:01.000 --> 00:00:04.500
Hello, this is a test.

2
00:00:05.000 --> 00:00:08.200
Second segment here.
"""

MULTILINE_VTT = """\
WEBVTT

00:00:01.000 --> 00:00:05.000
This is line one
and this is line two
and line three.
"""

NO_HEADER_VTT = """\
00:00:00.000 --> 00:00:03.000
No WEBVTT header here.

00:00:04.000 --> 00:00:07.000
Still works fine.
"""

EMPTY_VTT = """\
WEBVTT
"""

MALFORMED_TIMESTAMP_VTT = """\
WEBVTT

00:00:01.000 --> 00:00:03.000
Good segment.

NOT_A_TIMESTAMP --> INVALID
Bad segment.

00:00:05.000 --> 00:00:07.000
Another good segment.
"""

BOM_VTT = "\ufeff" + """\
WEBVTT

00:00:01.000 --> 00:00:03.000
BOM content here.
"""

EMPTY_CUE_VTT = """\
WEBVTT

00:00:01.000 --> 00:00:03.000
Valid text.

00:00:04.000 --> 00:00:06.000


00:00:07.000 --> 00:00:09.000
After empty cue.
"""

REVERSED_TIMESTAMP_VTT = """\
WEBVTT

00:00:05.000 --> 00:00:02.000
Reversed timestamps.

00:00:06.000 --> 00:00:08.000
Valid after reversed.
"""

ZOOM_METADATA_VTT = """\
WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:03.000
Zoom format with metadata.

00:00:04.000 --> 00:00:06.000
Second segment.
"""


# ===========================================================================
# Tests
# ===========================================================================


class TestParseVttBasic:
    """Tests for parse_vtt with standard Zoom VTT format."""

    def test_parse_basic(self) -> None:
        """Standard VTT with numbered cues parses correctly."""
        segments = parse_vtt(BASIC_VTT)

        assert len(segments) == 2
        assert segments[0].start == 1.0
        assert segments[0].end == 4.5
        assert segments[0].text == "Hello, this is a test."
        assert segments[0].speaker == ""

        assert segments[1].start == 5.0
        assert segments[1].end == 8.2
        assert segments[1].text == "Second segment here."

    def test_parse_returns_segment_type(self) -> None:
        """Return values are Segment dataclass instances."""
        segments = parse_vtt(BASIC_VTT)

        assert all(isinstance(s, Segment) for s in segments)


class TestParseVttMultiline:
    def test_multiline_cue(self) -> None:
        """Multiline cues are joined with spaces."""
        segments = parse_vtt(MULTILINE_VTT)

        assert len(segments) == 1
        assert segments[0].text == "This is line one and this is line two and line three."


class TestParseVttEdgeCases:
    def test_empty_vtt(self) -> None:
        """Empty VTT (header only) returns empty list."""
        assert parse_vtt(EMPTY_VTT) == []

    def test_empty_string(self) -> None:
        """Completely empty string returns empty list."""
        assert parse_vtt("") == []

    def test_no_header(self) -> None:
        """VTT without WEBVTT header still parses."""
        segments = parse_vtt(NO_HEADER_VTT)

        assert len(segments) == 2
        assert segments[0].text == "No WEBVTT header here."
        assert segments[1].text == "Still works fine."

    def test_malformed_timestamp_skipped(self) -> None:
        """Malformed timestamps are skipped; valid cues still parse."""
        segments = parse_vtt(MALFORMED_TIMESTAMP_VTT)

        assert len(segments) == 2
        assert segments[0].text == "Good segment."
        assert segments[1].text == "Another good segment."

    def test_bom_removal(self) -> None:
        """BOM (U+FEFF) is stripped before parsing."""
        segments = parse_vtt(BOM_VTT)

        assert len(segments) == 1
        assert segments[0].text == "BOM content here."

    def test_empty_cue_skipped(self) -> None:
        """Cues with empty text are skipped."""
        segments = parse_vtt(EMPTY_CUE_VTT)

        assert len(segments) == 2
        assert segments[0].text == "Valid text."
        assert segments[1].text == "After empty cue."

    def test_reversed_timestamp_skipped(self) -> None:
        """Cues with end < start are skipped."""
        segments = parse_vtt(REVERSED_TIMESTAMP_VTT)

        assert len(segments) == 1
        assert segments[0].text == "Valid after reversed."

    def test_zoom_metadata_lines(self) -> None:
        """VTT with Kind/Language metadata after WEBVTT header parses correctly."""
        segments = parse_vtt(ZOOM_METADATA_VTT)

        assert len(segments) == 2
        assert segments[0].text == "Zoom format with metadata."
        assert segments[1].text == "Second segment."


class TestParseVttFile:
    def test_parse_file(self, tmp_path: Path) -> None:
        """parse_vtt_file reads and parses a VTT file."""
        vtt_file = tmp_path / "test.vtt"
        vtt_file.write_text(BASIC_VTT, encoding="utf-8")

        segments = parse_vtt_file(vtt_file)

        assert len(segments) == 2
        assert segments[0].text == "Hello, this is a test."

    def test_file_not_found(self, tmp_path: Path) -> None:
        """Missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parse_vtt_file(tmp_path / "nonexistent.vtt")

    def test_bom_file(self, tmp_path: Path) -> None:
        """File with BOM is handled by utf-8-sig encoding."""
        vtt_file = tmp_path / "bom.vtt"
        vtt_file.write_bytes(b"\xef\xbb\xbf" + BASIC_VTT.encode("utf-8"))

        segments = parse_vtt_file(vtt_file)

        assert len(segments) == 2
        assert segments[0].text == "Hello, this is a test."

    def test_hour_timestamps(self) -> None:
        """Timestamps with non-zero hours parse correctly."""
        vtt = """\
WEBVTT

01:30:00.000 --> 01:30:05.500
Hour-level timestamp.
"""
        segments = parse_vtt(vtt)

        assert len(segments) == 1
        assert segments[0].start == 5400.0
        assert segments[0].end == 5405.5
