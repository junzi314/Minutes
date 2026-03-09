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
from src.config import GoogleDriveConfig
from src.audio_source import ZIP_FILENAME_PATTERN
from src.drive_watcher import DriveWatcher
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
        file_pattern="craig[_-]*.aac.zip",
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
        """craig_12345.aac.zip and craig-12345.aac.zip match the default pattern."""
        import fnmatch

        pattern = "craig[_-]*.aac.zip"
        assert fnmatch.fnmatch("craig_12345.aac.zip", pattern) is True
        assert fnmatch.fnmatch("craig_abc_def.aac.zip", pattern) is True
        assert fnmatch.fnmatch("craig-Q92fATPSYVKt_2026-3-2.aac.zip", pattern) is True

    def test_non_matching_pattern(self) -> None:
        """Files that don't match the pattern are rejected."""
        import fnmatch

        pattern = "craig[_-]*.aac.zip"
        assert fnmatch.fnmatch("random.zip", pattern) is False
        assert fnmatch.fnmatch("craig_12345.flac.zip", pattern) is False
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
