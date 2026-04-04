"""Pipeline orchestrator for Zoom → Slack minutes generation."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from src.audio_extractor import extract_audio
from src.diarizer import DiariZenDiarizer
from src.errors import DiarizationError
from src.generator import MinutesGenerator
from src.merger import merge_transcripts
from src.segment_aligner import align_segments
from src.slack_config import SlackServiceConfig
from src.slack_poster import SlackPoster
from src.state_store import StateStore
from src.vtt_parser import parse_vtt_file

logger = logging.getLogger(__name__)


def _transcript_hash(transcript: str) -> str:
    """Compute a deterministic cache key from transcript text."""
    key = f"minutes:{transcript}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


async def run_slack_pipeline(
    audio_path: Path,
    vtt_path: Path,
    cfg: SlackServiceConfig,
    generator: MinutesGenerator,
    slack_poster: SlackPoster,
    state_store: StateStore,
    source_label: str = "zoom",
) -> None:
    """Run the full Zoom → Slack minutes pipeline.

    Stages:
    1. Parse VTT → list[Segment] (no speaker)
    2. Extract audio → WAV 16kHz mono
    3. Diarize → list[DiarSegment]
    4. Align VTT segments with speaker labels
    5. Merge transcript
    6. Generate minutes via Claude API
    7. Post to Slack

    IMPORTANT: audio_path and vtt_path may reside in a temp directory.
    All file I/O must complete within this function.
    """
    pipeline_start = time.monotonic()
    thread_ts: str | None = None
    diarizer: DiariZenDiarizer | None = None

    try:
        # Stage 1: Parse VTT
        logger.info("[%s] Stage 1: Parsing VTT %s", source_label, vtt_path.name)
        vtt_segments = parse_vtt_file(vtt_path)
        if not vtt_segments:
            logger.warning("[%s] VTT produced no segments", source_label)
            await slack_poster.post_error_to_slack(
                "VTT file produced no segments", source_label
            )
            return
        logger.info("[%s] Parsed %d VTT segments", source_label, len(vtt_segments))

        # Stage 2: Extract audio → WAV
        logger.info("[%s] Stage 2: Extracting audio", source_label)
        with tempfile.TemporaryDirectory(prefix="slack-pipeline-") as tmp_dir:
            wav_path = Path(tmp_dir) / "audio.wav"
            await extract_audio(
                audio_path,
                wav_path,
                timeout_sec=cfg.diarization.ffmpeg_timeout_sec,
            )

            # Stage 3: Diarize (with fallback on failure)
            logger.info("[%s] Stage 3: Speaker diarization", source_label)
            diar_segments = []
            diarizer = DiariZenDiarizer(cfg.diarization)
            try:
                await asyncio.to_thread(diarizer.load_model)
                diar_segments = await asyncio.to_thread(diarizer.diarize, wav_path)
                logger.info(
                    "[%s] Diarization: %d segments", source_label, len(diar_segments)
                )
            except DiarizationError:
                logger.warning(
                    "[%s] Diarization failed, falling back to single speaker",
                    source_label,
                    exc_info=True,
                )
            finally:
                diarizer.unload_model()
                diarizer = None

        # Stage 4: Align segments
        logger.info("[%s] Stage 4: Aligning segments", source_label)
        aligned = align_segments(vtt_segments, diar_segments)

        # Stage 5: Merge transcript
        logger.info("[%s] Stage 5: Merging transcript", source_label)
        transcript = merge_transcripts(aligned, cfg.merger)
        if not transcript:
            logger.warning("[%s] Merged transcript is empty", source_label)
            await slack_poster.post_error_to_slack(
                "Merged transcript is empty", source_label
            )
            return

        speaker_names = sorted({s.speaker for s in aligned if s.speaker})

        # Stage 6: Generate minutes (with caching)
        logger.info("[%s] Stage 6: Generating minutes", source_label)
        th = _transcript_hash(transcript)
        cached = state_store.get_cached_minutes(th)

        if cached:
            logger.info("[%s] Cache hit for transcript hash %s...", source_label, th[:12])
            minutes_md = cached
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            speakers_str = ", ".join(speaker_names) if speaker_names else "Unknown"
            minutes_md = await generator.generate(
                transcript=transcript,
                date=date_str,
                speakers=speakers_str,
            )
            state_store.put_cached_minutes(th, minutes_md)

        # Stage 7: Post to Slack
        logger.info("[%s] Stage 7: Posting to Slack", source_label)
        elapsed = time.monotonic() - pipeline_start
        title = f"Meeting Minutes — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
        metadata = {
            "speakers": ", ".join(speaker_names) if speaker_names else "N/A",
            "segments": str(len(aligned)),
            "processing_time": f"{elapsed:.1f}s",
        }
        thread_ts = await slack_poster.post_minutes_to_slack(
            minutes_md, title, metadata
        )

        # Attach transcript if configured
        if cfg.slack.include_transcript:
            await slack_poster.post_transcript_file(
                thread_ts, transcript, filename=f"{source_label}_transcript.md"
            )

        logger.info(
            "[%s] Pipeline complete in %.1fs (ts=%s)",
            source_label,
            elapsed,
            thread_ts,
        )

    except Exception as exc:
        elapsed = time.monotonic() - pipeline_start
        logger.error(
            "[%s] Pipeline failed after %.1fs: %s",
            source_label,
            elapsed,
            exc,
            exc_info=True,
        )
        try:
            await slack_poster.post_error_to_slack(str(exc), source_label)
        except Exception:
            logger.warning("[%s] Failed to post error to Slack", source_label)
        raise

    finally:
        # Ensure VRAM is freed even on unexpected exit
        if diarizer is not None:
            try:
                diarizer.unload_model()
            except Exception:
                pass
