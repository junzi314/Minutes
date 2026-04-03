"""Unit tests for src/drive_watcher.py (Google Drive folder watcher)."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.audio_source import SpeakerAudio, SpeakerInfo
from src.config import DiarizationConfig, GoogleDriveConfig
from src.audio_source import ZIP_FILENAME_PATTERN
from src.drive_watcher import DriveWatcher, VideoDriveWatcher, _build_drive_service, _download_drive_file
from src.errors import DriveWatchError
from src.state_store import StateStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path, **overrides) -> GoogleDriveConfig:
    """Build a GoogleDriveConfig pointing at tmp_path for file-system artefacts."""
    defaults = dict(
        enabled=True,
        folder_id="test-folder",
        credentials_path=str(tmp_path / "creds.json"),
        poll_interval_sec=1,
        file_pattern="craig[_-]*.zip",
    )
    defaults.update(overrides)
    return GoogleDriveConfig(**defaults)


def _make_state_store(tmp_path: Path) -> StateStore:
    """Create a StateStore in a temp directory."""
    return StateStore(tmp_path / "state", legacy_db_path=tmp_path / "nonexistent.json")


def _make_zip(files: dict[str, bytes]) -> bytes:
    """Create an in-memory ZIP file with the given filename->content mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_watcher(
    cfg: GoogleDriveConfig,
    state_store: StateStore,
    callback: AsyncMock | None = None,
) -> DriveWatcher:
    """Create a DriveWatcher with a mock callback."""
    if callback is None:
        callback = AsyncMock()
    return DriveWatcher(cfg, state_store, on_new_tracks=callback)


# ===========================================================================
# 6-9: ZIP extraction
# ===========================================================================

class TestZipExtraction:
    """Tests for DriveWatcher._extract_zip (static method)."""

    def test_valid_craig_zip(self, tmp_path: Path) -> None:
        """ZIP with standard Craig entries produces correct SpeakerAudio list."""
        zip_bytes = _make_zip({
            "1-alice.aac": b"audio data alice",
            "2-bob.aac": b"audio data bob",
        })

        results = DriveWatcher._extract_zip(zip_bytes, tmp_path)

        assert len(results) == 2
        assert all(isinstance(r, SpeakerAudio) for r in results)

        # Check first speaker
        assert results[0].speaker.track == 1
        assert results[0].speaker.username == "alice"
        assert results[0].speaker.user_id == 0
        assert results[0].file_path == tmp_path / "1-alice.aac"
        assert results[0].file_path.read_bytes() == b"audio data alice"

        # Check second speaker
        assert results[1].speaker.track == 2
        assert results[1].speaker.username == "bob"
        assert results[1].file_path == tmp_path / "2-bob.aac"
        assert results[1].file_path.read_bytes() == b"audio data bob"

    def test_empty_zip(self, tmp_path: Path) -> None:
        """ZIP with no matching audio entries returns empty list."""
        zip_bytes = _make_zip({
            "info.json": b"{}",
            "readme.txt": b"hello",
        })

        results = DriveWatcher._extract_zip(zip_bytes, tmp_path)

        assert results == []

    def test_mixed_entries(self, tmp_path: Path) -> None:
        """Only entries matching the track-username.ext pattern are extracted."""
        zip_bytes = _make_zip({
            "1-alice.aac": b"audio",
            "info.json": b"{}",
            "2-bob.flac": b"audio flac",
            "README.md": b"# readme",
            "metadata.txt": b"data",
        })

        results = DriveWatcher._extract_zip(zip_bytes, tmp_path)

        assert len(results) == 2
        usernames = {r.speaker.username for r in results}
        assert usernames == {"alice", "bob"}

    def test_bad_zip_raises(self, tmp_path: Path) -> None:
        """Invalid bytes raise DriveWatchError."""
        with pytest.raises(DriveWatchError, match="Invalid ZIP"):
            DriveWatcher._extract_zip(b"this is not a zip", tmp_path)


# ===========================================================================
# 10: File pattern matching
# ===========================================================================

class TestFilePatternMatching:
    """Tests for fnmatch-based file pattern filtering."""

    def test_matching_pattern(self) -> None:
        """craig_12345.zip, craig-12345.aac.zip etc. match the default pattern."""
        import fnmatch

        pattern = "craig[_-]*.zip"
        assert fnmatch.fnmatch("craig_12345.aac.zip", pattern) is True
        assert fnmatch.fnmatch("craig_abc_def.aac.zip", pattern) is True
        assert fnmatch.fnmatch("craig-Q92fATPSYVKt_2026-3-2.aac.zip", pattern) is True
        assert fnmatch.fnmatch("craig-ElIRZgL22aDQ-2026-03-21.zip", pattern) is True
        assert fnmatch.fnmatch("craig-IGseKiVHcOMb-2026-03-23.zip", pattern) is True

    def test_non_matching_pattern(self) -> None:
        """Files that don't match the pattern are rejected."""
        import fnmatch

        pattern = "craig[_-]*.zip"
        assert fnmatch.fnmatch("random.zip", pattern) is False
        assert fnmatch.fnmatch("meeting_notes.aac.zip", pattern) is False


# ===========================================================================
# 11: Build service - missing credentials
# ===========================================================================

class TestBuildService:
    """Tests for _build_service."""

    def test_missing_credentials_raises(self, tmp_path: Path) -> None:
        """_build_service raises DriveWatchError when credentials file does not exist."""
        cfg = _make_cfg(tmp_path, credentials_path=str(tmp_path / "nonexistent.json"))
        state_store = _make_state_store(tmp_path)
        watcher = _make_watcher(cfg, state_store)

        with pytest.raises(DriveWatchError, match="credentials not found"):
            watcher._build_service()


# ===========================================================================
# 12-13: Watch loop early-exit conditions
# ===========================================================================

class TestWatchLoopEarlyExit:
    """Tests for _watch_loop pre-flight validation."""

    @pytest.mark.asyncio
    async def test_empty_folder_id_exits(self, tmp_path: Path) -> None:
        """Loop returns immediately when folder_id is empty."""
        cfg = _make_cfg(tmp_path, folder_id="")
        state_store = _make_state_store(tmp_path)
        watcher = _make_watcher(cfg, state_store)

        # _watch_loop should return immediately (no infinite loop)
        await asyncio.wait_for(watcher._watch_loop(), timeout=2.0)

        # Callback should never have been called
        assert watcher._on_new_tracks.call_count == 0

    @pytest.mark.asyncio
    async def test_missing_credentials_exits(self, tmp_path: Path) -> None:
        """Loop returns immediately when credentials file does not exist."""
        cfg = _make_cfg(
            tmp_path,
            credentials_path=str(tmp_path / "no_such_creds.json"),
        )
        state_store = _make_state_store(tmp_path)
        watcher = _make_watcher(cfg, state_store)

        await asyncio.wait_for(watcher._watch_loop(), timeout=2.0)

        assert watcher._on_new_tracks.call_count == 0


# ===========================================================================
# 14-15: Process file
# ===========================================================================

class TestProcessFile:
    """Tests for _process_file."""

    @pytest.mark.asyncio
    async def test_callback_invoked(self, tmp_path: Path) -> None:
        """After download and extraction, callback is called with correct args."""
        cfg = _make_cfg(tmp_path)
        state_store = _make_state_store(tmp_path)
        callback = AsyncMock()
        watcher = DriveWatcher(cfg, state_store, on_new_tracks=callback)

        zip_bytes = _make_zip({
            "1-alice.aac": b"audio alice",
            "2-bob.aac": b"audio bob",
        })

        with patch.object(watcher, "_download_file_sync", return_value=zip_bytes):
            loop = asyncio.get_running_loop()
            await watcher._process_file(loop, "file-id-1", "craig_testTESTtest_2026.aac.zip")

        # Callback must have been called exactly once
        callback.assert_awaited_once()

        call_args = callback.call_args
        tracks, source_label, dest_path = call_args[0]

        # Verify tracks
        assert len(tracks) == 2
        assert all(isinstance(t, SpeakerAudio) for t in tracks)
        usernames = {t.speaker.username for t in tracks}
        assert usernames == {"alice", "bob"}

        # Verify source label
        assert source_label == "drive:craig_testTESTtest_2026.aac.zip"

        # Verify dest_path is a Path
        assert isinstance(dest_path, Path)

    @pytest.mark.asyncio
    async def test_marks_processed_on_success(self, tmp_path: Path) -> None:
        """After successful callback, the rec_id is known in state_store."""
        cfg = _make_cfg(tmp_path)
        state_store = _make_state_store(tmp_path)
        callback = AsyncMock()
        watcher = DriveWatcher(cfg, state_store, on_new_tracks=callback)

        zip_bytes = _make_zip({"1-alice.aac": b"audio"})

        with patch.object(watcher, "_download_file_sync", return_value=zip_bytes):
            loop = asyncio.get_running_loop()
            await watcher._process_file(loop, "file-xyz", "craig_rec123456789_2026.aac.zip")

        # rec_id extracted from filename
        assert state_store.is_known("rec123456789")
        entry = state_store.get_entry("rec123456789")
        assert entry["status"] == "success"
        assert entry["source_id"] == "file-xyz"

    @pytest.mark.asyncio
    async def test_callback_failure_marks_failed(self, tmp_path: Path) -> None:
        """If the callback raises, _process_file marks the rec_id as failed."""
        cfg = _make_cfg(tmp_path)
        state_store = _make_state_store(tmp_path)
        callback = AsyncMock(side_effect=RuntimeError("pipeline failed"))
        watcher = DriveWatcher(cfg, state_store, on_new_tracks=callback)

        zip_bytes = _make_zip({"1-alice.aac": b"audio"})

        with patch.object(watcher, "_download_file_sync", return_value=zip_bytes):
            loop = asyncio.get_running_loop()
            with pytest.raises(RuntimeError, match="pipeline failed"):
                await watcher._process_file(loop, "fail-id", "craig_failID123456_2026.aac.zip")

        # rec_id should be marked as error
        assert state_store.is_known("failID123456")
        entry = state_store.get_entry("failID123456")
        assert entry["status"] == "error"


# ===========================================================================
# VideoDriveWatcher tests
# ===========================================================================

def _make_diar_cfg(**overrides) -> DiarizationConfig:
    """Build a DiarizationConfig for testing."""
    defaults = dict(
        enabled=True,
        drive_file_pattern="*.mp4",
        drive_mime_types=("video/mp4",),
    )
    defaults.update(overrides)
    return DiarizationConfig(**defaults)


def _make_video_watcher(
    drive_cfg: GoogleDriveConfig,
    diar_cfg: DiarizationConfig,
    state_store: StateStore,
    callback: AsyncMock | None = None,
) -> VideoDriveWatcher:
    if callback is None:
        callback = AsyncMock()
    return VideoDriveWatcher(drive_cfg, diar_cfg, state_store, on_new_video=callback)


class TestVideoDriveWatcher:
    def test_list_files_uses_mime_types(self, tmp_path: Path) -> None:
        """_list_files_sync builds query with configured mimeTypes."""
        drive_cfg = _make_cfg(tmp_path)
        diar_cfg = _make_diar_cfg(
            drive_mime_types=("video/mp4", "video/webm"),
            drive_file_pattern="*.mp4",
        )
        state_store = _make_state_store(tmp_path)
        watcher = _make_video_watcher(drive_cfg, diar_cfg, state_store)

        # Mock the Drive service
        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_list = MagicMock()
        mock_list.execute.return_value = {
            "files": [
                {"id": "f1", "name": "meeting.mp4", "mimeType": "video/mp4"},
                {"id": "f2", "name": "other.txt", "mimeType": "text/plain"},
            ]
        }
        mock_files.list.return_value = mock_list
        mock_service.files.return_value = mock_files
        watcher._service = mock_service

        results = watcher._list_files_sync()

        # Only meeting.mp4 matches *.mp4 pattern
        assert len(results) == 1
        assert results[0]["name"] == "meeting.mp4"

        # Verify query includes mimeType
        call_kwargs = mock_files.list.call_args.kwargs
        assert "video/mp4" in call_kwargs["q"]
        assert "video/webm" in call_kwargs["q"]

    def test_filters_by_pattern(self, tmp_path: Path) -> None:
        """Only files matching drive_file_pattern are returned."""
        drive_cfg = _make_cfg(tmp_path)
        diar_cfg = _make_diar_cfg(drive_file_pattern="meeting_*.mp4")
        state_store = _make_state_store(tmp_path)
        watcher = _make_video_watcher(drive_cfg, diar_cfg, state_store)

        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_list = MagicMock()
        mock_list.execute.return_value = {
            "files": [
                {"id": "f1", "name": "meeting_2026.mp4", "mimeType": "video/mp4"},
                {"id": "f2", "name": "random_video.mp4", "mimeType": "video/mp4"},
            ]
        }
        mock_files.list.return_value = mock_list
        mock_service.files.return_value = mock_files
        watcher._service = mock_service

        results = watcher._list_files_sync()

        assert len(results) == 1
        assert results[0]["name"] == "meeting_2026.mp4"

    @pytest.mark.asyncio
    async def test_downloads_raw_file(self, tmp_path: Path) -> None:
        """_process_file writes raw bytes to disk (no ZIP extraction)."""
        drive_cfg = _make_cfg(tmp_path)
        diar_cfg = _make_diar_cfg()
        state_store = _make_state_store(tmp_path)
        callback = AsyncMock()
        watcher = _make_video_watcher(drive_cfg, diar_cfg, state_store, callback)

        fake_bytes = b"fake mp4 data"
        mock_service = MagicMock()
        watcher._service = mock_service

        with patch("src.drive_watcher._download_drive_file", return_value=fake_bytes):
            loop = asyncio.get_running_loop()
            await watcher._process_file(loop, "vid-1", "test.mp4")

        callback.assert_awaited_once()
        file_path, source_label = callback.call_args[0]

        assert isinstance(file_path, Path)
        assert file_path.name == "test.mp4"
        assert source_label == "diarization:test.mp4"

    @pytest.mark.asyncio
    async def test_calls_callback(self, tmp_path: Path) -> None:
        """Callback receives (Path, source_label) arguments."""
        drive_cfg = _make_cfg(tmp_path)
        diar_cfg = _make_diar_cfg()
        state_store = _make_state_store(tmp_path)
        callback = AsyncMock()
        watcher = _make_video_watcher(drive_cfg, diar_cfg, state_store, callback)

        watcher._service = MagicMock()

        with patch("src.drive_watcher._download_drive_file", return_value=b"data"):
            loop = asyncio.get_running_loop()
            await watcher._process_file(loop, "vid-2", "meeting.mp4")

        callback.assert_awaited_once()
        args = callback.call_args[0]
        assert len(args) == 2
        assert isinstance(args[0], Path)
        assert isinstance(args[1], str)

    @pytest.mark.asyncio
    async def test_dedup(self, tmp_path: Path) -> None:
        """Already-processed files are skipped via StateStore."""
        drive_cfg = _make_cfg(tmp_path)
        diar_cfg = _make_diar_cfg()
        state_store = _make_state_store(tmp_path)
        callback = AsyncMock()
        watcher = _make_video_watcher(drive_cfg, diar_cfg, state_store, callback)

        watcher._service = MagicMock()

        with patch("src.drive_watcher._download_drive_file", return_value=b"data"):
            loop = asyncio.get_running_loop()
            # First call succeeds
            await watcher._process_file(loop, "vid-3", "test.mp4")
            # Second call with same ID should be skipped
            await watcher._process_file(loop, "vid-3", "test.mp4")

        # Callback called only once
        assert callback.await_count == 1
