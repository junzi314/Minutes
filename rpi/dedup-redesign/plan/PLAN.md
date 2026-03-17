# Implementation Plan: Deduplication & State Persistence Redesign

**Date**: 2026-03-09
**Complexity**: Medium
**Total Tasks**: 28
**Phases**: 7 milestones (M1-M7)
**Branch**: `feat/state-store-dedup-redesign`

---

## Prerequisites

- Research report reviewed: `rpi/dedup-redesign/research/RESEARCH.md` (GO, 95% confidence)
- Band-aid fix PR #9 merged or rebased (branch `fix/duplicate-minutes-permission-error`)
- Current `processed_files.json` backed up for migration testing

---

## Phase Overview

| Phase | Name | Tasks | Depends On | Validation Gate |
|-------|------|-------|------------|-----------------|
| M1 | StateStore core + unit tests | 8 | None | All unit tests pass; `mark_processing` returns `False` on duplicate |
| M2 | Migration function + tests | 5 | M1 | All 10 entries migrated correctly; `.bak` created; idempotent |
| M3 | DriveWatcher integration | 4 | M1 | DriveWatcher has zero state file I/O; uses StateStore exclusively |
| M4 | bot.py integration | 4 | M1 | `_processing_ids` removed; all 3 entry points use StateStore |
| M5 | pipeline.py integration | 3 | M1 | Pipeline cache reads/writes via StateStore; MinutesCache not imported |
| M6 | Config + cleanup + test fixes | 3 | M3, M4, M5 | All tests pass; `minutes_cache.py` deleted; no references remain |
| M7 | End-to-end live validation | 1 | M6 | Bot processes recording, restarts, does NOT reprocess |

---

## M1: StateStore Core + Unit Tests

**Goal**: Create `src/state_store.py` with full API and comprehensive tests.
**No dependencies. Can start immediately.**

### Tasks

#### M1-1: Create `src/state_store.py` skeleton
**Complexity**: Medium
**File**: `src/state_store.py` (NEW, ~150 lines)

Create the StateStore class with:
- `__init__(self, state_dir: Path, legacy_db_path: Path | None = None)`
- Constructor creates `state_dir`, loads `processing.json` and `minutes_cache.json` (or initializes empty dicts on missing/corrupt files)
- `_flush(self, data: dict, target: Path)` — atomic write helper: `json.dumps` → `target.with_suffix('.tmp')` → `os.replace()`

```python
# Key imports
import json, logging, os, re
from datetime import datetime, timezone
from pathlib import Path
```

#### M1-2: Implement processing state methods
**Complexity**: Medium
**File**: `src/state_store.py`

Implement:
- `mark_processing(rec_id, source, source_id, file_name) -> bool` — returns `False` if rec_id already known (any status)
- `mark_success(rec_id)` — updates status, adds `completed_at`
- `mark_failed(rec_id, error)` — updates status, adds `error` and `failed_at`. **NEVER raises.**
- `is_known(rec_id) -> bool` — O(1) dict lookup
- `get_entry(rec_id) -> dict | None` — returns copy for diagnostics
- `processing_count` property

Error handling contract: in-memory dict always updated; disk flush failure logged at WARNING, never raised.

#### M1-3: Implement cleanup_stale
**Complexity**: Low
**File**: `src/state_store.py`

- `cleanup_stale(max_age_sec: int = 7200) -> int`
- Iterate `_processing`, remove entries where `status == "processing"` and `started_at` is older than `max_age_sec`
- Flush if any removed. Return count.

#### M1-4: Implement minutes cache methods
**Complexity**: Low
**File**: `src/state_store.py`

- `get_cached_minutes(transcript_hash: str) -> str | None` — O(1) dict lookup
- `put_cached_minutes(transcript_hash: str, minutes_md: str)` — update dict, flush `minutes_cache.json`

#### M1-5: Implement `extract_rec_id` helper
**Complexity**: Low
**File**: `src/state_store.py`

Module-level function:
```python
_REC_ID_PATTERN = re.compile(r"^craig[_-]([A-Za-z0-9]{12})[_-]")

def extract_rec_id(file_name: str) -> str | None:
    match = _REC_ID_PATTERN.match(file_name)
    return match.group(1) if match else None
```

#### M1-6: Add DrvFs detection warning
**Complexity**: Low
**File**: `src/state_store.py`

In constructor, after resolving `state_dir`:
```python
if str(state_dir.resolve()).startswith("/mnt/"):
    logger.warning("state_dir is on a Windows filesystem (%s). Atomic writes may not be reliable.", state_dir)
```

#### M1-7: Write unit tests — StateStore core
**Complexity**: High
**File**: `tests/test_state_store.py` (NEW, ~300 lines)

Tests 1-26 from eng.md Section 7.1:
- Construction/loading (5 tests)
- `mark_processing` (6 tests, including disk failure mock)
- `mark_success` / `mark_failed` (5 tests, including never-raises guarantee)
- `cleanup_stale` (4 tests)
- Minutes cache (3 tests)
- Atomic write correctness (3 tests)

#### M1-8: Write unit tests — `extract_rec_id`
**Complexity**: Low
**File**: `tests/test_state_store.py`

Tests 27-31 from eng.md Section 7.1:
- Underscore separator, dash separator, non-Craig filename, short ID
- Parametrized test against all 10 production filenames

### M1 Validation Gate
```bash
pytest tests/test_state_store.py -v
# All tests pass
# mark_processing returns False on duplicate
# Atomic writes leave no .tmp files
# mark_failed never raises even with mocked os.replace failure
```

---

## M2: Migration Function + Tests

**Goal**: Migrate legacy `processed_files.json` to new `state/` structure.
**Depends on**: M1

### Tasks

#### M2-1: Implement migration in StateStore constructor
**Complexity**: Medium
**File**: `src/state_store.py`

Migration logic in `__init__` when:
1. `legacy_db_path` exists (default: `processed_files.json`)
2. `state_dir/processing.json` does NOT exist

Transformation per entry:
- Extract `rec_id` from filename (fallback to original `file_id` key)
- Normalize schema: add missing `status` (default `"success"`), rename `name` → `file_name`, rename `processed_at` → `completed_at`, set `source` = `"drive"`, set `source_id` = original file_id
- Skip entries with `status == "processing"` (stale from crash)
- Handle rec_id collision: later timestamp wins, log warning

Migrate `minutes_cache` section to `state/minutes_cache.json`.
Rename legacy file to `.bak`.

#### M2-2: Test migration with fixture data
**Complexity**: Medium
**File**: `tests/test_state_store.py`

Tests 32-39 from eng.md Section 7.2:
- Full migration from legacy format
- Idempotent behavior (skip if new files exist)
- Schema normalization (missing status field)
- Stale processing entry skipped
- Error entries preserved
- Minutes cache section migrated
- rec_id fallback for non-standard filenames
- No legacy file → no migration

#### M2-3: Test migration against real `processed_files.json`
**Complexity**: Low
**File**: Manual validation

Copy current `processed_files.json` to test fixtures. Run migration. Verify all 10 entries produce correct rec_id keys.

#### M2-4: Verify `.bak` creation and rollback
**Complexity**: Low
**File**: `tests/test_state_store.py`

Assert `.bak` file exists after migration. Assert original file no longer exists. Test rollback by reversing the rename.

#### M2-5: Test collision handling
**Complexity**: Low
**File**: `tests/test_state_store.py`

Create two legacy entries with different file_ids but same extracted rec_id. Verify later timestamp wins and warning is logged.

### M2 Validation Gate
```bash
pytest tests/test_state_store.py -v -k "migration"
# All migration tests pass
# 10 entries migrated with correct rec_id keys
# .bak file created
# Idempotent re-run skips migration
```

---

## M3: DriveWatcher Integration

**Goal**: Remove all state management from `DriveWatcher`. Inject StateStore.
**Depends on**: M1

### Tasks

#### M3-1: Update DriveWatcher constructor
**Complexity**: Low
**File**: `src/drive_watcher.py`

- Add `state_store: StateStore` parameter to `__init__`
- Store as `self._state_store`
- Remove: `self._processed: dict` (line 59)
- Add import: `from src.state_store import StateStore, extract_rec_id`

#### M3-2: Replace state methods
**Complexity**: Medium
**File**: `src/drive_watcher.py`

Remove these methods entirely (~100 lines):
- `_load_processed_db()` (lines 106-127)
- `_save_processed_db()` (lines 129-175) — includes the `time.sleep` bug
- `_mark_processed()` (lines 177-184)
- `_mark_failed()` (lines 186-203)
- `processed_count` property (lines 72-74)

#### M3-3: Update `_watch_loop` and `_process_file`
**Complexity**: Medium
**File**: `src/drive_watcher.py`

`_watch_loop` changes:
- Remove `self._load_processed_db()` call
- Replace file-id filtering with rec_id-based StateStore check:
  ```python
  rec_id = extract_rec_id(file_name) or file_id
  if self._state_store.is_known(rec_id):
      continue
  ```
- Replace `self._mark_failed(...)` in except blocks with `self._state_store.mark_failed(rec_id, str(exc))`

`_process_file` changes:
- Replace in-memory "processing" mark with `self._state_store.mark_processing(rec_id, source="drive", source_id=file_id, file_name=file_name)`
- Replace `self._mark_processed(...)` with `self._state_store.mark_success(rec_id)`
- Add `mark_failed` in except block

#### M3-4: Update `tests/test_drive_watcher.py`
**Complexity**: Medium
**File**: `tests/test_drive_watcher.py`

- Delete `TestProcessedDB` class (tests 1-5) — tests removed methods
- Delete `TestMarkFailed` class (tests 16-17) — tests removed methods
- Update `_make_cfg` helper: remove `processed_db_path`
- Update `_make_watcher` helper: accept and inject StateStore
- Update `TestProcessFile`: verify `state_store.is_known()` instead of `watcher._processed`
- Update `TestWatchLoopEarlyExit`: pass StateStore

### M3 Validation Gate
```bash
pytest tests/test_drive_watcher.py -v
# All remaining tests pass
# No references to _processed, _save_processed_db, _mark_processed, _mark_failed
# grep -r "time.sleep" src/drive_watcher.py returns 0 matches
```

---

## M4: bot.py Integration

**Goal**: Remove `_processing_ids` in-memory set. All 3 entry points use StateStore.
**Depends on**: M1

### Tasks

#### M4-1: Add StateStore to MinutesBot
**Complexity**: Low
**File**: `bot.py`

- Add `state_store: StateStore` parameter to `MinutesBot.__init__`
- Store as `self.state_store`
- Remove `self._processing_ids: set[str]` (line 131)
- Import StateStore

#### M4-2: Update `_launch_pipeline` and `_on_done`
**Complexity**: Medium
**File**: `bot.py`

Replace `_processing_ids` logic:
```python
def _launch_pipeline(self, recording, output_channel):
    rec_id = recording.rec_id
    if not self.state_store.mark_processing(rec_id, source="craig", source_id=rec_id, file_name=""):
        logger.warning("Skipping duplicate pipeline for rec_id=%s (already known)", rec_id)
        return
    # ... create task ...
    def _on_done(t: asyncio.Task):
        if t.cancelled():
            self.state_store.mark_failed(rec_id, "Pipeline cancelled")
        elif (exc := t.exception()) is not None:
            self.state_store.mark_failed(rec_id, str(exc))
        else:
            self.state_store.mark_success(rec_id)
    task.add_done_callback(_on_done)
```

#### M4-3: Update `_on_drive_tracks` and `/minutes process`
**Complexity**: Medium
**File**: `bot.py`

`_on_drive_tracks` closure: Remove `_processing_ids` check and discard — DriveWatcher handles dedup via StateStore before calling the callback.

`/minutes process`: Add `is_known` check before `_launch_pipeline`:
```python
if client.state_store.is_known(rec_id):
    await interaction.response.send_message(f"Recording `{rec_id}` has already been processed.", ephemeral=True)
    return
```

`/minutes drive-status`: Replace `watcher.processed_count` with `client.state_store.processing_count`.

#### M4-4: Update `main()` — create StateStore, pass to components
**Complexity**: Low
**File**: `bot.py`

```python
from src.state_store import StateStore

# In main():
state_store = StateStore(Path(cfg.pipeline.state_dir))
stale_count = state_store.cleanup_stale()
if stale_count:
    logger.info("Cleaned up %d stale processing entries", stale_count)

client = MinutesBot(cfg=cfg, transcriber=transcriber, generator=generator, state_store=state_store, intents=intents)
```

Pass `state_store` when creating DriveWatcher in `on_ready`.

### M4 Validation Gate
```bash
grep -r "_processing_ids" bot.py  # returns 0 matches
pytest tests/ -v -k "bot"  # if bot tests exist, they pass
```

---

## M5: pipeline.py Integration

**Goal**: Replace MinutesCache with StateStore cache methods.
**Depends on**: M1

### Tasks

#### M5-1: Add StateStore parameter to pipeline functions
**Complexity**: Low
**File**: `src/pipeline.py`

- Add `state_store: StateStore` parameter to `run_pipeline` and `run_pipeline_from_tracks`
- Add `import hashlib` and `from src.state_store import StateStore`
- Remove `from src.minutes_cache import MinutesCache`

#### M5-2: Replace cache usage
**Complexity**: Low
**File**: `src/pipeline.py`

Add transcript hash helper:
```python
def _transcript_hash(transcript: str) -> str:
    return hashlib.sha256(transcript.encode("utf-8")).hexdigest()
```

Replace in `run_pipeline_from_tracks`:
```python
# Before:
cache = MinutesCache(cfg.pipeline.minutes_cache_path)
minutes_md = cache.get(transcript)
...
cache.put(transcript, minutes_md)

# After:
th = _transcript_hash(transcript)
minutes_md = state_store.get_cached_minutes(th)
...
state_store.put_cached_minutes(th, minutes_md)
```

Pass `state_store` from `run_pipeline` to `run_pipeline_from_tracks`.

#### M5-3: Update pipeline tests
**Complexity**: Low
**File**: `tests/test_pipeline.py`

- Update all `run_pipeline` / `run_pipeline_from_tracks` calls to include `state_store` parameter
- Create a StateStore instance in test fixtures (using `tmp_path`)
- No test logic changes needed — pipeline tests mock all stages

### M5 Validation Gate
```bash
pytest tests/test_pipeline.py -v
# All pipeline tests pass
# grep -r "MinutesCache" src/pipeline.py returns 0 matches
```

---

## M6: Config + Cleanup + Test Fixes

**Goal**: Update config, delete `minutes_cache.py`, ensure all tests pass.
**Depends on**: M3, M4, M5

### Tasks

#### M6-1: Update `src/config.py`
**Complexity**: Low
**File**: `src/config.py`

PipelineConfig — replace `minutes_cache_path`:
```python
@dataclass(frozen=True)
class PipelineConfig:
    processing_timeout_sec: int = 3600
    state_dir: str = "state"
```

GoogleDriveConfig — remove `processed_db_path`:
```python
@dataclass(frozen=True)
class GoogleDriveConfig:
    enabled: bool = False
    credentials_path: str = "credentials.json"
    folder_id: str = ""
    file_pattern: str = "craig[_-]*.aac.zip"
    poll_interval_sec: int = 30
```

Update `tests/test_config.py` if it references the removed fields.

#### M6-2: Delete `src/minutes_cache.py`
**Complexity**: Low
**File**: `src/minutes_cache.py` (DELETE)

Verify no remaining imports:
```bash
grep -r "minutes_cache" src/ tests/
# Should only find references in this plan, not in code
```

#### M6-3: Full test suite validation
**Complexity**: Low

```bash
pytest tests/ -v
# ALL tests pass
# No import errors for minutes_cache
# No references to removed config fields
```

### M6 Validation Gate
```bash
pytest tests/ -v  # 100% pass
grep -rn "minutes_cache" src/  # 0 matches
grep -rn "processed_db_path" src/  # 0 matches
grep -rn "minutes_cache_path" src/  # 0 matches
grep -rn "_processing_ids" bot.py  # 0 matches
grep -rn "time.sleep" src/drive_watcher.py src/state_store.py  # 0 matches
```

---

## M7: End-to-End Live Validation

**Goal**: Confirm the redesign works with real recordings and bot restarts.
**Depends on**: M6

### Task

#### M7-1: Live validation scenarios
**Complexity**: Low

Run these scenarios against the live bot:

| # | Scenario | Steps | Expected |
|---|----------|-------|----------|
| 1 | **Restart dedup** | Process a recording via Drive. Stop bot. Start bot. Wait one poll cycle. | Recording NOT reprocessed. `state/processing.json` has entry. |
| 2 | **Cross-path dedup** | Process `craig_XYZ_date.aac.zip` via Drive. Then `/minutes process` with same rec_id. | Slash command replies "already processed". |
| 3 | **Stale cleanup** | Manually add a "processing" entry with `started_at` 3h ago. Start bot. | Entry cleaned up. Log shows count. |
| 4 | **Migration** | Place `processed_files.json` in root. Delete `state/`. Start bot. | `state/` created. 10 entries with rec_id keys. `.bak` exists. |

### M7 Validation Gate
All 4 scenarios pass. Bot runs without duplicate posts for 24+ hours.

---

## Dependency Graph

```
M1 (StateStore + tests)
  |
  +---> M2 (Migration)
  |
  +---> M3 (drive_watcher)  --+
  |                            |
  +---> M4 (bot.py)        ---+--> M6 (config + cleanup) --> M7 (live validation)
  |                            |
  +---> M5 (pipeline.py)   --+
```

M3, M4, M5 can proceed **in parallel** after M1.

---

## Files Changed Summary

| File | Action | Est. Lines |
|------|--------|-----------|
| `src/state_store.py` | NEW | +150 |
| `tests/test_state_store.py` | NEW | +350 |
| `src/drive_watcher.py` | MODIFY | -100, +20 |
| `bot.py` | MODIFY | -20, +25 |
| `src/pipeline.py` | MODIFY | -5, +15 |
| `src/config.py` | MODIFY | -3, +2 |
| `src/minutes_cache.py` | DELETE | -78 |
| `tests/test_drive_watcher.py` | MODIFY | -80, +30 |
| `tests/test_pipeline.py` | MODIFY | -5, +10 |
| `tests/test_config.py` | MODIFY | -2, +2 |

**Net**: ~+420 new, ~-290 removed (+130 net). State management surface area simplified from 3 independent mechanisms to 1 unified class.

---

## Risk Checklist

- [ ] `os.replace()` tested on project's ext4 filesystem
- [ ] No `time.sleep()` in any modified file
- [ ] `mark_failed` never raises (verified by unit test with mocked OSError)
- [ ] Migration tested against real `processed_files.json` (10 entries)
- [ ] `.bak` backup confirmed after migration
- [ ] All 3 entry points use `state_store.is_known()` for dedup
- [ ] `_processing_ids` set fully removed from bot.py
- [ ] `minutes_cache.py` deleted with no remaining imports
