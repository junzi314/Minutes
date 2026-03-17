"""Unit tests for src/state_store.py (StateStore and extract_rec_id)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.state_store import StateStore, extract_rec_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path, **kwargs) -> StateStore:
    """Create a StateStore in a temp directory with no legacy migration."""
    state_dir = tmp_path / "state"
    return StateStore(state_dir, legacy_db_path=tmp_path / "nonexistent.json", **kwargs)


def _read_processing(tmp_path: Path) -> dict:
    """Read the processing.json file from the state directory."""
    return json.loads((tmp_path / "state" / "processing.json").read_text(encoding="utf-8"))


def _read_cache(tmp_path: Path) -> dict:
    """Read the minutes_cache.json file from the state directory."""
    return json.loads((tmp_path / "state" / "minutes_cache.json").read_text(encoding="utf-8"))


# ===========================================================================
# 1-5: Construction and loading
# ===========================================================================


class TestConstruction:

    def test_init_creates_state_dir(self, tmp_path: Path) -> None:
        """State directory is created if it does not exist."""
        state_dir = tmp_path / "new_state"
        assert not state_dir.exists()
        StateStore(state_dir, legacy_db_path=tmp_path / "none.json")
        assert state_dir.is_dir()

    def test_init_loads_empty_when_no_files(self, tmp_path: Path) -> None:
        """No state files -> starts with empty dicts."""
        store = _make_store(tmp_path)
        assert store.processing_count == 0
        assert store.get_cached_minutes("anything") is None

    def test_init_loads_existing_processing(self, tmp_path: Path) -> None:
        """Pre-written processing.json is loaded correctly."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "processing.json").write_text(json.dumps({
            "abc123456789": {
                "source": "drive",
                "source_id": "file-1",
                "file_name": "craig_abc123456789_2026.aac.zip",
                "status": "success",
                "completed_at": "2026-01-01T00:00:00+00:00",
            }
        }), encoding="utf-8")

        store = StateStore(state_dir, legacy_db_path=tmp_path / "none.json")
        assert store.is_known("abc123456789")
        assert store.processing_count == 1

    def test_init_loads_existing_cache(self, tmp_path: Path) -> None:
        """Pre-written minutes_cache.json is loaded correctly."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "minutes_cache.json").write_text(json.dumps({
            "hash123": "# Minutes"
        }), encoding="utf-8")

        store = StateStore(state_dir, legacy_db_path=tmp_path / "none.json")
        assert store.get_cached_minutes("hash123") == "# Minutes"

    def test_init_handles_corrupt_json(self, tmp_path: Path) -> None:
        """Corrupt JSON files -> starts empty, logs warning."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "processing.json").write_text("{invalid json!!!", encoding="utf-8")
        (state_dir / "minutes_cache.json").write_text("not json", encoding="utf-8")

        store = StateStore(state_dir, legacy_db_path=tmp_path / "none.json")
        assert store.processing_count == 0
        assert store.get_cached_minutes("x") is None


# ===========================================================================
# 6-11: mark_processing
# ===========================================================================


class TestMarkProcessing:

    def test_new_entry(self, tmp_path: Path) -> None:
        """Returns True; entry is created and persisted to disk."""
        store = _make_store(tmp_path)

        result = store.mark_processing("rec001", source="craig", source_id="rec001", file_name="")
        assert result is True
        assert store.is_known("rec001")

        # Verify on disk
        data = _read_processing(tmp_path)
        assert "rec001" in data
        assert data["rec001"]["status"] == "processing"
        assert data["rec001"]["source"] == "craig"

    def test_duplicate_returns_false(self, tmp_path: Path) -> None:
        """Second call with same rec_id returns False; entry not overwritten."""
        store = _make_store(tmp_path)
        store.mark_processing("rec001", source="craig", source_id="rec001", file_name="")

        result = store.mark_processing("rec001", source="drive", source_id="file-1", file_name="test.zip")
        assert result is False
        # Original entry preserved
        entry = store.get_entry("rec001")
        assert entry["source"] == "craig"

    def test_persists_across_instances(self, tmp_path: Path) -> None:
        """New StateStore instance sees the previously marked entry."""
        store1 = _make_store(tmp_path)
        store1.mark_processing("rec001", source="craig", source_id="rec001", file_name="")

        store2 = StateStore(tmp_path / "state", legacy_db_path=tmp_path / "none.json")
        assert store2.is_known("rec001")

    def test_after_success_returns_false(self, tmp_path: Path) -> None:
        """After mark_success, mark_processing with same rec_id returns False."""
        store = _make_store(tmp_path)
        store.mark_processing("rec001", source="craig", source_id="rec001", file_name="")
        store.mark_success("rec001")

        assert store.mark_processing("rec001", source="drive", source_id="f1", file_name="") is False

    def test_after_failure_returns_false(self, tmp_path: Path) -> None:
        """After mark_failed, mark_processing with same rec_id returns False."""
        store = _make_store(tmp_path)
        store.mark_processing("rec001", source="craig", source_id="rec001", file_name="")
        store.mark_failed("rec001", "some error")

        assert store.mark_processing("rec001", source="drive", source_id="f1", file_name="") is False

    def test_disk_failure_still_returns_true(self, tmp_path: Path) -> None:
        """When os.replace raises OSError, still returns True (in-memory dedup works)."""
        store = _make_store(tmp_path)

        with patch("os.replace", side_effect=OSError("disk full")):
            result = store.mark_processing("rec001", source="craig", source_id="rec001", file_name="")

        assert result is True
        assert store.is_known("rec001")


# ===========================================================================
# 12-16: mark_success / mark_failed
# ===========================================================================


class TestMarkSuccessAndFailed:

    def test_mark_success_updates_status(self, tmp_path: Path) -> None:
        """Entry status changes to 'success'; completed_at is set."""
        store = _make_store(tmp_path)
        store.mark_processing("rec001", source="drive", source_id="f1", file_name="test.zip")
        store.mark_success("rec001")

        entry = store.get_entry("rec001")
        assert entry["status"] == "success"
        assert "completed_at" in entry
        assert "started_at" not in entry

    def test_mark_success_unknown_creates_entry(self, tmp_path: Path) -> None:
        """mark_success for unknown rec_id creates a defensive entry."""
        store = _make_store(tmp_path)
        store.mark_success("unknown001")

        assert store.is_known("unknown001")
        entry = store.get_entry("unknown001")
        assert entry["status"] == "success"

    def test_mark_failed_updates_status(self, tmp_path: Path) -> None:
        """Entry status changes to 'error'; error and failed_at are set."""
        store = _make_store(tmp_path)
        store.mark_processing("rec001", source="drive", source_id="f1", file_name="test.zip")
        store.mark_failed("rec001", "Download timeout")

        entry = store.get_entry("rec001")
        assert entry["status"] == "error"
        assert entry["error"] == "Download timeout"
        assert "failed_at" in entry
        assert "started_at" not in entry

    def test_mark_failed_never_raises(self, tmp_path: Path) -> None:
        """mark_failed never raises, even when os.replace fails."""
        store = _make_store(tmp_path)
        store.mark_processing("rec001", source="drive", source_id="f1", file_name="test.zip")

        with patch("os.replace", side_effect=OSError("disk full")):
            # Should NOT raise
            store.mark_failed("rec001", "some error")

        # In-memory state should still be updated
        entry = store.get_entry("rec001")
        assert entry["status"] == "error"

    def test_mark_failed_unknown_creates_entry(self, tmp_path: Path) -> None:
        """mark_failed for unknown rec_id creates a defensive entry."""
        store = _make_store(tmp_path)
        store.mark_failed("unknown001", "pipeline crashed")

        assert store.is_known("unknown001")
        entry = store.get_entry("unknown001")
        assert entry["status"] == "error"
        assert entry["error"] == "pipeline crashed"


# ===========================================================================
# 17-20: cleanup_stale
# ===========================================================================


class TestCleanupStale:

    def test_removes_old_processing(self, tmp_path: Path) -> None:
        """Entry with started_at >2h ago is removed."""
        store = _make_store(tmp_path)
        store.mark_processing("stale001", source="craig", source_id="stale001", file_name="")

        # Backdate the started_at to 3 hours ago
        entry = store._processing["stale001"]
        entry["started_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()

        removed = store.cleanup_stale()
        assert removed == 1
        assert not store.is_known("stale001")

    def test_keeps_recent_processing(self, tmp_path: Path) -> None:
        """Entry with started_at <2h ago is kept."""
        store = _make_store(tmp_path)
        store.mark_processing("recent001", source="craig", source_id="recent001", file_name="")

        removed = store.cleanup_stale()
        assert removed == 0
        assert store.is_known("recent001")

    def test_ignores_success_and_error(self, tmp_path: Path) -> None:
        """Entries with status success or error are never removed."""
        store = _make_store(tmp_path)
        store.mark_processing("s1", source="craig", source_id="s1", file_name="")
        store.mark_success("s1")
        store.mark_processing("e1", source="craig", source_id="e1", file_name="")
        store.mark_failed("e1", "error")

        removed = store.cleanup_stale()
        assert removed == 0
        assert store.is_known("s1")
        assert store.is_known("e1")

    def test_persists_removal(self, tmp_path: Path) -> None:
        """After cleanup, new instance does not see the removed entry."""
        store = _make_store(tmp_path)
        store.mark_processing("stale001", source="craig", source_id="stale001", file_name="")
        store._processing["stale001"]["started_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=3)
        ).isoformat()
        store.cleanup_stale()

        store2 = StateStore(tmp_path / "state", legacy_db_path=tmp_path / "none.json")
        assert not store2.is_known("stale001")


# ===========================================================================
# 21-23: Minutes cache
# ===========================================================================


class TestMinutesCache:

    def test_put_and_get(self, tmp_path: Path) -> None:
        """Put then get returns the value."""
        store = _make_store(tmp_path)
        store.put_cached_minutes("hash001", "# Meeting Minutes\n## Summary")

        result = store.get_cached_minutes("hash001")
        assert result == "# Meeting Minutes\n## Summary"

    def test_get_miss_returns_none(self, tmp_path: Path) -> None:
        """Unknown hash returns None."""
        store = _make_store(tmp_path)
        assert store.get_cached_minutes("nonexistent") is None

    def test_cache_persists_across_instances(self, tmp_path: Path) -> None:
        """New instance reads the cached value."""
        store1 = _make_store(tmp_path)
        store1.put_cached_minutes("hash001", "# Minutes")

        store2 = StateStore(tmp_path / "state", legacy_db_path=tmp_path / "none.json")
        assert store2.get_cached_minutes("hash001") == "# Minutes"


# ===========================================================================
# 24-26: Atomic write correctness
# ===========================================================================


class TestAtomicWrites:

    def test_no_tmp_file_left_after_write(self, tmp_path: Path) -> None:
        """After any write, no .tmp file remains in state_dir."""
        store = _make_store(tmp_path)
        store.mark_processing("rec001", source="craig", source_id="rec001", file_name="")
        store.put_cached_minutes("h1", "minutes")

        state_dir = tmp_path / "state"
        tmp_files = list(state_dir.glob("*.tmp"))
        assert tmp_files == []

    def test_original_intact_on_write_failure(self, tmp_path: Path) -> None:
        """Seed a file; mock write to raise; original is unchanged."""
        store = _make_store(tmp_path)
        store.mark_processing("rec001", source="craig", source_id="rec001", file_name="")

        # Read the current file contents
        original = (tmp_path / "state" / "processing.json").read_text(encoding="utf-8")

        with patch("os.replace", side_effect=OSError("disk full")):
            store.mark_processing("rec002", source="drive", source_id="f2", file_name="test.zip")

        # Original file should be unchanged (os.replace was blocked)
        current = (tmp_path / "state" / "processing.json").read_text(encoding="utf-8")
        assert current == original

    def test_concurrent_logical_writes(self, tmp_path: Path) -> None:
        """Call mark_processing then put_cached_minutes; both files correct."""
        store = _make_store(tmp_path)
        store.mark_processing("rec001", source="craig", source_id="rec001", file_name="")
        store.put_cached_minutes("h1", "# Minutes")

        proc = _read_processing(tmp_path)
        cache = _read_cache(tmp_path)

        assert "rec001" in proc
        assert "h1" in cache
        assert cache["h1"] == "# Minutes"


# ===========================================================================
# 27-31: extract_rec_id
# ===========================================================================


class TestExtractRecId:

    def test_underscore_separator(self) -> None:
        assert extract_rec_id("craig_leH5ivxXSepT_2026-2-10.aac.zip") == "leH5ivxXSepT"

    def test_dash_separator(self) -> None:
        assert extract_rec_id("craig-Q92fATPSYVKt_2026-3-2.aac.zip") == "Q92fATPSYVKt"

    def test_non_craig_returns_none(self) -> None:
        assert extract_rec_id("meeting_notes.zip") is None

    def test_short_id_returns_none(self) -> None:
        assert extract_rec_id("craig_abc_2026.aac.zip") is None

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("craig_leH5ivxXSepT_2026-2-10_9-16-23.aac.zip", "leH5ivxXSepT"),
            ("craig_3wdx2qkYdodO_2026-2-10_10-41-27.aac.zip", "3wdx2qkYdodO"),
            ("craig_wppVszajUk99_2026-2-10_14-53-42.aac.zip", "wppVszajUk99"),
            ("craig_ntC4DVLmZK2L_2026-2-14_12-6-2.aac.zip", "ntC4DVLmZK2L"),
            ("craig_VdkWdOBwJupk_2026-2-20_8-4-51.aac.zip", "VdkWdOBwJupk"),
            ("craig_uJ9Q5tIj1awt_2026-2-21.aac.zip", "uJ9Q5tIj1awt"),
            ("craig_7UvnH9BiY2EI_2026_2_23.aac.zip", "7UvnH9BiY2EI"),
            ("craig_6ZRI6Dwld5kB_2026_2_2.aac.zip", "6ZRI6Dwld5kB"),
            ("craig-Q92fATPSYVKt_2026-3-2.aac.zip", "Q92fATPSYVKt"),
            ("craig-xZH0rkeudmL1-2026-3-9.aac.zip", "xZH0rkeudmL1"),
        ],
    )
    def test_all_production_filenames(self, filename: str, expected: str) -> None:
        assert extract_rec_id(filename) == expected


# ===========================================================================
# 32-39: Migration from legacy processed_files.json
# ===========================================================================


class TestMigration:

    def _write_legacy(self, tmp_path: Path, data: dict) -> Path:
        """Write a legacy processed_files.json and return its path."""
        legacy_path = tmp_path / "processed_files.json"
        legacy_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return legacy_path

    def test_migration_from_legacy(self, tmp_path: Path) -> None:
        """Full migration: entries keyed by rec_id, .bak created."""
        legacy_path = self._write_legacy(tmp_path, {
            "processed": {
                "file-id-1": {
                    "name": "craig_leH5ivxXSepT_2026-2-10.aac.zip",
                    "processed_at": "2026-02-10T10:39:12+00:00",
                },
                "file-id-2": {
                    "name": "craig-Q92fATPSYVKt_2026-3-2.aac.zip",
                    "status": "success",
                    "processed_at": "2026-03-02T14:00:00+00:00",
                },
            },
            "minutes_cache": {
                "hashA": "# Minutes A",
            },
        })

        state_dir = tmp_path / "state"
        store = StateStore(state_dir, legacy_db_path=legacy_path)

        assert store.is_known("leH5ivxXSepT")
        assert store.is_known("Q92fATPSYVKt")
        assert store.processing_count == 2

        # Check migrated entry fields
        entry = store.get_entry("leH5ivxXSepT")
        assert entry["source"] == "drive"
        assert entry["source_id"] == "file-id-1"
        assert entry["status"] == "success"

        # Cache migrated
        assert store.get_cached_minutes("hashA") == "# Minutes A"

        # .bak created, original gone
        assert (tmp_path / "processed_files.json.bak").exists()
        assert not legacy_path.exists()

    def test_migration_idempotent(self, tmp_path: Path) -> None:
        """If processing.json already exists, legacy file is not read."""
        legacy_path = self._write_legacy(tmp_path, {
            "processed": {"file-1": {"name": "craig_abc123456789_date.aac.zip", "processed_at": "2026-01-01T00:00:00+00:00"}},
        })

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "processing.json").write_text("{}", encoding="utf-8")

        store = StateStore(state_dir, legacy_db_path=legacy_path)

        # Legacy entry should NOT be migrated
        assert not store.is_known("abc123456789")
        # Legacy file should NOT be renamed
        assert legacy_path.exists()

    def test_migration_schema_normalization(self, tmp_path: Path) -> None:
        """Entries without status field get status='success'."""
        legacy_path = self._write_legacy(tmp_path, {
            "processed": {
                "file-1": {
                    "name": "craig_leH5ivxXSepT_2026.aac.zip",
                    "processed_at": "2026-01-01T00:00:00+00:00",
                    # No "status" field
                },
            },
        })

        store = StateStore(tmp_path / "state", legacy_db_path=legacy_path)
        entry = store.get_entry("leH5ivxXSepT")
        assert entry["status"] == "success"

    def test_migration_skips_stale_processing(self, tmp_path: Path) -> None:
        """Entry with status='processing' is not migrated."""
        legacy_path = self._write_legacy(tmp_path, {
            "processed": {
                "file-1": {
                    "name": "craig_leH5ivxXSepT_2026.aac.zip",
                    "status": "processing",
                    "started_at": "2026-01-01T00:00:00+00:00",
                },
            },
        })

        store = StateStore(tmp_path / "state", legacy_db_path=legacy_path)
        assert not store.is_known("leH5ivxXSepT")

    def test_migration_preserves_error_entries(self, tmp_path: Path) -> None:
        """Error entries are migrated with error details."""
        legacy_path = self._write_legacy(tmp_path, {
            "processed": {
                "file-1": {
                    "name": "craig_leH5ivxXSepT_2026.aac.zip",
                    "status": "error",
                    "error": "Download timeout",
                    "failed_at": "2026-01-01T00:00:00+00:00",
                },
            },
        })

        store = StateStore(tmp_path / "state", legacy_db_path=legacy_path)
        entry = store.get_entry("leH5ivxXSepT")
        assert entry["status"] == "error"
        assert entry["error"] == "Download timeout"

    def test_migration_cache_section(self, tmp_path: Path) -> None:
        """minutes_cache section is written to state/minutes_cache.json."""
        legacy_path = self._write_legacy(tmp_path, {
            "processed": {},
            "minutes_cache": {
                "hash1": "# Minutes 1",
                "hash2": "# Minutes 2",
            },
        })

        store = StateStore(tmp_path / "state", legacy_db_path=legacy_path)
        assert store.get_cached_minutes("hash1") == "# Minutes 1"
        assert store.get_cached_minutes("hash2") == "# Minutes 2"

    def test_migration_rec_id_fallback(self, tmp_path: Path) -> None:
        """Non-standard filename uses file_id as key."""
        legacy_path = self._write_legacy(tmp_path, {
            "processed": {
                "file-id-abc": {
                    "name": "meeting_recording.zip",
                    "processed_at": "2026-01-01T00:00:00+00:00",
                },
            },
        })

        store = StateStore(tmp_path / "state", legacy_db_path=legacy_path)
        # Falls back to file_id as the key
        assert store.is_known("file-id-abc")

    def test_migration_no_legacy_file(self, tmp_path: Path) -> None:
        """No legacy file -> no migration, no errors."""
        state_dir = tmp_path / "state"
        store = StateStore(state_dir, legacy_db_path=tmp_path / "does_not_exist.json")

        assert store.processing_count == 0
        assert not (tmp_path / "does_not_exist.json.bak").exists()


# ===========================================================================
# Guild settings
# ===========================================================================


class TestGuildSettings:
    def test_guild_template_default_none(self, tmp_path: Path) -> None:
        """Unset guild returns None."""
        store = _make_store(tmp_path)
        assert store.get_guild_template(12345) is None

    def test_guild_template_set_get(self, tmp_path: Path) -> None:
        """Set and get a guild template."""
        store = _make_store(tmp_path)
        store.set_guild_template(12345, "todo-focused")
        assert store.get_guild_template(12345) == "todo-focused"

    def test_guild_template_overwrite(self, tmp_path: Path) -> None:
        """Setting a template overwrites the previous value."""
        store = _make_store(tmp_path)
        store.set_guild_template(12345, "minutes")
        store.set_guild_template(12345, "custom")
        assert store.get_guild_template(12345) == "custom"

    def test_guild_template_persistence(self, tmp_path: Path) -> None:
        """Guild settings are persisted to disk and survive reload."""
        store = _make_store(tmp_path)
        store.set_guild_template(99, "todo-focused")

        # Verify file written
        settings_path = tmp_path / "state" / "guild_settings.json"
        assert settings_path.exists()
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert data["99"]["template"] == "todo-focused"

        # Reload from disk
        store2 = _make_store(tmp_path)
        assert store2.get_guild_template(99) == "todo-focused"

    def test_guild_template_multiple_guilds(self, tmp_path: Path) -> None:
        """Multiple guilds can have independent templates."""
        store = _make_store(tmp_path)
        store.set_guild_template(1, "minutes")
        store.set_guild_template(2, "todo-focused")
        assert store.get_guild_template(1) == "minutes"
        assert store.get_guild_template(2) == "todo-focused"
