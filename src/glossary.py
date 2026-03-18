"""Transcript glossary: apply term replacements to transcription segments."""

from __future__ import annotations

import re

from src.transcriber import Segment


def apply_glossary(
    segments: list[Segment],
    glossary: dict[str, str],
    case_sensitive: bool = False,
) -> list[Segment]:
    """Apply glossary replacements to segment text.

    For each segment, all glossary entries are applied sequentially.
    Segment is a frozen dataclass, so new instances are created for
    any modified segments. Unmodified segments are returned as-is
    (identity preservation).

    Args:
        segments: Raw transcription segments from Whisper.
        glossary: Mapping of {wrong_text: correct_text}.
        case_sensitive: If False (default), matching ignores case.

    Returns:
        New list of Segment instances with glossary applied.
    """
    if not glossary:
        return segments

    if case_sensitive:
        return [_apply_case_sensitive(seg, glossary) for seg in segments]

    # Pre-compile regex patterns for case-insensitive mode
    compiled = [
        (re.compile(re.escape(pattern), re.IGNORECASE), replacement)
        for pattern, replacement in glossary.items()
    ]
    return [_apply_regex(seg, compiled) for seg in segments]


def _apply_case_sensitive(
    seg: Segment,
    glossary: dict[str, str],
) -> Segment:
    """Apply glossary using str.replace (case-sensitive)."""
    text = seg.text
    for pattern, replacement in glossary.items():
        text = text.replace(pattern, replacement)
    if text is seg.text:  # identity check: no change
        return seg
    return Segment(start=seg.start, end=seg.end, text=text, speaker=seg.speaker)


def _apply_regex(
    seg: Segment,
    compiled: list[tuple[re.Pattern, str]],
) -> Segment:
    """Apply glossary using pre-compiled regex patterns (case-insensitive)."""
    text = seg.text
    for pattern, replacement in compiled:
        text = pattern.sub(replacement, text)
    if text == seg.text:  # no change
        return seg
    return Segment(start=seg.start, end=seg.end, text=text, speaker=seg.speaker)
