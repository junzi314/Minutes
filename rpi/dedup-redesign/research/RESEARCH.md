# Research Report: Deduplication & State Persistence Redesign

**Date**: 2026-03-09
**Decision**: GO -- Unconditional (95% confidence)
**Effort**: ~10 hours across 7 milestones

---

## Executive Summary

The Discord Minutes Bot has a critical production defect: recordings are reprocessed multiple times due to fundamental design flaws in state persistence and deduplication. The recording `craig_PKgcDUSPh7v9` was reprocessed 6+ times over 4 days, and every bot restart risks generating duplicate posts. Six confirmed bugs stem from a shared-file dual-writer architecture, lack of cross-path deduplication, and event-loop-blocking `time.sleep()` calls. The recommended fix is **Option D: a unified StateStore class** with atomic JSON writes, separate state files, and rec_id-based cross-path dedup. This is a mechanical refactoring with low-medium risk, estimated at 10 hours across 7 incremental milestones. The risk of NOT proceeding is high and escalating -- every restart compounds the duplicate posting problem, eroding user trust in the bot's core "set-and-forget" value proposition.

---

## Feature Overview

| Attribute | Detail |
|-----------|--------|
| **Name** | Deduplication and Processing State Persistence Redesign |
| **Type** | Infrastructure / Reliability Enhancement |
| **Complexity** | Medium |
| **Primary target** | `src/drive_watcher.py` |
| **Secondary targets** | `src/minutes_cache.py`, `src/pipeline.py`, `bot.py`, `src/config.py` |
| **New module** | `src/state_store.py` (~120 lines) |
| **Deleted module** | `src/minutes_cache.py` (absorbed into StateStore) |

**Goals**:
1. Eliminate duplicate minutes posting completely
2. Make processing state persistence reliable across restarts
3. Unify deduplication across Craig detection, Drive watcher, and `/minutes process` paths
4. Handle concurrent access to state files safely
5. Keep the solution simple and maintainable

---

## Requirements Summary

### Functional Requirements (Must-Have)

1. **Single source of truth** shared across Craig detection, Drive watcher, and slash command paths
2. **Persistent dedup guard** that survives bot restarts -- in-memory-only dedup is insufficient
3. **Atomic "mark as processing"** before pipeline work begins, not after completion
4. **PermissionError resilience** -- file I/O failures must never crash the watch loop or cause silent state loss
5. **Minutes cache isolation** -- the `minutes_cache` section must not be overwritten by processing DB updates
6. **Cross-path dedup via rec_id extraction** -- a recording arriving via both Craig event and Drive folder must produce exactly one minutes post
7. **Stale entry cleanup** -- entries stuck in "processing" status across restarts must be recoverable
8. **Schema normalization** -- older entries lacking the `status` field must be handled consistently

### Non-Functional Requirements

| Requirement | Detail |
|-------------|--------|
| **Event loop safety** | No `time.sleep()` in async context; atomic writes eliminate the need for retry-with-delay |
| **Atomic state transitions** | Write to `.tmp` then `os.replace()` -- no partial writes |
| **WSL2 compatibility** | `os.replace()` is atomic on ext4 (project filesystem); DrvFs paths avoided |
| **Backward compatibility** | One-time migration from `processed_files.json` with `.bak` preservation |
| **Single process** | No distributed locking needed; in-memory dict mirrors disk state |

### Key Bug from PR #9

`time.sleep(0.5)` was introduced in retry loops for PermissionError handling. In a single-threaded asyncio application, this blocks ALL concurrent tasks (Discord heartbeat, other pipelines, slash command responses) for up to 1.5 seconds per save attempt, potentially triggering Discord gateway disconnections.

---

## Product Analysis

### User Value Assessment

**Priority**: P0 -- Fix Immediately
**Product Viability**: HIGH
**Strategic Alignment**: Perfect -- deduplication is a prerequisite, not a feature

The bot's core value proposition is **reliable, zero-touch automation**. A minutes bot that produces duplicate minutes is worse than no bot at all, because it creates confusion while consuming GPU and API resources.

| Symptom | User Impact |
|---------|------------|
| 6+ duplicate posts for one recording | Channel becomes noisy and confusing; users cannot tell which post is authoritative |
| Duplicates posted over 4 days | Creates the impression that meetings happened multiple times or that the bot is broken |
| PermissionError on every completion | Silent data loss -- state fails to persist, ensuring duplicates on next restart |
| No cross-path dedup | If both Craig detection and Drive watcher see the same recording, two posts appear |

### Cost of Inaction

Each duplicate processing run incurs:
- GPU time (Whisper transcription on RTX 3060)
- Claude API cost for minutes generation (cache write may also fail due to the shared-file conflict)
- Discord channel pollution requiring manual cleanup
- Cumulative trust erosion -- users stop treating the minutes channel as a source of truth

### Success Metrics

| Type | Metric |
|------|--------|
| **Leading** | Zero PermissionError entries in logs; processed DB entry count matches unique recordings |
| **Leading** | No duplicate thread titles in the forum output channel |
| **Lagging** | Zero duplicate minutes posts over a 30-day observation period |
| **Lagging** | Bot uptime without manual intervention (target: indefinite) |

---

## Technical Discovery

### Current Architecture: Three Entry Points, Two Dedup Mechanisms, One Shared File

```
Entry Point              Dedup Mechanism          State Write Target
-------------------      ---------------------    ----------------------
Craig detection          _processing_ids (RAM)    None (no disk persist)
  (on_raw_message_update)  key: "craig:{rec_id}"

Drive watcher            _processed (RAM+disk)    processed_files.json
  (_watch_loop)            key: Drive file_id       section: "processed"

/minutes process         _processing_ids (RAM)    None (no disk persist)
  (slash command)          key: "craig:{rec_id}"

Pipeline cache           MinutesCache (disk)      processed_files.json
  (run_pipeline)           key: SHA256(transcript)  section: "minutes_cache"
```

The fundamental problem: three entry points, two dedup mechanisms, one shared file with two independent writers and no coordination.

### Dual-Writer Conflict

Both `PipelineConfig.minutes_cache_path` and `GoogleDriveConfig.processed_db_path` default to `"processed_files.json"`. Two classes write to this file independently:

- `DriveWatcher._save_processed_db()` -- full-file write with `{"processed": ...}`
- `MinutesCache._save()` -- read-modify-write, preserves other sections

Despite PR #9 changing DriveWatcher to read-modify-write, there is a TOCTOU race: Writer A reads, Writer B reads the same state, Writer A writes, Writer B writes -- erasing A's changes.

### Six Confirmed Bugs

| # | Bug | Source | Severity |
|---|-----|--------|----------|
| 1 | **Dual-writer TOCTOU race** -- DriveWatcher and MinutesCache both do read-modify-write on the same file; last writer wins, first writer's changes are lost | `drive_watcher.py:129`, `minutes_cache.py:41` | Critical |
| 2 | **`time.sleep(0.5)` blocks event loop** -- blocks ALL async tasks (heartbeat, pipelines, commands) for up to 1.5s per retry | `drive_watcher.py:168`, `minutes_cache.py:54` | High |
| 3 | **No cross-path deduplication** -- Craig path uses `"craig:{rec_id}"` in RAM, Drive path uses `file_id` on disk; same recording arriving via both paths is processed twice | `bot.py:131` | Critical |
| 4 | **`_mark_failed` cascade crash** -- when `_mark_failed` itself fails on disk write, stack trace pollution obscures root cause; disk state is not persisted | `drive_watcher.py:186` | Medium |
| 5 | **Stale "processing" entries persist forever** -- bot crash between `mark_processing` and `mark_success`/`mark_failed` leaves the entry stuck; on restart, the file is never retried | `drive_watcher.py:454` | High |
| 6 | **Schema inconsistency** -- older entries lack `status` field; no normalization on load | `processed_files.json` | Low |

### File Naming Patterns for rec_id Extraction

All observed Craig filenames follow a consistent pattern:

| Filename | Separator | rec_id | Verified |
|----------|-----------|--------|----------|
| `craig_leH5ivxXSepT_2026-2-10_9-16-23.aac.zip` | `_` | `leH5ivxXSepT` | Yes |
| `craig_3wdx2qkYdodO_2026-2-10_10-41-27.aac.zip` | `_` | `3wdx2qkYdodO` | Yes |
| `craig-Q92fATPSYVKt_2026-3-2.aac.zip` | `-` | `Q92fATPSYVKt` | Yes |
| `craig-xZH0rkeudmL1-2026-3-9.aac.zip` | `-` | `xZH0rkeudmL1` | Yes |

Reliable extraction regex: `^craig[_-]([A-Za-z0-9]{12})[_-]`

Validated against all 10 entries in the current `processed_files.json`. The rec_id is always exactly 12 characters of `[A-Za-z0-9]`, positioned immediately after `craig_` or `craig-`, followed by `_` or `-` and a date component.

---

## Technical Analysis

### Options Comparison

| Criterion | A: Atomic Write Only | B: SQLite WAL | C: Separate Files | **D: Unified StateStore** |
|-----------|---------------------|---------------|-------------------|--------------------------|
| Dual-writer conflict | Not fixed | Fixed | Fixed | **Fixed** |
| WSL2 PermissionError | Fixed | Fixed | Fixed | **Fixed** |
| Cross-path dedup | Not fixed | Fixed | Not fixed | **Fixed** |
| Stale entry cleanup | Not fixed | Possible | Not fixed | **Fixed** |
| `time.sleep` blocking | Partial | Fixed | Partial | **Fixed** |
| Schema inconsistency | Not fixed | Fixed | Not fixed | **Fixed** |
| Bugs addressed | 1 of 6 | 6 of 6 | 2 of 6 | **6 of 6** |
| Effort | ~2h | ~6-8h | ~3h | **~10h** |
| New dependencies | None | None (stdlib) | None | **None** |
| Proportionality | Under-engineered | Over-engineered | Incomplete | **Right-sized** |

### Recommended Approach: Option D -- Unified StateStore

Option D is the only approach that addresses all six confirmed bugs. It combines:
- **Atomic writes** from Option A (`os.replace()` via temp file)
- **Separate files** from Option C (one writer per file, no TOCTOU)
- **Unified dedup key** that Option B would provide (rec_id across all paths)

SQLite (Option B) solves the same problems but introduces a persistence paradigm shift disproportionate to the data volume (~10 records, growing ~2-3/week, single writer process). If future requirements demand query capabilities or multi-process access, SQLite can be adopted as a StateStore backend change with zero API changes to callers.

### StateStore API

```python
class StateStore:
    def __init__(self, state_dir: Path): ...

    # Processing state (persistent dedup)
    def mark_processing(self, rec_id: str, source: str, source_id: str, file_name: str) -> bool
    def mark_success(self, rec_id: str) -> None
    def mark_failed(self, rec_id: str, error: str) -> None
    def is_known(self, rec_id: str) -> bool
    def cleanup_stale(self, max_age_sec: int = 7200) -> int

    # Minutes cache
    def get_cached_minutes(self, transcript_hash: str) -> str | None
    def put_cached_minutes(self, transcript_hash: str, minutes_md: str) -> None
```

### File Layout

```
state/
  processing.json    # {rec_id: {source, source_id, file_name, status, timestamps}}
  minutes_cache.json # {transcript_hash: minutes_md}
```

### Key Design Decisions

1. **Atomic writes**: Write to `{path}.tmp`, then `os.replace()`. Atomic at the filesystem level on WSL2/ext4.
2. **Single writer per file**: StateStore is the sole writer for both files. DriveWatcher and bot.py call StateStore methods instead of doing their own I/O.
3. **rec_id as universal key**: Extracted from Drive filenames via regex. Craig detection and `/minutes process` already have rec_id. All paths converge on the same key.
4. **`mark_processing()` returns bool**: Returns `False` if rec_id is already known (any status). Replaces both `_processing_ids` in-memory set and `_processed` dict filtering.
5. **Startup cleanup**: `cleanup_stale()` called once at startup to reset entries stuck in "processing" for longer than 2 hours.
6. **No retry loops**: Atomic rename either succeeds or raises OSError. No `time.sleep()` needed.
7. **In-memory mirror**: Reads are O(1) dict lookups (no disk I/O). Writes update the dict then flush to disk atomically. Single-process means the in-memory dict is always authoritative.

### Cross-Path Dedup Flow

```
Craig detection path:
  rec_id = recording.rec_id              # already available
  if state_store.is_known(rec_id): skip
  state_store.mark_processing(rec_id, source="craig", ...)

Drive watcher path:
  rec_id = extract_rec_id(file_name)     # extract from filename
  if rec_id is None: rec_id = file_id    # fallback for non-standard names
  if state_store.is_known(rec_id): skip
  state_store.mark_processing(rec_id, source="drive", ...)

/minutes process path:
  rec_id = parsed from URL               # already available
  if state_store.is_known(rec_id): skip
  state_store.mark_processing(rec_id, source="command", ...)
```

### Migration Path

One-time migration at startup if the legacy file exists and new state files do not:
1. Read existing `processed_files.json`
2. Extract rec_id from each Drive filename as the new key (fallback to file_id)
3. Normalize schema (add missing `status` field as `"success"`)
4. Split into `state/processing.json` and `state/minutes_cache.json`
5. Rename legacy file to `processed_files.json.bak`
6. Idempotent: skips if new files already exist

Rollback: rename `.bak` back to `.json` and revert code.

### Effort Estimate

~10 hours total across 7 milestones (see Implementation Roadmap below).

---

## Strategic Recommendation

### Decision: GO -- Unconditional

**Confidence**: 95%

### Rationale

| Factor | Assessment |
|--------|------------|
| Risk of proceeding | Low-Medium -- mechanical refactoring, reversible via `.bak` restore |
| Risk of NOT proceeding | High and escalating -- every restart reprocesses, `time.sleep` bug compounds |
| Technical feasibility | HIGH -- all components use stdlib Python (json, os, re, datetime) |
| Product priority | P0 -- active production defect degrading core value proposition |
| Strategic alignment | Perfect -- dedup is a prerequisite for the product to function |

Option D is the only option that addresses all 6 confirmed bugs. It is the minimum viable fix that achieves comprehensive coverage.

### Why Not SQLite?

SQLite (Option B) is technically superior for concurrent access patterns but disproportionate for ~10 records in a single-process bot. If multi-process access or complex queries become necessary, SQLite can be adopted as a StateStore backend change with zero API surface changes.

### Conditions for Proceeding

1. **Validate rec_id regex** against any additional Craig recordings before implementation
2. **Test migration** against the real `processed_files.json` (10 entries)
3. **No `time.sleep()`** anywhere in the new implementation
4. **Preserve `.bak`** of the legacy file for rollback

---

## Implementation Roadmap

| Milestone | Description | Effort | Dependencies | Validation Gate |
|-----------|-------------|--------|--------------|-----------------|
| **M1** | Create `src/state_store.py` with full API + unit tests | 3h | None | All unit tests pass; `mark_processing` returns `False` on duplicate |
| **M2** | Migration function + test with real `processed_files.json` | 1.5h | M1 | All 10 existing entries migrated correctly; `.bak` created; idempotent re-run |
| **M3** | Integrate into `drive_watcher.py` (replace `_processed`, `_save`, `_mark_*`) | 1.5h | M1 | Drive watcher uses StateStore exclusively; no direct file I/O for state |
| **M4** | Integrate into `bot.py` (replace `_processing_ids`, inject StateStore) | 1.5h | M1, M3 | Craig detection and `/minutes process` use `state_store.is_known()` |
| **M5** | Integrate into `pipeline.py` (replace MinutesCache usage) | 1h | M1 | Pipeline reads/writes minutes cache via StateStore |
| **M6** | Update `config.py`, delete `minutes_cache.py`, update existing tests | 1h | M3, M4, M5 | All existing tests pass; `minutes_cache.py` removed; config uses `state_dir` |
| **M7** | End-to-end test with live Drive watcher | 0.5h | M6 | Bot processes a recording, restarts, does NOT reprocess the same recording |

**Total**: ~10 hours

**Incremental delivery**: M1-M2 are standalone and can be reviewed/merged independently. M3-M5 can be done in any order after M1. M6 is the integration checkpoint. M7 is the final validation.

---

## Key Risks

| # | Risk | Probability | Impact | Mitigation |
|---|------|-------------|--------|------------|
| 1 | **Integration regression across modules** -- 6 files touched simultaneously | Medium | Medium | Incremental milestones (M1-M7); each milestone is independently testable and reviewable |
| 2 | **rec_id extraction failure** on unforeseen filename format | Low | Low | Fallback to `file_id` as dedup key when regex does not match; log a warning for visibility |
| 3 | **`os.replace()` on DrvFs** (Windows filesystem via WSL2) | Very Low | Medium | Project runs on ext4 (`/home/junzi/projects/`), not DrvFs; add startup warning if `state_dir` is under `/mnt/` |
| 4 | **Drive `file_id` lookup regression** -- current code filters by file_id, new code keys by rec_id | Low | Medium | Preserve `source_id` field in new schema; add secondary lookup method `is_source_id_known(file_id)` if needed |
| 5 | **In-memory dict diverges from disk** after write failure | Low | Low | Same graceful degradation as current code: in-memory state prevents same-session duplicates; disk state reloaded on restart |
| 6 | **Migration data loss** | Very Low | High | Legacy file preserved as `.bak`; migration is idempotent (skips if new files exist); test against real data in M2 |

---

## Next Steps

1. **Validate rec_id regex**: Run the extraction regex against all filenames in the current `processed_files.json` and any Craig recordings not yet in the database. Confirm 100% match rate.
2. **Begin M1**: Implement `src/state_store.py` with the full API and comprehensive unit tests. This milestone has no dependencies and can start immediately.
3. **Test migration (M2)**: Run the migration function against a copy of the real `processed_files.json` to verify all 10 entries are correctly transformed.
4. **Incremental integration (M3-M5)**: Wire StateStore into each caller one at a time, verifying existing behavior is preserved at each step.
5. **Final validation (M7)**: End-to-end test with a live Craig recording through the Drive watcher path, including a bot restart to confirm persistence.
