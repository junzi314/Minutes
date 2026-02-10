"""Pipeline orchestrator: download -> transcribe -> merge -> generate -> post.

This module ties together audio acquisition, transcription, transcript
merging, LLM minutes generation, and Discord posting into a single
async pipeline.

Two entry points:
  - run_pipeline(): Craig detection flow (download + process)
  - run_pipeline_from_tracks(): Pre-downloaded audio (Drive watcher, etc.)
"""

from __future__ import annotations

import logging
import tempfile
import time
from datetime import datetime
from pathlib import Path

import aiohttp
import discord

from src.audio_source import SpeakerAudio
from src.config import Config
from src.craig_client import CraigClient
from src.detector import DetectedRecording
from src.errors import MinutesBotError, TranscriptionError
from src.generator import MinutesGenerator
from src.merger import merge_transcripts
from src.poster import post_error, post_minutes, send_status_update
from src.transcriber import Segment, Transcriber

logger = logging.getLogger(__name__)


async def run_pipeline_from_tracks(
    tracks: list[SpeakerAudio],
    cfg: Config,
    transcriber: Transcriber,
    generator: MinutesGenerator,
    output_channel: discord.TextChannel,
    source_label: str = "unknown",
) -> None:
    """Execute stages 2-5 (transcribe -> merge -> generate -> post) on pre-downloaded tracks.

    This is the shared core used by both Craig detection and Drive watcher flows.
    """
    pipeline_start = time.monotonic()
    status_msg: discord.Message | None = None

    logger.info(
        "Pipeline (from tracks) starting for source=%s (%d tracks)",
        source_label,
        len(tracks),
    )

    try:
        # Status: transcribing
        speaker_names = [t.speaker.username for t in tracks]
        status_msg = await send_status_update(
            output_channel, status_msg,
            f"文字起こし中... ({len(tracks)}人: {', '.join(speaker_names)})",
        )

        # Stage 2: Transcribe
        segments = _stage_transcribe(transcriber, tracks)

        # Stage 3: Merge transcript
        transcript = merge_transcripts(segments, cfg.merger)
        if not transcript:
            raise TranscriptionError(
                f"Transcription produced empty result for source {source_label}"
            )

        # Status: generating
        status_msg = await send_status_update(
            output_channel, status_msg, "議事録を生成中..."
        )

        # Stage 4: Generate minutes
        speakers_str = ", ".join(speaker_names)
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        guild_name = output_channel.guild.name if output_channel.guild else ""

        minutes_md = await generator.generate(
            transcript=transcript,
            date=date_str,
            speakers=speakers_str,
            guild_name=guild_name,
            channel_name=output_channel.name,
        )

        # Status: posting
        status_msg = await send_status_update(
            output_channel, status_msg, "議事録を投稿中..."
        )

        # Stage 5: Post to Discord
        await post_minutes(
            channel=output_channel,
            minutes_md=minutes_md,
            date=date_str,
            speakers=speakers_str,
            cfg=cfg.poster,
        )

        # Clean up status message
        if status_msg:
            try:
                await status_msg.delete()
            except discord.HTTPException:
                pass

        elapsed = time.monotonic() - pipeline_start
        logger.info(
            "Pipeline complete for source=%s in %.1fs (%d segments)",
            source_label,
            elapsed,
            len(segments),
        )

    except MinutesBotError as exc:
        elapsed = time.monotonic() - pipeline_start
        logger.error(
            "Pipeline failed for source=%s at stage '%s' after %.1fs: %s",
            source_label,
            exc.stage,
            elapsed,
            exc,
        )
        if status_msg:
            try:
                await status_msg.delete()
            except discord.HTTPException:
                pass
        await post_error(
            channel=output_channel,
            error_message=str(exc),
            stage=exc.stage,
            error_mention_role_id=cfg.discord.error_mention_role_id,
        )
        raise

    except Exception as exc:
        elapsed = time.monotonic() - pipeline_start
        logger.exception(
            "Unexpected pipeline error for source=%s after %.1fs",
            source_label,
            elapsed,
        )
        if status_msg:
            try:
                await status_msg.delete()
            except discord.HTTPException:
                pass
        await post_error(
            channel=output_channel,
            error_message=str(exc),
            stage="unknown",
            error_mention_role_id=cfg.discord.error_mention_role_id,
        )
        raise


async def run_pipeline(
    recording: DetectedRecording,
    session: aiohttp.ClientSession,
    cfg: Config,
    transcriber: Transcriber,
    generator: MinutesGenerator,
    output_channel: discord.TextChannel,
) -> None:
    """Execute the full pipeline from Craig download through Discord posting."""
    status_msg: discord.Message | None = None

    logger.info(
        "Pipeline starting for rec_id=%s (channel=%d, guild=%d)",
        recording.rec_id,
        recording.channel_id,
        recording.guild_id,
    )

    # Status: downloading
    status_msg = await send_status_update(
        output_channel, status_msg, "音声ファイルをダウンロード中..."
    )

    try:
        with tempfile.TemporaryDirectory(prefix=f"minutes-{recording.rec_id}-") as tmp_dir:
            dest = Path(tmp_dir)

            # Stage 1: Download audio
            tracks = await _stage_download(recording, session, cfg, dest)

            # Clean up download status before handing off
            if status_msg:
                try:
                    await status_msg.delete()
                except discord.HTTPException:
                    pass

            # Stages 2-5: transcribe -> merge -> generate -> post
            await run_pipeline_from_tracks(
                tracks=tracks,
                cfg=cfg,
                transcriber=transcriber,
                generator=generator,
                output_channel=output_channel,
                source_label=f"craig:{recording.rec_id}",
            )

    except MinutesBotError as exc:
        # Clean up status if download itself failed
        if status_msg:
            try:
                await status_msg.delete()
            except discord.HTTPException:
                pass
        if exc.stage == "audio_acquisition":
            await post_error(
                channel=output_channel,
                error_message=str(exc),
                stage=exc.stage,
                error_mention_role_id=cfg.discord.error_mention_role_id,
            )
        raise

    except Exception as exc:
        if status_msg:
            try:
                await status_msg.delete()
            except discord.HTTPException:
                pass
        await post_error(
            channel=output_channel,
            error_message=str(exc),
            stage="unknown",
            error_mention_role_id=cfg.discord.error_mention_role_id,
        )
        raise


async def _stage_download(
    recording: DetectedRecording,
    session: aiohttp.ClientSession,
    cfg: Config,
    dest: Path,
) -> list[SpeakerAudio]:
    """Stage 1: Download per-speaker audio from Craig."""
    t0 = time.monotonic()
    logger.info("[download] Starting for rec_id=%s", recording.rec_id)

    client = CraigClient(session, recording, cfg.craig)
    tracks = await client.download(dest)

    elapsed = time.monotonic() - t0
    logger.info(
        "[download] Complete: %d tracks in %.1fs",
        len(tracks),
        elapsed,
    )
    return tracks


def _stage_transcribe(
    transcriber: Transcriber,
    tracks: list[SpeakerAudio],
) -> list[Segment]:
    """Stage 2: Transcribe all audio tracks."""
    t0 = time.monotonic()
    logger.info("[transcribe] Starting for %d tracks", len(tracks))

    if not transcriber.is_loaded:
        raise TranscriptionError("Whisper model not loaded")

    segments = transcriber.transcribe_all(tracks)

    elapsed = time.monotonic() - t0
    logger.info(
        "[transcribe] Complete: %d segments in %.1fs",
        len(segments),
        elapsed,
    )
    return segments
