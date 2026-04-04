"""Unit tests for src/slack_pipeline.py (Slack minutes pipeline)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.slack_config import SlackConfig, SlackServiceConfig, ZoomConfig
from src.config import DiarizationConfig, GeneratorConfig, GoogleDriveConfig, MergerConfig, PipelineConfig
from src.errors import DiarizationError
from src.slack_pipeline import run_slack_pipeline
from src.transcriber import Segment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(**overrides) -> SlackServiceConfig:
    return SlackServiceConfig(
        slack=SlackConfig(bot_token="xoxb-t", channel_id="C1", **overrides.get("slack", {})),
        zoom=ZoomConfig(),
        diarization=DiarizationConfig(enabled=True),
        generator=GeneratorConfig(),
        merger=MergerConfig(),
        google_drive=GoogleDriveConfig(),
        pipeline=PipelineConfig(),
    )


def _make_vtt_file(tmp_path: Path, content: str | None = None) -> Path:
    if content is None:
        content = """\
WEBVTT

00:00:01.000 --> 00:00:04.000
Hello, this is a test.

00:00:05.000 --> 00:00:08.000
Second segment here.
"""
    vtt_file = tmp_path / "meeting.vtt"
    vtt_file.write_text(content, encoding="utf-8")
    return vtt_file


def _make_audio_file(tmp_path: Path) -> Path:
    audio_file = tmp_path / "meeting.m4a"
    audio_file.write_bytes(b"fake audio data")
    return audio_file


# ===========================================================================
# Tests
# ===========================================================================


class TestSlackPipelineSuccess:
    @pytest.mark.asyncio
    async def test_full_pipeline(self, tmp_path: Path) -> None:
        """Full pipeline: VTT → extract → diarize → align → merge → generate → Slack."""
        cfg = _make_cfg()
        vtt_path = _make_vtt_file(tmp_path)
        audio_path = _make_audio_file(tmp_path)

        generator = MagicMock()
        generator.generate = AsyncMock(return_value="## Minutes\n- Item 1")

        slack_poster = MagicMock()
        slack_poster.post_minutes_to_slack = AsyncMock(return_value="1234.5678")
        slack_poster.post_transcript_file = AsyncMock()
        slack_poster.post_error_to_slack = AsyncMock()
        slack_poster.send_slack_status = AsyncMock()

        state_store = MagicMock()
        state_store.get_cached_minutes.return_value = None
        state_store.put_cached_minutes = MagicMock()

        mock_diarizer = MagicMock()
        mock_diarizer.load_model = MagicMock()
        mock_diarizer.unload_model = MagicMock()
        mock_diarizer.diarize = MagicMock(return_value=[])  # No diar segments → fallback

        with patch("src.slack_pipeline.extract_audio", new_callable=AsyncMock) as mock_extract, \
             patch("src.slack_pipeline.DiariZenDiarizer", return_value=mock_diarizer):
            mock_extract.return_value = tmp_path / "audio.wav"

            await run_slack_pipeline(
                audio_path=audio_path,
                vtt_path=vtt_path,
                cfg=cfg,
                generator=generator,
                slack_poster=slack_poster,
                state_store=state_store,
                source_label="test:meeting.m4a",
            )

        # Generator was called
        generator.generate.assert_awaited_once()

        # Slack poster was called
        slack_poster.post_minutes_to_slack.assert_awaited_once()
        call_kwargs = slack_poster.post_minutes_to_slack.call_args
        assert "Minutes" in call_kwargs[0][0]  # minutes_md

        # Transcript was attached
        slack_poster.post_transcript_file.assert_awaited_once()

        # State was cached
        state_store.put_cached_minutes.assert_called_once()


class TestSlackPipelineCache:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_generation(self, tmp_path: Path) -> None:
        """Cached minutes skip Claude API call."""
        cfg = _make_cfg()
        vtt_path = _make_vtt_file(tmp_path)
        audio_path = _make_audio_file(tmp_path)

        generator = MagicMock()
        generator.generate = AsyncMock()

        slack_poster = MagicMock()
        slack_poster.post_minutes_to_slack = AsyncMock(return_value="ts")
        slack_poster.post_transcript_file = AsyncMock()

        state_store = MagicMock()
        state_store.get_cached_minutes.return_value = "## Cached Minutes"

        mock_diarizer = MagicMock()
        mock_diarizer.load_model = MagicMock()
        mock_diarizer.unload_model = MagicMock()
        mock_diarizer.diarize = MagicMock(return_value=[])

        with patch("src.slack_pipeline.extract_audio", new_callable=AsyncMock), \
             patch("src.slack_pipeline.DiariZenDiarizer", return_value=mock_diarizer):
            await run_slack_pipeline(
                audio_path=audio_path,
                vtt_path=vtt_path,
                cfg=cfg,
                generator=generator,
                slack_poster=slack_poster,
                state_store=state_store,
            )

        # Generator should NOT be called
        generator.generate.assert_not_awaited()

        # But Slack post should still happen
        slack_poster.post_minutes_to_slack.assert_awaited_once()


class TestSlackPipelineDiarizationFallback:
    @pytest.mark.asyncio
    async def test_diarization_failure_continues(self, tmp_path: Path) -> None:
        """Diarization failure falls back to single speaker."""
        cfg = _make_cfg()
        vtt_path = _make_vtt_file(tmp_path)
        audio_path = _make_audio_file(tmp_path)

        generator = MagicMock()
        generator.generate = AsyncMock(return_value="## Minutes")

        slack_poster = MagicMock()
        slack_poster.post_minutes_to_slack = AsyncMock(return_value="ts")
        slack_poster.post_transcript_file = AsyncMock()

        state_store = MagicMock()
        state_store.get_cached_minutes.return_value = None
        state_store.put_cached_minutes = MagicMock()

        mock_diarizer = MagicMock()
        mock_diarizer.load_model = MagicMock()
        mock_diarizer.unload_model = MagicMock()
        mock_diarizer.diarize = MagicMock(side_effect=DiarizationError("GPU OOM"))

        with patch("src.slack_pipeline.extract_audio", new_callable=AsyncMock), \
             patch("src.slack_pipeline.DiariZenDiarizer", return_value=mock_diarizer):
            await run_slack_pipeline(
                audio_path=audio_path,
                vtt_path=vtt_path,
                cfg=cfg,
                generator=generator,
                slack_poster=slack_poster,
                state_store=state_store,
            )

        # Pipeline should still complete
        slack_poster.post_minutes_to_slack.assert_awaited_once()

        # VRAM should be cleaned up
        mock_diarizer.unload_model.assert_called()


class TestSlackPipelineErrors:
    @pytest.mark.asyncio
    async def test_empty_vtt_posts_error(self, tmp_path: Path) -> None:
        """Empty VTT posts error and returns early."""
        cfg = _make_cfg()
        vtt_path = _make_vtt_file(tmp_path, content="WEBVTT\n")
        audio_path = _make_audio_file(tmp_path)

        slack_poster = MagicMock()
        slack_poster.post_error_to_slack = AsyncMock()

        await run_slack_pipeline(
            audio_path=audio_path,
            vtt_path=vtt_path,
            cfg=cfg,
            generator=MagicMock(),
            slack_poster=slack_poster,
            state_store=MagicMock(),
        )

        slack_poster.post_error_to_slack.assert_awaited_once()
        error_msg = slack_poster.post_error_to_slack.call_args[0][0]
        assert "no segments" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_generation_error_posts_to_slack(self, tmp_path: Path) -> None:
        """Generation failure posts error to Slack and re-raises."""
        cfg = _make_cfg()
        vtt_path = _make_vtt_file(tmp_path)
        audio_path = _make_audio_file(tmp_path)

        generator = MagicMock()
        generator.generate = AsyncMock(side_effect=RuntimeError("API down"))

        slack_poster = MagicMock()
        slack_poster.post_minutes_to_slack = AsyncMock()
        slack_poster.post_error_to_slack = AsyncMock()

        state_store = MagicMock()
        state_store.get_cached_minutes.return_value = None

        mock_diarizer = MagicMock()
        mock_diarizer.load_model = MagicMock()
        mock_diarizer.unload_model = MagicMock()
        mock_diarizer.diarize = MagicMock(return_value=[])

        with patch("src.slack_pipeline.extract_audio", new_callable=AsyncMock), \
             patch("src.slack_pipeline.DiariZenDiarizer", return_value=mock_diarizer):
            with pytest.raises(RuntimeError, match="API down"):
                await run_slack_pipeline(
                    audio_path=audio_path,
                    vtt_path=vtt_path,
                    cfg=cfg,
                    generator=generator,
                    slack_poster=slack_poster,
                    state_store=state_store,
                )

        slack_poster.post_error_to_slack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_vram_cleanup_on_error(self, tmp_path: Path) -> None:
        """VRAM is released even when pipeline fails mid-diarization."""
        cfg = _make_cfg()
        vtt_path = _make_vtt_file(tmp_path)
        audio_path = _make_audio_file(tmp_path)

        mock_diarizer = MagicMock()
        mock_diarizer.load_model = MagicMock(side_effect=RuntimeError("CUDA init failed"))
        mock_diarizer.unload_model = MagicMock()

        slack_poster = MagicMock()
        slack_poster.post_error_to_slack = AsyncMock()

        with patch("src.slack_pipeline.extract_audio", new_callable=AsyncMock), \
             patch("src.slack_pipeline.DiariZenDiarizer", return_value=mock_diarizer):
            with pytest.raises(RuntimeError, match="CUDA init"):
                await run_slack_pipeline(
                    audio_path=audio_path,
                    vtt_path=vtt_path,
                    cfg=cfg,
                    generator=MagicMock(),
                    slack_poster=slack_poster,
                    state_store=MagicMock(),
                )

        # unload_model should still be called via finally block
        mock_diarizer.unload_model.assert_called()
