"""Unit tests for src/poster.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.config import PosterConfig
from src.poster import (
    _extract_section,
    _truncate,
    build_error_embed,
    build_minutes_embed,
    build_minutes_file,
    post_error,
    post_minutes,
    send_status_update,
)
from src.poster import _SUMMARY_PATTERN, _DECISIONS_PATTERN

_CFG = PosterConfig()

_SAMPLE_MINUTES = """\
# 会議議事録
- 日時: 2026-02-10
- 参加者: Alice, Bob

## 要約
本会議ではプロジェクトの進捗確認と次期マイルストーンの設定を行った。
主要タスクの期限を2/14に決定。

## アジェンダ / 議題
1. プロジェクト進捗報告
2. 次期マイルストーン

## 議論の詳細
### 1. プロジェクト進捗報告
- Aliceより現状報告。

## 決定事項
- タスクAの期限を2/14に設定（担当: Bob）
- レビューを来週月曜に実施（担当: Alice）

## 次回アクション / TODO
| 担当 | タスク | 期限 |
|------|--------|------|
| Bob | タスクA完了 | 2/14 |

## 懸念事項・リスク
- 特になし
"""


# --- _truncate ---


class TestTruncate:
    def test_no_truncation_needed(self) -> None:
        assert _truncate("short", 100) == "short"

    def test_truncation_with_ellipsis(self) -> None:
        result = _truncate("a" * 20, 10)
        assert len(result) == 10
        assert result.endswith("...")

    def test_exact_length(self) -> None:
        text = "exact"
        assert _truncate(text, 5) == "exact"


# --- _extract_section ---


class TestExtractSection:
    def test_extract_summary(self) -> None:
        result = _extract_section(_SAMPLE_MINUTES, _SUMMARY_PATTERN)
        assert "進捗確認" in result
        assert "マイルストーン" in result

    def test_extract_decisions(self) -> None:
        result = _extract_section(_SAMPLE_MINUTES, _DECISIONS_PATTERN)
        assert "タスクA" in result
        assert "Bob" in result

    def test_extract_missing_section(self) -> None:
        result = _extract_section("no sections here", _SUMMARY_PATTERN)
        assert result == ""


# --- build_minutes_embed ---


class TestBuildMinutesEmbed:
    def test_embed_has_title(self) -> None:
        embed = build_minutes_embed(_SAMPLE_MINUTES, "2026-02-10", "Alice, Bob", _CFG)
        assert "2026-02-10" in embed.title
        assert "会議議事録" in embed.title

    def test_embed_has_fields(self) -> None:
        embed = build_minutes_embed(_SAMPLE_MINUTES, "2026-02-10", "Alice, Bob", _CFG)
        field_names = [f.name for f in embed.fields]
        assert "参加者" in field_names
        assert "要約" in field_names
        assert "決定事項" in field_names

    def test_embed_color(self) -> None:
        embed = build_minutes_embed(_SAMPLE_MINUTES, "2026-02-10", "Alice", _CFG)
        assert embed.color.value == 0x5865F2

    def test_embed_footer(self) -> None:
        embed = build_minutes_embed(_SAMPLE_MINUTES, "2026-02-10", "Alice", _CFG)
        assert "添付ファイル" in embed.footer.text

    def test_embed_no_speakers(self) -> None:
        embed = build_minutes_embed(_SAMPLE_MINUTES, "2026-02-10", "", _CFG)
        field_names = [f.name for f in embed.fields]
        assert "参加者" not in field_names

    def test_embed_long_summary_truncated(self) -> None:
        long_summary = "## 要約\n" + "あ" * 2000 + "\n## 決定事項\n- テスト"
        embed = build_minutes_embed(long_summary, "2026-02-10", "Alice", _CFG)
        summary_field = next(f for f in embed.fields if f.name == "要約")
        assert len(summary_field.value) <= 1024

    def test_embed_respects_max_length(self) -> None:
        cfg = PosterConfig(max_embed_length=200)
        embed = build_minutes_embed(_SAMPLE_MINUTES, "2026-02-10", "Alice, Bob", cfg)
        total = len(embed.title or "") + sum(
            len(f.name) + len(f.value) for f in embed.fields
        ) + len(embed.footer.text or "")
        # Should have been trimmed to fit
        assert total <= 400  # allow some tolerance for field names


# --- build_error_embed ---


class TestBuildErrorEmbed:
    def test_error_embed_fields(self) -> None:
        embed, mention = build_error_embed("Something failed", "transcription")
        assert "エラー" in embed.title
        assert "Something failed" in embed.description
        field_names = [f.name for f in embed.fields]
        assert "失敗ステージ" in field_names
        assert mention == ""

    def test_error_embed_with_role_mention(self) -> None:
        embed, mention = build_error_embed("Error", "download", error_mention_role_id=123456)
        assert mention == "<@&123456>"

    def test_error_embed_color_red(self) -> None:
        embed, _ = build_error_embed("Error", "test")
        assert embed.color.value == 0xFF0000

    def test_error_embed_long_message_truncated(self) -> None:
        long_msg = "x" * 5000
        embed, _ = build_error_embed(long_msg, "test")
        assert len(embed.description) <= 2003  # 2000 + "..."


# --- build_minutes_file ---


class TestBuildMinutesFile:
    def test_file_filename(self) -> None:
        f = build_minutes_file("content", "2026-02-10")
        assert f.filename == "minutes_2026-02-10.md"

    def test_file_content(self) -> None:
        f = build_minutes_file("テスト内容", "2026-02-10")
        data = f.fp.read()
        assert data == "テスト内容".encode("utf-8")

    def test_file_date_sanitization(self) -> None:
        f = build_minutes_file("content", "2026/02/10 14:00")
        assert "/" not in f.filename
        assert " " not in f.filename


# --- post_minutes mention ---


class TestPostMinutesMention:
    @pytest.mark.asyncio
    async def test_mention_user_ids_included(self) -> None:
        cfg = PosterConfig(mention_user_ids=(111, 222))
        channel = MagicMock(spec=discord.TextChannel)
        msg = MagicMock()
        msg.id = 1
        channel.send = AsyncMock(return_value=msg)
        channel.name = "test"

        await post_minutes(channel, _SAMPLE_MINUTES, "2026-02-10", "Alice", cfg)

        call_kwargs = channel.send.call_args.kwargs
        assert "<@111>" in call_kwargs["content"]
        assert "<@222>" in call_kwargs["content"]

    @pytest.mark.asyncio
    async def test_no_mention_when_empty(self) -> None:
        cfg = PosterConfig(mention_user_ids=())
        channel = MagicMock(spec=discord.TextChannel)
        msg = MagicMock()
        msg.id = 1
        channel.send = AsyncMock(return_value=msg)
        channel.name = "test"

        await post_minutes(channel, _SAMPLE_MINUTES, "2026-02-10", "Alice", cfg)

        call_kwargs = channel.send.call_args.kwargs
        assert call_kwargs["content"] is None


# --- ForumChannel support ---


def _make_forum_channel() -> MagicMock:
    """Create a mock ForumChannel with create_thread returning ThreadWithMessage."""
    channel = MagicMock(spec=discord.ForumChannel)
    channel.name = "test-forum"

    msg = MagicMock(spec=discord.Message)
    msg.id = 42

    # create_thread returns ThreadWithMessage(thread, message)
    thread_result = MagicMock()
    thread_result.message = msg
    channel.create_thread = AsyncMock(return_value=thread_result)
    return channel


class TestPostMinutesForum:
    @pytest.mark.asyncio
    async def test_forum_creates_thread(self) -> None:
        channel = _make_forum_channel()

        result = await post_minutes(channel, _SAMPLE_MINUTES, "2026-02-10", "Alice", _CFG)

        channel.create_thread.assert_called_once()
        call_kwargs = channel.create_thread.call_args.kwargs
        assert call_kwargs["name"] == "会議議事録 — 2026-02-10"
        assert isinstance(call_kwargs["embed"], discord.Embed)
        assert call_kwargs["file"] is not None
        assert result.id == 42

    @pytest.mark.asyncio
    async def test_forum_thread_includes_mentions(self) -> None:
        cfg = PosterConfig(mention_user_ids=(111, 222))
        channel = _make_forum_channel()

        await post_minutes(channel, _SAMPLE_MINUTES, "2026-02-10", "Alice", cfg)

        call_kwargs = channel.create_thread.call_args.kwargs
        assert "<@111>" in call_kwargs["content"]
        assert "<@222>" in call_kwargs["content"]

    @pytest.mark.asyncio
    async def test_forum_no_mention_when_empty(self) -> None:
        cfg = PosterConfig(mention_user_ids=())
        channel = _make_forum_channel()

        await post_minutes(channel, _SAMPLE_MINUTES, "2026-02-10", "Alice", cfg)

        call_kwargs = channel.create_thread.call_args.kwargs
        assert call_kwargs["content"] is None


class TestPostErrorForum:
    @pytest.mark.asyncio
    async def test_forum_creates_error_thread(self) -> None:
        channel = _make_forum_channel()

        result = await post_error(channel, "Something failed", "transcription")

        channel.create_thread.assert_called_once()
        call_kwargs = channel.create_thread.call_args.kwargs
        assert "エラー" in call_kwargs["name"]
        assert "transcription" in call_kwargs["name"]
        assert result.id == 42


class TestSendStatusUpdateForum:
    @pytest.mark.asyncio
    async def test_forum_skips_status_update(self) -> None:
        channel = MagicMock(spec=discord.ForumChannel)
        result = await send_status_update(channel, None, "status text")
        assert result is None

    @pytest.mark.asyncio
    async def test_text_channel_sends_status(self) -> None:
        channel = MagicMock(spec=discord.TextChannel)
        msg = MagicMock(spec=discord.Message)
        msg.id = 1
        channel.send = AsyncMock(return_value=msg)

        result = await send_status_update(channel, None, "status text")

        channel.send.assert_called_once_with("status text")
        assert result is not None
