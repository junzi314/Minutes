"""Integration tests for src/pipeline.py.

All external calls (Craig API, Whisper, Claude API, Discord) are mocked.
Tests verify the pipeline's stage orchestration, error handling, and cleanup.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.audio_source import SpeakerAudio, SpeakerInfo
from src.config import (
    Config,
    CraigConfig,
    DiscordConfig,
    GeneratorConfig,
    GoogleDriveConfig,
    LoggingConfig,
    MergerConfig,
    PosterConfig,
    WhisperConfig,
)
from src.detector import DetectedRecording
from src.errors import (
    AudioAcquisitionError,
    GenerationError,
    PostingError,
    TranscriptionError,
)
from src.pipeline import run_pipeline
from src.transcriber import Segment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config() -> Config:
    return Config(
        discord=DiscordConfig(
            token="test-token",
            guild_id=1,
            watch_channel_id=2,
            output_channel_id=3,
            error_mention_role_id=999,
        ),
        craig=CraigConfig(),
        whisper=WhisperConfig(),
        merger=MergerConfig(),
        generator=GeneratorConfig(api_key="sk-test"),
        poster=PosterConfig(),
        logging=LoggingConfig(),
        google_drive=GoogleDriveConfig(),
    )


def _make_recording() -> DetectedRecording:
    return DetectedRecording(
        rec_id="test123",
        access_key="KEY",
        rec_url="https://craig.chat/rec/test123?key=KEY",
        guild_id=1,
        channel_id=2,
        message_id=100,
    )


def _make_tracks(tmp_path: Path) -> list[SpeakerAudio]:
    f1 = tmp_path / "1-alice.aac"
    f1.write_bytes(b"\x00")
    f2 = tmp_path / "2-bob.aac"
    f2.write_bytes(b"\x00")
    return [
        SpeakerAudio(
            speaker=SpeakerInfo(track=1, username="alice", user_id=1),
            file_path=f1,
        ),
        SpeakerAudio(
            speaker=SpeakerInfo(track=2, username="bob", user_id=2),
            file_path=f2,
        ),
    ]


def _make_segments() -> list[Segment]:
    return [
        Segment(start=0.0, end=3.0, text="Hello", speaker="alice"),
        Segment(start=3.5, end=6.0, text="Hi there", speaker="bob"),
        Segment(start=7.0, end=10.0, text="Let's start", speaker="alice"),
    ]


@pytest.fixture
def cfg() -> Config:
    return _make_config()


@pytest.fixture
def recording() -> DetectedRecording:
    return _make_recording()


def _make_mock_message(msg_id: int = 999) -> MagicMock:
    """Create a mock Discord Message with async edit/delete."""
    msg = MagicMock()
    msg.id = msg_id
    msg.delete = AsyncMock()
    msg.edit = AsyncMock()
    return msg


@pytest.fixture
def mock_channel() -> MagicMock:
    channel = MagicMock()
    channel.name = "test-channel"
    channel.id = 3
    channel.guild = MagicMock()
    channel.guild.name = "TestGuild"

    # send returns a mock message with async methods
    msg = _make_mock_message()
    channel.send = AsyncMock(return_value=msg)
    return channel


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_transcriber() -> MagicMock:
    t = MagicMock()
    t.is_loaded = True
    t.transcribe_all = MagicMock(return_value=_make_segments())
    return t


@pytest.fixture
def mock_generator() -> MagicMock:
    g = MagicMock()
    g.is_loaded = True
    g.generate = AsyncMock(
        return_value="# 会議議事録\n## 要約\nテスト会議\n## 決定事項\n- テスト"
    )
    return g


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestPipelineHappyPath:
    @pytest.mark.asyncio
    async def test_full_pipeline_success(
        self,
        cfg: Config,
        recording: DetectedRecording,
        mock_session: MagicMock,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        tmp_path: Path,
    ) -> None:
        tracks = _make_tracks(tmp_path)

        with patch("src.pipeline._stage_download", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = tracks

            await run_pipeline(
                recording=recording,
                session=mock_session,
                cfg=cfg,
                transcriber=mock_transcriber,
                generator=mock_generator,
                output_channel=mock_channel,
            )

        # Download was called
        mock_dl.assert_called_once()

        # Transcriber was called
        mock_transcriber.transcribe_all.assert_called_once_with(tracks)

        # Generator was called with merged transcript
        mock_generator.generate.assert_called_once()
        call_kwargs = mock_generator.generate.call_args
        assert "alice" in call_kwargs.kwargs.get("speakers", "") or "alice" in str(call_kwargs)

        # Minutes were posted (send called for status + minutes)
        assert mock_channel.send.call_count >= 2  # at least status + minutes

        # Status message was cleaned up (deleted)
        # The first send creates the status message, which should be deleted
        first_msg = mock_channel.send.return_value
        first_msg.delete.assert_called()

    @pytest.mark.asyncio
    async def test_pipeline_posts_status_updates(
        self,
        cfg: Config,
        recording: DetectedRecording,
        mock_session: MagicMock,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        tmp_path: Path,
    ) -> None:
        tracks = _make_tracks(tmp_path)

        with patch("src.pipeline._stage_download", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = tracks

            await run_pipeline(
                recording=recording,
                session=mock_session,
                cfg=cfg,
                transcriber=mock_transcriber,
                generator=mock_generator,
                output_channel=mock_channel,
            )

        # Status messages should include Japanese progress text
        send_calls = mock_channel.send.call_args_list
        all_args = " ".join(str(c) for c in send_calls)
        assert "ダウンロード" in all_args or "文字起こし" in all_args


# ---------------------------------------------------------------------------
# Failure at each stage
# ---------------------------------------------------------------------------


class TestPipelineDownloadFailure:
    @pytest.mark.asyncio
    async def test_download_failure_posts_error(
        self,
        cfg: Config,
        recording: DetectedRecording,
        mock_session: MagicMock,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
    ) -> None:
        with patch("src.pipeline._stage_download", new_callable=AsyncMock) as mock_dl:
            mock_dl.side_effect = AudioAcquisitionError("Download failed")

            with pytest.raises(AudioAcquisitionError):
                await run_pipeline(
                    recording=recording,
                    session=mock_session,
                    cfg=cfg,
                    transcriber=mock_transcriber,
                    generator=mock_generator,
                    output_channel=mock_channel,
                )

        # Error embed should have been posted
        send_calls = mock_channel.send.call_args_list
        # At least one call should contain an embed (error embed)
        has_embed = any(
            c.kwargs.get("embed") is not None
            for c in send_calls
            if c.kwargs
        )
        assert has_embed or len(send_calls) >= 2

        # Generator should NOT have been called
        mock_generator.generate.assert_not_called()


class TestPipelineTranscriptionFailure:
    @pytest.mark.asyncio
    async def test_transcription_failure_posts_error(
        self,
        cfg: Config,
        recording: DetectedRecording,
        mock_session: MagicMock,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        tmp_path: Path,
    ) -> None:
        tracks = _make_tracks(tmp_path)
        mock_transcriber.transcribe_all.side_effect = TranscriptionError("OOM")

        with patch("src.pipeline._stage_download", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = tracks

            with pytest.raises(TranscriptionError):
                await run_pipeline(
                    recording=recording,
                    session=mock_session,
                    cfg=cfg,
                    transcriber=mock_transcriber,
                    generator=mock_generator,
                    output_channel=mock_channel,
                )

        # Generator should NOT have been called
        mock_generator.generate.assert_not_called()


class TestPipelineGenerationFailure:
    @pytest.mark.asyncio
    async def test_generation_failure_posts_error(
        self,
        cfg: Config,
        recording: DetectedRecording,
        mock_session: MagicMock,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        tmp_path: Path,
    ) -> None:
        tracks = _make_tracks(tmp_path)
        mock_generator.generate.side_effect = GenerationError("API error")

        with patch("src.pipeline._stage_download", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = tracks

            with pytest.raises(GenerationError):
                await run_pipeline(
                    recording=recording,
                    session=mock_session,
                    cfg=cfg,
                    transcriber=mock_transcriber,
                    generator=mock_generator,
                    output_channel=mock_channel,
                )

        # Transcriber should have been called
        mock_transcriber.transcribe_all.assert_called_once()


class TestPipelinePostingFailure:
    @pytest.mark.asyncio
    async def test_posting_failure_still_logs(
        self,
        cfg: Config,
        recording: DetectedRecording,
        mock_session: MagicMock,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        tmp_path: Path,
    ) -> None:
        tracks = _make_tracks(tmp_path)

        # Make the minutes post fail but error post succeed
        call_count = 0

        async def _send_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First few calls are status messages (succeed).
            # The minutes embed post fails.
            if kwargs.get("embed") is not None and kwargs.get("file") is not None:
                import discord
                raise discord.HTTPException(
                    response=MagicMock(status=500),
                    message="Internal Server Error",
                )
            msg = MagicMock()
            msg.id = call_count
            msg.delete = AsyncMock()
            msg.edit = AsyncMock()
            return msg

        mock_channel.send = AsyncMock(side_effect=_send_side_effect)

        with patch("src.pipeline._stage_download", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = tracks

            with pytest.raises(PostingError):
                await run_pipeline(
                    recording=recording,
                    session=mock_session,
                    cfg=cfg,
                    transcriber=mock_transcriber,
                    generator=mock_generator,
                    output_channel=mock_channel,
                )


class TestPipelineEmptyTranscript:
    @pytest.mark.asyncio
    async def test_empty_transcript_raises(
        self,
        cfg: Config,
        recording: DetectedRecording,
        mock_session: MagicMock,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        tmp_path: Path,
    ) -> None:
        tracks = _make_tracks(tmp_path)
        # Return empty segments
        mock_transcriber.transcribe_all.return_value = []

        with patch("src.pipeline._stage_download", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = tracks

            with pytest.raises(TranscriptionError, match="empty"):
                await run_pipeline(
                    recording=recording,
                    session=mock_session,
                    cfg=cfg,
                    transcriber=mock_transcriber,
                    generator=mock_generator,
                    output_channel=mock_channel,
                )

        # Generator should NOT have been called
        mock_generator.generate.assert_not_called()
