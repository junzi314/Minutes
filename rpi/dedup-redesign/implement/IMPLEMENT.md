# Implementation Record

**Feature**: dedup-redesign
**Started**: 2026-03-09
**Status**: COMPLETED (M1-M6)

---

## M1: StateStore Core + Unit Tests

**Verdict**: PASS

### Deliverables
- [x] `src/state_store.py` — Unified StateStore class (~250 lines)
- [x] `tests/test_state_store.py` — 48 unit tests, all passing
- [x] Atomic writes via `os.replace()` (no `time.sleep`, no retry loops)
- [x] `extract_rec_id()` regex validated against all 10 production filenames
- [x] Separate `state/processing.json` and `state/minutes_cache.json`

### Key Design Decisions
- `mark_failed()` never raises (swallows all exceptions)
- `mark_processing()` returns `bool` for dedup gating
- `_flush()` writes to `.tmp` then `os.replace()` for atomicity

---

## M2: Legacy Migration

**Verdict**: PASS

### Deliverables
- [x] One-time migration from `processed_files.json` to `state/` directory
- [x] `.bak` backup of legacy file before migration
- [x] Schema normalization (old entries → new schema)
- [x] Stale "processing" entries skipped during migration
- [x] 8 migration tests, all passing

---

## M3: DriveWatcher Integration

**Verdict**: PASS

### Deliverables
- [x] Removed `_processed` dict, `_load_processed_db()`, `_save_processed_db()`, `_mark_processed()`, `_mark_failed()` (~100 lines)
- [x] Constructor takes `state_store: StateStore` parameter
- [x] `_watch_loop` uses `extract_rec_id()` + `state_store.is_known()` for filtering
- [x] `_process_file` calls `state_store.mark_processing/success/failed()`
- [x] Tests updated: 12 tests pass (removed 7 obsolete, updated 5)

---

## M4: bot.py Integration

**Verdict**: PASS

### Deliverables
- [x] Removed `_processing_ids: set[str]` and all `.add()/.discard()` calls
- [x] `MinutesBot.__init__` takes `state_store: StateStore`
- [x] `_launch_pipeline` uses `state_store.mark_processing()` for dedup
- [x] `_on_done` callback uses `state_store.mark_success/failed()`
- [x] `/minutes process` checks `state_store.is_known(rec_id)` before processing
- [x] `/minutes drive-status` uses `state_store.processing_count`
- [x] `main()` creates StateStore, calls `cleanup_stale()`, passes to all consumers

---

## M5: pipeline.py Integration

**Verdict**: PASS

### Deliverables
- [x] Removed `from src.minutes_cache import MinutesCache`
- [x] Added `_transcript_hash()` free function
- [x] `run_pipeline` and `run_pipeline_from_tracks` accept `state_store: StateStore`
- [x] Cache via `state_store.get_cached_minutes()` / `put_cached_minutes()`
- [x] 7 pipeline tests updated and passing

---

## M6: Config Cleanup + Delete minutes_cache.py

**Verdict**: PASS

### Deliverables
- [x] `PipelineConfig.minutes_cache_path` replaced with `state_dir: str = "state"`
- [x] `GoogleDriveConfig.processed_db_path` removed
- [x] `src/minutes_cache.py` deleted (functionality absorbed into StateStore)
- [x] Zero remaining references to old fields in source code

---

## M7: End-to-End Live Validation

**Status**: NOT STARTED (requires manual testing with live bot)

---

## Summary

**Milestones Completed**: 6 of 7 (M7 is manual validation)
**Final Status**: COMPLETED (code changes)

### Files Modified/Created
| File | Change Type | Description |
|------|-------------|-------------|
| `src/state_store.py` | NEW | Unified StateStore (~250 lines) |
| `tests/test_state_store.py` | NEW | 48 unit tests |
| `src/drive_watcher.py` | MODIFIED | Replaced internal dedup with StateStore |
| `tests/test_drive_watcher.py` | MODIFIED | Updated for StateStore integration |
| `bot.py` | MODIFIED | Replaced `_processing_ids` with StateStore |
| `src/pipeline.py` | MODIFIED | Added `state_store` param, inline cache |
| `tests/test_pipeline.py` | MODIFIED | Added `state_store` fixture to all tests |
| `src/config.py` | MODIFIED | `state_dir` replaces old paths |
| `src/minutes_cache.py` | DELETED | Absorbed into StateStore |

### Test Results
- 48 StateStore tests: PASS
- 7 pipeline tests: PASS
- 12 drive_watcher tests: PASS
- **67 total tests: ALL PASS**

### Bugs Fixed
1. TOCTOU race (dual-writer to `processed_files.json`)
2. `time.sleep(0.1)` blocking event loop in DriveWatcher
3. No cross-path dedup (Craig vs Drive watcher)
4. `_mark_failed` cascade crash on disk error
5. Stale "processing" entries never cleaned up
6. Schema inconsistency between DriveWatcher and MinutesCache
