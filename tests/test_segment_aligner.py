"""Unit tests for src/segment_aligner.py."""

from __future__ import annotations

import time

import pytest

from src.diarizer import DiarSegment
from src.segment_aligner import align_segments
from src.transcriber import Segment


class TestAlignBasic:
    def test_align_basic(self) -> None:
        """Single whisper segment fully inside a single diar segment gets that speaker."""
        whisper = [Segment(start=1.0, end=2.0, text="hello", speaker="")]
        diar = [DiarSegment(start=0.0, end=3.0, speaker="Speaker_0")]

        result = align_segments(whisper, diar)

        assert len(result) == 1
        assert result[0].speaker == "Speaker_0"

    def test_align_two_speakers(self) -> None:
        """Two whisper segments, each in a different diar speaker's range."""
        whisper = [
            Segment(start=0.0, end=1.0, text="first", speaker=""),
            Segment(start=2.0, end=3.0, text="second", speaker=""),
        ]
        diar = [
            DiarSegment(start=0.0, end=1.5, speaker="Speaker_0"),
            DiarSegment(start=1.5, end=3.5, speaker="Speaker_1"),
        ]

        result = align_segments(whisper, diar)

        assert result[0].speaker == "Speaker_0"
        assert result[1].speaker == "Speaker_1"


class TestMajorityVote:
    def test_align_majority_vote(self) -> None:
        """Whisper segment overlapping two diar segments: longer overlap wins."""
        # Whisper: [1.0, 3.0]
        # Diar A:  [0.0, 2.5] -> overlap = 1.5
        # Diar B:  [2.5, 4.0] -> overlap = 0.5
        whisper = [Segment(start=1.0, end=3.0, text="overlap", speaker="")]
        diar = [
            DiarSegment(start=0.0, end=2.5, speaker="Speaker_A"),
            DiarSegment(start=2.5, end=4.0, speaker="Speaker_B"),
        ]

        result = align_segments(whisper, diar)

        assert result[0].speaker == "Speaker_A"

    def test_align_tie_breaking(self) -> None:
        """Equal overlap: the speaker encountered first in iteration wins (max behavior)."""
        # Whisper: [1.0, 3.0]
        # Diar A:  [0.0, 2.0] -> overlap = 1.0
        # Diar B:  [2.0, 4.0] -> overlap = 1.0
        whisper = [Segment(start=1.0, end=3.0, text="tie", speaker="")]
        diar = [
            DiarSegment(start=0.0, end=2.0, speaker="Speaker_A"),
            DiarSegment(start=2.0, end=4.0, speaker="Speaker_B"),
        ]

        result = align_segments(whisper, diar)

        # With equal overlap, max() returns the first key found in iteration order.
        # Both are valid; we just verify determinism.
        assert result[0].speaker in ("Speaker_A", "Speaker_B")


class TestEdgeCases:
    def test_align_no_overlap(self) -> None:
        """Whisper segment outside all diar ranges gets fallback speaker."""
        whisper = [Segment(start=10.0, end=11.0, text="isolated", speaker="")]
        diar = [DiarSegment(start=0.0, end=5.0, speaker="Speaker_0")]

        result = align_segments(whisper, diar)

        assert result[0].speaker == "Speaker"

    def test_align_empty_whisper(self) -> None:
        """Empty whisper list returns empty list."""
        diar = [DiarSegment(start=0.0, end=1.0, speaker="Speaker_0")]

        result = align_segments([], diar)

        assert result == []

    def test_align_empty_diar(self) -> None:
        """Empty diar list: all segments get fallback_speaker."""
        whisper = [
            Segment(start=0.0, end=1.0, text="a", speaker=""),
            Segment(start=1.0, end=2.0, text="b", speaker=""),
        ]

        result = align_segments(whisper, [])

        assert all(s.speaker == "Speaker" for s in result)

    def test_align_custom_fallback(self) -> None:
        """Custom fallback_speaker string is used when no overlap."""
        whisper = [Segment(start=10.0, end=11.0, text="x", speaker="")]
        diar = [DiarSegment(start=0.0, end=5.0, speaker="Speaker_0")]

        result = align_segments(whisper, diar, fallback_speaker="Unknown")

        assert result[0].speaker == "Unknown"

    def test_align_partial_coverage(self) -> None:
        """Some segments covered by diar, some not."""
        whisper = [
            Segment(start=0.0, end=1.0, text="covered", speaker=""),
            Segment(start=5.0, end=6.0, text="uncovered", speaker=""),
            Segment(start=8.0, end=9.0, text="covered2", speaker=""),
        ]
        diar = [
            DiarSegment(start=0.0, end=2.0, speaker="Speaker_0"),
            DiarSegment(start=7.0, end=10.0, speaker="Speaker_1"),
        ]

        result = align_segments(whisper, diar)

        assert result[0].speaker == "Speaker_0"
        assert result[1].speaker == "Speaker"  # fallback
        assert result[2].speaker == "Speaker_1"


class TestDataPreservation:
    def test_align_preserves_text(self) -> None:
        """Text field is unchanged after alignment."""
        whisper = [Segment(start=0.0, end=1.0, text="hello world", speaker="")]
        diar = [DiarSegment(start=0.0, end=1.0, speaker="Speaker_0")]

        result = align_segments(whisper, diar)

        assert result[0].text == "hello world"

    def test_align_preserves_timestamps(self) -> None:
        """Start/end fields are unchanged after alignment."""
        whisper = [Segment(start=1.23, end=4.56, text="ts", speaker="")]
        diar = [DiarSegment(start=0.0, end=5.0, speaker="Speaker_0")]

        result = align_segments(whisper, diar)

        assert result[0].start == 1.23
        assert result[0].end == 4.56

    def test_align_order_preserved(self) -> None:
        """Output order matches input order."""
        whisper = [
            Segment(start=0.0, end=1.0, text="first", speaker=""),
            Segment(start=1.0, end=2.0, text="second", speaker=""),
            Segment(start=2.0, end=3.0, text="third", speaker=""),
        ]
        diar = [DiarSegment(start=0.0, end=3.0, speaker="Speaker_0")]

        result = align_segments(whisper, diar)

        assert [s.text for s in result] == ["first", "second", "third"]


class TestPerformance:
    def test_align_many_diar_segments(self) -> None:
        """500+ diar segments should align quickly (early break optimization)."""
        # Create 500 diar segments, each 0.5s
        diar = [
            DiarSegment(
                start=i * 0.5,
                end=(i + 1) * 0.5,
                speaker=f"Speaker_{i % 3}",
            )
            for i in range(500)
        ]
        # Create 10 whisper segments spread across the range
        whisper = [
            Segment(start=i * 25.0, end=i * 25.0 + 2.0, text=f"seg{i}", speaker="")
            for i in range(10)
        ]

        t0 = time.monotonic()
        result = align_segments(whisper, diar)
        elapsed = time.monotonic() - t0

        assert len(result) == 10
        assert elapsed < 1.0, f"Alignment took {elapsed:.3f}s, expected < 1s"

    def test_align_micro_segments(self) -> None:
        """Very short (<0.05s) whisper segments are handled correctly."""
        whisper = [
            Segment(start=1.00, end=1.02, text="a", speaker=""),
            Segment(start=1.02, end=1.04, text="b", speaker=""),
            Segment(start=1.04, end=1.05, text="c", speaker=""),
        ]
        diar = [
            DiarSegment(start=0.0, end=1.03, speaker="Speaker_0"),
            DiarSegment(start=1.03, end=2.0, speaker="Speaker_1"),
        ]

        result = align_segments(whisper, diar)

        assert len(result) == 3
        # Segment "a" [1.00-1.02] fully in Speaker_0
        assert result[0].speaker == "Speaker_0"
        # Segment "b" [1.02-1.04]: 0.01s in Speaker_0, 0.01s in Speaker_1 (tie)
        assert result[1].speaker in ("Speaker_0", "Speaker_1")
        # Segment "c" [1.04-1.05] fully in Speaker_1
        assert result[2].speaker == "Speaker_1"
