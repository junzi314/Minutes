"""Unit tests for src/slack_poster.py (Slack Web API poster)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.errors import PostingError
from src.slack_config import SlackConfig
from src.slack_poster import SlackPoster


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(**overrides) -> SlackConfig:
    defaults = dict(
        bot_token="xoxb-test-token",
        channel_id="C0123456789",
        include_transcript=True,
        thread_replies=True,
    )
    defaults.update(overrides)
    return SlackConfig(**defaults)


def _make_poster(cfg: SlackConfig | None = None) -> SlackPoster:
    if cfg is None:
        cfg = _make_cfg()
    return SlackPoster(cfg)


def _mock_slack_response(ts: str = "1234567890.123456", ok: bool = True) -> MagicMock:
    """Create a mock Slack API response."""
    resp = MagicMock()
    resp.__getitem__ = lambda self, key: {"ts": ts, "ok": ok}[key]
    resp.get = lambda key, default=None: {"ts": ts, "ok": ok}.get(key, default)
    resp.status_code = 200
    return resp


# ===========================================================================
# Tests
# ===========================================================================


class TestPostMinutes:
    @pytest.mark.asyncio
    async def test_post_minutes_success(self) -> None:
        """Minutes are posted with Block Kit format."""
        poster = _make_poster()
        poster._client = MagicMock()
        poster._client.chat_postMessage.return_value = _mock_slack_response()

        ts = await poster.post_minutes_to_slack(
            "## Meeting Notes\n- Item 1", "Weekly Meeting", {"speakers": "3"}
        )

        assert ts == "1234567890.123456"
        poster._client.chat_postMessage.assert_called_once()

        call_kwargs = poster._client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "C0123456789"
        assert call_kwargs["text"] == "Weekly Meeting"

    @pytest.mark.asyncio
    async def test_post_minutes_block_kit_format(self) -> None:
        """Blocks include header, divider, section, and context."""
        poster = _make_poster()
        poster._client = MagicMock()
        poster._client.chat_postMessage.return_value = _mock_slack_response()

        await poster.post_minutes_to_slack(
            "Content", "Title", {"key": "value"}
        )

        call_kwargs = poster._client.chat_postMessage.call_args.kwargs
        blocks = call_kwargs["blocks"]

        block_types = [b["type"] for b in blocks]
        assert "header" in block_types
        assert "divider" in block_types
        assert "section" in block_types
        assert "context" in block_types

    @pytest.mark.asyncio
    async def test_post_minutes_no_metadata(self) -> None:
        """Without metadata, no context block is added."""
        poster = _make_poster()
        poster._client = MagicMock()
        poster._client.chat_postMessage.return_value = _mock_slack_response()

        await poster.post_minutes_to_slack("Content", "Title")

        call_kwargs = poster._client.chat_postMessage.call_args.kwargs
        blocks = call_kwargs["blocks"]
        block_types = [b["type"] for b in blocks]
        assert "context" not in block_types

    @pytest.mark.asyncio
    async def test_post_minutes_returns_ts(self) -> None:
        """Return value is the message timestamp string."""
        poster = _make_poster()
        poster._client = MagicMock()
        poster._client.chat_postMessage.return_value = _mock_slack_response(
            ts="9999.0000"
        )

        ts = await poster.post_minutes_to_slack("Content", "Title")
        assert ts == "9999.0000"


class TestPostTranscriptFile:
    @pytest.mark.asyncio
    async def test_upload_success(self) -> None:
        """Transcript file is uploaded via files_upload_v2."""
        poster = _make_poster()
        poster._client = MagicMock()
        poster._client.files_upload_v2.return_value = _mock_slack_response()

        await poster.post_transcript_file("1234.5678", "Full text here")

        poster._client.files_upload_v2.assert_called_once()
        call_kwargs = poster._client.files_upload_v2.call_args.kwargs
        assert call_kwargs["thread_ts"] == "1234.5678"
        assert call_kwargs["content"] == "Full text here"

    @pytest.mark.asyncio
    async def test_upload_skipped_when_disabled(self) -> None:
        """Upload is skipped when include_transcript=False."""
        cfg = _make_cfg(include_transcript=False)
        poster = _make_poster(cfg)
        poster._client = MagicMock()

        await poster.post_transcript_file("1234.5678", "Text")

        poster._client.files_upload_v2.assert_not_called()


class TestSendStatus:
    @pytest.mark.asyncio
    async def test_status_in_thread(self) -> None:
        """Status is posted as a thread reply."""
        poster = _make_poster()
        poster._client = MagicMock()
        poster._client.chat_postMessage.return_value = _mock_slack_response()

        await poster.send_slack_status("1234.5678", "Processing...")

        call_kwargs = poster._client.chat_postMessage.call_args.kwargs
        assert call_kwargs["thread_ts"] == "1234.5678"
        assert call_kwargs["text"] == "Processing..."

    @pytest.mark.asyncio
    async def test_status_skipped_when_disabled(self) -> None:
        """Status is skipped when thread_replies=False."""
        cfg = _make_cfg(thread_replies=False)
        poster = _make_poster(cfg)
        poster._client = MagicMock()

        await poster.send_slack_status("1234.5678", "Processing...")

        poster._client.chat_postMessage.assert_not_called()


class TestPostError:
    @pytest.mark.asyncio
    async def test_error_format(self) -> None:
        """Error notification includes source and error message."""
        poster = _make_poster()
        poster._client = MagicMock()
        poster._client.chat_postMessage.return_value = _mock_slack_response()

        await poster.post_error_to_slack("Pipeline failed", "zoom:meeting.m4a")

        poster._client.chat_postMessage.assert_called_once()
        call_kwargs = poster._client.chat_postMessage.call_args.kwargs
        assert "zoom:meeting.m4a" in call_kwargs["text"]


class TestRateLimitRetry:
    @pytest.mark.asyncio
    async def test_retries_on_429(self) -> None:
        """Rate-limited requests are retried after Retry-After delay."""
        from slack_sdk.errors import SlackApiError

        poster = _make_poster()
        poster._client = MagicMock()

        # First call: 429, second call: success
        rate_limit_response = MagicMock()
        rate_limit_response.status_code = 429
        rate_limit_response.headers = {"Retry-After": "0"}
        rate_limit_exc = SlackApiError("rate_limited", rate_limit_response)

        poster._client.chat_postMessage.side_effect = [
            rate_limit_exc,
            _mock_slack_response(),
        ]

        ts = await poster.post_minutes_to_slack("Content", "Title")

        assert ts == "1234567890.123456"
        assert poster._client.chat_postMessage.call_count == 2

    @pytest.mark.asyncio
    async def test_non_429_raises_immediately(self) -> None:
        """Non-rate-limit errors raise PostingError immediately."""
        from slack_sdk.errors import SlackApiError

        poster = _make_poster()
        poster._client = MagicMock()

        error_response = MagicMock()
        error_response.status_code = 400
        poster._client.chat_postMessage.side_effect = SlackApiError(
            "invalid_blocks", error_response
        )

        with pytest.raises(PostingError, match="failed"):
            await poster.post_minutes_to_slack("Content", "Title")

        assert poster._client.chat_postMessage.call_count == 1
