"""Unit tests for src/audio_extractor.py."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.audio_extractor import AudioExtractionError, extract_audio


@pytest.fixture
def input_file(tmp_path: Path) -> Path:
    """Create a dummy input file."""
    p = tmp_path / "test.mp4"
    p.write_bytes(b"fake video data")
    return p


@pytest.fixture
def output_file(tmp_path: Path) -> Path:
    """Output path (does not exist yet)."""
    return tmp_path / "output.wav"


def _make_mock_process(returncode: int = 0, stderr: bytes = b"") -> AsyncMock:
    """Create a mock subprocess with configurable returncode and stderr."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
class TestExtractAudio:
    async def test_success(
        self, input_file: Path, output_file: Path
    ) -> None:
        proc = _make_mock_process(returncode=0)

        with patch("src.audio_extractor.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            # Create output file to simulate FFmpeg writing it
            output_file.write_bytes(b"RIFF" + b"\x00" * 100)

            result = await extract_audio(input_file, output_file)

        assert result == output_file
        # Verify FFmpeg was called with correct args
        call_args = mock_exec.call_args[0]
        assert call_args[0] == "ffmpeg"
        assert "-vn" in call_args
        assert "-ar" in call_args
        assert "16000" in call_args

    async def test_ffmpeg_not_found(
        self, input_file: Path, output_file: Path
    ) -> None:
        with patch(
            "src.audio_extractor.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("ffmpeg"),
        ):
            with pytest.raises(AudioExtractionError, match="FFmpeg not found"):
                await extract_audio(input_file, output_file)

    async def test_nonzero_exit(
        self, input_file: Path, output_file: Path
    ) -> None:
        proc = _make_mock_process(returncode=1, stderr=b"Error: invalid input")

        with patch("src.audio_extractor.asyncio.create_subprocess_exec", return_value=proc):
            with pytest.raises(AudioExtractionError, match="FFmpeg failed.*rc=1"):
                await extract_audio(input_file, output_file)

    async def test_timeout(
        self, input_file: Path, output_file: Path
    ) -> None:
        proc = AsyncMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        proc.kill = MagicMock()

        with patch("src.audio_extractor.asyncio.create_subprocess_exec", return_value=proc):
            with pytest.raises(AudioExtractionError, match="timed out"):
                await extract_audio(input_file, output_file, timeout_sec=1)

        proc.kill.assert_called_once()

    async def test_empty_output(
        self, input_file: Path, output_file: Path
    ) -> None:
        proc = _make_mock_process(returncode=0)

        with patch("src.audio_extractor.asyncio.create_subprocess_exec", return_value=proc):
            # Don't create output file — simulates FFmpeg producing nothing
            with pytest.raises(AudioExtractionError, match="empty output"):
                await extract_audio(input_file, output_file)

    async def test_input_not_found(
        self, tmp_path: Path, output_file: Path
    ) -> None:
        missing = tmp_path / "nonexistent.mp4"
        with pytest.raises(FileNotFoundError, match="Input file not found"):
            await extract_audio(missing, output_file)

    async def test_custom_sample_rate(
        self, input_file: Path, output_file: Path
    ) -> None:
        proc = _make_mock_process(returncode=0)

        with patch("src.audio_extractor.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            output_file.write_bytes(b"RIFF" + b"\x00" * 100)

            await extract_audio(input_file, output_file, sample_rate=44100)

        call_args = mock_exec.call_args[0]
        assert "44100" in call_args
