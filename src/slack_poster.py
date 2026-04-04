"""Slack Web API poster for meeting minutes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.errors import PostingError
from src.slack_config import SlackConfig

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3


class SlackPoster:
    """Posts meeting minutes and status updates to Slack."""

    def __init__(self, cfg: SlackConfig) -> None:
        self._cfg = cfg
        self._client = WebClient(token=cfg.bot_token)

    async def post_minutes_to_slack(
        self,
        minutes_md: str,
        title: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Post minutes to Slack using Block Kit format.

        Returns the message timestamp (ts) for threading.
        """
        blocks = self._build_minutes_blocks(minutes_md, title, metadata)

        response = await self._send_with_retry(
            lambda: self._client.chat_postMessage(
                channel=self._cfg.channel_id,
                blocks=blocks,
                text=title,  # fallback for notifications
            ),
            description=f"post minutes '{title}'",
        )

        ts: str = response["ts"]
        logger.info("Posted minutes to Slack: ts=%s, title=%s", ts, title)
        return ts

    async def post_transcript_file(
        self,
        thread_ts: str,
        transcript_md: str,
        filename: str = "transcript.md",
    ) -> None:
        """Upload transcript as a file in the thread.

        Skipped if include_transcript is False.
        """
        if not self._cfg.include_transcript:
            logger.debug("Transcript upload skipped (include_transcript=False)")
            return

        try:
            await self._send_with_retry(
                lambda: self._client.files_upload_v2(
                    channel=self._cfg.channel_id,
                    thread_ts=thread_ts,
                    content=transcript_md,
                    filename=filename,
                    title="Full Transcript",
                ),
                description="upload transcript file",
            )
            logger.info("Uploaded transcript file: %s", filename)
        except PostingError:
            logger.warning("Failed to upload transcript file, continuing")

    async def send_slack_status(self, thread_ts: str, message: str) -> None:
        """Post a status update in the thread.

        Skipped if thread_replies is False. Non-critical — failures are logged.
        """
        if not self._cfg.thread_replies:
            return

        try:
            await self._send_with_retry(
                lambda: self._client.chat_postMessage(
                    channel=self._cfg.channel_id,
                    thread_ts=thread_ts,
                    text=message,
                ),
                description="send status update",
            )
        except PostingError:
            logger.warning("Failed to send status update: %s", message)

    async def post_error_to_slack(
        self, error_message: str, source_label: str
    ) -> None:
        """Post an error notification to Slack."""
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Error: Minutes Generation Failed"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Source:* `{source_label}`\n*Error:* {error_message}",
                },
            },
        ]

        try:
            await self._send_with_retry(
                lambda: self._client.chat_postMessage(
                    channel=self._cfg.channel_id,
                    blocks=blocks,
                    text=f"Error processing {source_label}: {error_message}",
                    attachments=[{"color": "#FF0000", "blocks": []}],
                ),
                description="post error notification",
            )
            logger.info("Posted error notification for %s", source_label)
        except PostingError:
            logger.error(
                "Failed to post error to Slack: %s — %s", source_label, error_message
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_minutes_blocks(
        self,
        minutes_md: str,
        title: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Build Block Kit blocks for minutes message."""
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": title, "emoji": True},
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": self._truncate(minutes_md, 3000),
                },
            },
        ]

        if metadata:
            context_elements = [
                {"type": "mrkdwn", "text": f"*{k}:* {v}"}
                for k, v in metadata.items()
            ]
            blocks.append({"type": "context", "elements": context_elements})

        return blocks

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        """Truncate text to max_len, appending '...' if needed."""
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."

    async def _send_with_retry(
        self,
        call: Any,
        description: str,
    ) -> Any:
        """Execute a Slack API call with retry on rate limits.

        Runs the synchronous WebClient call in an executor to avoid
        blocking the asyncio event loop.
        """
        loop = asyncio.get_running_loop()
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return await loop.run_in_executor(None, call)
            except SlackApiError as exc:
                last_exc = exc
                if exc.response.status_code == 429:
                    retry_after = float(
                        exc.response.headers.get("Retry-After", 2 ** (attempt - 1))
                    )
                    logger.warning(
                        "%s rate-limited (attempt %d/%d), retrying in %.1fs",
                        description,
                        attempt,
                        _MAX_RETRIES,
                        retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                raise PostingError(
                    f"{description} failed: {exc}"
                ) from exc

        raise PostingError(
            f"{description} failed after {_MAX_RETRIES} retries: {last_exc}"
        ) from last_exc
