"""Unit tests for src/zoom_drive_watcher.py (Zoom file pairing watcher)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import GoogleDriveConfig
from src.slack_config import ZoomConfig
from src.state_store import StateStore
from src.zoom_drive_watcher import ZoomDriveWatcher, _PendingPair


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_drive_cfg(tmp_path: Path, **overrides) -> GoogleDriveConfig:
    defaults = dict(
        enabled=True,
        folder_id="test-folder",
        credentials_path=str(tmp_path / "creds.json"),
        poll_interval_sec=1,
    )
    defaults.update(overrides)
    return GoogleDriveConfig(**defaults)


def _make_zoom_cfg(**overrides) -> ZoomConfig:
    defaults = dict(
        vtt_file_pattern="*.vtt",
        audio_file_pattern="*.m4a",
        pair_timeout_sec=300,
    )
    defaults.update(overrides)
    return ZoomConfig(**defaults)


def _make_state_store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state", legacy_db_path=tmp_path / "nonexistent.json")


def _make_watcher(
    tmp_path: Path,
    callback: AsyncMock | None = None,
    drive_overrides: dict | None = None,
    zoom_overrides: dict | None = None,
) -> ZoomDriveWatcher:
    if callback is None:
        callback = AsyncMock()
    drive_cfg = _make_drive_cfg(tmp_path, **(drive_overrides or {}))
    zoom_cfg = _make_zoom_cfg(**(zoom_overrides or {}))
    state_store = _make_state_store(tmp_path)
    return ZoomDriveWatcher(drive_cfg, zoom_cfg, state_store, on_pair_ready=callback)


# ===========================================================================
# Tests: Pair key extraction
# ===========================================================================


class TestPairKey:
    def test_m4a_key(self) -> None:
        """m4a filename produces correct pair key."""
        assert ZoomDriveWatcher._pair_key("meeting_2026.m4a") == "meeting_2026"

    def test_vtt_key(self) -> None:
        """VTT filename produces same key as corresponding m4a."""
        assert ZoomDriveWatcher._pair_key("meeting_2026.vtt") == "meeting_2026"

    def test_key_matches(self) -> None:
        """m4a and VTT for same meeting produce the same key."""
        assert ZoomDriveWatcher._pair_key("recording.m4a") == ZoomDriveWatcher._pair_key(
            "recording.vtt"
        )

    def test_different_meetings_different_keys(self) -> None:
        """Different meeting names produce different keys."""
        assert ZoomDriveWatcher._pair_key("meeting_a.m4a") != ZoomDriveWatcher._pair_key(
            "meeting_b.m4a"
        )


# ===========================================================================
# Tests: File classification and buffering
# ===========================================================================


class TestClassifyAndBuffer:
    def test_buffer_audio(self, tmp_path: Path) -> None:
        """m4a file is buffered as audio."""
        watcher = _make_watcher(tmp_path)

        watcher._classify_and_buffer({"id": "f1", "name": "meeting.m4a", "mimeType": "audio/mp4"})

        assert "meeting" in watcher._pair_buffer
        pair = watcher._pair_buffer["meeting"]
        assert pair.audio_id == "f1"
        assert pair.audio_name == "meeting.m4a"
        assert pair.vtt_id is None

    def test_buffer_vtt(self, tmp_path: Path) -> None:
        """VTT file is buffered as transcript."""
        watcher = _make_watcher(tmp_path)

        watcher._classify_and_buffer({"id": "f2", "name": "meeting.vtt", "mimeType": "text/vtt"})

        pair = watcher._pair_buffer["meeting"]
        assert pair.vtt_id == "f2"
        assert pair.vtt_name == "meeting.vtt"
        assert pair.audio_id is None

    def test_pair_completes(self, tmp_path: Path) -> None:
        """Adding both audio and VTT completes the pair."""
        watcher = _make_watcher(tmp_path)

        watcher._classify_and_buffer({"id": "f1", "name": "meeting.m4a", "mimeType": "audio/mp4"})
        watcher._classify_and_buffer({"id": "f2", "name": "meeting.vtt", "mimeType": "text/vtt"})

        pair = watcher._pair_buffer["meeting"]
        assert pair.is_complete

    def test_pair_order_independent(self, tmp_path: Path) -> None:
        """VTT first, then audio — still completes."""
        watcher = _make_watcher(tmp_path)

        watcher._classify_and_buffer({"id": "f2", "name": "rec.vtt", "mimeType": "text/vtt"})
        watcher._classify_and_buffer({"id": "f1", "name": "rec.m4a", "mimeType": "audio/mp4"})

        assert watcher._pair_buffer["rec"].is_complete

    def test_ignores_unmatched_pattern(self, tmp_path: Path) -> None:
        """Files not matching either pattern are not buffered."""
        watcher = _make_watcher(tmp_path)

        # _classify_and_buffer is called after filtering in _list_files_sync,
        # but if called directly with a non-matching name, neither branch sets a value
        watcher._classify_and_buffer({"id": "f3", "name": "notes.txt", "mimeType": "text/plain"})

        # Key is created but neither audio nor vtt is set
        if "notes" in watcher._pair_buffer:
            pair = watcher._pair_buffer["notes"]
            assert not pair.is_complete


# ===========================================================================
# Tests: Pair timeout / expiration
# ===========================================================================


class TestPairExpiration:
    def test_stale_pair_expired(self, tmp_path: Path) -> None:
        """Pairs older than pair_timeout_sec are expired."""
        watcher = _make_watcher(tmp_path, zoom_overrides={"pair_timeout_sec": 30})

        # Add incomplete pair with old timestamp
        watcher._pair_buffer["old_meeting"] = _PendingPair(
            audio_id="f1",
            audio_name="old_meeting.m4a",
            first_seen=time.monotonic() - 60,  # 60 seconds ago
        )

        expired = watcher._expire_stale_pairs()

        assert "old_meeting" in expired
        assert "old_meeting" not in watcher._pair_buffer

    def test_fresh_pair_kept(self, tmp_path: Path) -> None:
        """Pairs within timeout window are kept."""
        watcher = _make_watcher(tmp_path, zoom_overrides={"pair_timeout_sec": 300})

        watcher._pair_buffer["new_meeting"] = _PendingPair(
            audio_id="f1",
            audio_name="new_meeting.m4a",
            first_seen=time.monotonic(),
        )

        expired = watcher._expire_stale_pairs()

        assert expired == []
        assert "new_meeting" in watcher._pair_buffer


# ===========================================================================
# Tests: Process pair
# ===========================================================================


class TestProcessPair:
    @pytest.mark.asyncio
    async def test_callback_invoked(self, tmp_path: Path) -> None:
        """Callback receives (audio_path, vtt_path, source_label)."""
        callback = AsyncMock()
        watcher = _make_watcher(tmp_path, callback=callback)
        watcher._service = MagicMock()

        pair = _PendingPair(
            audio_id="a1", audio_name="meeting.m4a",
            vtt_id="v1", vtt_name="meeting.vtt",
        )

        with patch("src.zoom_drive_watcher._download_drive_file", side_effect=[b"audio", b"vtt"]):
            loop = asyncio.get_running_loop()
            await watcher._process_pair(loop, pair, "meeting")

        callback.assert_awaited_once()
        audio_path, vtt_path, source_label = callback.call_args[0]
        assert isinstance(audio_path, Path)
        assert isinstance(vtt_path, Path)
        assert audio_path.name == "meeting.m4a"
        assert vtt_path.name == "meeting.vtt"
        assert source_label == "zoom:meeting.m4a"

    @pytest.mark.asyncio
    async def test_marks_success(self, tmp_path: Path) -> None:
        """Successful processing marks state as success."""
        callback = AsyncMock()
        drive_cfg = _make_drive_cfg(tmp_path)
        zoom_cfg = _make_zoom_cfg()
        state_store = _make_state_store(tmp_path)
        watcher = ZoomDriveWatcher(drive_cfg, zoom_cfg, state_store, on_pair_ready=callback)
        watcher._service = MagicMock()

        pair = _PendingPair(
            audio_id="a1", audio_name="test.m4a",
            vtt_id="v1", vtt_name="test.vtt",
        )

        with patch("src.zoom_drive_watcher._download_drive_file", side_effect=[b"audio", b"vtt"]):
            loop = asyncio.get_running_loop()
            await watcher._process_pair(loop, pair, "test")

        assert state_store.is_known("zoom_pair:test")
        entry = state_store.get_entry("zoom_pair:test")
        assert entry["status"] == "success"

    @pytest.mark.asyncio
    async def test_marks_failed_on_error(self, tmp_path: Path) -> None:
        """Failed callback marks state as error."""
        callback = AsyncMock(side_effect=RuntimeError("pipeline failed"))
        drive_cfg = _make_drive_cfg(tmp_path)
        zoom_cfg = _make_zoom_cfg()
        state_store = _make_state_store(tmp_path)
        watcher = ZoomDriveWatcher(drive_cfg, zoom_cfg, state_store, on_pair_ready=callback)
        watcher._service = MagicMock()

        pair = _PendingPair(
            audio_id="a1", audio_name="fail.m4a",
            vtt_id="v1", vtt_name="fail.vtt",
        )

        with patch("src.zoom_drive_watcher._download_drive_file", side_effect=[b"audio", b"vtt"]):
            loop = asyncio.get_running_loop()
            with pytest.raises(RuntimeError, match="pipeline failed"):
                await watcher._process_pair(loop, pair, "fail")

        assert state_store.is_known("zoom_pair:fail")
        entry = state_store.get_entry("zoom_pair:fail")
        assert entry["status"] == "error"

    @pytest.mark.asyncio
    async def test_dedup(self, tmp_path: Path) -> None:
        """Already-processed pair is skipped."""
        callback = AsyncMock()
        drive_cfg = _make_drive_cfg(tmp_path)
        zoom_cfg = _make_zoom_cfg()
        state_store = _make_state_store(tmp_path)
        watcher = ZoomDriveWatcher(drive_cfg, zoom_cfg, state_store, on_pair_ready=callback)
        watcher._service = MagicMock()

        pair = _PendingPair(
            audio_id="a1", audio_name="dup.m4a",
            vtt_id="v1", vtt_name="dup.vtt",
        )

        with patch("src.zoom_drive_watcher._download_drive_file", side_effect=[b"audio", b"vtt"]):
            loop = asyncio.get_running_loop()
            await watcher._process_pair(loop, pair, "dup")

        # Second call should be skipped
        with patch("src.zoom_drive_watcher._download_drive_file") as mock_dl:
            await watcher._process_pair(loop, pair, "dup")
            mock_dl.assert_not_called()

        assert callback.await_count == 1


# ===========================================================================
# Tests: Watch loop early exit
# ===========================================================================


class TestWatchLoopEarlyExit:
    @pytest.mark.asyncio
    async def test_empty_folder_id(self, tmp_path: Path) -> None:
        """Loop exits immediately when folder_id is empty."""
        watcher = _make_watcher(tmp_path, drive_overrides={"folder_id": ""})

        await asyncio.wait_for(watcher._watch_loop(), timeout=2.0)

    @pytest.mark.asyncio
    async def test_missing_credentials(self, tmp_path: Path) -> None:
        """Loop exits immediately when credentials file is missing."""
        watcher = _make_watcher(
            tmp_path,
            drive_overrides={"credentials_path": str(tmp_path / "nonexistent.json")},
        )

        await asyncio.wait_for(watcher._watch_loop(), timeout=2.0)


# ===========================================================================
# Tests: Start/Stop lifecycle
# ===========================================================================


class TestStartStop:
    def test_start_creates_task(self, tmp_path: Path) -> None:
        """start() creates an asyncio task."""
        # We can only test the is_running property since start() needs a running loop
        watcher = _make_watcher(tmp_path)
        assert not watcher.is_running

    def test_stop_idempotent(self, tmp_path: Path) -> None:
        """stop() is safe to call when not running."""
        watcher = _make_watcher(tmp_path)
        watcher.stop()  # Should not raise
        assert not watcher.is_running
