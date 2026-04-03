"""Align speaker diarization results with transcription segments."""

from __future__ import annotations

import logging

from src.diarizer import DiarSegment
from src.transcriber import Segment

logger = logging.getLogger(__name__)


def align_segments(
    whisper_segments: list[Segment],
    diar_segments: list[DiarSegment],
    *,
    fallback_speaker: str = "Speaker",
) -> list[Segment]:
    """Assign speaker labels to transcription segments via majority-vote overlap.

    For each Whisper segment, finds the diarization segment(s) that overlap
    in time. The speaker with the greatest total overlap duration is assigned.

    Args:
        whisper_segments: Transcription segments from faster-whisper.
        diar_segments: Speaker segments from diarization.
        fallback_speaker: Speaker label for segments with no diarization overlap.

    Returns:
        New list[Segment] with speaker fields set from diarization results.
    """
    if not whisper_segments:
        return []

    if not diar_segments:
        return [
            Segment(start=s.start, end=s.end, text=s.text, speaker=fallback_speaker)
            for s in whisper_segments
        ]

    sorted_diar = sorted(diar_segments, key=lambda s: s.start)

    result: list[Segment] = []
    for ws in whisper_segments:
        speaker = _find_best_speaker(ws.start, ws.end, sorted_diar, fallback_speaker)
        result.append(
            Segment(
                start=ws.start,
                end=ws.end,
                text=ws.text,
                speaker=speaker,
            )
        )

    speakers_found = {s.speaker for s in result}
    logger.info(
        "Aligned %d segments to %d speakers",
        len(result),
        len(speakers_found),
    )
    return result


def _find_best_speaker(
    start: float,
    end: float,
    diar_segments: list[DiarSegment],
    fallback: str,
) -> str:
    """Find the speaker with maximum overlap for a given time range."""
    overlaps: dict[str, float] = {}

    for ds in diar_segments:
        if ds.start >= end:
            break
        if ds.end <= start:
            continue

        overlap_start = max(start, ds.start)
        overlap_end = min(end, ds.end)
        overlap_duration = overlap_end - overlap_start

        if overlap_duration > 0:
            overlaps[ds.speaker] = overlaps.get(ds.speaker, 0.0) + overlap_duration

    if not overlaps:
        return fallback

    return max(overlaps, key=overlaps.get)  # type: ignore[arg-type]
