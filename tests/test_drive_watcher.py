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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path, **overrides) -> GoogleDriveConfig:
    """Build a GoogleDriveConfig pointing at tmp_path for file-system artefacts."""
    defaults = dict(
        enabled=True,
        folder_id="test-folder",
        credentials_path=str(tmp_path / "creds.json"),
        processed_db_path=str(tmp_path / "processed.json"),
        poll_interval_sec=1,
        file_pattern="craig_*.aac.zip",
    )
    defaults.update(overrides)
    return GoogleDriveConfig(**defaults)


def _make_zip(files: dict[str, bytes]) -> bytes:
    """Create an in-memory ZIP file with the given filename->content mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_watcher(
    cfg: GoogleDriveConfig,
    callback: AsyncMock | None = None,
) -> DriveWatcher:
    """Create a DriveWatcher with a mock callback."""
    if callback is None:
        callback = AsyncMock()
    return DriveWatcher(cfg, on_new_tracks=callback)


# ===========================================================================
# 1-5: Processed-file database
# ===========================================================================

class TestProcessedDB:
    """Tests for _load_processed_db, _save_processed_db, _mark_processed."""

    def test_load_empty_no_file(self, tmp_path: Path) -> None:
        """No DB file on disk -> starts with an empty dict."""
        cfg = _make_cfg(tmp_path)
        watcher = _make_watcher(cfg)

        watcher._load_processed_db()

        assert watcher._processed == {}

    def test_load_existing(self, tmp_path: Path) -> None:
        """JSON file with entries loads correctly."""
        cfg = _make_cfg(tmp_path)
        db_path = Path(cfg.processed_db_path)
        db_path.write_text(json.dumps({
            "processed": {
                "file-1": {"name": "craig_001.aac.zip", "processed_at": "2026-01-01T00:00:00+00:00"},
                "file-2": {"name": "craig_002.aac.zip", "processed_at": "2026-01-02T00:00:00+00:00"},
            }
        }), encoding="utf-8")

        watcher = _make_watcher(cfg)
        watcher._load_processed_db()

        assert len(watcher._processed) == 2
        assert "file-1" in watcher._processed
        assert "file-2" in watcher._processed
        assert watcher._processed["file-1"]["name"] == "craig_001.aac.zip"

    def test_load_corrupted_falls_back_to_empty(self, tmp_path: Path) -> None:
        """Invalid JSON falls back to empty dict without raising."""
        cfg = _make_cfg(tmp_path)
        db_path = Path(cfg.processed_db_path)
        db_path.write_text("{this is not valid json!!!", encoding="utf-8")

        watcher = _make_watcher(cfg)
        watcher._load_processed_db()

        assert watcher._processed == {}

    def test_save_and_reload(self, tmp_path: Path) -> None:
        """_mark_processed persists to disk; a new instance can reload it."""
        cfg = _make_cfg(tmp_path)

        watcher1 = _make_watcher(cfg)
        watcher1._load_processed_db()
        watcher1._mark_processed("abc-123", "craig_test.aac.zip")

        # Verify file was written
        db_path = Path(cfg.processed_db_path)
        assert db_path.exists()
        data = json.loads(db_path.read_text(encoding="utf-8"))
        assert "abc-123" in data["processed"]

        # New instance should load the same data
        watcher2 = _make_watcher(cfg)
        watcher2._load_processed_db()
        assert "abc-123" in watcher2._processed
        assert watcher2._processed["abc-123"]["name"] == "craig_test.aac.zip"

    def test_duplicate_prevention(self, tmp_path: Path) -> None:
        """A file ID already in the processed dict is not reprocessed.

        This tests the filtering logic used in _watch_loop: files whose id
        is in self._processed are skipped.
        """
        cfg = _make_cfg(tmp_path)
        watcher = _make_watcher(cfg)
        watcher._load_processed_db()
        watcher._mark_processed("already-done", "craig_old.aac.zip")

        # Simulate the filtering that _watch_loop performs
        listed_files = [
            {"id": "already-done", "name": "craig_old.aac.zip"},
            {"id": "brand-new", "name": "craig_new.aac.zip"},
        ]
        new_files = [f for f in listed_files if f["id"] not in watcher._processed]

        assert len(new_files) == 1
        assert new_files[0]["id"] == "brand-new"


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
        """craig_12345.aac.zip matches the default pattern."""
        import fnmatch

        pattern = "craig_*.aac.zip"
        assert fnmatch.fnmatch("craig_12345.aac.zip", pattern) is True
        assert fnmatch.fnmatch("craig_abc_def.aac.zip", pattern) is True

    def test_non_matching_pattern(self) -> None:
        """Files that don't match the pattern are rejected."""
        import fnmatch

        pattern = "craig_*.aac.zip"
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
        watcher = _make_watcher(cfg)

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
        watcher = _make_watcher(cfg)

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
        watcher = _make_watcher(cfg)

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
        callback = AsyncMock()
        watcher = DriveWatcher(cfg, on_new_tracks=callback)

        zip_bytes = _make_zip({
            "1-alice.aac": b"audio alice",
            "2-bob.aac": b"audio bob",
        })

        with patch.object(watcher, "_download_file_sync", return_value=zip_bytes):
            loop = asyncio.get_running_loop()
            await watcher._process_file(loop, "file-id-1", "craig_test.aac.zip")

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
        assert source_label == "drive:craig_test.aac.zip"

        # Verify dest_path is a Path
        assert isinstance(dest_path, Path)

    @pytest.mark.asyncio
    async def test_marks_processed_on_success(self, tmp_path: Path) -> None:
        """After successful callback, the file_id is in the processed dict."""
        cfg = _make_cfg(tmp_path)
        callback = AsyncMock()
        watcher = DriveWatcher(cfg, on_new_tracks=callback)

        zip_bytes = _make_zip({"1-alice.aac": b"audio"})

        with patch.object(watcher, "_download_file_sync", return_value=zip_bytes):
            loop = asyncio.get_running_loop()
            await watcher._process_file(loop, "file-xyz", "craig_rec.aac.zip")

        assert "file-xyz" in watcher._processed
        assert watcher._processed["file-xyz"]["name"] == "craig_rec.aac.zip"

        # Also verify it was persisted to disk
        db_path = Path(cfg.processed_db_path)
        assert db_path.exists()
        data = json.loads(db_path.read_text(encoding="utf-8"))
        assert "file-xyz" in data["processed"]

    @pytest.mark.asyncio
    async def test_callback_failure_does_not_mark_processed(self, tmp_path: Path) -> None:
        """If the callback raises, _process_file must NOT mark the file as processed
        (the caller _watch_loop handles marking it as failed instead)."""
        cfg = _make_cfg(tmp_path)
        callback = AsyncMock(side_effect=RuntimeError("pipeline failed"))
        watcher = DriveWatcher(cfg, on_new_tracks=callback)

        zip_bytes = _make_zip({"1-alice.aac": b"audio"})

        with patch.object(watcher, "_download_file_sync", return_value=zip_bytes):
            loop = asyncio.get_running_loop()
            with pytest.raises(RuntimeError, match="pipeline failed"):
                await watcher._process_file(loop, "fail-id", "craig_fail.aac.zip")

        assert "fail-id" not in watcher._processed


# ===========================================================================
# 16-17: Failed file recording
# ===========================================================================

class TestMarkFailed:
    """Tests for _mark_failed and reprocessing prevention."""

    def test_mark_failed_records_error(self, tmp_path: Path) -> None:
        """_mark_failed records the file with error status and details."""
        cfg = _make_cfg(tmp_path)
        watcher = _make_watcher(cfg)
        watcher._load_processed_db()

        watcher._mark_failed("err-id", "craig_bad.aac.zip", "Download timeout")

        assert "err-id" in watcher._processed
        entry = watcher._processed["err-id"]
        assert entry["name"] == "craig_bad.aac.zip"
        assert entry["status"] == "error"
        assert entry["error"] == "Download timeout"
        assert "failed_at" in entry

        # Verify persisted to disk
        db_path = Path(cfg.processed_db_path)
        data = json.loads(db_path.read_text(encoding="utf-8"))
        assert "err-id" in data["processed"]
        assert data["processed"]["err-id"]["status"] == "error"

    def test_failed_file_not_reprocessed(self, tmp_path: Path) -> None:
        """A file marked as failed is filtered out in the next poll cycle."""
        cfg = _make_cfg(tmp_path)
        watcher = _make_watcher(cfg)
        watcher._load_processed_db()
        watcher._mark_failed("err-id", "craig_bad.aac.zip", "Invalid ZIP")

        # Simulate the filtering that _watch_loop performs
        listed_files = [
            {"id": "err-id", "name": "craig_bad.aac.zip"},
            {"id": "new-id", "name": "craig_new.aac.zip"},
        ]
        new_files = [f for f in listed_files if f["id"] not in watcher._processed]

        assert len(new_files) == 1
        assert new_files[0]["id"] == "new-id"
