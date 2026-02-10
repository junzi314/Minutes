"""Unit tests for src/merger.py."""

from __future__ import annotations

from src.config import MergerConfig
from src.merger import _format_timestamp, merge_transcripts
from src.transcriber import Segment

# Default config
_CFG = MergerConfig()

# Config with HH:MM:SS format for testing
_HH_CFG = MergerConfig(timestamp_format="[{hh}:{mm}:{ss}]")


def _seg(start: float, end: float, text: str, speaker: str) -> Segment:
    return Segment(start=start, end=end, text=text, speaker=speaker)


# --- _format_timestamp ---


class TestFormatTimestamp:
    def test_zero(self) -> None:
        assert _format_timestamp(0.0, "[{mm}:{ss}]") == "[00:00]"

    def test_seconds_only(self) -> None:
        assert _format_timestamp(45.0, "[{mm}:{ss}]") == "[00:45]"

    def test_minutes_and_seconds(self) -> None:
        assert _format_timestamp(125.0, "[{mm}:{ss}]") == "[02:05]"

    def test_hours(self) -> None:
        assert _format_timestamp(3661.0, "[{hh}:{mm}:{ss}]") == "[01:01:01]"

    def test_custom_format(self) -> None:
        assert _format_timestamp(90.0, "{mm}m{ss}s") == "01m30s"


# --- merge_transcripts ---


class TestMergeTranscripts:
    def test_empty_input(self) -> None:
        assert merge_transcripts([], _CFG) == ""

    def test_single_segment(self) -> None:
        result = merge_transcripts(
            [_seg(0.0, 5.0, "Hello", "Alice")],
            _CFG,
        )
        assert result == "[00:00] Alice: Hello"

    def test_two_speakers_interleaved(self) -> None:
        segments = [
            _seg(0.0, 3.0, "First point", "Alice"),
            _seg(3.5, 6.0, "I agree", "Bob"),
            _seg(6.5, 9.0, "Next topic", "Alice"),
            _seg(10.0, 12.0, "Good idea", "Bob"),
        ]
        result = merge_transcripts(segments, _CFG)
        lines = result.split("\n")
        assert len(lines) == 4
        assert "Alice: First point" in lines[0]
        assert "Bob: I agree" in lines[1]
        assert "Alice: Next topic" in lines[2]
        assert "Bob: Good idea" in lines[3]

    def test_segments_sorted_by_start_time(self) -> None:
        # Provide segments out of order
        segments = [
            _seg(10.0, 12.0, "Third", "Bob"),
            _seg(0.0, 3.0, "First", "Alice"),
            _seg(5.0, 7.0, "Second", "Alice"),
        ]
        result = merge_transcripts(segments, _CFG)
        lines = result.split("\n")
        assert "First" in lines[0]
        assert "Second" in lines[1]
        assert "Third" in lines[2]

    def test_adjacent_same_speaker_merged_within_threshold(self) -> None:
        # Gap of 0.5s < default threshold of 1.0s => should merge
        segments = [
            _seg(0.0, 2.0, "Hello", "Alice"),
            _seg(2.5, 4.0, "world", "Alice"),
        ]
        result = merge_transcripts(segments, _CFG)
        lines = result.split("\n")
        assert len(lines) == 1
        assert "Alice: Hello world" in lines[0]

    def test_adjacent_same_speaker_not_merged_beyond_threshold(self) -> None:
        # Gap of 2.0s > default threshold of 1.0s => should NOT merge
        segments = [
            _seg(0.0, 2.0, "Hello", "Alice"),
            _seg(4.0, 6.0, "world", "Alice"),
        ]
        result = merge_transcripts(segments, _CFG)
        lines = result.split("\n")
        assert len(lines) == 2

    def test_different_speakers_never_merged(self) -> None:
        # Even with 0 gap, different speakers should not merge
        segments = [
            _seg(0.0, 2.0, "Hello", "Alice"),
            _seg(2.0, 4.0, "Hi", "Bob"),
        ]
        result = merge_transcripts(segments, _CFG)
        lines = result.split("\n")
        assert len(lines) == 2

    def test_custom_gap_threshold(self) -> None:
        cfg = MergerConfig(gap_merge_threshold_sec=5.0)
        segments = [
            _seg(0.0, 2.0, "Part one", "Alice"),
            _seg(6.0, 8.0, "Part two", "Alice"),  # gap=4s < 5s threshold
        ]
        result = merge_transcripts(segments, cfg)
        lines = result.split("\n")
        assert len(lines) == 1
        assert "Part one Part two" in lines[0]

    def test_min_segment_chars_filter(self) -> None:
        cfg = MergerConfig(min_segment_chars=3)
        segments = [
            _seg(0.0, 1.0, "Hi", "Alice"),  # 2 chars => filtered
            _seg(2.0, 4.0, "Hello there", "Alice"),  # 11 chars => kept
        ]
        result = merge_transcripts(segments, cfg)
        lines = result.split("\n")
        assert len(lines) == 1
        assert "Hello there" in lines[0]

    def test_all_segments_below_min_chars(self) -> None:
        cfg = MergerConfig(min_segment_chars=100)
        segments = [
            _seg(0.0, 1.0, "Hi", "Alice"),
            _seg(2.0, 3.0, "Ok", "Bob"),
        ]
        result = merge_transcripts(segments, cfg)
        assert result == ""

    def test_hh_mm_ss_format(self) -> None:
        segments = [_seg(3661.0, 3665.0, "Late in meeting", "Alice")]
        result = merge_transcripts(segments, _HH_CFG)
        assert result == "[01:01:01] Alice: Late in meeting"

    def test_timestamp_at_exact_minute(self) -> None:
        segments = [_seg(60.0, 65.0, "One minute in", "Bob")]
        result = merge_transcripts(segments, _CFG)
        assert result == "[01:00] Bob: One minute in"

    def test_merge_preserves_merged_end_time(self) -> None:
        """When merging, the merged segment should use the latest end time."""
        cfg = MergerConfig(gap_merge_threshold_sec=2.0)
        segments = [
            _seg(0.0, 2.0, "A", "Alice"),
            _seg(2.5, 5.0, "B", "Alice"),
            _seg(5.5, 8.0, "C", "Alice"),
        ]
        result = merge_transcripts(segments, cfg)
        lines = result.split("\n")
        assert len(lines) == 1  # all merged
        assert "A B C" in lines[0]
