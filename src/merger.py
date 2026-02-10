"""Merge per-speaker transcription segments into a chronological transcript."""

from __future__ import annotations

import logging

from src.config import MergerConfig
from src.transcriber import Segment

logger = logging.getLogger(__name__)


def _format_timestamp(seconds: float, fmt: str) -> str:
    """Format a timestamp in seconds using the configured format string.

    Supported placeholders: {hh}, {mm}, {ss}.
    """
    total_seconds = int(seconds)
    hh = total_seconds // 3600
    mm = (total_seconds % 3600) // 60
    ss = total_seconds % 60
    return fmt.format(hh=f"{hh:02d}", mm=f"{mm:02d}", ss=f"{ss:02d}")


def merge_transcripts(
    segments: list[Segment],
    cfg: MergerConfig,
) -> str:
    """Sort segments chronologically and merge adjacent same-speaker segments.

    Adjacent segments from the same speaker are merged when the gap between
    them is less than ``cfg.gap_merge_threshold_sec``.

    Returns a formatted transcript string with one line per merged segment:
        [HH:MM:SS] Speaker: text
    """
    if not segments:
        return ""

    # Sort by start time, breaking ties by end time
    sorted_segs = sorted(segments, key=lambda s: (s.start, s.end))

    # Filter segments below minimum character threshold
    min_chars = cfg.min_segment_chars
    sorted_segs = [s for s in sorted_segs if len(s.text) >= min_chars]

    if not sorted_segs:
        return ""

    # Merge adjacent same-speaker segments within gap threshold
    merged: list[Segment] = [sorted_segs[0]]

    for seg in sorted_segs[1:]:
        prev = merged[-1]
        gap = max(0.0, seg.start - prev.end)

        if seg.speaker == prev.speaker and gap <= cfg.gap_merge_threshold_sec:
            # Merge: extend the previous segment
            merged[-1] = Segment(
                start=prev.start,
                end=seg.end,
                text=prev.text + " " + seg.text,
                speaker=prev.speaker,
            )
        else:
            merged.append(seg)

    # Format output
    lines: list[str] = []
    for seg in merged:
        ts = _format_timestamp(seg.start, cfg.timestamp_format)
        lines.append(f"{ts} {seg.speaker}: {seg.text}")

    transcript = "\n".join(lines)

    logger.info(
        "Merged %d raw segments into %d lines (%d speakers)",
        len(segments),
        len(merged),
        len({s.speaker for s in merged}),
    )
    return transcript
