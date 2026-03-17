# Deduplication & State Persistence Redesign

**Priority**: P0 -- Fix Immediately
**Date**: 2026-03-09
**Status**: Ready for implementation

---

## 1. Context & Why Now

The Discord Minutes Bot automatically generates meeting minutes from Craig Bot recordings. It currently has a critical production defect: recordings are reprocessed multiple times, generating duplicate minutes posts in Discord. The recording `craig_PKgcDUSPh7v9` was reprocessed 6+ times over 4 days (Mar 4--8), and `craig-xZH0rkeudmL1` generated 2 duplicate posts on the same day.

The root cause is architectural: two independent writers (`DriveWatcher` and `MinutesCache`) share a single `processed_files.json` file with no coordination, three entry points use independent dedup mechanisms with no shared state, and `PermissionError` on WSL2 prevents state from persisting to disk. A band-aid fix (commit `40e8fdc`) reduced frequency but did not eliminate the defect. Each bot restart risks reprocessing every previously-completed recording.

This is the highest-priority work item. A minutes bot that produces duplicate minutes is worse than no bot at all -- it creates confusion while consuming GPU time and API credits.

---

## 2. Users & Jobs To Be Done

**Primary user**: Server admin / bot operator who deployed the bot for their Discord server.

| User Story | JTBD |
|------------|------|
| As a server admin, I want each recording to produce exactly one minutes post | Trust the automation -- the output channel is a reliable source of truth |
| As a server admin, I want the bot to survive restarts without reprocessing already-completed recordings | Set and forget -- no manual monitoring or cleanup required |
| As a server admin, I want the bot to recover from failed processing without manual intervention | Resilient operation -- transient failures self-heal on next startup |

---

## 3. Success Metrics

### Leading Indicators
- Zero `PermissionError` entries in logs after deployment
- Processed DB entry count matches the number of unique recordings processed
- No duplicate thread titles in the forum output channel

### Lagging Indicators
- Zero duplicate minutes posts over a 30-day observation period
- Bot uptime without manual intervention (target: indefinite)
- Zero wasted Claude API calls for already-processed recordings

---

## 4. Functional Requirements

### FR-1: Unified StateStore as single source of truth

A new `src/state_store.py` module provides all processing state and minutes cache operations. All entry points (Craig detection, Drive watcher, `/minutes process` slash command) call StateStore methods instead of managing their own state.

**Acceptance criteria**:
- a. `DriveWatcher` contains zero direct file I/O for processing state
- b. `bot.py` does not use `_processing_ids` in-memory set for dedup
- c. `MinutesCache` class is deleted; its functionality is absorbed into StateStore
- d. StateStore is the sole writer for both `state/processing.json` and `state/minutes_cache.json`

### FR-2: Cross-path deduplication via `rec_id`

All entry points converge on a single dedup key: the Craig recording ID (`rec_id`). For Drive watcher files, `rec_id` is extracted from the filename using the regex `^craig[_-]([A-Za-z0-9]{12})[_-]`. For Craig detection and slash commands, `rec_id` is already available.

**Acceptance criteria**:
- a. A recording arriving via Craig detection AND Drive watcher produces exactly 1 minutes post
- b. A recording arriving via `/minutes process` AND Drive watcher produces exactly 1 minutes post
- c. When `rec_id` extraction fails (non-standard filename), Drive `file_id` is used as a fallback dedup key with a warning logged

### FR-3: Pre-pipeline dedup guard with persistent state

`mark_processing(rec_id)` is called BEFORE pipeline work begins and returns `False` if the `rec_id` is already known (any status: processing, success, or error). State is persisted to disk immediately.

**Acceptance criteria**:
- a. After bot restart, previously processed recordings (status: success) are not reprocessed
- b. After bot restart, previously failed recordings (status: error) are not reprocessed
- c. `mark_processing()` returns `False` for any already-known `rec_id`, regardless of which entry point originally processed it

### FR-4: Stale entry recovery on startup

Entries stuck in `"processing"` status (indicating a crash or timeout during the previous run) are automatically reset on startup, allowing them to be retried.

**Acceptance criteria**:
- a. A recording stuck in "processing" for >2 hours is automatically eligible for retry on next startup
- b. `cleanup_stale()` is called once during bot initialization
- c. The number of stale entries cleaned up is logged at INFO level

### FR-5: Atomic file writes

All state file writes use the atomic write pattern: write to a temporary file in the same directory, then `os.replace()` to the target path. No retry loops. No `time.sleep()`.

**Acceptance criteria**:
- a. `time.sleep()` is not used anywhere in `state_store.py`, `drive_watcher.py`, `minutes_cache.py` (deleted), or `pipeline.py`
- b. A write failure raises immediately (no retry-with-delay); in-memory state remains authoritative for the current session
- c. No `PermissionError` in logs under normal operation on WSL2/ext4

### FR-6: One-time migration from legacy format

On first startup after upgrade, existing `processed_files.json` data is migrated to the new `state/` directory structure. The legacy file is preserved as `processed_files.json.bak`.

**Acceptance criteria**:
- a. All 10 existing entries in `processed_files.json` are migrated with correct `rec_id` extraction
- b. Entries lacking a `status` field are normalized to `"success"`
- c. Migration is idempotent: if `state/processing.json` already exists, migration is skipped
- d. Legacy file is renamed to `.bak`, not deleted
- e. Rollback: renaming `.bak` back and reverting code restores previous behavior

---

## 5. Non-Functional Requirements

| Category | Requirement |
|----------|-------------|
| **Event loop safety** | No blocking calls (`time.sleep`, synchronous file I/O in hot path) in the async event loop. Atomic `os.replace()` is effectively instantaneous. |
| **Atomicity** | State file writes are atomic at the filesystem level. No partial writes, no TOCTOU races between writers. |
| **WSL2 compatibility** | `os.replace()` is atomic on ext4 (project lives under `/home/`, not `/mnt/`). Startup warning logged if `state_dir` is under `/mnt/` (DrvFs). |
| **Performance** | State reads are O(1) in-memory dict lookups (no disk I/O). Writes flush to disk but are non-blocking for typical JSON sizes (<100KB). |
| **Scale** | Designed for ~10 records growing ~2-3/week. Single-process, single-writer. No distributed locking needed. |
| **Backward compatibility** | One-time migration with `.bak` preservation. No breaking changes to config.yaml schema (new `state_dir` field with default). |
| **Observability** | INFO-level log on every state transition (processing, success, failed, stale cleanup). WARNING on rec_id extraction failure. |

---

## 6. Scope

### In Scope
- New `src/state_store.py` module (~120 lines)
- Integration into `drive_watcher.py` (replace `_processed`, `_save_processed_db`, `_mark_*`)
- Integration into `bot.py` (replace `_processing_ids` in-memory set)
- Integration into `pipeline.py` (replace `MinutesCache` usage)
- Update `config.py` (add `state_dir` field to `PipelineConfig`)
- Delete `src/minutes_cache.py` (absorbed into StateStore)
- Migration logic for existing `processed_files.json`
- Unit tests for StateStore; update existing tests for changed callers

### Out of Scope
- Audio processing, transcription, LLM generation, Discord posting logic
- Multi-process / distributed locking support
- External database (SQLite deferred unless JSON proves insufficient)
- UI changes or new slash commands
- Cross-guild Drive watcher support (separate feature)

---

## 7. Rollout Plan

| Phase | Description | Gate |
|-------|-------------|------|
| **M1** | Implement `src/state_store.py` + unit tests (3h) | All unit tests pass; `mark_processing` returns `False` on duplicate |
| **M2** | Migration function + test against real `processed_files.json` (1.5h) | All 10 entries migrated; `.bak` created; idempotent re-run verified |
| **M3** | Integrate into `drive_watcher.py` (1.5h) | Drive watcher uses StateStore exclusively; zero direct state file I/O |
| **M4** | Integrate into `bot.py` (1.5h) | Craig detection and `/minutes process` use `state_store.is_known()` |
| **M5** | Integrate into `pipeline.py` (1h) | Pipeline reads/writes minutes cache via StateStore |
| **M6** | Config update, delete `minutes_cache.py`, fix existing tests (1h) | All tests pass; no references to deleted module |
| **M7** | End-to-end validation with live recording + bot restart (0.5h) | Recording processed once; restart does NOT reprocess |

**Total estimate**: ~10 hours across 7 incremental milestones.

M1-M2 are standalone and can be reviewed independently. M3-M5 can proceed in any order after M1. M6 is the integration checkpoint. M7 is final validation.

---

## 8. Risks & Open Questions

### Risks

| # | Risk | Prob. | Impact | Mitigation |
|---|------|-------|--------|------------|
| 1 | Integration regression (6 files modified simultaneously) | Medium | Medium | Incremental milestones; each independently testable |
| 2 | `rec_id` extraction failure on unforeseen filename format | Low | Low | Fallback to Drive `file_id` as dedup key; log warning |
| 3 | Migration data loss from legacy `processed_files.json` | Very Low | High | `.bak` backup preserved; migration is idempotent; tested against real data in M2 |
| 4 | `os.replace()` behavior on DrvFs (Windows FS via WSL2) | Very Low | Medium | Project runs on ext4 (`/home/`); startup warning if state_dir under `/mnt/` |
| 5 | In-memory dict diverges from disk after write failure | Low | Low | Same-session dedup still works via in-memory state; disk reloaded on restart |

### Open Questions

1. **Should stale "error" entries ever be retried?** Current design treats errors as terminal (no automatic retry). Manual retry via `/minutes process` is always available. Is this sufficient?
2. **Should we add a `/minutes history` command?** Deferred to a follow-up. Current `/minutes drive-status` shows count but not details.
3. **When should we consider SQLite?** If record count exceeds ~1000 or multi-process access becomes necessary. StateStore API is designed so the backend can be swapped without caller changes.
