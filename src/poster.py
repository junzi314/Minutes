"""Discord embed formatting and message posting for meeting minutes."""

from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import datetime
from typing import Any

import discord

from src.config import PosterConfig
from src.errors import PostingError

logger = logging.getLogger(__name__)

# Type alias for channels that support minutes posting
OutputChannel = discord.TextChannel | discord.ForumChannel

# Max retry attempts for Discord API calls
_MAX_RETRIES = 3

# Regex patterns to extract sections from generated minutes markdown
_SUMMARY_PATTERN = re.compile(
    r"## まとめ\s*\n(.*?)(?=\n## |\Z)", re.DOTALL
)
_DECISIONS_PATTERN = re.compile(
    r"## 推奨される次のステップ\s*\n(.*?)(?=\n## |\Z)", re.DOTALL
)
_SPEAKERS_PATTERN = re.compile(
    r"- 参加者:\s*(.+)"
)


def _extract_section(text: str, pattern: re.Pattern) -> str:
    """Extract a section from the minutes markdown."""
    match = pattern.search(text)
    if match:
        return match.group(1).strip()
    return ""


def _truncate(text: str, max_length: int) -> str:
    """Truncate text to max_length, adding ellipsis if truncated."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def build_minutes_embed(
    minutes_md: str,
    date: str,
    speakers: str,
    cfg: PosterConfig,
    speaker_stats: str | None = None,
    event_title: str | None = None,
    google_docs_url: str | None = None,
) -> discord.Embed:
    """Build a Discord embed summarising the generated minutes."""
    summary = _extract_section(minutes_md, _SUMMARY_PATTERN)
    decisions = _extract_section(minutes_md, _DECISIONS_PATTERN)

    embed = discord.Embed(
        title=f"会議議事録 — {date}",
        color=cfg.embed_color,
        timestamp=datetime.now(),
    )

    # Meeting name from calendar
    if event_title:
        embed.add_field(
            name="会議名",
            value=event_title,
            inline=False,
        )

    # Participants
    if speakers:
        embed.add_field(
            name="参加者",
            value=speakers,
            inline=False,
        )

    # Summary
    if summary:
        embed.add_field(
            name="まとめ",
            value=_truncate(summary, 1024),
            inline=False,
        )

    # Next steps
    if decisions:
        embed.add_field(
            name="次のステップ",
            value=_truncate(decisions, 1024),
            inline=False,
        )

    # Speaker statistics (after decisions, before footer)
    if speaker_stats:
        embed.add_field(
            name="\U0001f4ca 話者統計",
            value=_truncate(speaker_stats, 1024),
            inline=False,
        )

    if google_docs_url:
        embed.set_footer(text="詳細はGoogle Docsを参照")
    else:
        embed.set_footer(text="詳細議事録は添付ファイルを参照")

    # Ensure total embed length stays within Discord limits
    total_len = len(embed.title or "") + sum(
        len(f.name) + len(f.value) for f in embed.fields
    ) + len(embed.footer.text or "")

    if total_len > cfg.max_embed_length:
        # Trim the summary field to fit
        if embed.fields and len(embed.fields) >= 2:
            overshoot = total_len - cfg.max_embed_length
            current_summary = embed.fields[1].value
            trimmed = current_summary[: len(current_summary) - overshoot - 3] + "..."
            embed.set_field_at(
                1,
                name="まとめ",
                value=trimmed,
                inline=False,
            )

    return embed


def build_error_embed(
    error_message: str,
    stage: str,
    error_mention_role_id: int | None = None,
) -> tuple[discord.Embed, str]:
    """Build an error embed and optional role mention string.

    Returns (embed, mention_text) where mention_text may be empty.
    """
    embed = discord.Embed(
        title="議事録生成エラー",
        description=_truncate(error_message, 2000),
        color=0xFF0000,
        timestamp=datetime.now(),
    )
    embed.add_field(name="失敗ステージ", value=stage, inline=True)

    mention = ""
    if error_mention_role_id:
        mention = f"<@&{error_mention_role_id}>"

    return embed, mention


def build_minutes_file(minutes_md: str, date: str) -> discord.File:
    """Create a discord.File attachment from the full minutes markdown."""
    safe_date = date.replace("/", "-").replace(" ", "_")
    filename = f"minutes_{safe_date}.md"
    buffer = io.BytesIO(minutes_md.encode("utf-8"))
    return discord.File(fp=buffer, filename=filename)


def build_transcript_file(transcript_md: str, date: str) -> discord.File:
    """Create a discord.File attachment from the formatted transcript markdown."""
    safe_date = date.replace("/", "-").replace(" ", "_")
    filename = f"transcript_{safe_date}.md"
    buffer = io.BytesIO(transcript_md.encode("utf-8"))
    return discord.File(fp=buffer, filename=filename)


async def _send_with_retry(coro_factory, description: str) -> discord.Message:
    """Retry a Discord send/edit operation on rate limit (429) errors.

    *coro_factory* is a callable that returns a new coroutine each invocation
    (needed because discord.py File objects are single-use).
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return await coro_factory()
        except discord.HTTPException as exc:
            last_exc = exc
            if exc.status == 429:
                retry_after = getattr(exc, "retry_after", 2 ** (attempt - 1))
                logger.warning(
                    "%s rate-limited (attempt %d/%d), retrying in %.1fs",
                    description, attempt, _MAX_RETRIES, retry_after,
                )
                await asyncio.sleep(retry_after)
                continue
            # Non-rate-limit HTTP errors are not retried
            raise PostingError(f"{description} failed: {exc}") from exc
    raise PostingError(
        f"{description} failed after {_MAX_RETRIES} retries: {last_exc}"
    ) from last_exc


async def post_minutes(
    channel: OutputChannel,
    minutes_md: str,
    date: str,
    speakers: str,
    cfg: PosterConfig,
    speaker_stats: str | None = None,
    transcript_md: str | None = None,
    event_title: str | None = None,
    google_docs_url: str | None = None,
) -> discord.Message:
    """Post minutes embed + markdown file(s) to the channel.

    For ForumChannel, creates a new thread with the minutes title.
    For TextChannel, sends a message directly.
    When *transcript_md* is provided, attaches both minutes and transcript files.
    Retries on Discord rate limits. Returns the sent message.
    """
    embed = build_minutes_embed(
        minutes_md, date, speakers, cfg,
        speaker_stats=speaker_stats, event_title=event_title,
        google_docs_url=google_docs_url,
    )

    mention_text = " ".join(f"<@{uid}>" for uid in cfg.mention_user_ids) or None

    # Build a persistent View with a link button when Google Docs URL is available
    view: discord.ui.View | None = None
    if google_docs_url:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            style=discord.ButtonStyle.link,
            label="▶ Google Docs で全文を見る",
            url=google_docs_url,
            emoji="\U0001f4c4",
        ))

    def _build_files() -> list[discord.File]:
        """Build the list of file attachments (recreated each attempt).

        When a Google Docs URL is available, the minutes MD file is omitted
        (the link is in the embed instead). Transcript is still attached
        if configured.
        """
        files: list[discord.File] = []
        if not google_docs_url:
            files.append(build_minutes_file(minutes_md, date))
        if transcript_md:
            files.append(build_transcript_file(transcript_md, date))
        return files

    if isinstance(channel, discord.ForumChannel):
        thread_title = _truncate(f"会議議事録 — {date}", 100)

        # Step 1: Create thread with embed
        async def _create_thread():
            kwargs: dict[str, Any] = dict(
                name=thread_title,
                content=mention_text,
                embed=embed,
            )
            if view is not None:
                kwargs["view"] = view
            result = await channel.create_thread(**kwargs)
            return result

        thread_result = await _send_with_retry(
            _create_thread, "Create forum thread"
        )
        thread = thread_result.thread
        message = thread_result.message

        # Step 2: Send file(s) as a follow-up in the same thread (if any)
        files_to_send = _build_files()
        if files_to_send:
            async def _send_files():
                files = _build_files()
                return await thread.send(files=files)

            await _send_with_retry(_send_files, "Post minutes file(s) (forum thread)")

        logger.info(
            "Minutes posted to forum #%s as thread '%s' (message_id=%d, files=%d)",
            channel.name,
            thread_title,
            message.id,
            1 + (1 if transcript_md else 0),
        )
        return message

    async def _send():
        # Recreate Files each attempt (discord.py closes the buffer after send)
        files = _build_files()
        kwargs: dict[str, Any] = dict(
            content=mention_text, embed=embed, files=files,
        )
        if view is not None:
            kwargs["view"] = view
        return await channel.send(**kwargs)

    message = await _send_with_retry(_send, "Post minutes")
    logger.info(
        "Minutes posted to #%s (message_id=%d, files=%d)",
        channel.name,
        message.id,
        1 + (1 if transcript_md else 0),
    )
    return message


async def post_error(
    channel: OutputChannel,
    error_message: str,
    stage: str,
    error_mention_role_id: int | None = None,
) -> discord.Message:
    """Post an error embed to the channel with optional admin mention.

    For ForumChannel, creates a new thread for the error.
    """
    embed, mention = build_error_embed(error_message, stage, error_mention_role_id)

    if isinstance(channel, discord.ForumChannel):
        async def _send():
            result = await channel.create_thread(
                name=_truncate(f"議事録生成エラー — {stage}", 100),
                content=mention or None,
                embed=embed,
            )
            return result.message

        try:
            message = await _send_with_retry(_send, "Post error (forum thread)")
            logger.info(
                "Error posted to forum #%s as thread (stage=%s, message_id=%d)",
                channel.name,
                stage,
                message.id,
            )
            return message
        except PostingError:
            logger.error("Failed to post error thread after retries")
            raise

    async def _send():
        return await channel.send(content=mention or None, embed=embed)

    try:
        message = await _send_with_retry(_send, "Post error")
        logger.info(
            "Error posted to #%s (stage=%s, message_id=%d)",
            channel.name,
            stage,
            message.id,
        )
        return message
    except PostingError:
        logger.error("Failed to post error embed after retries")
        raise


async def send_status_update(
    channel: OutputChannel,
    message: discord.Message | None,
    status_text: str,
) -> discord.Message | None:
    """Create or edit a status message in the channel.

    If *message* is None, sends a new message. Otherwise edits the existing one.
    Returns the message object, or None if the initial send fails. Failures are
    logged but do not raise -- status messages are non-critical.

    For ForumChannel, status updates are silently skipped since forum channels
    do not support direct messages (threads are created only for final output).
    """
    if isinstance(channel, discord.ForumChannel):
        return None

    try:
        if message is None:
            return await channel.send(status_text)
        else:
            await message.edit(content=status_text)
            return message
    except discord.HTTPException as exc:
        logger.warning("Status update failed (non-critical): %s", exc)
        # Return the existing message (or None) so the pipeline continues
        return message
