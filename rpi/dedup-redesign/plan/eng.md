# Engineering Specification: `src/state_store.py` -- Unified State Management

**Date**: 2026-03-09
**Milestone**: M1-M7 (see Implementation Roadmap in RESEARCH.md)
**Effort**: ~10 hours total
**Risk**: Low-Medium (mechanical refactoring, reversible)

---

## 1. Architecture Overview

### 1.1 Component Diagram

```
                         +-------------------+
                         |     bot.py        |
                         |   MinutesBot      |
                         +--------+----------+
                                  |
              owns (created in main(), injected to all callers)
                                  |
                                  v
                         +-------------------+
                         |   StateStore      |
                         |                   |
                         | processing: dict  |  <-- in-memory mirror
                         | cache: dict       |  <-- in-memory mirror
                         +---+----------+----+
                             |          |
                    atomic   |          |   atomic
                    write    |          |   write
                             v          v
                   state/           state/
                   processing.json  minutes_cache.json
```

### 1.2 Callers and Injection

```
bot.py::main()
  |
  +-- creates StateStore(state_dir=Path(cfg.pipeline.state_dir))
  |
  +-- passes to DriveWatcher(cfg.google_drive, state_store, on_new_tracks=...)
  |
  +-- uses directly in on_raw_message_update / _launch_pipeline
  |       state_store.is_known(rec_id) replaces _processing_ids check
  |       state_store.mark_processing(rec_id, ...) replaces _processing_ids.add()
  |       mark_success / mark_failed in _on_done callback
  |
  +-- passes to run_pipeline / run_pipeline_from_tracks (via new parameter)
        pipeline.py uses state_store.get_cached_minutes / put_cached_minutes
```

### 1.3 Data Flow by Entry Point

**Craig detection** (`on_raw_message_update`):

```
1. parse_recording_ended() -> DetectedRecording(rec_id=...)
2. state_store.is_known(rec_id) -> True? skip. False? continue.
3. state_store.mark_processing(rec_id, source="craig", source_id=rec_id, file_name="")
   -> returns False if another path already claimed it. Skip if False.
4. run_pipeline(recording, ..., state_store=state_store)
5. on success: state_store.mark_success(rec_id)
   on failure: state_store.mark_failed(rec_id, error=str(exc))
```

**Drive watcher** (`_watch_loop`):

```
1. list files from Drive API -> [{id, name}, ...]
2. for each file:
   a. rec_id = extract_rec_id(file_name) or file_id as fallback
   b. state_store.is_known(rec_id) -> True? skip. False? continue.
   c. state_store.mark_processing(rec_id, source="drive", source_id=file_id, file_name=file_name)
      -> returns False if already claimed. Skip if False.
   d. download, extract, run_pipeline_from_tracks(..., state_store=state_store)
   e. on success: state_store.mark_success(rec_id)
      on failure: state_store.mark_failed(rec_id, error=str(exc))
```

**Slash command** (`/minutes process`):

```
1. parse URL -> rec_id, key
2. state_store.is_known(rec_id) -> True? reply "already processed". False? continue.
3. state_store.mark_processing(rec_id, source="command", source_id=rec_id, file_name="")
   -> returns False if already claimed. Reply "already in progress" if False.
4. _launch_pipeline(recording, output_channel)  [uses state_store internally]
5. on success: state_store.mark_success(rec_id)
   on failure: state_store.mark_failed(rec_id, error=str(exc))
```

---

## 2. StateStore API Specification

### 2.1 Class Definition

```python
class StateStore:
    """Unified persistent state for processing dedup and minutes cache.

    Single writer for both state files. In-memory dict mirrors disk state.
    All reads are O(1) dict lookups. Writes update dict then flush atomically.
    """

    def __init__(self, state_dir: Path) -> None: ...
```

**Constructor behavior**:
- Creates `state_dir` if it does not exist (`mkdir(parents=True, exist_ok=True)`).
- Loads `state_dir/processing.json` into `self._processing: dict[str, dict]`.
- Loads `state_dir/minutes_cache.json` into `self._cache: dict[str, str]`.
- If a file does not exist or is corrupt JSON, initializes with empty dict and logs a warning.
- Runs migration check (see section 5).
- Does NOT call `cleanup_stale()` automatically -- that is the caller's responsibility to invoke after construction, so the caller can control timing and logging.

### 2.2 Processing State Methods

#### `mark_processing`

```python
def mark_processing(
    self,
    rec_id: str,
    source: str,
    source_id: str,
    file_name: str,
) -> bool:
```

**Purpose**: Atomically claim a recording for processing. Returns whether the claim succeeded.

**Preconditions**: `rec_id` is a non-empty string. `source` is one of `"craig"`, `"drive"`, `"command"`.

**Postconditions**:
- If `rec_id` is NOT in `self._processing`: creates an entry with `status="processing"`, `started_at=<utc iso>`, `source`, `source_id`, `file_name`. Flushes to disk. Returns `True`.
- If `rec_id` IS already in `self._processing` (any status): does nothing. Returns `False`.

**Error handling**: If the disk flush fails (OSError), logs a warning. The in-memory dict is already updated, so same-session dedup still works. Returns `True` (the claim succeeded in memory).

**Rationale for "any status" blocking**: A recording that previously failed should NOT be auto-retried by the watcher. Manual retry via `/minutes process` with a `--force` flag can be added later if needed. This prevents infinite reprocessing loops for recordings that consistently fail.

---

#### `mark_success`

```python
def mark_success(self, rec_id: str) -> None:
```

**Preconditions**: `rec_id` exists in `self._processing` with `status="processing"`.

**Postconditions**: Updates entry to `status="success"`, adds `completed_at=<utc iso>`, removes `started_at`. Flushes to disk.

**If `rec_id` not found**: Logs a warning and creates a minimal entry with `status="success"` and `completed_at`. This handles the defensive case where mark_processing's disk write failed and the bot restarted between claim and completion (should not happen in practice given single-process, but is safe).

**Error handling**: Disk flush failure logged; in-memory dict is updated regardless.

---

#### `mark_failed`

```python
def mark_failed(self, rec_id: str, error: str) -> None:
```

**Preconditions**: `rec_id` exists in `self._processing`.

**Postconditions**: Updates entry to `status="error"`, adds `error=<message>`, `failed_at=<utc iso>`. Flushes to disk.

**If `rec_id` not found**: Same defensive behavior as `mark_success` -- creates a minimal entry.

**Error handling**: Disk flush failure logged; in-memory dict is updated regardless.

**Critical guarantee**: This method NEVER raises. It is called from exception handlers in both `_watch_loop` and `_on_done` callbacks. An exception here would mask the original error.

---

#### `is_known`

```python
def is_known(self, rec_id: str) -> bool:
```

**Purpose**: Check if a rec_id has been seen before (any status).

**Behavior**: `return rec_id in self._processing`. O(1) dict lookup. No disk I/O.

---

#### `cleanup_stale`

```python
def cleanup_stale(self, max_age_sec: int = 7200) -> int:
```

**Purpose**: Reset entries stuck in `"processing"` status for longer than `max_age_sec` seconds.

**Behavior**:
1. Iterates `self._processing`.
2. For each entry where `status == "processing"` and `started_at` is older than `max_age_sec` from now (UTC):
   - Removes the entry entirely (not marks as failed -- the recording can be retried).
3. If any entries were removed, flushes to disk.
4. Returns the count of removed entries.

**Rationale for removal vs. mark-failed**: Stale processing entries indicate a crash. The recording was never actually processed. Removing the entry allows any path (Craig, Drive, command) to pick it up again on the next cycle. Marking it as failed would permanently prevent reprocessing, which is the wrong default for a crash recovery scenario.

**When called**: Once at startup, after StateStore construction, before any entry points begin processing.

---

#### `get_entry` (read-only diagnostic)

```python
def get_entry(self, rec_id: str) -> dict | None:
```

**Purpose**: Return a copy of the processing entry for `rec_id`, or `None`. Used by `/minutes drive-status` and similar diagnostic commands. Returns a shallow copy to prevent external mutation.

---

#### `processing_count` (property)

```python
@property
def processing_count(self) -> int:
```

Returns `len(self._processing)`. Used by `/minutes drive-status` to replace `DriveWatcher.processed_count`.

---

### 2.3 Minutes Cache Methods

#### `get_cached_minutes`

```python
def get_cached_minutes(self, transcript_hash: str) -> str | None:
```

**Behavior**: `return self._cache.get(transcript_hash)`. O(1) dict lookup. No disk I/O.

**Note**: The caller is responsible for computing the hash. The hashing logic (`hashlib.sha256(transcript.encode("utf-8")).hexdigest()`) stays in the pipeline module as a free function (moved from `minutes_cache.py`).

---

#### `put_cached_minutes`

```python
def put_cached_minutes(self, transcript_hash: str, minutes_md: str) -> None:
```

**Behavior**: `self._cache[transcript_hash] = minutes_md`. Flushes `minutes_cache.json` to disk.

**Error handling**: Disk flush failure logged; in-memory dict is updated regardless.

---

### 2.4 Thread Safety

StateStore is designed for single-threaded asyncio. No locking is needed.

All StateStore methods are synchronous and complete in microseconds (dict ops + one atomic file write). They are safe to call from the asyncio event loop without blocking it, because:
- Dict operations are O(1).
- `json.dumps` on ~10-50 entries takes <1ms.
- `os.replace()` is a single syscall.

The `transcriber.transcribe_all` call runs in a thread via `asyncio.to_thread`, but it does not touch StateStore. StateStore calls happen before and after the threaded work, always on the event loop thread.

### 2.5 Error Handling Philosophy

StateStore methods that write to disk (mark_processing, mark_success, mark_failed, put_cached_minutes, cleanup_stale) follow this contract:

1. **In-memory dict is ALWAYS updated first.** This guarantees same-session dedup even if disk writes fail.
2. **Disk flush failure is logged at WARNING level** with the full exception. It is never raised.
3. **No retry loops.** Atomic rename either succeeds or it does not. The next write will succeed or fail on its own.
4. **No `time.sleep()` anywhere.** This is a hard constraint.

The one exception is `mark_processing()` which returns a `bool` -- the return value itself is the primary API, and a disk flush failure does not change the return value.

---

## 3. File Format Specification

### 3.1 `state/processing.json`

```json
{
  "leH5ivxXSepT": {
    "source": "drive",
    "source_id": "1HBrJPLQJ6E-PqtEQVI4gYwQKu8yt_Kds",
    "file_name": "craig_leH5ivxXSepT_2026-2-10_9-16-23.aac.zip",
    "status": "success",
    "completed_at": "2026-02-10T10:39:12.015644+00:00"
  },
  "Q92fATPSYVKt": {
    "source": "craig",
    "source_id": "Q92fATPSYVKt",
    "file_name": "",
    "status": "processing",
    "started_at": "2026-03-09T14:00:00+00:00"
  },
  "7UvnH9BiY2EI": {
    "source": "drive",
    "source_id": "1lq_85EY_9EfThqPdVvA6YjFrf4eOF-K7",
    "file_name": "craig_7UvnH9BiY2EI_2026_2_23.aac.zip",
    "status": "error",
    "error": "CookTimeoutError: Cook job timed out after 600s",
    "failed_at": "2026-02-23T15:30:00+00:00"
  }
}
```

**Schema per entry**:

| Field | Type | Present when | Description |
|-------|------|-------------|-------------|
| `source` | `str` | always | `"craig"`, `"drive"`, or `"command"` |
| `source_id` | `str` | always | Drive file_id for drive entries; rec_id for craig/command |
| `file_name` | `str` | always | Original filename (empty string for craig/command) |
| `status` | `str` | always | `"processing"`, `"success"`, or `"error"` |
| `started_at` | `str` (ISO 8601 UTC) | `status == "processing"` | When mark_processing was called |
| `completed_at` | `str` (ISO 8601 UTC) | `status == "success"` | When mark_success was called |
| `failed_at` | `str` (ISO 8601 UTC) | `status == "error"` | When mark_failed was called |
| `error` | `str` | `status == "error"` | Error message |

**Top-level key**: rec_id (12-char alphanumeric string, or fallback file_id).

**Invariant**: Every entry has exactly one timestamp field (`started_at`, `completed_at`, or `failed_at`) matching its status. Status transitions: `processing -> success` or `processing -> error`. No other transitions are valid. Entries removed by `cleanup_stale` are deleted entirely.

### 3.2 `state/minutes_cache.json`

```json
{
  "a3f1b2c4d5e6f7890123456789abcdef0123456789abcdef0123456789abcdef": "# Meeting Minutes\n## Summary\n...",
  "b4e2c3d5f6a7890123456789abcdef0123456789abcdef0123456789abcdef01": "# Another Meeting\n..."
}
```

**Schema**: Flat `dict[str, str]` mapping SHA-256 hex digest (64 chars) to minutes markdown text.

No metadata (timestamp, transcript length, etc.) is stored. Cache entries are never evicted automatically. If the cache grows large enough to cause concern (unlikely at ~2-3 entries/week), a future `cleanup_old_cache(max_entries)` method can be added.

### 3.3 Atomic Write Strategy

All file writes use the same helper method:

```python
def _flush(self, data: dict, target: Path) -> None:
```

**Algorithm**:
1. Serialize `data` to JSON string: `json.dumps(data, indent=2, ensure_ascii=False)`.
2. Compute temp path: `target.with_suffix('.tmp')` (e.g. `state/processing.tmp`).
3. Write JSON bytes to temp path: `tmp_path.write_text(json_str, encoding="utf-8")`.
4. Atomic rename: `os.replace(str(tmp_path), str(target))`.

**Why this is safe**:
- `os.replace()` is atomic on POSIX filesystems (ext4, which is what `/home/junzi/projects/` uses on WSL2).
- If the process crashes between step 3 and step 4, the `.tmp` file is orphaned but the original file is intact. On next startup, the `.tmp` file is ignored (we only read the `.json` file).
- If step 3 fails (disk full, permission error), the original file is untouched.
- No other process writes to these files (single-writer guarantee).

**Why NOT `tempfile.NamedTemporaryFile`**: The temp file must be in the same directory as the target for `os.replace()` to work (same filesystem). `NamedTemporaryFile` defaults to `/tmp` which may be a different mount. Using `target.with_suffix('.tmp')` guarantees same-directory, same-filesystem.

---

## 4. Integration Changes

### 4.1 `src/config.py`

**Removed**:
- `PipelineConfig.minutes_cache_path` field (default `"processed_files.json"`)
- `GoogleDriveConfig.processed_db_path` field (default `"processed_files.json"`)

**Added**:
- `PipelineConfig.state_dir` field (default `"state"`)

**Resulting PipelineConfig**:
```python
@dataclass(frozen=True)
class PipelineConfig:
    processing_timeout_sec: int = 3600
    state_dir: str = "state"
```

**Resulting GoogleDriveConfig**:
```python
@dataclass(frozen=True)
class GoogleDriveConfig:
    enabled: bool = False
    credentials_path: str = "credentials.json"
    folder_id: str = ""
    file_pattern: str = "craig[_-]*.aac.zip"
    poll_interval_sec: int = 30
    # processed_db_path removed -- state is managed by StateStore
```

**Validation**: No new validation needed. `state_dir` is a path string; the StateStore constructor handles directory creation.

**YAML config change**: Users who had `pipeline.minutes_cache_path` or `google_drive.processed_db_path` explicitly set in their config.yaml will see those keys silently ignored (they are no longer fields). A comment in the sample config.yaml should note the change. The migration logic (section 5) handles the actual data.

### 4.2 `bot.py`

**Removed**:
- `MinutesBot._processing_ids: set[str]` (line 131)
- All `_processing_ids.add()` / `_processing_ids.discard()` calls
- `_processing_ids` check in `_launch_pipeline` (lines 295-299)
- `_processing_ids` check in `_on_drive_tracks` closure (lines 191-196)
- `_processing_ids` discard in `_on_done` callback (line 316)
- `_processing_ids` discard in `_on_drive_tracks` finally block (line 209)

**Added**:
- `MinutesBot.state_store: StateStore` attribute, set via constructor parameter
- In `main()`: `state_store = StateStore(Path(cfg.pipeline.state_dir))` followed by `state_store.cleanup_stale()` at startup
- StateStore passed to `MinutesBot.__init__()` as a required parameter
- StateStore passed to `DriveWatcher.__init__()` as a required parameter

**Changed flow in `_launch_pipeline`**:
```python
def _launch_pipeline(self, recording, output_channel):
    rec_id = recording.rec_id
    if not self.state_store.mark_processing(rec_id, source="craig", source_id=rec_id, file_name=""):
        logger.warning("Skipping duplicate pipeline for rec_id=%s (already known)", rec_id)
        return
    # ... create task as before ...
    def _on_done(t: asyncio.Task):
        if t.cancelled():
            self.state_store.mark_failed(rec_id, "Pipeline cancelled")
        elif (exc := t.exception()) is not None:
            self.state_store.mark_failed(rec_id, str(exc))
        else:
            self.state_store.mark_success(rec_id)
    task.add_done_callback(_on_done)
```

**Changed flow in `/minutes process`**:
```python
if client.state_store.is_known(rec_id):
    await interaction.response.send_message(
        f"Recording `{rec_id}` has already been processed.", ephemeral=True,
    )
    return
# ... rest of flow uses _launch_pipeline which calls mark_processing ...
```

**Changed flow in `_on_drive_tracks` closure**:
The closure no longer does its own dedup check -- DriveWatcher handles it via `state_store.mark_processing()` before invoking the callback. The closure simply runs the pipeline.

**Changed flow in `/minutes drive-status`**:
Replace `watcher.processed_count` with `client.state_store.processing_count`.

### 4.3 `src/drive_watcher.py`

**Removed**:
- `self._processed: dict[str, dict[str, str]]` (line 59)
- `_load_processed_db()` method (lines 106-127)
- `_save_processed_db()` method (lines 129-175) -- including the `time.sleep(0.5)` bug
- `_mark_processed()` method (lines 177-184)
- `_mark_failed()` method (lines 186-203)
- `processed_count` property (lines 72-74)
- `_load_processed_db()` call in `_watch_loop` (line 389)
- File-id filtering in `_watch_loop`: `f["id"] not in self._processed` (line 400)
- `_mark_processed(file_id, file_name)` calls in `_process_file` (lines 481, 500)
- `_mark_failed(file_id, file_name, str(exc))` calls in `_watch_loop` (lines 423, 431)
- In-memory "processing" mark in `_process_file` (lines 454-458)

**Added**:
- `self._state_store: StateStore` attribute, set via constructor parameter
- `from src.state_store import StateStore, extract_rec_id` import

**Changed `__init__` signature**:
```python
def __init__(
    self,
    cfg: GoogleDriveConfig,
    state_store: StateStore,
    on_new_tracks: OnNewTracksCallback,
) -> None:
```

**Changed `_watch_loop` filtering**:
```python
for file_info in files:
    file_id = file_info["id"]
    file_name = file_info["name"]
    rec_id = extract_rec_id(file_name) or file_id
    if self._state_store.is_known(rec_id):
        continue
    # ... process ...
```

**Changed `_process_file`**:
```python
async def _process_file(self, loop, file_id, file_name):
    rec_id = extract_rec_id(file_name) or file_id
    if not self._state_store.mark_processing(rec_id, source="drive", source_id=file_id, file_name=file_name):
        logger.info("Skipping %s (%s) -- already known as rec_id=%s", file_name, file_id, rec_id)
        return
    try:
        # ... download, extract, callback ...
        self._state_store.mark_success(rec_id)
    except Exception as exc:
        self._state_store.mark_failed(rec_id, str(exc))
        raise
```

**Changed error handling in `_watch_loop`**: Since `_process_file` now handles `mark_failed` internally, the except blocks in `_watch_loop` that called `self._mark_failed(...)` are simplified to just log the error.

### 4.4 `src/pipeline.py`

**Removed**:
- `from src.minutes_cache import MinutesCache` import (line 31)
- `MinutesCache(cfg.pipeline.minutes_cache_path)` instantiation (line 85)
- `cache.get(transcript)` call (line 86)
- `cache.put(transcript, minutes_md)` call (line 99)

**Added**:
- `from src.state_store import StateStore` import
- `state_store: StateStore` parameter on both `run_pipeline` and `run_pipeline_from_tracks`
- `transcript_hash()` free function (moved from `minutes_cache._cache_key`):
  ```python
  def _transcript_hash(transcript: str) -> str:
      return hashlib.sha256(transcript.encode("utf-8")).hexdigest()
  ```
- `import hashlib` at top of file

**Changed `run_pipeline_from_tracks` signature**:
```python
async def run_pipeline_from_tracks(
    tracks: list[SpeakerAudio],
    cfg: Config,
    transcriber: Transcriber,
    generator: MinutesGenerator,
    output_channel: OutputChannel,
    state_store: StateStore,          # NEW
    source_label: str = "unknown",
) -> None:
```

**Changed cache usage**:
```python
# Stage 4: Generate minutes (with cache)
th = _transcript_hash(transcript)
minutes_md = state_store.get_cached_minutes(th)

if minutes_md is None:
    status_msg = await send_status_update(...)
    minutes_md = await generator.generate(...)
    state_store.put_cached_minutes(th, minutes_md)
else:
    logger.info("Using cached minutes for source=%s", source_label)
```

**Changed `run_pipeline` signature**:
```python
async def run_pipeline(
    recording: DetectedRecording,
    session: aiohttp.ClientSession,
    cfg: Config,
    transcriber: Transcriber,
    generator: MinutesGenerator,
    output_channel: OutputChannel,
    state_store: StateStore,          # NEW
) -> None:
```

`run_pipeline` passes `state_store` through to `run_pipeline_from_tracks`.

### 4.5 `src/minutes_cache.py`

**Deleted entirely.** All functionality is absorbed into StateStore:
- `_cache_key()` -> `pipeline._transcript_hash()` (free function in pipeline.py)
- `MinutesCache.get()` -> `StateStore.get_cached_minutes()`
- `MinutesCache.put()` -> `StateStore.put_cached_minutes()`

---

## 5. Migration Strategy

### 5.1 Trigger

Migration runs inside `StateStore.__init__()` when ALL of these conditions are met:
1. Legacy file exists: `Path("processed_files.json").exists()` (checked relative to CWD, which is the project root).
2. New processing file does NOT exist: `not (state_dir / "processing.json").exists()`.

If condition 2 is false (new files already exist), the migration is skipped entirely. This makes it idempotent.

### 5.2 Legacy File Detection

The legacy path is passed as an explicit parameter to avoid hardcoding:

```python
def __init__(self, state_dir: Path, legacy_db_path: Path | None = None) -> None:
```

When `legacy_db_path` is `None`, defaults to `Path("processed_files.json")`. Tests can override this to point at fixture files.

### 5.3 Data Transformation

For each entry in `legacy["processed"]`:

```python
# Legacy entry (keyed by Drive file_id):
# "1HBrJPLQJ6E-PqtEQVI4gYwQKu8yt_Kds": {
#     "name": "craig_leH5ivxXSepT_2026-2-10_9-16-23.aac.zip",
#     "processed_at": "2026-02-10T10:39:12.015644+00:00"
# }

# Transformation:
file_id = "1HBrJPLQJ6E-PqtEQVI4gYwQKu8yt_Kds"
file_name = entry["name"]  # "craig_leH5ivxXSepT_2026-2-10_9-16-23.aac.zip"
rec_id = extract_rec_id(file_name)  # "leH5ivxXSepT"
# If extraction fails: rec_id = file_id (fallback)

new_key = rec_id  # or file_id as fallback

# New entry:
# "leH5ivxXSepT": {
#     "source": "drive",
#     "source_id": "1HBrJPLQJ6E-PqtEQVI4gYwQKu8yt_Kds",
#     "file_name": "craig_leH5ivxXSepT_2026-2-10_9-16-23.aac.zip",
#     "status": "success",        # or entry.get("status", "success")
#     "completed_at": "2026-02-10T10:39:12.015644+00:00"
# }
```

### 5.4 Schema Normalization

| Legacy field | New field | Transformation |
|-------------|-----------|----------------|
| `name` | `file_name` | Rename |
| `processed_at` | `completed_at` | Rename (for status=success) |
| `failed_at` | `failed_at` | Keep as-is (for status=error) |
| `started_at` | removed | Stale processing entries: skip entirely (cleanup_stale equivalent) |
| (missing) `status` | `status` | Default to `"success"` if missing and `processed_at` exists |
| (missing) `source` | `source` | Default to `"drive"` (all legacy entries came from Drive watcher) |
| (missing) `source_id` | `source_id` | Set to the original file_id key |

**Entries with `status == "processing"`**: These represent stale entries from a previous crash. They are skipped during migration (equivalent to `cleanup_stale`). A log message notes each skipped entry.

**Entries with `status == "error"`**: Migrated as-is, preserving the error message and failed_at timestamp.

### 5.5 Minutes Cache Migration

If `legacy["minutes_cache"]` exists, it is written directly to `state/minutes_cache.json` without transformation (the format is already correct -- flat dict of hash->markdown).

### 5.6 Backup

After successful migration:
1. Rename `processed_files.json` to `processed_files.json.bak`.
2. Log at INFO level: "Migrated N processing entries and M cache entries from processed_files.json to state/. Legacy file backed up to processed_files.json.bak"

### 5.7 Rollback

If the migration needs to be rolled back:
1. Delete `state/processing.json` and `state/minutes_cache.json`.
2. Rename `processed_files.json.bak` back to `processed_files.json`.
3. Revert code to pre-StateStore version.

This is a manual process documented in the commit message, not automated.

### 5.8 Collision Handling

Theoretical: two legacy entries could have filenames that extract to the same rec_id (e.g. re-uploaded file). If a collision occurs during migration, the later entry (by `completed_at` timestamp) wins. A warning is logged.

Validated against current production data: all 10 entries have unique rec_ids. No collisions expected.

---

## 6. rec_id Extraction

### 6.1 Regex Specification

```python
_REC_ID_PATTERN = re.compile(r"^craig[_-]([A-Za-z0-9]{12})[_-]")
```

**Captures**: Group 1 is the 12-character alphanumeric rec_id.

**Matches** (validated against all 10 production entries):

| Filename | Extracted rec_id |
|----------|-----------------|
| `craig_leH5ivxXSepT_2026-2-10_9-16-23.aac.zip` | `leH5ivxXSepT` |
| `craig_3wdx2qkYdodO_2026-2-10_10-41-27.aac.zip` | `3wdx2qkYdodO` |
| `craig_wppVszajUk99_2026-2-10_14-53-42.aac.zip` | `wppVszajUk99` |
| `craig_ntC4DVLmZK2L_2026-2-14_12-6-2.aac.zip` | `ntC4DVLmZK2L` |
| `craig_VdkWdOBwJupk_2026-2-20_8-4-51.aac.zip` | `VdkWdOBwJupk` |
| `craig_uJ9Q5tIj1awt_2026-2-21.aac.zip` | `uJ9Q5tIj1awt` |
| `craig_7UvnH9BiY2EI_2026_2_23.aac.zip` | `7UvnH9BiY2EI` |
| `craig_6ZRI6Dwld5kB_2026_2_2.aac.zip` | `6ZRI6Dwld5kB` |
| `craig-Q92fATPSYVKt_2026-3-2.aac.zip` | `Q92fATPSYVKt` |
| `craig-xZH0rkeudmL1-2026-3-9.aac.zip` | `xZH0rkeudmL1` |

### 6.2 Fallback Strategy

If the regex does not match (non-standard filename, e.g. a manually uploaded ZIP):
- Return `None` from `extract_rec_id()`.
- The caller uses the Drive `file_id` as the dedup key instead.
- Log at WARNING: `"Could not extract rec_id from filename '%s', falling back to file_id '%s'"`.

This provides dedup by file_id for non-standard files (same quality as the current system) while enabling cross-path dedup for standard Craig filenames.

### 6.3 Helper Function

```python
def extract_rec_id(file_name: str) -> str | None:
    """Extract the Craig recording ID from a filename.

    Returns the 12-char rec_id, or None if the filename does not match
    the expected Craig pattern.
    """
    match = _REC_ID_PATTERN.match(file_name)
    return match.group(1) if match else None
```

This is a module-level function in `src/state_store.py`, exported for use by `drive_watcher.py` and tests.

---

## 7. Testing Strategy

### 7.1 Unit Tests for StateStore (`tests/test_state_store.py`)

#### Construction and loading

| # | Test | Assertion |
|---|------|-----------|
| 1 | `test_init_creates_state_dir` | `state_dir` is created if it does not exist |
| 2 | `test_init_loads_empty_when_no_files` | `processing_count == 0`, `get_cached_minutes("x") is None` |
| 3 | `test_init_loads_existing_processing` | Pre-written `processing.json` is loaded; `is_known()` returns True for existing entries |
| 4 | `test_init_loads_existing_cache` | Pre-written `minutes_cache.json` is loaded; `get_cached_minutes()` returns the value |
| 5 | `test_init_handles_corrupt_json` | Corrupt JSON files -> starts empty, logs warning |

#### mark_processing

| # | Test | Assertion |
|---|------|-----------|
| 6 | `test_mark_processing_new_entry` | Returns `True`; `is_known(rec_id)` returns `True`; file written to disk |
| 7 | `test_mark_processing_duplicate_returns_false` | Second call with same rec_id returns `False`; entry not overwritten |
| 8 | `test_mark_processing_persists_across_instances` | New StateStore instance sees the entry |
| 9 | `test_mark_processing_after_success_returns_false` | After `mark_success`, `mark_processing` with same rec_id returns `False` |
| 10 | `test_mark_processing_after_failure_returns_false` | After `mark_failed`, `mark_processing` with same rec_id returns `False` |
| 11 | `test_mark_processing_disk_failure_still_returns_true` | Mock `os.replace` to raise OSError; returns `True`; in-memory dedup works |

#### mark_success / mark_failed

| # | Test | Assertion |
|---|------|-----------|
| 12 | `test_mark_success_updates_status` | Entry status changes to `"success"`; `completed_at` is set |
| 13 | `test_mark_success_unknown_rec_id_creates_entry` | Does not raise; creates a defensive entry |
| 14 | `test_mark_failed_updates_status` | Entry status changes to `"error"`; `error` and `failed_at` are set |
| 15 | `test_mark_failed_never_raises` | Mock `os.replace` to raise; no exception propagates |
| 16 | `test_mark_failed_unknown_rec_id_creates_entry` | Does not raise; creates a defensive entry |

#### cleanup_stale

| # | Test | Assertion |
|---|------|-----------|
| 17 | `test_cleanup_removes_old_processing` | Entry with `started_at` >2h ago is removed; returns 1 |
| 18 | `test_cleanup_keeps_recent_processing` | Entry with `started_at` <2h ago is kept; returns 0 |
| 19 | `test_cleanup_ignores_success_and_error` | Entries with status `"success"` or `"error"` are never removed regardless of age |
| 20 | `test_cleanup_persists_removal` | After cleanup, new instance does not see the removed entry |

#### Minutes cache

| # | Test | Assertion |
|---|------|-----------|
| 21 | `test_put_and_get_cached_minutes` | Put then get returns the value |
| 22 | `test_get_miss_returns_none` | Unknown hash returns `None` |
| 23 | `test_cache_persists_across_instances` | New instance reads the cached value |

#### Atomic write correctness

| # | Test | Assertion |
|---|------|-----------|
| 24 | `test_no_tmp_file_left_after_write` | After any write, no `.tmp` file remains in `state_dir` |
| 25 | `test_original_intact_on_write_failure` | Seed a file, mock write to raise mid-way; original file is unchanged |
| 26 | `test_concurrent_logical_writes` | Call `mark_processing` then `put_cached_minutes` rapidly; both files are correct |

#### extract_rec_id

| # | Test | Assertion |
|---|------|-----------|
| 27 | `test_extract_underscore_separator` | `"craig_leH5ivxXSepT_2026-2-10.aac.zip"` -> `"leH5ivxXSepT"` |
| 28 | `test_extract_dash_separator` | `"craig-Q92fATPSYVKt_2026-3-2.aac.zip"` -> `"Q92fATPSYVKt"` |
| 29 | `test_extract_non_craig_returns_none` | `"meeting_notes.zip"` -> `None` |
| 30 | `test_extract_short_id_returns_none` | `"craig_abc_2026.aac.zip"` -> `None` (only 3 chars, not 12) |
| 31 | `test_extract_all_production_filenames` | Parametrized test against all 10 filenames from `processed_files.json` |

### 7.2 Unit Tests for Migration (`tests/test_state_store.py`)

| # | Test | Assertion |
|---|------|-----------|
| 32 | `test_migration_from_legacy` | Write legacy `processed_files.json` to tmp_path; construct StateStore; all 10 entries are present with correct rec_id keys; `.bak` file created |
| 33 | `test_migration_idempotent` | If `processing.json` already exists, legacy file is not read; no `.bak` created |
| 34 | `test_migration_schema_normalization` | Legacy entries without `status` field get `status="success"` |
| 35 | `test_migration_skips_stale_processing` | Legacy entry with `status="processing"` is not migrated |
| 36 | `test_migration_preserves_error_entries` | Legacy entry with `status="error"` is migrated with error details |
| 37 | `test_migration_cache_section` | Legacy `minutes_cache` section is written to `state/minutes_cache.json` |
| 38 | `test_migration_rec_id_fallback` | Legacy entry with non-standard filename uses file_id as key |
| 39 | `test_migration_no_legacy_file` | No legacy file -> no migration, no `.bak`, no errors |

### 7.3 Existing Test Changes

#### `tests/test_drive_watcher.py`

**`TestProcessedDB` class** (tests 1-5): **Deleted entirely.** These test `_load_processed_db`, `_save_processed_db`, and `_mark_processed` which no longer exist on DriveWatcher.

**`TestMarkFailed` class** (tests 16-17): **Deleted entirely.** `_mark_failed` is removed from DriveWatcher.

**`_make_cfg` helper**: Remove `processed_db_path` parameter.

**`_make_watcher` helper**: Updated to accept and inject a StateStore instance:
```python
def _make_watcher(
    cfg: GoogleDriveConfig,
    state_store: StateStore | None = None,
    callback: AsyncMock | None = None,
) -> DriveWatcher:
    if state_store is None:
        state_store = StateStore(tmp_path / "state")  # uses tmp_path from test
    if callback is None:
        callback = AsyncMock()
    return DriveWatcher(cfg, state_store, on_new_tracks=callback)
```

**`TestProcessFile` class**: Updated to inject StateStore and verify `state_store.is_known()` instead of checking `watcher._processed`.

**`TestWatchLoopEarlyExit` class**: Updated to pass a StateStore; behavior unchanged.

#### `tests/test_pipeline.py`

**`_make_config` helper**: Remove `cache_path` parameter. Add `state_dir` parameter.

**`cfg` fixture**: Create a StateStore instance in tmp_path and pass it through.

**All `run_pipeline` and `run_pipeline_from_tracks` calls**: Add `state_store=state_store` parameter.

**No test logic changes needed** -- the pipeline tests mock all stages. The only change is wiring the StateStore parameter through.

#### `tests/test_config.py`

**`test_all_defaults_applied`**: Verify `cfg.pipeline.state_dir == "state"` instead of `cfg.pipeline.minutes_cache_path`.

No other config test changes needed (GoogleDriveConfig tests do not reference `processed_db_path` directly).

### 7.4 Integration Test Scenarios

These are documented for M7 (live validation) and can be run manually:

| # | Scenario | Steps | Expected |
|---|----------|-------|----------|
| I1 | **Restart dedup** | Process a recording via Drive. Stop bot. Start bot. Wait one poll cycle. | Recording is NOT reprocessed. `state/processing.json` has the entry. |
| I2 | **Cross-path dedup** | Process `craig_XYZ_date.aac.zip` via Drive. Then run `/minutes process` with the same rec_id. | Slash command replies "already processed". |
| I3 | **Stale cleanup** | Manually edit `state/processing.json` to add a `"processing"` entry with `started_at` 3 hours ago. Start bot. | Entry is cleaned up at startup. Log shows "Cleaned up N stale entries". |
| I4 | **Migration from legacy** | Place current `processed_files.json` in project root. Delete `state/` directory. Start bot. | `state/` directory created. `processing.json` has 10 entries keyed by rec_id. `processed_files.json.bak` exists. Original file no longer exists. |

---

## 8. Risk Mitigations

### 8.1 `os.replace()` Atomicity on WSL2/ext4

**Risk**: `os.replace()` may not be atomic on all filesystems.

**Mitigation**: The project directory is on ext4 (`/home/junzi/projects/discord-minutes-bot`), where `os.replace()` maps to the `rename(2)` syscall, which is atomic for files on the same filesystem. The temp file is always in the same directory as the target (same filesystem guaranteed).

**Verification**: Add a startup log line showing the resolved `state_dir` path and its filesystem type (via `os.statvfs` or a `/proc/mounts` check). This is informational, not a gate.

### 8.2 DrvFs Detection and Warning

**Risk**: If a user configures `state_dir` to a path under `/mnt/c/` (Windows DrvFs via WSL2), `os.replace()` may not be atomic, and PermissionError behavior is unpredictable.

**Mitigation**: At StateStore construction, check if the resolved `state_dir` path starts with `/mnt/`. If so, log a WARNING:

```
StateStore: state_dir is on a Windows filesystem (/mnt/...).
Atomic writes may not be reliable. Move state_dir to a Linux filesystem
(e.g. /home/...) for best reliability.
```

This is a warning, not an error. The bot still starts.

### 8.3 Write Failure Graceful Degradation

**Risk**: Disk write fails (full disk, permission error, filesystem error).

**Mitigation**:

| Layer | Behavior |
|-------|----------|
| **In-memory dict** | Always updated. Same-session dedup works even if disk is broken. |
| **Disk state** | Last successful write remains intact (atomic write ensures no corruption). |
| **On restart** | If disk writes failed, in-memory state is lost. The bot will re-load from the last good disk state. Recordings processed since the last successful write will be reprocessed once. This is strictly better than the current behavior (where the entire processed DB can be corrupted). |
| **Logging** | Every write failure is logged at WARNING with the full exception and the affected file path. |

### 8.4 Large Cache File

**Risk**: `minutes_cache.json` grows unbounded. Each entry is ~4-8KB of markdown. At 3 entries/week, after 1 year that is ~150 entries, ~1MB. This is negligible.

**Mitigation**: No action needed now. If cache size becomes a concern, add a `max_cache_entries` parameter to StateStore and evict oldest entries on put. The API does not change.

### 8.5 rec_id Regex Mismatch on Future Craig Versions

**Risk**: Craig changes its filename format (e.g. longer rec_id, different prefix).

**Mitigation**: The regex fallback returns `None`, and the caller falls back to `file_id`. This provides Drive-path dedup at the current quality level. Cross-path dedup degrades for new-format files but does not break. The regex can be updated when the new format is observed.

### 8.6 Migration Collision

**Risk**: Two legacy entries map to the same rec_id (re-uploaded file with same recording).

**Mitigation**: Later entry (by timestamp) wins. Warning logged. Validated: all 10 current entries have unique rec_ids.

### 8.7 Bot Crash During Migration

**Risk**: Bot crashes after writing `state/processing.json` but before renaming legacy to `.bak`.

**Mitigation**: On next startup, `processing.json` exists, so migration is skipped (idempotent check). The legacy file remains but is harmless (no code reads it anymore). The `.bak` rename can be done manually or will happen on the next migration attempt if `processing.json` is deleted.

---

## Appendix A: Files Changed Summary

| File | Action | Lines Changed (est.) |
|------|--------|---------------------|
| `src/state_store.py` | NEW | ~150 |
| `src/drive_watcher.py` | MODIFY | -100, +20 |
| `bot.py` | MODIFY | -20, +25 |
| `src/pipeline.py` | MODIFY | -5, +15 |
| `src/config.py` | MODIFY | -3, +2 |
| `src/minutes_cache.py` | DELETE | -78 |
| `tests/test_state_store.py` | NEW | ~350 |
| `tests/test_drive_watcher.py` | MODIFY | -80, +30 |
| `tests/test_pipeline.py` | MODIFY | -5, +10 |
| `tests/test_config.py` | MODIFY | -2, +2 |

**Net**: ~+420 new, ~-290 removed. The codebase grows by ~130 lines, but the state management surface area is significantly simpler (1 class vs. 3 independent mechanisms).

## Appendix B: Milestone Dependency Graph

```
M1 (StateStore + unit tests)
  |
  +---> M2 (Migration + tests)
  |
  +---> M3 (drive_watcher integration)
  |       |
  +---> M4 (bot.py integration)
  |       |
  +---> M5 (pipeline.py integration)
          |
          v
        M6 (config.py, delete minutes_cache.py, update all tests)
          |
          v
        M7 (end-to-end live validation)
```

M1 has no dependencies and can start immediately. M3, M4, M5 depend on M1 and can proceed in parallel. M6 depends on M3+M4+M5. M7 depends on M6.
