"""Pipeline orchestrator: download -> transcribe -> merge -> generate -> post.

This module ties together audio acquisition, transcription, transcript
merging, LLM minutes generation, and Discord posting into a single
async pipeline.

Two entry points:
  - run_pipeline(): Craig detection flow (download + process)
  - run_pipeline_from_tracks(): Pre-downloaded audio (Drive watcher, etc.)
"""

from __future__ import annotations

import asyncio
import hashlib
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
from src.errors import MinutesBotError, ProcessingTimeoutError, TranscriptionError
from src.generator import MinutesGenerator
from src.merger import format_transcript_markdown, merge_transcripts
from src.poster import OutputChannel, post_error, post_minutes, send_status_update
from src.state_store import StateStore
from src.minutes_archive import MinutesArchive
from src.transcriber import Segment, Transcriber

logger = logging.getLogger(__name__)


def _transcript_hash(transcript: str, template_name: str = "minutes") -> str:
    """Compute a deterministic cache key from the transcript text and template."""
    key = f"{template_name}:{transcript}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


async def run_pipeline_from_tracks(
    tracks: list[SpeakerAudio],
    cfg: Config,
    transcriber: Transcriber,
    generator: MinutesGenerator,
    output_channel: OutputChannel,
    state_store: StateStore,
    source_label: str = "unknown",
    template_name: str = "minutes",
    archive: MinutesArchive | None = None,
    exporter: object | None = None,
    error_mention_role_id: int | None = None,
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

    timeout_sec = cfg.pipeline.processing_timeout_sec

    try:
        async with asyncio.timeout(timeout_sec):
            # Status: transcribing
            speaker_names = [t.speaker.username for t in tracks]
            status_msg = await send_status_update(
                output_channel, status_msg,
                f"文字起こし中... ({len(tracks)}人: {', '.join(speaker_names)})",
            )

            # Stage 2: Transcribe (runs in thread to keep event loop free)
            segments = await _stage_transcribe(transcriber, tracks)

            # Glossary correction (between transcribe and merge)
            if cfg.transcript_glossary.enabled:
                guild_id = output_channel.guild.id if output_channel.guild else 0
                glossary = state_store.get_guild_glossary(guild_id)
                if glossary:
                    from src.glossary import apply_glossary
                    segments = apply_glossary(
                        segments, glossary, cfg.transcript_glossary.case_sensitive,
                    )
                    logger.info("[glossary] Applied %d entries for guild=%d", len(glossary), guild_id)

            # Speaker analytics (between transcribe and merge)
            speaker_stats_text: str | None = None
            if cfg.speaker_analytics.enabled:
                from src.speaker_analytics import calculate_speaker_stats, format_stats_embed
                stats = calculate_speaker_stats(segments)
                if stats:
                    speaker_stats_text = format_stats_embed(stats)

            # Stage 3: Merge transcript
            transcript = merge_transcripts(segments, cfg.merger)
            if not transcript:
                raise TranscriptionError(
                    f"Transcription produced empty result for source {source_label}"
                )

            # Stage 3.5: Calendar event fetch (optional, non-blocking)
            event_title = ""
            event_attendees = ""
            event_description = ""

            if cfg.calendar.enabled:
                try:
                    from src.calendar_client import CalendarClient, estimate_recording_window

                    rec_start, rec_end = estimate_recording_window(
                        segments, datetime.now(), cfg.calendar.timezone,
                    )
                    calendar_client = CalendarClient(cfg.calendar)
                    cal_result = await calendar_client.fetch_event(rec_start, rec_end)

                    if cal_result.event:
                        event_title = cal_result.event.title
                        event_attendees = ", ".join(cal_result.event.attendees)
                        event_description = cal_result.event.description
                        logger.info(
                            "Calendar event matched: '%s' (%d candidates)",
                            event_title, cal_result.candidates_count,
                        )
                    elif cal_result.error:
                        logger.warning("Calendar fetch error: %s", cal_result.error)
                    else:
                        logger.info("No calendar event found for recording window")
                except Exception:
                    logger.warning("Calendar integration failed (non-critical)", exc_info=True)

            # Stage 4: Generate minutes (with cache)
            speakers_str = ", ".join(speaker_names)
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            # Prepare transcript markdown for attachment (if enabled)
            transcript_md: str | None = None
            if cfg.poster.include_transcript:
                transcript_md = format_transcript_markdown(
                    transcript, date_str, speakers_str,
                )
            guild_name = output_channel.guild.name if output_channel.guild else ""

            th = _transcript_hash(transcript, template_name)
            minutes_md = state_store.get_cached_minutes(th)

            if minutes_md is None:
                status_msg = await send_status_update(
                    output_channel, status_msg, "議事録を生成中..."
                )
                minutes_md = await generator.generate(
                    transcript=transcript,
                    date=date_str,
                    speakers=speakers_str,
                    guild_name=guild_name,
                    channel_name=output_channel.name,
                    template_name=template_name,
                    event_title=event_title,
                    event_attendees=event_attendees,
                    event_description=event_description,
                )
                state_store.put_cached_minutes(th, minutes_md)
            else:
                logger.info("Using cached minutes for source=%s", source_label)

            # Status: posting
            status_msg = await send_status_update(
                output_channel, status_msg, "議事録を投稿中..."
            )

            # Stage 5: Post to Discord
            message = await post_minutes(
                channel=output_channel,
                minutes_md=minutes_md,
                date=date_str,
                speakers=speakers_str,
                cfg=cfg.poster,
                speaker_stats=speaker_stats_text,
                transcript_md=transcript_md,
                event_title=event_title or None,
            )

            # Archive (fault-tolerant)
            if archive is not None and cfg.minutes_archive.enabled:
                try:
                    archive.store(
                        guild_id=output_channel.guild.id,
                        date_str=date_str,
                        speakers=speakers_str,
                        minutes_md=minutes_md,
                        source_label=source_label,
                        channel_name=output_channel.name,
                        template_name=template_name,
                        transcript_hash=th,
                        message_id=message.id if message else None,
                    )
                except Exception:
                    logger.warning("Archive write failed (non-critical)", exc_info=True)

            # Export to Google Docs (fault-tolerant)
            if exporter is not None and cfg.export_google_docs.enabled:
                try:
                    title = f"Meeting Minutes — {date_str}"
                    export_result = await exporter.export(
                        minutes_md=minutes_md,
                        title=title,
                        metadata={"date": date_str, "speakers": speakers_str, "source": source_label},
                    )
                    if export_result.success:
                        logger.info("Minutes exported to Google Docs: %s", export_result.url)
                    else:
                        logger.warning("Google Docs export failed (non-critical): %s", export_result.error)
                except Exception:
                    logger.warning("Google Docs export raised unexpected error (non-critical)", exc_info=True)

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

    except TimeoutError:
        elapsed = time.monotonic() - pipeline_start
        logger.error(
            "Pipeline processing timed out for source=%s after %.1fs (limit=%ds)",
            source_label,
            elapsed,
            timeout_sec,
        )
        if status_msg:
            try:
                await status_msg.delete()
            except discord.HTTPException:
                pass
        raise ProcessingTimeoutError(
            f"ローカル処理がタイムアウトしました ({timeout_sec}秒): {source_label}"
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
            error_mention_role_id=error_mention_role_id,
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
            error_mention_role_id=error_mention_role_id,
        )
        raise


async def run_pipeline(
    recording: DetectedRecording,
    session: aiohttp.ClientSession,
    cfg: Config,
    transcriber: Transcriber,
    generator: MinutesGenerator,
    output_channel: OutputChannel,
    state_store: StateStore,
    template_name: str = "minutes",
    archive: MinutesArchive | None = None,
    exporter: object | None = None,
    error_mention_role_id: int | None = None,
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
                state_store=state_store,
                source_label=f"craig:{recording.rec_id}",
                template_name=template_name,
                archive=archive,
                exporter=exporter,
                error_mention_role_id=error_mention_role_id,
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
                error_mention_role_id=error_mention_role_id,
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
            error_mention_role_id=error_mention_role_id,
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


async def _stage_transcribe(
    transcriber: Transcriber,
    tracks: list[SpeakerAudio],
) -> list[Segment]:
    """Stage 2: Transcribe all audio tracks (runs in a thread to avoid blocking the event loop)."""
    t0 = time.monotonic()
    logger.info("[transcribe] Starting for %d tracks", len(tracks))

    if not transcriber.is_loaded:
        raise TranscriptionError("Whisper model not loaded")

    segments = await asyncio.to_thread(transcriber.transcribe_all, tracks)

    elapsed = time.monotonic() - t0
    logger.info(
        "[transcribe] Complete: %d segments in %.1fs",
        len(segments),
        elapsed,
    )
    return segments
