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
from src.config import Config, GoogleDriveConfig, GuildConfig, load
from src.detector import DetectedRecording, RECORDING_URL_PATTERN, parse_recording_ended
from src.drive_watcher import DriveWatcher
from src.errors import MinutesBotError
from src.generator import MinutesGenerator
from src.minutes_archive import MinutesArchive
from src.pipeline import run_pipeline, run_pipeline_from_tracks
from src.poster import OutputChannel
from src.state_store import StateStore
from src.transcriber import Transcriber, create_transcriber

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
        state_store: StateStore,
        archive: MinutesArchive | None = None,
        exporter: object | None = None,
        **kwargs: object,
    ) -> None:
        self.cfg = cfg
        self.transcriber = transcriber
        self.generator = generator
        self.state_store = state_store
        self.archive = archive
        self.exporter = exporter
        self.http_session: aiohttp.ClientSession | None = None
        self.drive_watchers: dict[int, DriveWatcher] = {}
        self._start_time = time.monotonic()
        super().__init__(**kwargs)
        self.tree = discord.app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        """Called once when the bot starts, before connecting to the gateway."""
        self.http_session = aiohttp.ClientSession()
        logger.debug("aiohttp.ClientSession created in setup_hook")

    async def close(self) -> None:
        """Clean up resources on shutdown."""
        for watcher in self.drive_watchers.values():
            watcher.stop()
        if self.archive is not None:
            self.archive.close()
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
            logger.debug("aiohttp.ClientSession closed")
        await super().close()

    async def on_ready(self) -> None:
        logger.info("Bot connected as %s (id=%d)", self.user, self.user.id)

        # Sync slash commands to all configured guilds
        for gcfg in self.cfg.discord.guilds:
            guild = discord.Object(id=gcfg.guild_id)
            self.tree.copy_global_to(guild=guild)
            try:
                await self.tree.sync(guild=guild)
                logger.info("Slash commands synced to guild %d", gcfg.guild_id)
            except discord.Forbidden:
                logger.warning(
                    "Failed to sync slash commands to guild %d: Missing Access. "
                    "Re-invite the bot with the 'applications.commands' OAuth2 scope.",
                    gcfg.guild_id,
                )

        for gcfg in self.cfg.discord.guilds:
            logger.info(
                "Watching channel %d in guild %d",
                gcfg.watch_channel_id,
                gcfg.guild_id,
            )

        # Start per-guild Google Drive watchers
        self._start_drive_watchers()

    def resolve_template(self, guild_id: int) -> str:
        """Resolve template name for a guild.

        Priority: state_store override -> GuildConfig.template -> "minutes"
        """
        override = self.state_store.get_guild_template(guild_id)
        if override:
            return override
        guild_cfg = self.cfg.discord.get_guild(guild_id)
        if guild_cfg:
            return guild_cfg.template
        return "minutes"

    def _get_output_channel_for_guild(
        self, guild_cfg: GuildConfig | None
    ) -> OutputChannel | None:
        """Resolve the output channel for a specific guild config.

        Supports both TextChannel and ForumChannel as output destinations.
        """
        if guild_cfg is None:
            return None
        ch = self.get_channel(guild_cfg.output_channel_id)
        if isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
            return ch
        return None

    def _start_drive_watchers(self) -> None:
        """Start per-guild Google Drive watchers based on config.

        For each guild, resolve Drive settings (guild-level overrides or
        global fallback) and create a DriveWatcher with a callback bound
        to that guild's output channel.
        """
        global_drive = self.cfg.google_drive

        for gcfg in self.cfg.discord.guilds:
            # Resolve per-guild Drive config
            guild_drive = gcfg.google_drive
            if guild_drive is not None:
                enabled = guild_drive.enabled
                folder_id = guild_drive.folder_id or global_drive.folder_id
            else:
                enabled = global_drive.enabled
                folder_id = global_drive.folder_id

            if not enabled or not folder_id:
                continue

            output_channel = self._get_output_channel_for_guild(gcfg)
            if output_channel is None:
                logger.error(
                    "Output channel %d not found for guild %d, Drive watcher will not start",
                    gcfg.output_channel_id,
                    gcfg.guild_id,
                )
                continue

            # Build a DriveWatcher config using guild folder_id + global shared settings
            watcher_cfg = GoogleDriveConfig(
                enabled=True,
                credentials_path=global_drive.credentials_path,
                folder_id=folder_id,
                file_pattern=global_drive.file_pattern,
                poll_interval_sec=global_drive.poll_interval_sec,
            )

            # Closure captures gcfg and output_channel for this guild
            _guild_cfg = gcfg
            _out_ch = output_channel

            async def _on_drive_tracks(
                tracks: list[SpeakerAudio],
                source_label: str,
                tmp_dir: Path,
                *,
                _gcfg: GuildConfig = _guild_cfg,
                _channel: OutputChannel = _out_ch,
            ) -> None:
                template_name = self.resolve_template(_gcfg.guild_id)
                error_role = self.cfg.discord.resolve_error_role(_gcfg.guild_id)
                await run_pipeline_from_tracks(
                    tracks=tracks,
                    cfg=self.cfg,
                    transcriber=self.transcriber,
                    generator=self.generator,
                    output_channel=_channel,
                    state_store=self.state_store,
                    source_label=source_label,
                    template_name=template_name,
                    archive=self.archive,
                    exporter=self.exporter,
                    error_mention_role_id=error_role,
                )

            watcher = DriveWatcher(
                cfg=watcher_cfg,
                state_store=self.state_store,
                on_new_tracks=_on_drive_tracks,
            )
            watcher.start()
            self.drive_watchers[gcfg.guild_id] = watcher
            logger.info(
                "Google Drive watcher started for guild %d (folder=%s, output=%d)",
                gcfg.guild_id,
                folder_id,
                gcfg.output_channel_id,
            )

    async def on_raw_message_update(
        self, payload: discord.RawMessageUpdateEvent
    ) -> None:
        """Handle message edits -- Craig updates its panel on recording end."""
        try:
            data = payload.data
            channel_id = payload.channel_id
            guild_id = payload.guild_id or 0
            message_id = payload.message_id

            # Look up guild config for this event's guild
            guild_cfg = self.cfg.discord.get_guild(guild_id)
            if guild_cfg is None:
                return  # Event from unconfigured guild — ignore silently

            recording = parse_recording_ended(
                payload_data=data,
                channel_id=channel_id,
                guild_id=guild_id,
                message_id=message_id,
                watch_channel_id=guild_cfg.watch_channel_id,
            )

            if recording is None:
                return

            logger.info(
                "Recording ended detected: rec_id=%s channel=%d guild=%d",
                recording.rec_id,
                recording.channel_id,
                recording.guild_id,
            )

            output_channel = self._get_output_channel_for_guild(guild_cfg)
            if output_channel is None:
                logger.error(
                    "Output channel %d not found for guild %d, skipping pipeline",
                    guild_cfg.output_channel_id,
                    guild_cfg.guild_id,
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
        output_channel: OutputChannel,
    ) -> None:
        """Fire-and-forget pipeline task with error logging and dedup guard."""
        rec_id = recording.rec_id

        if not self.state_store.mark_processing(
            rec_id, source="craig", source_id=rec_id, file_name=""
        ):
            logger.warning(
                "Skipping duplicate pipeline for rec_id=%s (already known)", rec_id,
            )
            return

        template_name = self.resolve_template(recording.guild_id)
        error_role = self.cfg.discord.resolve_error_role(recording.guild_id)

        task = asyncio.create_task(
            run_pipeline(
                recording=recording,
                session=self.http_session,
                cfg=self.cfg,
                transcriber=self.transcriber,
                generator=self.generator,
                output_channel=output_channel,
                state_store=self.state_store,
                template_name=template_name,
                archive=self.archive,
                exporter=self.exporter,
                error_mention_role_id=error_role,
            ),
            name=f"pipeline-{rec_id}",
        )

        def _on_done(t: asyncio.Task, rid: str = rec_id) -> None:
            if t.cancelled():
                self.state_store.mark_failed(rid, "Pipeline cancelled")
                logger.warning("Pipeline cancelled for rec_id=%s", rid)
            elif (exc := t.exception()) is not None:
                self.state_store.mark_failed(rid, str(exc))
                logger.exception("Pipeline failed for rec_id=%s", rid, exc_info=exc)
            else:
                self.state_store.mark_success(rid)
                logger.info("Pipeline completed successfully for rec_id=%s", rid)

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

        guild_cfg = client.cfg.discord.get_guild(interaction.guild_id or 0)

        backend = getattr(client.transcriber, 'backend_name', 'local')
        model = getattr(client.transcriber, 'model_name', client.cfg.whisper.model)

        lines = [
            f"**Uptime**: {hours}h {minutes}m {seconds}s",
            f"**Whisper backend**: {backend} ({model}, {'loaded' if client.transcriber.is_loaded else 'not loaded'})",
            f"**GPU**: {'available' if gpu_available else 'not available'}",
            f"**Generator**: {client.cfg.generator.model} ({'ready' if client.generator.is_loaded else 'not ready'})",
        ]
        lines.append(f"**Template**: {client.resolve_template(interaction.guild_id or 0)}")
        if guild_cfg:
            lines.append(f"**Watch channel**: <#{guild_cfg.watch_channel_id}>")
            lines.append(f"**Output channel**: <#{guild_cfg.output_channel_id}>")
        else:
            lines.append("**Guild**: not configured")
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

        guild_cfg = client.cfg.discord.get_guild(interaction.guild_id or 0)
        output_channel = client._get_output_channel_for_guild(guild_cfg)
        if output_channel is None:
            await interaction.response.send_message(
                "Output channel not configured or not found for this guild.", ephemeral=True,
            )
            return

        if client.http_session is None:
            await interaction.response.send_message(
                "Bot HTTP session not ready. Try again shortly.", ephemeral=True,
            )
            return

        if client.state_store.is_known(rec_id):
            await interaction.response.send_message(
                f"Recording `{rec_id}` has already been processed.", ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Processing recording `{rec_id}`... Results will be posted to <#{output_channel.id}>.",
            ephemeral=True,
        )

        client._launch_pipeline(recording, output_channel)

    @group.command(name="drive-status", description="Show Google Drive watcher status")
    async def minutes_drive_status(interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id or 0
        gdcfg = client.cfg.google_drive
        watcher = client.drive_watchers.get(guild_id)

        lines: list[str] = []

        if watcher is not None:
            running = watcher.is_running
            # Get folder_id from the watcher's config
            guild_cfg = client.cfg.discord.get_guild(guild_id)
            guild_drive = guild_cfg.google_drive if guild_cfg else None
            folder_id = (
                (guild_drive.folder_id if guild_drive and guild_drive.folder_id else None)
                or gdcfg.folder_id
            )
            lines.append(f"**このギルドのDrive監視**: {'running' if running else 'stopped'}")
            lines.append(f"**Folder ID**: `{folder_id or '(not set)'}`")
        else:
            lines.append("**このギルドのDrive監視**: 無効")

        # Global info
        total_running = sum(1 for w in client.drive_watchers.values() if w.is_running)
        total_watchers = len(client.drive_watchers)
        if total_watchers > 0:
            lines.append(f"**全体**: {total_running}/{total_watchers} ギルド稼働中")
        lines.append(f"**File pattern**: `{gdcfg.file_pattern}`")
        lines.append(f"**Poll interval**: {gdcfg.poll_interval_sec}s")
        lines.append(f"**Processed files**: {client.state_store.processing_count}")

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @group.command(name="template-list", description="Show available templates")
    async def template_list(interaction: discord.Interaction) -> None:
        templates = client.generator.list_templates()
        current = client.resolve_template(interaction.guild_id or 0)
        embed = discord.Embed(title="利用可能なテンプレート", color=0x5865F2)
        for t in templates:
            marker = " (現在)" if t.name == current else ""
            embed.add_field(
                name=f"{t.display_name}{marker}",
                value=t.description or "説明なし",
                inline=False,
            )
        embed.set_footer(text="/minutes template-set <名前> で変更")
        await interaction.response.send_message(embed=embed)

    @group.command(name="template-set", description="Set the template for this guild")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    @discord.app_commands.describe(name="Template name")
    async def template_set(interaction: discord.Interaction, name: str) -> None:
        available = {t.name for t in client.generator.list_templates()}
        if name not in available:
            await interaction.response.send_message(
                f"テンプレート `{name}` は見つかりません。\n"
                f"`/minutes template-list` で利用可能なテンプレートを確認してください。",
                ephemeral=True,
            )
            return
        client.state_store.set_guild_template(interaction.guild_id or 0, name)
        await interaction.response.send_message(
            f"テンプレートを **{name}** に変更しました。次回の議事録生成から適用されます。",
            ephemeral=True,
        )

    @template_set.error
    async def template_set_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
        if isinstance(error, discord.app_commands.MissingPermissions):
            await interaction.response.send_message(
                "テンプレート変更には「サーバー管理」権限が必要です。", ephemeral=True
            )
        else:
            raise error

    @template_set.autocomplete("name")
    async def template_name_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[discord.app_commands.Choice[str]]:
        templates = client.generator.list_templates()
        return [
            discord.app_commands.Choice(name=t.display_name, value=t.name)
            for t in templates if current.lower() in t.name.lower()
        ][:25]

    @group.command(name="search", description="過去の議事録をキーワードで検索")
    @discord.app_commands.describe(keyword="検索キーワード")
    async def minutes_search(interaction: discord.Interaction, keyword: str) -> None:
        if client.archive is None:
            await interaction.response.send_message(
                "議事録アーカイブが有効になっていません。", ephemeral=True
            )
            return

        guild_id = interaction.guild_id or 0
        limit = client.cfg.minutes_archive.max_search_results

        results = await asyncio.to_thread(
            client.archive.search, guild_id, keyword, limit
        )

        if not results:
            total = await asyncio.to_thread(client.archive.count, guild_id)
            if total == 0:
                await interaction.response.send_message(
                    "まだアーカイブされた議事録がありません。"
                    "議事録が生成されると自動的にアーカイブされます。",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"「{keyword}」に一致する議事録は見つかりませんでした。",
                    ephemeral=True,
                )
            return

        embed = discord.Embed(
            title=f"議事録検索結果 — 「{keyword}」",
            color=0x5865F2,
        )
        for r in results:
            speakers_text = f" — 参加者: {r.speakers}" if r.speakers else ""
            embed.add_field(
                name=f"{r.date_str}{speakers_text}",
                value=r.snippet[:200] or "(スニペットなし)",
                inline=False,
            )
        total = await asyncio.to_thread(client.archive.count, guild_id)
        embed.set_footer(text=f"{len(results)}件 / {total}件のアーカイブから検索")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="calendar-status", description="カレンダー連携の状態を表示")
    async def minutes_calendar_status(interaction: discord.Interaction) -> None:
        cal_cfg = client.cfg.calendar
        if not cal_cfg.enabled:
            await interaction.response.send_message(
                "カレンダー連携は**無効**です。\nconfig.yamlの `calendar.enabled` を `true` に設定してください。",
                ephemeral=True,
            )
            return

        lines = [
            "**カレンダー連携**: 有効",
            f"**カレンダーID**: `{cal_cfg.calendar_id}`",
            f"**タイムゾーン**: {cal_cfg.timezone}",
            f"**マッチ許容範囲**: 前後{cal_cfg.match_tolerance_minutes}分",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # -- Glossary commands --

    @group.command(name="glossary-add", description="Add a glossary entry for transcript correction")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    @discord.app_commands.describe(wrong="Incorrect text from Whisper", correct="Correct replacement text")
    async def glossary_add(interaction: discord.Interaction, wrong: str, correct: str) -> None:
        guild_id = interaction.guild_id or 0
        glossary = client.state_store.get_guild_glossary(guild_id)
        glossary[wrong] = correct
        client.state_store.set_guild_glossary(guild_id, glossary)
        await interaction.response.send_message(
            f"辞書に追加しました: `{wrong}` → `{correct}`", ephemeral=True,
        )

    @glossary_add.error
    async def glossary_add_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
        if isinstance(error, discord.app_commands.MissingPermissions):
            await interaction.response.send_message(
                "辞書の編集には「サーバー管理」権限が必要です。", ephemeral=True,
            )
        else:
            raise error

    @group.command(name="glossary-remove", description="Remove a glossary entry")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    @discord.app_commands.describe(wrong="The incorrect text entry to remove")
    async def glossary_remove(interaction: discord.Interaction, wrong: str) -> None:
        guild_id = interaction.guild_id or 0
        glossary = client.state_store.get_guild_glossary(guild_id)
        if wrong not in glossary:
            await interaction.response.send_message(
                f"辞書にエントリ `{wrong}` が見つかりません。", ephemeral=True,
            )
            return
        del glossary[wrong]
        client.state_store.set_guild_glossary(guild_id, glossary)
        await interaction.response.send_message(
            f"辞書から削除しました: `{wrong}`", ephemeral=True,
        )

    @glossary_remove.error
    async def glossary_remove_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
        if isinstance(error, discord.app_commands.MissingPermissions):
            await interaction.response.send_message(
                "辞書の編集には「サーバー管理」権限が必要です。", ephemeral=True,
            )
        else:
            raise error

    @group.command(name="glossary-list", description="Show current glossary entries")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def glossary_list(interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id or 0
        glossary = client.state_store.get_guild_glossary(guild_id)
        if not glossary:
            await interaction.response.send_message(
                "辞書が空です。`/minutes glossary-add` でエントリを追加してください。",
                ephemeral=True,
            )
            return
        embed = discord.Embed(title="用語辞書", color=0x5865F2)
        for wrong, correct in list(glossary.items())[-25:]:
            embed.add_field(name=wrong, value=f"→ {correct}", inline=True)
        footer = f"{len(glossary)}件のエントリ"
        if len(glossary) > 25:
            footer += "（直近25件を表示）"
        embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @glossary_list.error
    async def glossary_list_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
        if isinstance(error, discord.app_commands.MissingPermissions):
            await interaction.response.send_message(
                "辞書の閲覧には「サーバー管理」権限が必要です。", ephemeral=True,
            )
        else:
            raise error

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

    # Initialize unified state store
    state_store = StateStore(Path(cfg.pipeline.state_dir))
    stale_count = state_store.cleanup_stale()
    if stale_count:
        logger.info("Cleaned up %d stale processing entries", stale_count)
    logger.info("StateStore ready (%d entries)", state_store.processing_count)

    # Preload transcriber (factory selects local or API backend)
    transcriber = create_transcriber(cfg.whisper)
    transcriber.load_model()

    # Initialise minutes generator
    generator = MinutesGenerator(cfg.generator)
    generator.load()

    # Initialise minutes archive
    archive: MinutesArchive | None = None
    if cfg.minutes_archive.enabled:
        archive_path = Path(cfg.pipeline.state_dir) / "minutes_archive.db"
        archive = MinutesArchive(archive_path)
        logger.info("MinutesArchive enabled at %s", archive_path)

    # Initialise Google Docs exporter
    exporter = None
    if cfg.export_google_docs.enabled:
        from src.exporter import GoogleDocsExporter
        exporter = GoogleDocsExporter(cfg.export_google_docs)
        logger.info("Google Docs exporter enabled (folder_id=%s)", cfg.export_google_docs.folder_id)

    # Create client with required intents
    intents = discord.Intents.default()
    intents.guilds = True
    intents.guild_messages = True
    intents.message_content = True

    client = MinutesBot(
        cfg=cfg,
        transcriber=transcriber,
        generator=generator,
        state_store=state_store,
        archive=archive,
        exporter=exporter,
        intents=intents,
    )

    # Register slash commands
    register_commands(client, client.tree)

    # Run
    client.run(cfg.discord.token, log_handler=None)


if __name__ == "__main__":
    main()
