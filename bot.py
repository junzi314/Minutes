"""Discord Minutes Bot -- entry point.

Listens for Craig Bot recording-ended events and triggers the
transcription/summarization pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import re
import sys
import time
from pathlib import Path

import aiohttp
import discord

from src.audio_source import SpeakerAudio
from src.config import Config, load
from src.detector import DetectedRecording, RECORDING_URL_PATTERN, parse_recording_ended
from src.drive_watcher import DriveWatcher
from src.errors import MinutesBotError
from src.generator import MinutesGenerator
from src.pipeline import run_pipeline, run_pipeline_from_tracks
from src.transcriber import Transcriber

logger = logging.getLogger("minutes_bot")


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

# Patterns for sensitive data that must be masked in log output.
_SENSITIVE_PATTERNS = re.compile(
    r"(sk-ant-[a-zA-Z0-9_-]{20,})"         # Anthropic API keys
    r"|((?:Bot\s+)?[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,})"  # Discord bot tokens
    r"|(\?key=[a-zA-Z0-9]{6,})"             # Craig access keys in URLs
)


class _SensitiveMaskFilter(logging.Filter):
    """Logging filter that redacts sensitive tokens from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _SENSITIVE_PATTERNS.sub(self._mask, record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: _SENSITIVE_PATTERNS.sub(self._mask, v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    _SENSITIVE_PATTERNS.sub(self._mask, a) if isinstance(a, str) else a
                    for a in record.args
                )
        return True

    @staticmethod
    def _mask(match: re.Match) -> str:
        val = match.group(0)
        if val.startswith("?key="):
            return "?key=***"
        return val[:8] + "***"


def setup_logging(cfg: Config, level_override: str | None = None) -> None:
    """Configure root logger with rotating file handler and stream handler."""
    level_name = level_override or cfg.logging.level
    level = getattr(logging, level_name.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(
        fmt="{asctime} [{levelname}] {name}: {message}",
        style="{",
    )

    mask_filter = _SensitiveMaskFilter()

    # Rotating file handler
    log_path = Path(cfg.logging.file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_path,
        maxBytes=cfg.logging.max_bytes,
        backupCount=cfg.logging.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(mask_filter)
    root.addHandler(file_handler)

    # Console handler
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(mask_filter)
    root.addHandler(stream_handler)

    # Silence noisy libraries
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Bot client
# ---------------------------------------------------------------------------

class MinutesBot(discord.Client):
    """Discord client that detects Craig recording endings."""

    def __init__(
        self,
        cfg: Config,
        transcriber: Transcriber,
        generator: MinutesGenerator,
        **kwargs: object,
    ) -> None:
        self.cfg = cfg
        self.transcriber = transcriber
        self.generator = generator
        self.http_session: aiohttp.ClientSession | None = None
        self.drive_watcher: DriveWatcher | None = None
        self._start_time = time.monotonic()
        super().__init__(**kwargs)
        self.tree = discord.app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        """Called once when the bot starts, before connecting to the gateway."""
        self.http_session = aiohttp.ClientSession()
        logger.debug("aiohttp.ClientSession created in setup_hook")

    async def close(self) -> None:
        """Clean up resources on shutdown."""
        if self.drive_watcher is not None:
            self.drive_watcher.stop()
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
            logger.debug("aiohttp.ClientSession closed")
        await super().close()

    async def on_ready(self) -> None:
        logger.info("Bot connected as %s (id=%d)", self.user, self.user.id)

        guild = discord.Object(id=self.cfg.discord.guild_id)
        self.tree.copy_global_to(guild=guild)
        try:
            await self.tree.sync(guild=guild)
            logger.info("Slash commands synced to guild %d", self.cfg.discord.guild_id)
        except discord.Forbidden:
            logger.warning(
                "Failed to sync slash commands to guild %d: Missing Access. "
                "Re-invite the bot with the 'applications.commands' OAuth2 scope.",
                self.cfg.discord.guild_id,
            )

        logger.info(
            "Watching channel %d in guild %d",
            self.cfg.discord.watch_channel_id,
            self.cfg.discord.guild_id,
        )

        # Start Google Drive watcher if enabled
        if self.cfg.google_drive.enabled:
            output_channel = self._get_output_channel()
            if output_channel is None:
                logger.error(
                    "Output channel %d not found, Drive watcher will not start",
                    self.cfg.discord.output_channel_id,
                )
            else:
                async def _on_drive_tracks(
                    tracks: list[SpeakerAudio],
                    source_label: str,
                    tmp_dir: Path,
                ) -> None:
                    await run_pipeline_from_tracks(
                        tracks=tracks,
                        cfg=self.cfg,
                        transcriber=self.transcriber,
                        generator=self.generator,
                        output_channel=output_channel,
                        source_label=source_label,
                    )

                self.drive_watcher = DriveWatcher(
                    cfg=self.cfg.google_drive,
                    on_new_tracks=_on_drive_tracks,
                )
                self.drive_watcher.start()
                logger.info("Google Drive watcher started")

    def _get_output_channel(self) -> discord.TextChannel | None:
        """Resolve the output channel from config."""
        return self.get_channel(self.cfg.discord.output_channel_id)

    async def on_raw_message_update(
        self, payload: discord.RawMessageUpdateEvent
    ) -> None:
        """Handle message edits -- Craig updates its panel on recording end."""
        try:
            data = payload.data
            channel_id = payload.channel_id
            guild_id = payload.guild_id or 0
            message_id = payload.message_id

            recording = parse_recording_ended(
                payload_data=data,
                channel_id=channel_id,
                guild_id=guild_id,
                message_id=message_id,
                watch_channel_id=self.cfg.discord.watch_channel_id,
            )

            if recording is None:
                return

            logger.info(
                "Recording ended detected: rec_id=%s channel=%d guild=%d",
                recording.rec_id,
                recording.channel_id,
                recording.guild_id,
            )

            output_channel = self._get_output_channel()
            if output_channel is None:
                logger.error(
                    "Output channel %d not found, skipping pipeline",
                    self.cfg.discord.output_channel_id,
                )
                return

            if self.http_session is None:
                logger.error("HTTP session not initialized, skipping pipeline")
                return

            self._launch_pipeline(recording, output_channel)

        except Exception:
            logger.exception("Unhandled error in on_raw_message_update")

    def _launch_pipeline(
        self,
        recording: DetectedRecording,
        output_channel: discord.TextChannel,
    ) -> None:
        """Fire-and-forget pipeline task with error logging."""
        task = asyncio.create_task(
            run_pipeline(
                recording=recording,
                session=self.http_session,
                cfg=self.cfg,
                transcriber=self.transcriber,
                generator=self.generator,
                output_channel=output_channel,
            ),
            name=f"pipeline-{recording.rec_id}",
        )

        def _on_done(t: asyncio.Task, rec_id: str = recording.rec_id) -> None:
            if t.cancelled():
                logger.warning("Pipeline cancelled for rec_id=%s", rec_id)
            elif (exc := t.exception()) is not None:
                logger.exception("Pipeline failed for rec_id=%s", rec_id, exc_info=exc)
            else:
                logger.info("Pipeline completed successfully for rec_id=%s", rec_id)

        task.add_done_callback(_on_done)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

def register_commands(client: MinutesBot, tree: discord.app_commands.CommandTree) -> None:
    """Register slash commands on the command tree."""

    group = discord.app_commands.Group(name="minutes", description="Meeting minutes commands")

    @group.command(name="status", description="Show bot health and status")
    async def minutes_status(interaction: discord.Interaction) -> None:
        uptime_sec = time.monotonic() - client._start_time
        hours, remainder = divmod(int(uptime_sec), 3600)
        minutes, seconds = divmod(remainder, 60)

        try:
            import ctranslate2
            gpu_available = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            gpu_available = False

        lines = [
            f"**Uptime**: {hours}h {minutes}m {seconds}s",
            f"**Whisper model**: {client.cfg.whisper.model} ({'loaded' if client.transcriber.is_loaded else 'not loaded'})",
            f"**GPU**: {'available' if gpu_available else 'not available'}",
            f"**Generator**: {client.cfg.generator.model} ({'ready' if client.generator.is_loaded else 'not ready'})",
            f"**Watch channel**: <#{client.cfg.discord.watch_channel_id}>",
            f"**Output channel**: <#{client.cfg.discord.output_channel_id}>",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @group.command(name="process", description="Process a Craig recording URL")
    @discord.app_commands.describe(url="Craig recording URL (e.g. https://craig.chat/rec/abc?key=xyz)")
    async def minutes_process(interaction: discord.Interaction, url: str) -> None:
        # Parse the URL
        match = RECORDING_URL_PATTERN.search(url)
        if not match:
            await interaction.response.send_message(
                "Invalid Craig recording URL. Expected format: `https://craig.chat/rec/{id}?key={key}`",
                ephemeral=True,
            )
            return

        domain = match.group("domain")
        rec_id = match.group("rec_id")
        key = match.group("key")
        rec_url = f"https://{domain}/rec/{rec_id}?key={key}"

        recording = DetectedRecording(
            rec_id=rec_id,
            access_key=key,
            rec_url=rec_url,
            guild_id=interaction.guild_id or 0,
            channel_id=interaction.channel_id,
            message_id=0,
            craig_domain=domain,
        )

        output_channel = client._get_output_channel()
        if output_channel is None:
            await interaction.response.send_message(
                "Output channel not configured or not found.", ephemeral=True,
            )
            return

        if client.http_session is None:
            await interaction.response.send_message(
                "Bot HTTP session not ready. Try again shortly.", ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Processing recording `{rec_id}`... Results will be posted to <#{output_channel.id}>.",
            ephemeral=True,
        )

        client._launch_pipeline(recording, output_channel)

    @group.command(name="drive-status", description="Show Google Drive watcher status")
    async def minutes_drive_status(interaction: discord.Interaction) -> None:
        gdcfg = client.cfg.google_drive
        watcher = client.drive_watcher

        if not gdcfg.enabled:
            await interaction.response.send_message(
                "Google Drive watcher is **disabled** in config.", ephemeral=True,
            )
            return

        running = watcher is not None and watcher.is_running
        processed_count = watcher.processed_count if watcher else 0

        lines = [
            f"**Drive watcher**: {'running' if running else 'stopped'}",
            f"**Folder ID**: `{gdcfg.folder_id or '(not set)'}`",
            f"**File pattern**: `{gdcfg.file_pattern}`",
            f"**Poll interval**: {gdcfg.poll_interval_sec}s",
            f"**Processed files**: {processed_count}",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    tree.add_command(group)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discord Minutes Bot")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config file (default: config.yaml)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Override log level from config",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Load configuration
    cfg = load(config_path=args.config)

    # Setup logging
    setup_logging(cfg, level_override=args.log_level)

    logger.info("Starting Discord Minutes Bot")

    # Preload Whisper model (keeps it resident in VRAM)
    transcriber = Transcriber(cfg.whisper)
    transcriber.load_model()

    # Initialise minutes generator
    generator = MinutesGenerator(cfg.generator)
    generator.load()

    # Create client with required intents
    intents = discord.Intents.default()
    intents.guilds = True
    intents.guild_messages = True
    intents.message_content = True

    client = MinutesBot(
        cfg=cfg,
        transcriber=transcriber,
        generator=generator,
        intents=intents,
    )

    # Register slash commands
    register_commands(client, client.tree)

    # Run
    client.run(cfg.discord.token, log_handler=None)


if __name__ == "__main__":
    main()
