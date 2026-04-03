"""FFmpeg audio extraction: video/audio files → WAV 16kHz mono."""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class AudioExtractionError(Exception):
    """Raised when FFmpeg audio extraction fails."""
    pass


async def extract_audio(
    input_path: Path,
    output_path: Path,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    timeout_sec: int = 300,
) -> Path:
    """Extract audio from a video/audio file as WAV.

    Runs FFmpeg in a subprocess via asyncio.create_subprocess_exec().

    Args:
        input_path: Source video/audio file.
        output_path: Destination WAV file path.
        sample_rate: Target sample rate (16000 for Whisper/DiariZen).
        channels: Number of audio channels (1 = mono).
        timeout_sec: Maximum allowed time for FFmpeg process.

    Returns:
        Path to the output WAV file.

    Raises:
        AudioExtractionError: If FFmpeg fails or times out.
        FileNotFoundError: If input_path does not exist.
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", str(channels),
        str(output_path),
    ]

    logger.info("Extracting audio: %s → %s", input_path.name, output_path.name)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise AudioExtractionError(
            "FFmpeg not found. Install FFmpeg: apt install ffmpeg"
        )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_sec
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise AudioExtractionError(
            f"FFmpeg timed out after {timeout_sec}s: {input_path.name}"
        )

    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace")[-500:]
        raise AudioExtractionError(
            f"FFmpeg failed (rc={proc.returncode}) for {input_path.name}: {err_msg}"
        )

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise AudioExtractionError(
            f"FFmpeg produced empty output for {input_path.name}"
        )

    logger.info("Audio extracted: %s (%.1f MB)", output_path.name, output_path.stat().st_size / 1024**2)
    return output_path
