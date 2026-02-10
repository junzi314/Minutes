"""Unit tests for src/craig_client.py (Job API v1)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
import aiohttp
from aioresponses import aioresponses

from src.audio_source import SpeakerAudio, SpeakerInfo
from src.config import CraigConfig
from src.audio_source import ZIP_FILENAME_PATTERN
from src.craig_client import CraigClient
from src.detector import DetectedRecording
from src.errors import AudioAcquisitionError, CookTimeoutError


# --- Fixtures ---

@pytest.fixture
def recording() -> DetectedRecording:
    return DetectedRecording(
        rec_id="test123",
        access_key="key456",
        rec_url="https://craig.horse/rec/test123?key=key456",
        guild_id=1,
        channel_id=2,
        message_id=3,
        craig_domain="craig.horse",
    )


@pytest.fixture
def cfg() -> CraigConfig:
    return CraigConfig(
        download_timeout_sec=10,
        max_retries=1,
    )


def _make_zip(files: dict[str, bytes]) -> bytes:
    """Create an in-memory ZIP file with the given filename->content mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _job_response(
    status: str = "complete",
    output_filename: str = "abc123.aac.zip",
    state_type: str = "finalizing",
) -> dict:
    """Build a Craig Job API response."""
    resp: dict = {
        "job": {
            "id": "abc123",
            "type": "recording",
            "options": {"format": "aac", "container": "zip", "dynaudnorm": False},
            "status": status,
            "state": {"type": state_type},
            "continued": False,
        },
        "streamOpen": False,
    }
    if status == "complete":
        resp["job"]["outputFileName"] = output_filename
        resp["job"]["outputSize"] = 1005512
        resp["job"]["finishedAt"] = "2026-02-10T00:25:12.957Z"
    return resp


# URLs used across tests
_JOB_URL = "https://craig.horse/api/v1/recordings/test123/job?key=key456"
_DL_FILENAME = "abc123.aac.zip"
_DL_URL = f"https://craig.horse/dl/{_DL_FILENAME}"


def _mock_job_flow(
    mocked: aioresponses,
    *,
    zip_data: bytes | None = None,
    dl_filename: str = _DL_FILENAME,
    poll_pending_count: int = 0,
) -> None:
    """Set up mocked responses for the full poll-download flow."""
    # Pending polls before completion
    for _ in range(poll_pending_count):
        mocked.get(_JOB_URL, payload=_job_response(status="pending", state_type="cooking"))

    # Final: complete
    mocked.get(_JOB_URL, payload=_job_response(output_filename=dl_filename))

    # File download
    if zip_data is not None:
        dl_url = f"https://craig.horse/dl/{dl_filename}"
        mocked.get(dl_url, body=zip_data)


# --- ZIP filename pattern ---

def test_zip_pattern_standard() -> None:
    m = ZIP_FILENAME_PATTERN.match("1-shake344.aac")
    assert m is not None
    assert m.group(1) == "1"
    assert m.group(2) == "shake344"
    assert m.group(3) == "aac"


def test_zip_pattern_underscore_name() -> None:
    m = ZIP_FILENAME_PATTERN.match("2-john_doe.flac")
    assert m is not None
    assert m.group(2) == "john_doe"


def test_zip_pattern_no_match() -> None:
    assert ZIP_FILENAME_PATTERN.match("info.txt") is None
    assert ZIP_FILENAME_PATTERN.match("README") is None


# --- get_speakers (now raises NotImplementedError) ---

@pytest.mark.asyncio
async def test_get_speakers_raises(
    recording: DetectedRecording,
    cfg: CraigConfig,
) -> None:
    async with aiohttp.ClientSession() as session:
        client = CraigClient(session, recording, cfg)
        with pytest.raises(NotImplementedError):
            await client.get_speakers()


# --- download (job poll + download flow) ---

@pytest.mark.asyncio
async def test_download_success(
    recording: DetectedRecording,
    cfg: CraigConfig,
    tmp_path: Path,
) -> None:
    zip_data = _make_zip({
        "1-shake344.aac": b"fake audio 1",
        "2-john_doe.aac": b"fake audio 2",
        "3-tanaka_san.aac": b"fake audio 3",
        "info.txt": b"metadata",
    })

    async with aiohttp.ClientSession() as session:
        client = CraigClient(session, recording, cfg)

        with aioresponses() as mocked:
            _mock_job_flow(mocked, zip_data=zip_data)
            results = await client.download(tmp_path)

    assert len(results) == 3
    assert all(isinstance(r, SpeakerAudio) for r in results)
    # Speaker info derived from ZIP filenames
    assert results[0].speaker.username == "shake344"
    assert results[0].speaker.track == 1
    assert results[0].speaker.user_id == 0  # no user API, always 0
    assert results[0].file_path.exists()
    assert results[0].file_path.read_bytes() == b"fake audio 1"


@pytest.mark.asyncio
async def test_download_with_polling(
    recording: DetectedRecording,
    cfg: CraigConfig,
    tmp_path: Path,
) -> None:
    """Job is not ready immediately, requires polling."""
    zip_data = _make_zip({"1-shake344.aac": b"audio"})

    async with aiohttp.ClientSession() as session:
        client = CraigClient(session, recording, cfg)

        with aioresponses() as mocked:
            _mock_job_flow(mocked, zip_data=zip_data, poll_pending_count=2)
            results = await client.download(tmp_path)

    assert len(results) == 1
    assert results[0].speaker.username == "shake344"


@pytest.mark.asyncio
async def test_download_empty_zip(
    recording: DetectedRecording,
    cfg: CraigConfig,
    tmp_path: Path,
) -> None:
    zip_data = _make_zip({"info.txt": b"no audio"})

    async with aiohttp.ClientSession() as session:
        client = CraigClient(session, recording, cfg)

        with aioresponses() as mocked:
            _mock_job_flow(mocked, zip_data=zip_data)

            with pytest.raises(AudioAcquisitionError, match="No audio files"):
                await client.download(tmp_path)


@pytest.mark.asyncio
async def test_download_invalid_zip(
    recording: DetectedRecording,
    cfg: CraigConfig,
    tmp_path: Path,
) -> None:
    async with aiohttp.ClientSession() as session:
        client = CraigClient(session, recording, cfg)

        with aioresponses() as mocked:
            _mock_job_flow(mocked, zip_data=b"this is not a zip file")

            with pytest.raises(AudioAcquisitionError, match="Invalid ZIP"):
                await client.download(tmp_path)


@pytest.mark.asyncio
async def test_download_job_failed(
    recording: DetectedRecording,
    cfg: CraigConfig,
    tmp_path: Path,
) -> None:
    """Job reports error status -> should raise immediately."""
    async with aiohttp.ClientSession() as session:
        client = CraigClient(session, recording, cfg)

        with aioresponses() as mocked:
            mocked.get(_JOB_URL, payload=_job_response(status="error", state_type="error"))

            with pytest.raises(AudioAcquisitionError, match="Cook job failed"):
                await client.download(tmp_path)


@pytest.mark.asyncio
async def test_download_job_no_output_filename(
    recording: DetectedRecording,
    cfg: CraigConfig,
    tmp_path: Path,
) -> None:
    """Job complete but outputFileName missing -> should raise."""
    response = _job_response()
    del response["job"]["outputFileName"]

    async with aiohttp.ClientSession() as session:
        client = CraigClient(session, recording, cfg)

        with aioresponses() as mocked:
            mocked.get(_JOB_URL, payload=response)

            with pytest.raises(AudioAcquisitionError, match="no outputFileName"):
                await client.download(tmp_path)
