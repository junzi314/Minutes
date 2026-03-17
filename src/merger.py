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


def format_transcript_markdown(
    transcript: str,
    date: str,
    speakers: str,
    section_interval_sec: int = 180,
) -> str:
    """Convert ``[MM:SS] speaker: text`` transcript into Markdown with section headers.

    Output format::

        # 文字起こし
        - 日時: 2026-03-16 14:01
        - 参加者: Alice, Bob

        ### 00:00:00

        **Alice:** テキスト
        **Bob:** テキスト

        ### 00:03:00
        ...

    *section_interval_sec* controls how often ``### HH:MM:SS`` headers are inserted.
    """
    if not transcript:
        return ""

    lines = transcript.splitlines()
    output_lines: list[str] = [
        "# 文字起こし",
        f"- 日時: {date}",
        f"- 参加者: {speakers}",
        "",
    ]

    # Parse [MM:SS] or [HH:MM:SS] prefix to total seconds
    import re
    ts_pattern = re.compile(r"^\[(?:(\d+):)?(\d+):(\d+)\]\s*(.+)$")

    current_section_start: int | None = None

    for line in lines:
        m = ts_pattern.match(line)
        if not m:
            # Non-matching lines pass through as-is
            output_lines.append(line)
            continue

        hh = int(m.group(1)) if m.group(1) else 0
        mm = int(m.group(2))
        ss = int(m.group(3))
        total_sec = hh * 3600 + mm * 60 + ss
        rest = m.group(4)  # "speaker: text"

        # Determine section boundary
        section_start = (total_sec // section_interval_sec) * section_interval_sec
        if current_section_start is None or section_start != current_section_start:
            current_section_start = section_start
            sh = section_start // 3600
            sm = (section_start % 3600) // 60
            sss = section_start % 60
            output_lines.append(f"### {sh:02d}:{sm:02d}:{sss:02d}")
            output_lines.append("")

        # Convert "speaker: text" → "**speaker:** text"
        colon_idx = rest.find(": ")
        if colon_idx != -1:
            speaker = rest[:colon_idx]
            text = rest[colon_idx + 2:]
            output_lines.append(f"**{speaker}:** {text}")
        else:
            output_lines.append(rest)

    return "\n".join(output_lines)
