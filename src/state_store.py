"""Unified persistent state for processing dedup and minutes cache.

Single writer for both state files. In-memory dict mirrors disk state.
All reads are O(1) dict lookups. Writes update dict then flush atomically.

File layout::

    state/
      processing.json     # {rec_id: {source, source_id, file_name, status, ...}}
      minutes_cache.json  # {transcript_hash: minutes_md}
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Craig recording ID extraction pattern.
# Matches: craig_leH5ivxXSepT_... or craig-Q92fATPSYVKt_...
# Group 1 captures the 12-char alphanumeric rec_id.
_REC_ID_PATTERN = re.compile(r"^craig[_-]([A-Za-z0-9]{12})[_-]")


def extract_rec_id(file_name: str) -> str | None:
    """Extract the Craig recording ID from a filename.

    Returns the 12-char rec_id, or None if the filename does not match
    the expected Craig pattern.
    """
    match = _REC_ID_PATTERN.match(file_name)
    return match.group(1) if match else None


class StateStore:
    """Unified persistent state for processing dedup and minutes cache.

    Single writer for both state files. In-memory dict mirrors disk state.
    All reads are O(1) dict lookups. Writes update dict then flush atomically.
    """

    def __init__(
        self,
        state_dir: Path,
        legacy_db_path: Path | None = None,
    ) -> None:
        self._state_dir = state_dir
        self._processing_path = state_dir / "processing.json"
        self._cache_path = state_dir / "minutes_cache.json"

        # Create state directory
        state_dir.mkdir(parents=True, exist_ok=True)

        # DrvFs detection warning
        resolved = str(state_dir.resolve())
        if resolved.startswith("/mnt/"):
            logger.warning(
                "state_dir is on a Windows filesystem (%s). "
                "Atomic writes may not be reliable. "
                "Move state_dir to a Linux filesystem (e.g. /home/...) for best reliability.",
                state_dir,
            )

        # Run migration before loading (may create the new files)
        if legacy_db_path is None:
            legacy_db_path = Path("processed_files.json")
        self._migrate_legacy(legacy_db_path)

        # Load state from disk
        self._processing: dict[str, dict] = self._load_json(self._processing_path)
        self._cache: dict[str, str] = self._load_json(self._cache_path)
        self._guild_settings_path = state_dir / "guild_settings.json"
        self._guild_settings: dict[str, dict] = self._load_json(self._guild_settings_path)

    # ------------------------------------------------------------------
    # Processing state methods
    # ------------------------------------------------------------------

    def mark_processing(
        self,
        rec_id: str,
        source: str,
        source_id: str,
        file_name: str,
    ) -> bool:
        """Atomically claim a recording for processing.

        Returns True if the claim succeeded (rec_id was not previously known).
        Returns False if rec_id is already known (any status).
        """
        if rec_id in self._processing:
            return False

        self._processing[rec_id] = {
            "source": source,
            "source_id": source_id,
            "file_name": file_name,
            "status": "processing",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self._flush_processing()
        return True

    def mark_success(self, rec_id: str) -> None:
        """Mark a recording as successfully processed."""
        entry = self._processing.get(rec_id)
        if entry is None:
            logger.warning(
                "mark_success called for unknown rec_id=%s; creating defensive entry",
                rec_id,
            )
            entry = {"source": "unknown", "source_id": rec_id, "file_name": ""}
            self._processing[rec_id] = entry

        entry["status"] = "success"
        entry["completed_at"] = datetime.now(timezone.utc).isoformat()
        entry.pop("started_at", None)
        self._flush_processing()

    def mark_failed(self, rec_id: str, error: str) -> None:
        """Mark a recording as failed. NEVER raises."""
        try:
            entry = self._processing.get(rec_id)
            if entry is None:
                logger.warning(
                    "mark_failed called for unknown rec_id=%s; creating defensive entry",
                    rec_id,
                )
                entry = {"source": "unknown", "source_id": rec_id, "file_name": ""}
                self._processing[rec_id] = entry

            entry["status"] = "error"
            entry["error"] = error
            entry["failed_at"] = datetime.now(timezone.utc).isoformat()
            entry.pop("started_at", None)
            self._flush_processing()
        except Exception as exc:
            logger.warning("mark_failed itself failed for rec_id=%s: %s", rec_id, exc)

    def is_known(self, rec_id: str) -> bool:
        """Check if a rec_id has been seen before (any status)."""
        return rec_id in self._processing

    def get_entry(self, rec_id: str) -> dict | None:
        """Return a copy of the processing entry, or None."""
        entry = self._processing.get(rec_id)
        return dict(entry) if entry is not None else None

    @property
    def processing_count(self) -> int:
        """Number of entries in the processing state."""
        return len(self._processing)

    def cleanup_stale(self, max_age_sec: int = 7200) -> int:
        """Remove entries stuck in 'processing' for longer than max_age_sec.

        Returns the count of removed entries.
        """
        now = datetime.now(timezone.utc)
        stale_ids: list[str] = []

        for rec_id, entry in self._processing.items():
            if entry.get("status") != "processing":
                continue
            started_at = entry.get("started_at")
            if started_at is None:
                stale_ids.append(rec_id)
                continue
            try:
                started = datetime.fromisoformat(started_at)
                age_sec = (now - started).total_seconds()
                if age_sec > max_age_sec:
                    stale_ids.append(rec_id)
            except (ValueError, TypeError):
                stale_ids.append(rec_id)

        for rec_id in stale_ids:
            logger.info(
                "Removing stale processing entry: rec_id=%s (stuck >%ds)",
                rec_id,
                max_age_sec,
            )
            del self._processing[rec_id]

        if stale_ids:
            self._flush_processing()

        return len(stale_ids)

    # ------------------------------------------------------------------
    # Minutes cache methods
    # ------------------------------------------------------------------

    def get_cached_minutes(self, transcript_hash: str) -> str | None:
        """Return cached minutes markdown, or None on cache miss."""
        result = self._cache.get(transcript_hash)
        if result is not None:
            logger.info("Minutes cache hit (key=%s...)", transcript_hash[:12])
        return result

    def put_cached_minutes(self, transcript_hash: str, minutes_md: str) -> None:
        """Store generated minutes in the cache."""
        self._cache[transcript_hash] = minutes_md
        self._flush_cache()
        logger.info("Minutes cached (key=%s...)", transcript_hash[:12])

    # ------------------------------------------------------------------
    # Guild settings methods
    # ------------------------------------------------------------------

    def get_guild_template(self, guild_id: int) -> str | None:
        """Return the template override for a guild, or None."""
        settings = self._guild_settings.get(str(guild_id))
        if settings is None:
            return None
        return settings.get("template")

    def set_guild_template(self, guild_id: int, template_name: str) -> None:
        """Set the template for a guild."""
        key = str(guild_id)
        if key not in self._guild_settings:
            self._guild_settings[key] = {}
        self._guild_settings[key]["template"] = template_name
        self._flush_guild_settings()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_json(path: Path) -> dict:
        """Load a JSON file, returning empty dict on missing or corrupt file."""
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.warning("Expected dict in %s, got %s; starting empty", path, type(data).__name__)
                return {}
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load %s, starting empty: %s", path, exc)
            return {}

    def _flush(self, data: dict, target: Path) -> None:
        """Atomic write: serialize to .tmp then os.replace()."""
        tmp_path = target.with_suffix(".tmp")
        try:
            json_str = json.dumps(data, indent=2, ensure_ascii=False)
            tmp_path.write_text(json_str, encoding="utf-8")
            os.replace(str(tmp_path), str(target))
        except OSError as exc:
            logger.warning(
                "Failed to write %s (in-memory state preserved): %s",
                target,
                exc,
            )
            # Clean up orphaned tmp file
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _flush_processing(self) -> None:
        self._flush(self._processing, self._processing_path)

    def _flush_cache(self) -> None:
        self._flush(self._cache, self._cache_path)

    def _flush_guild_settings(self) -> None:
        self._flush(self._guild_settings, self._guild_settings_path)

    # ------------------------------------------------------------------
    # Migration from legacy processed_files.json
    # ------------------------------------------------------------------

    def _migrate_legacy(self, legacy_path: Path) -> None:
        """One-time migration from legacy processed_files.json.

        Runs when the legacy file exists AND new processing.json does not.
        """
        if not legacy_path.exists():
            return
        if self._processing_path.exists():
            return  # Already migrated — idempotent guard

        logger.info("Migrating legacy state from %s", legacy_path)

        try:
            legacy_data = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read legacy file %s: %s", legacy_path, exc)
            return

        if not isinstance(legacy_data, dict):
            logger.warning("Legacy file is not a dict, skipping migration")
            return

        # Migrate processing entries
        processing: dict[str, dict] = {}
        legacy_processed = legacy_data.get("processed", {})
        migrated_count = 0
        skipped_count = 0

        for file_id, entry in legacy_processed.items():
            if not isinstance(entry, dict):
                continue

            status = entry.get("status", "success")

            # Skip stale processing entries (equivalent to cleanup_stale)
            if status == "processing":
                logger.info(
                    "Skipping stale processing entry during migration: file_id=%s name=%s",
                    file_id,
                    entry.get("name", ""),
                )
                skipped_count += 1
                continue

            file_name = entry.get("name", "")
            rec_id = extract_rec_id(file_name) if file_name else None
            if rec_id is None:
                rec_id = file_id
                if file_name:
                    logger.warning(
                        "Could not extract rec_id from '%s', using file_id '%s'",
                        file_name,
                        file_id,
                    )

            # Handle collision: later timestamp wins
            if rec_id in processing:
                existing_ts = processing[rec_id].get("completed_at") or processing[rec_id].get("failed_at", "")
                new_ts = entry.get("processed_at") or entry.get("failed_at", "")
                if new_ts <= existing_ts:
                    logger.warning(
                        "Migration collision for rec_id=%s: keeping earlier entry",
                        rec_id,
                    )
                    continue
                logger.warning(
                    "Migration collision for rec_id=%s: replacing with later entry",
                    rec_id,
                )

            new_entry: dict[str, str] = {
                "source": "drive",
                "source_id": file_id,
                "file_name": file_name,
                "status": status,
            }

            if status == "error":
                new_entry["error"] = entry.get("error", "")
                new_entry["failed_at"] = entry.get("failed_at", datetime.now(timezone.utc).isoformat())
            else:
                new_entry["completed_at"] = entry.get(
                    "processed_at",
                    entry.get("completed_at", datetime.now(timezone.utc).isoformat()),
                )

            processing[rec_id] = new_entry
            migrated_count += 1

        # Migrate minutes cache
        cache: dict[str, str] = {}
        legacy_cache = legacy_data.get("minutes_cache", {})
        if isinstance(legacy_cache, dict):
            cache = legacy_cache

        # Write new state files
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._flush(processing, self._processing_path)
        self._flush(cache, self._cache_path)

        # Backup legacy file
        bak_path = legacy_path.with_suffix(legacy_path.suffix + ".bak")
        try:
            os.replace(str(legacy_path), str(bak_path))
            logger.info(
                "Migrated %d processing entries (%d stale skipped) and %d cache entries. "
                "Legacy file backed up to %s",
                migrated_count,
                skipped_count,
                len(cache),
                bak_path,
            )
        except OSError as exc:
            logger.warning("Failed to rename legacy file to .bak: %s", exc)
