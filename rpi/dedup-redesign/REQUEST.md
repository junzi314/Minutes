# Feature Request: Deduplication & Processed DB Redesign

## Problem Statement

The current system suffers from recurring duplicate minutes generation due to fundamental design flaws in how processing state is tracked and persisted.

### Observed Symptoms
- `craig_PKgcDUSPh7v9` was reprocessed **6+ times over 4 days** (3/4~3/8)
- `craig-xZH0rkeudmL1-2026-3-9` generated 2 minutes posts for same recording
- `processed_files.json` consistently shows only 9 entries despite multiple successful processings
- `PermissionError: [Errno 13]` on `processed_files.json` after every pipeline completion

### Root Causes Identified
1. **Shared file conflict**: `DriveWatcher._save_processed_db()` and `MinutesCache._save()` both write to `processed_files.json`. The DriveWatcher overwrites the entire file with `{"processed": ...}`, losing the `minutes_cache` section.
2. **WSL2 PermissionError**: Rapid sequential writes to the same file cause `PermissionError` on WSL2, preventing `_mark_processed` from persisting.
3. **No cross-path deduplication**: Craig detection path (`on_raw_message_update`) and Drive watcher path use independent dedup mechanisms with no shared state.
4. **Post-processing mark**: Files are only marked as processed AFTER the full pipeline completes (including Discord posting), creating a window for duplicate posting if the mark fails.
5. **`_mark_failed` cascade crash**: When `_mark_failed` also hits PermissionError inside the exception handler, it crashes the entire watch loop, potentially skipping remaining files.

### Current Architecture (relevant)
- `processed_files.json`: Single JSON file shared by DriveWatcher (processed DB) and MinutesCache (LLM output cache)
- `DriveWatcher._save_processed_db()`: Overwrites entire file with `{"processed": ...}`
- `MinutesCache._save()`: Read-modify-write, preserves other sections
- `bot.py._processing_ids`: In-memory set for concurrent dedup (lost on restart)
- Craig path dedup key: `"craig:{rec_id}"`
- Drive path dedup key: `"drive:{file_name}"` + file_id in processed DB

### Current Band-Aid Fix (PR #9)
- Added retry with delay for PermissionError
- Changed `_save_processed_db` to read-modify-write
- Added early "processing" mark in memory before pipeline starts
- These mitigate but don't solve the fundamental design issues

## Goals
1. Eliminate duplicate minutes posting completely
2. Make processing state persistence reliable across restarts
3. Unify deduplication across Craig detection and Drive watcher paths
4. Handle concurrent access to state files safely
5. Keep the solution simple and maintainable

## Constraints
- Must run on WSL2 (Windows file system quirks)
- Single bot process (no distributed system concerns)
- Should be backward compatible with existing `processed_files.json` data
- Python 3.12, asyncio-based architecture
- No external database (SQLite acceptable if justified)

## Scope
- Redesign of processing state management
- Deduplication strategy across input sources
- File I/O reliability on WSL2
- NOT changing: audio processing, transcription, LLM generation, Discord posting logic
