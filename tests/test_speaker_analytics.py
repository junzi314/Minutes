"""Unit tests for src/speaker_analytics.py."""

from __future__ import annotations

from src.speaker_analytics import (
    SpeakerStats,
    calculate_speaker_stats,
    format_stats_embed,
)
from src.transcriber import Segment


def _seg(speaker: str, start: float, end: float, text: str = "test") -> Segment:
    return Segment(start=start, end=end, text=text, speaker=speaker)


# --- calculate_speaker_stats ---


class TestCalculateSpeakerStats:
    def test_empty_segments(self) -> None:
        assert calculate_speaker_stats([]) == []

    def test_single_speaker(self) -> None:
        segments = [
            _seg("alice", 0.0, 5.0, "hello"),
            _seg("alice", 10.0, 15.0, "world"),
        ]
        stats = calculate_speaker_stats(segments)
        assert len(stats) == 1
        assert stats[0].speaker == "alice"
        assert stats[0].talk_time_sec == 10.0
        assert stats[0].char_count == 10  # "hello" + "world"
        assert stats[0].segment_count == 2

    def test_multiple_speakers_sorted_by_time(self) -> None:
        segments = [
            _seg("alice", 0.0, 10.0, "long speech"),
            _seg("bob", 10.0, 13.0, "short"),
            _seg("charlie", 13.0, 20.0, "medium text"),
        ]
        stats = calculate_speaker_stats(segments)
        assert len(stats) == 3
        assert stats[0].speaker == "alice"  # 10s
        assert stats[1].speaker == "charlie"  # 7s
        assert stats[2].speaker == "bob"  # 3s

    def test_char_count_accuracy(self) -> None:
        segments = [
            _seg("alice", 0.0, 1.0, "abc"),
            _seg("alice", 1.0, 2.0, "de"),
        ]
        stats = calculate_speaker_stats(segments)
        assert stats[0].char_count == 5  # 3 + 2

    def test_segment_count(self) -> None:
        segments = [
            _seg("alice", 0.0, 1.0),
            _seg("alice", 2.0, 3.0),
            _seg("alice", 4.0, 5.0),
            _seg("bob", 1.0, 2.0),
        ]
        stats = calculate_speaker_stats(segments)
        alice = next(s for s in stats if s.speaker == "alice")
        bob = next(s for s in stats if s.speaker == "bob")
        assert alice.segment_count == 3
        assert bob.segment_count == 1


# --- format_stats_embed ---


class TestFormatStatsEmbed:
    def test_empty_stats(self) -> None:
        assert format_stats_embed([]) == ""

    def test_basic_format(self) -> None:
        stats = [
            SpeakerStats(speaker="alice", talk_time_sec=60.0, char_count=100, segment_count=5),
            SpeakerStats(speaker="bob", talk_time_sec=30.0, char_count=50, segment_count=3),
        ]
        result = format_stats_embed(stats)
        assert "alice" in result
        assert "bob" in result
        assert "1:00" in result
        assert "0:30" in result
        assert "100\u5b57" in result  # 100字
        assert "\u2588" in result  # filled bar
        assert "\u2591" in result  # empty bar

    def test_single_speaker_full_bar(self) -> None:
        stats = [
            SpeakerStats(speaker="alice", talk_time_sec=60.0, char_count=100, segment_count=5),
        ]
        result = format_stats_embed(stats, bar_width=10)
        # Single speaker should have full bar (10 filled blocks)
        assert "\u2588" * 10 in result

    def test_max_speakers_truncation(self) -> None:
        stats = [
            SpeakerStats(speaker=f"user{i}", talk_time_sec=float(20 - i), char_count=10, segment_count=1)
            for i in range(15)
        ]
        result = format_stats_embed(stats, max_speakers=10)
        assert "\u4ed65\u4eba" in result  # 他5人

    def test_max_chars_limit(self) -> None:
        stats = [
            SpeakerStats(speaker=f"user{i}", talk_time_sec=float(10 - i), char_count=10, segment_count=1)
            for i in range(10)
        ]
        result = format_stats_embed(stats, max_chars=1024)
        assert len(result) <= 1024

    def test_long_speaker_name_truncated(self) -> None:
        stats = [
            SpeakerStats(speaker="verylongusername", talk_time_sec=60.0, char_count=100, segment_count=1),
        ]
        result = format_stats_embed(stats)
        # Name should be truncated to 7 chars + ellipsis
        assert "verylon\u2026" in result

    def test_char_count_with_comma(self) -> None:
        stats = [
            SpeakerStats(speaker="alice", talk_time_sec=60.0, char_count=1234, segment_count=1),
        ]
        result = format_stats_embed(stats)
        assert "1,234\u5b57" in result

    def test_zero_talk_time(self) -> None:
        """Zero talk time should not cause division by zero."""
        stats = [
            SpeakerStats(speaker="alice", talk_time_sec=0.0, char_count=0, segment_count=1),
        ]
        result = format_stats_embed(stats)
        assert "alice" in result
        assert "0:00" in result
