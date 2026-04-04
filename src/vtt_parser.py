"""Zoom VTT file parser — converts WebVTT to list[Segment]."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from src.transcriber import Segment

logger = logging.getLogger(__name__)

# Matches VTT timestamp lines: "00:01:23.456 --> 00:01:25.789"
_TIMESTAMP_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})"
)


def _parse_timestamp(ts: str) -> float:
    """Convert 'HH:MM:SS.mmm' to seconds."""
    h, m, rest = ts.split(":")
    s, ms = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_vtt(vtt_text: str) -> list[Segment]:
    """Parse WebVTT text into a list of Segment objects.

    Handles:
    - BOM (U+FEFF) removal
    - Optional WEBVTT header
    - Multiline cues (joined with space)
    - Empty cues (skipped)
    - Malformed timestamps (skipped with warning)
    - Reversed start/end (skipped with warning)
    """
    # Strip BOM
    text = vtt_text.lstrip("\ufeff")

    lines = text.splitlines()
    segments: list[Segment] = []

    i = 0
    total = len(lines)

    # Skip WEBVTT header and any metadata lines
    while i < total:
        stripped = lines[i].strip()
        if stripped.upper().startswith("WEBVTT"):
            i += 1
            # Skip blank lines / NOTE blocks after header
            while i < total and lines[i].strip():
                i += 1
            break
        if stripped == "":
            i += 1
            continue
        # No WEBVTT header — start parsing cues directly
        break

    while i < total:
        line = lines[i].strip()

        # Skip blank lines and cue identifiers (numeric lines)
        if line == "" or line.isdigit():
            i += 1
            continue

        # Try matching a timestamp line
        m = _TIMESTAMP_RE.match(line)
        if not m:
            i += 1
            continue

        try:
            start = _parse_timestamp(m.group(1))
            end = _parse_timestamp(m.group(2))
        except (ValueError, IndexError):
            logger.warning("Malformed VTT timestamp at line %d: %s", i + 1, line)
            i += 1
            continue

        if end < start:
            logger.warning(
                "Reversed VTT timestamp at line %d: %.3f > %.3f", i + 1, start, end
            )
            i += 1
            continue

        # Collect text lines until blank line or next timestamp
        i += 1
        text_parts: list[str] = []
        while i < total:
            tl = lines[i].strip()
            if tl == "" or _TIMESTAMP_RE.match(tl):
                break
            # Skip cue identifiers that appear before next timestamp
            if tl.isdigit():
                break
            text_parts.append(tl)
            i += 1

        cue_text = " ".join(text_parts).strip()
        if not cue_text:
            continue

        segments.append(Segment(start=start, end=end, text=cue_text, speaker=""))

    return segments


def parse_vtt_file(vtt_path: Path) -> list[Segment]:
    """Read a VTT file and parse it into Segment objects.

    Supports BOM-prefixed UTF-8 and standard UTF-8 encodings.
    Raises FileNotFoundError if the file does not exist.
    """
    vtt_path = Path(vtt_path)
    # Read with utf-8-sig to auto-strip BOM
    text = vtt_path.read_text(encoding="utf-8-sig")
    return parse_vtt(text)
