"""Speaker analytics: per-speaker talk time and character count aggregation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from src.transcriber import Segment


@dataclass(frozen=True)
class SpeakerStats:
    """Aggregated statistics for a single speaker."""

    speaker: str
    talk_time_sec: float
    char_count: int
    segment_count: int


def calculate_speaker_stats(segments: list[Segment]) -> list[SpeakerStats]:
    """Aggregate per-speaker statistics from segments.

    Returns a list of SpeakerStats sorted by talk_time_sec descending.
    Empty input returns an empty list.
    """
    if not segments:
        return []

    acc: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"talk_time": 0.0, "chars": 0, "count": 0}
    )

    for seg in segments:
        d = acc[seg.speaker]
        d["talk_time"] += seg.end - seg.start
        d["chars"] += len(seg.text)
        d["count"] += 1

    stats = [
        SpeakerStats(
            speaker=speaker,
            talk_time_sec=d["talk_time"],
            char_count=int(d["chars"]),
            segment_count=int(d["count"]),
        )
        for speaker, d in acc.items()
    ]

    stats.sort(key=lambda s: s.talk_time_sec, reverse=True)
    return stats


def _format_time(seconds: float) -> str:
    """Format seconds as M:SS."""
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"


def format_stats_embed(
    stats: list[SpeakerStats],
    bar_width: int = 10,
    max_speakers: int = 10,
    max_chars: int = 1024,
) -> str:
    """Format SpeakerStats as a text bar graph for Discord Embed.

    Returns a formatted string. Empty stats returns an empty string.
    """
    if not stats:
        return ""

    max_time = max(s.talk_time_sec for s in stats)
    if max_time <= 0:
        max_time = 1.0  # avoid division by zero

    shown = stats[:max_speakers]
    others_count = len(stats) - len(shown)

    lines: list[str] = []
    for s in shown:
        # Truncate speaker name to 8 chars
        name = s.speaker
        if len(name) > 8:
            name = name[:7] + "\u2026"

        ratio = s.talk_time_sec / max_time
        filled = round(ratio * bar_width)
        bar = "\u2588" * filled + "\u2591" * (bar_width - filled)

        time_str = _format_time(s.talk_time_sec)
        char_str = f"{s.char_count:,}\u5b57"

        lines.append(f"{name:<8s} {bar} {time_str:>5s} {char_str:>7s}")

    if others_count > 0:
        lines.append(f"\u4ed6{others_count}\u4eba")

    result = "\n".join(lines)

    # If exceeding max_chars, retry with smaller bar_width
    if len(result) > max_chars and bar_width > 3:
        return format_stats_embed(stats, bar_width=bar_width - 2, max_speakers=max_speakers, max_chars=max_chars)

    return result[:max_chars]
