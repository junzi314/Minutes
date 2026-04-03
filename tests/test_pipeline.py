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
    CalendarConfig,
    Config,
    CraigConfig,
    DiarizationConfig,
    DiscordConfig,
    ExportGoogleDocsConfig,
    GeneratorConfig,
    GoogleDriveConfig,
    GuildConfig,
    LoggingConfig,
    MergerConfig,
    MinutesArchiveConfig,
    PipelineConfig,
    PosterConfig,
    SpeakerAnalyticsConfig,
    TranscriptGlossaryConfig,
    WhisperConfig,
)
from src.detector import DetectedRecording
from src.errors import (
    AudioAcquisitionError,
    GenerationError,
    PostingError,
    TranscriptionError,
)
from src.pipeline import _transcript_hash, run_pipeline, run_pipeline_from_segments, run_pipeline_from_tracks
from src.state_store import StateStore
from src.transcriber import Segment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(**overrides: object) -> Config:
    kwargs: dict[str, object] = dict(
        discord=DiscordConfig(
            token="test-token",
            guilds=(GuildConfig(guild_id=1, watch_channel_id=2, output_channel_id=3),),
            error_mention_role_id=999,
        ),
        craig=CraigConfig(),
        whisper=WhisperConfig(),
        merger=MergerConfig(),
        generator=GeneratorConfig(api_key="sk-test"),
        poster=PosterConfig(),
        logging=LoggingConfig(),
        google_drive=GoogleDriveConfig(),
        pipeline=PipelineConfig(),
        speaker_analytics=SpeakerAnalyticsConfig(),
        minutes_archive=MinutesArchiveConfig(),
        export_google_docs=ExportGoogleDocsConfig(),
        calendar=CalendarConfig(),
        transcript_glossary=TranscriptGlossaryConfig(),
        diarization=DiarizationConfig(),
    )
    kwargs.update(overrides)
    return Config(**kwargs)


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
def state_store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state", legacy_db_path=tmp_path / "none.json")


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
        return_value="# 会議議事録\n## まとめ\nテスト会議\n## 推奨される次のステップ\n- [ ] テスト"
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
        state_store: StateStore,
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
                state_store=state_store,
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
        state_store: StateStore,
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
                state_store=state_store,
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
        state_store: StateStore,
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
                    state_store=state_store,
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
        state_store: StateStore,
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
                    state_store=state_store,
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
        state_store: StateStore,
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
                    state_store=state_store,
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
        state_store: StateStore,
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
            if kwargs.get("embed") is not None and (kwargs.get("file") is not None or kwargs.get("files") is not None):
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
                    state_store=state_store,
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
        state_store: StateStore,
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
                    state_store=state_store,
                )

        # Generator should NOT have been called
        mock_generator.generate.assert_not_called()


class TestPipelineSpeakerAnalytics:
    @pytest.mark.asyncio
    async def test_speaker_stats_passed_to_post_minutes(
        self,
        cfg: Config,
        recording: DetectedRecording,
        mock_session: MagicMock,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
        tmp_path: Path,
    ) -> None:
        """When speaker_analytics is enabled, stats are passed to post_minutes."""
        tracks = _make_tracks(tmp_path)

        with (
            patch("src.pipeline._stage_download", new_callable=AsyncMock) as mock_dl,
            patch("src.pipeline.post_minutes", new_callable=AsyncMock) as mock_post,
        ):
            mock_dl.return_value = tracks
            mock_post.return_value = MagicMock(id=1)

            await run_pipeline(
                recording=recording,
                session=mock_session,
                cfg=cfg,
                transcriber=mock_transcriber,
                generator=mock_generator,
                output_channel=mock_channel,
                state_store=state_store,
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["speaker_stats"] is not None
        assert "alice" in call_kwargs["speaker_stats"]

    @pytest.mark.asyncio
    async def test_speaker_stats_disabled(
        self,
        recording: DetectedRecording,
        mock_session: MagicMock,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
        tmp_path: Path,
    ) -> None:
        """When speaker_analytics is disabled, stats are None."""
        cfg = _make_config(speaker_analytics=SpeakerAnalyticsConfig(enabled=False))
        tracks = _make_tracks(tmp_path)

        with (
            patch("src.pipeline._stage_download", new_callable=AsyncMock) as mock_dl,
            patch("src.pipeline.post_minutes", new_callable=AsyncMock) as mock_post,
        ):
            mock_dl.return_value = tracks
            mock_post.return_value = MagicMock(id=1)

            await run_pipeline(
                recording=recording,
                session=mock_session,
                cfg=cfg,
                transcriber=mock_transcriber,
                generator=mock_generator,
                output_channel=mock_channel,
                state_store=state_store,
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["speaker_stats"] is None


class TestPipelineTranscriptAttachment:
    @pytest.mark.asyncio
    async def test_transcript_md_passed_when_enabled(
        self,
        recording: DetectedRecording,
        mock_session: MagicMock,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
        tmp_path: Path,
    ) -> None:
        """When include_transcript=True, transcript_md is passed to post_minutes."""
        cfg = _make_config(poster=PosterConfig(include_transcript=True))
        tracks = _make_tracks(tmp_path)

        with (
            patch("src.pipeline._stage_download", new_callable=AsyncMock) as mock_dl,
            patch("src.pipeline.post_minutes", new_callable=AsyncMock) as mock_post,
        ):
            mock_dl.return_value = tracks
            mock_post.return_value = MagicMock(id=1)

            await run_pipeline(
                recording=recording,
                session=mock_session,
                cfg=cfg,
                transcriber=mock_transcriber,
                generator=mock_generator,
                output_channel=mock_channel,
                state_store=state_store,
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["transcript_md"] is not None
        assert "# 文字起こし" in call_kwargs["transcript_md"]

    @pytest.mark.asyncio
    async def test_transcript_md_none_when_disabled(
        self,
        recording: DetectedRecording,
        mock_session: MagicMock,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
        tmp_path: Path,
    ) -> None:
        """When include_transcript=False (default), transcript_md is None."""
        cfg = _make_config(poster=PosterConfig(include_transcript=False))
        tracks = _make_tracks(tmp_path)

        with (
            patch("src.pipeline._stage_download", new_callable=AsyncMock) as mock_dl,
            patch("src.pipeline.post_minutes", new_callable=AsyncMock) as mock_post,
        ):
            mock_dl.return_value = tracks
            mock_post.return_value = MagicMock(id=1)

            await run_pipeline(
                recording=recording,
                session=mock_session,
                cfg=cfg,
                transcriber=mock_transcriber,
                generator=mock_generator,
                output_channel=mock_channel,
                state_store=state_store,
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["transcript_md"] is None


class TestTranscriptHash:
    def test_transcript_hash_includes_template(self) -> None:
        """Different template names produce different cache keys."""
        h1 = _transcript_hash("hello world", "minutes")
        h2 = _transcript_hash("hello world", "todo-focused")
        h3 = _transcript_hash("hello world", "minutes")
        assert h1 != h2
        assert h1 == h3

    def test_transcript_hash_default_is_minutes(self) -> None:
        """Default template_name is 'minutes'."""
        h_default = _transcript_hash("hello world")
        h_explicit = _transcript_hash("hello world", "minutes")
        assert h_default == h_explicit


# ---------------------------------------------------------------------------
# Minutes archive integration
# ---------------------------------------------------------------------------


class TestPipelineArchive:
    @pytest.mark.asyncio
    async def test_pipeline_archives_minutes(
        self,
        cfg: Config,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
        tmp_path: Path,
    ) -> None:
        """Pipeline success triggers archive.store() with correct metadata."""
        from src.minutes_archive import MinutesArchive

        tracks = _make_tracks(tmp_path)
        archive = MinutesArchive(tmp_path / "archive.db")

        mock_channel.guild.id = 1

        with patch("src.pipeline.post_minutes", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MagicMock(id=42)

            await run_pipeline_from_tracks(
                tracks=tracks,
                cfg=cfg,
                transcriber=mock_transcriber,
                generator=mock_generator,
                output_channel=mock_channel,
                state_store=state_store,
                source_label="test-source",
                archive=archive,
            )

        assert archive.count(1) == 1
        results = archive.search(1, "会議")
        assert len(results) == 1
        archive.close()

    @pytest.mark.asyncio
    async def test_pipeline_archive_failure_non_blocking(
        self,
        cfg: Config,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
        tmp_path: Path,
    ) -> None:
        """archive.store() failure does not block the pipeline."""
        mock_archive = MagicMock()
        mock_archive.store.side_effect = RuntimeError("DB write failed")

        tracks = _make_tracks(tmp_path)
        mock_channel.guild.id = 1

        with patch("src.pipeline.post_minutes", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MagicMock(id=42)

            # Should not raise
            await run_pipeline_from_tracks(
                tracks=tracks,
                cfg=cfg,
                transcriber=mock_transcriber,
                generator=mock_generator,
                output_channel=mock_channel,
                state_store=state_store,
                archive=mock_archive,
            )

        # post_minutes was still called successfully
        mock_post.assert_called_once()
        # archive.store was attempted
        mock_archive.store.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_no_archive_when_disabled(
        self,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
        tmp_path: Path,
    ) -> None:
        """When minutes_archive.enabled=False, archive.store() is not called."""
        cfg = _make_config(minutes_archive=MinutesArchiveConfig(enabled=False))
        mock_archive = MagicMock()

        tracks = _make_tracks(tmp_path)
        mock_channel.guild.id = 1

        with patch("src.pipeline.post_minutes", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MagicMock(id=42)

            await run_pipeline_from_tracks(
                tracks=tracks,
                cfg=cfg,
                transcriber=mock_transcriber,
                generator=mock_generator,
                output_channel=mock_channel,
                state_store=state_store,
                archive=mock_archive,
            )

        mock_archive.store.assert_not_called()


class TestPipelineErrorRoleParam:
    @pytest.mark.asyncio
    async def test_error_role_param_passed_to_post_error(
        self,
        cfg: Config,
        recording: DetectedRecording,
        mock_session: MagicMock,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
        tmp_path: Path,
    ) -> None:
        """error_mention_role_id parameter is forwarded to post_error on failure."""
        tracks = _make_tracks(tmp_path)
        mock_generator.generate.side_effect = GenerationError("API error")

        with (
            patch("src.pipeline._stage_download", new_callable=AsyncMock) as mock_dl,
            patch("src.pipeline.post_error", new_callable=AsyncMock) as mock_post_err,
        ):
            mock_dl.return_value = tracks

            with pytest.raises(GenerationError):
                await run_pipeline(
                    recording=recording,
                    session=mock_session,
                    cfg=cfg,
                    transcriber=mock_transcriber,
                    generator=mock_generator,
                    output_channel=mock_channel,
                    state_store=state_store,
                    error_mention_role_id=12345,
                )

        mock_post_err.assert_called_once()
        assert mock_post_err.call_args.kwargs["error_mention_role_id"] == 12345

    @pytest.mark.asyncio
    async def test_error_role_param_default_none(
        self,
        cfg: Config,
        recording: DetectedRecording,
        mock_session: MagicMock,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
        tmp_path: Path,
    ) -> None:
        """error_mention_role_id defaults to None when not provided."""
        tracks = _make_tracks(tmp_path)
        mock_generator.generate.side_effect = GenerationError("API error")

        with (
            patch("src.pipeline._stage_download", new_callable=AsyncMock) as mock_dl,
            patch("src.pipeline.post_error", new_callable=AsyncMock) as mock_post_err,
        ):
            mock_dl.return_value = tracks

            with pytest.raises(GenerationError):
                await run_pipeline(
                    recording=recording,
                    session=mock_session,
                    cfg=cfg,
                    transcriber=mock_transcriber,
                    generator=mock_generator,
                    output_channel=mock_channel,
                    state_store=state_store,
                )

        mock_post_err.assert_called_once()
        assert mock_post_err.call_args.kwargs["error_mention_role_id"] is None


# ---------------------------------------------------------------------------
# Glossary integration
# ---------------------------------------------------------------------------


class TestPipelineGlossary:
    @pytest.mark.asyncio
    async def test_glossary_applied_when_enabled(
        self,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
        tmp_path: Path,
    ) -> None:
        """When glossary is enabled and non-empty, segments are corrected."""
        cfg = _make_config(transcript_glossary=TranscriptGlossaryConfig(enabled=True))
        tracks = _make_tracks(tmp_path)
        mock_channel.guild.id = 1

        # Set a glossary entry
        state_store.set_guild_glossary(1, {"Hello": "Corrected"})

        with patch("src.pipeline.post_minutes", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MagicMock(id=42)

            await run_pipeline_from_tracks(
                tracks=tracks,
                cfg=cfg,
                transcriber=mock_transcriber,
                generator=mock_generator,
                output_channel=mock_channel,
                state_store=state_store,
            )

        # The merged transcript passed to generator should contain "Corrected"
        call_kwargs = mock_generator.generate.call_args.kwargs
        assert "Corrected" in call_kwargs["transcript"]

    @pytest.mark.asyncio
    async def test_glossary_skipped_when_disabled(
        self,
        mock_channel: MagicMock,
        mock_transcriber: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
        tmp_path: Path,
    ) -> None:
        """When glossary is disabled, segments are not modified."""
        cfg = _make_config(transcript_glossary=TranscriptGlossaryConfig(enabled=False))
        tracks = _make_tracks(tmp_path)
        mock_channel.guild.id = 1

        state_store.set_guild_glossary(1, {"Hello": "Corrected"})

        with patch("src.pipeline.post_minutes", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MagicMock(id=42)

            await run_pipeline_from_tracks(
                tracks=tracks,
                cfg=cfg,
                transcriber=mock_transcriber,
                generator=mock_generator,
                output_channel=mock_channel,
                state_store=state_store,
            )

        # The transcript should still contain original "Hello" not "Corrected"
        call_kwargs = mock_generator.generate.call_args.kwargs
        assert "Hello" in call_kwargs["transcript"]


# ---------------------------------------------------------------------------
# Pipeline from segments (diarization flow)
# ---------------------------------------------------------------------------


class TestPipelineFromSegments:
    @pytest.mark.asyncio
    async def test_success(
        self,
        cfg: Config,
        mock_channel: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
    ) -> None:
        """run_pipeline_from_segments posts minutes from pre-aligned segments."""
        segments = _make_segments()
        mock_channel.guild.id = 1

        with patch("src.pipeline.post_minutes", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MagicMock(id=42)

            await run_pipeline_from_segments(
                segments=segments,
                cfg=cfg,
                generator=mock_generator,
                output_channel=mock_channel,
                state_store=state_store,
                source_label="diarization:test.mp4",
            )

        mock_generator.generate.assert_called_once()
        mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_segments_raises(
        self,
        cfg: Config,
        mock_channel: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
    ) -> None:
        """Empty segments list raises TranscriptionError."""
        with pytest.raises(TranscriptionError, match="No segments"):
            await run_pipeline_from_segments(
                segments=[],
                cfg=cfg,
                generator=mock_generator,
                output_channel=mock_channel,
                state_store=state_store,
            )

    @pytest.mark.asyncio
    async def test_with_glossary(
        self,
        mock_channel: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
    ) -> None:
        """Glossary correction applies to segments."""
        cfg = _make_config(transcript_glossary=TranscriptGlossaryConfig(enabled=True))
        segments = _make_segments()
        mock_channel.guild.id = 1
        state_store.set_guild_glossary(1, {"Hello": "Corrected"})

        with patch("src.pipeline.post_minutes", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MagicMock(id=42)

            await run_pipeline_from_segments(
                segments=segments,
                cfg=cfg,
                generator=mock_generator,
                output_channel=mock_channel,
                state_store=state_store,
            )

        call_kwargs = mock_generator.generate.call_args.kwargs
        assert "Corrected" in call_kwargs["transcript"]

    @pytest.mark.asyncio
    async def test_with_exporter(
        self,
        cfg: Config,
        mock_channel: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
    ) -> None:
        """Google Docs exporter is called when enabled."""
        cfg_with_export = _make_config(
            export_google_docs=ExportGoogleDocsConfig(enabled=True, folder_id="test-folder"),
        )
        segments = _make_segments()
        mock_channel.guild.id = 1

        mock_exporter = MagicMock()
        mock_export_result = MagicMock()
        mock_export_result.success = True
        mock_export_result.url = "https://docs.google.com/test"
        mock_exporter.export = AsyncMock(return_value=mock_export_result)

        with patch("src.pipeline.post_minutes", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MagicMock(id=42)

            await run_pipeline_from_segments(
                segments=segments,
                cfg=cfg_with_export,
                generator=mock_generator,
                output_channel=mock_channel,
                state_store=state_store,
                exporter=mock_exporter,
            )

        mock_exporter.export.assert_called_once()
        mock_post.assert_called_once()
        assert mock_post.call_args.kwargs["google_docs_url"] == "https://docs.google.com/test"

    @pytest.mark.asyncio
    async def test_speaker_names_extracted(
        self,
        cfg: Config,
        mock_channel: MagicMock,
        mock_generator: MagicMock,
        state_store: StateStore,
    ) -> None:
        """Speaker names are extracted from segments and passed to generator."""
        segments = _make_segments()  # has alice and bob
        mock_channel.guild.id = 1

        with patch("src.pipeline.post_minutes", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = MagicMock(id=42)

            await run_pipeline_from_segments(
                segments=segments,
                cfg=cfg,
                generator=mock_generator,
                output_channel=mock_channel,
                state_store=state_store,
            )

        call_kwargs = mock_generator.generate.call_args.kwargs
        assert "alice" in call_kwargs["speakers"]
        assert "bob" in call_kwargs["speakers"]
