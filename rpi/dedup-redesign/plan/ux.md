# UX Design: Deduplication & State Persistence Redesign

## 1. Overview

This redesign is an **invisible infrastructure change**. No new UI surfaces are added, no existing messages change format, and no user workflows are altered. The sole user-facing effect is the elimination of duplicate minutes posts -- a bug fix, not a feature.

Users of the Discord Minutes Bot interact with it through two touchpoints:

1. **Passive observation** -- minutes posts appear automatically in the output channel after a meeting.
2. **Slash commands** -- `/minutes status`, `/minutes process <url>`, `/minutes drive-status`.

Neither touchpoint changes in appearance or behavior. The redesign replaces the backend state management layer that decides whether a recording has already been processed.

---

## 2. User-Facing Behavior: Before vs. After

### End Users (meeting participants who read the minutes channel)

| Scenario | Before | After |
|----------|--------|-------|
| Normal recording | One minutes post appears | One minutes post appears (identical) |
| Bot restart after processing | Same recording may be reposted (duplicate) | No repost; already-processed recordings are skipped |
| Recording detected via both Craig event and Drive folder | Two minutes posts for the same meeting | One minutes post; second detection silently skipped |
| Recording fails partway through | May or may not be retried depending on failure timing | Automatically retried on next startup if stuck for >2 hours |
| Rapid successive recordings | Occasionally produces duplicates due to state write failures | One post per recording; atomic writes prevent state loss |

### Bot Operators (admins who run `/minutes` commands)

| Command | Before | After |
|---------|--------|-------|
| `/minutes status` | No change | No change |
| `/minutes process <url>` | Processes even if already completed | Skips if `rec_id` is already marked as `success` |
| `/minutes drive-status` | Shows processed count from `DriveWatcher._processed` | Shows processed count from `StateStore` (same number, same format) |

The `/minutes drive-status` response remains functionally identical. The underlying data source changes from `DriveWatcher._processed` to `StateStore`, but the output format and values are unchanged.

---

## 3. State Flows

### Recording Lifecycle States

A recording progresses through exactly one of these paths:

```
                      mark_processing()
    [unknown] --------------------------> [processing]
                                            |       |
                              mark_success()|       | mark_failed()
                                            v       v
                                       [success]  [failed]

    On restart:
    - [success] entries --> remain; recording is never reprocessed
    - [failed] entries  --> remain; recording is not retried in same session
    - [processing] entries older than 2 hours --> reset to [unknown] for retry
```

### State Descriptions

| State | Meaning | Persisted to disk | Allows reprocessing |
|-------|---------|-------------------|---------------------|
| unknown | Recording not yet seen by the bot | N/A (no entry) | Yes |
| processing | Pipeline is actively running | Yes (atomic write) | No |
| success | Minutes posted successfully | Yes | No |
| failed | Pipeline encountered an error | Yes | No (same session); Yes (manual retry via `/minutes process`) |

### Cross-Path Deduplication

All three entry points converge on the same `rec_id` key:

```
Craig detection          -->  rec_id from Craig event payload
Drive watcher            -->  rec_id extracted from filename (regex)
/minutes process <url>   -->  rec_id parsed from URL

All three call: state_store.is_known(rec_id)
  - True  --> skip (log "already known, skipping")
  - False --> state_store.mark_processing(rec_id, ...) --> run pipeline
```

From the user's perspective, this is invisible. The only observable effect is that a recording arriving through multiple paths produces exactly one minutes post instead of two or more.

---

## 4. Error States & Recovery

### 4a. Pipeline Processing Fails

**User sees**: An error embed posted to the output channel (unchanged behavior).
**Backend change**: The recording is marked `failed` in the StateStore with an error message. It will not be retried automatically in the current session. The admin can manually retry via `/minutes process <url>`.

### 4b. State File Write Fails (disk I/O error)

**User sees**: Nothing. Processing continues normally.
**Backend change**: The in-memory state dict is always updated regardless of disk write outcome. This prevents same-session duplicates. On next bot restart, the state is reloaded from the last successful disk write. Recordings processed after the last successful write may be retried -- this is acceptable because the minutes cache (separate file) prevents duplicate LLM API calls, and the poster will create a new post. The probability of this scenario is extremely low with atomic writes (`os.replace`).

### 4c. Bot Crashes Mid-Processing

**User sees**: No minutes post for that recording (processing was interrupted). After restart, the recording is automatically retried and a minutes post appears.
**Backend change**: The entry remains in `processing` state on disk. On startup, `cleanup_stale()` resets entries stuck in `processing` for longer than 2 hours back to `unknown`, allowing the next poll cycle or Craig event to pick them up.

### 4d. Duplicate Detection

**User sees**: Nothing (the duplicate is silently suppressed).
**Backend change**: A `WARNING`-level log line is emitted:

```
WARNING state_store: Recording {rec_id} already known (status=processing), skipping
```

This is diagnostic information for operators reviewing logs. No Discord message is sent.

### 4e. Migration from Legacy Format

**User sees**: Nothing.
**Backend change**: On first startup after the upgrade, the legacy `processed_files.json` is migrated to the new `state/processing.json` and `state/minutes_cache.json` files. The legacy file is preserved as `processed_files.json.bak`. All previously processed recordings retain their `success` status, preventing reprocessing.

---

## 5. Admin Observability

### 5a. Slash Commands

No changes to command names, parameters, or response formats.

| Command | What it shows | Change |
|---------|---------------|--------|
| `/minutes status` | Uptime, model status, GPU, channels | None |
| `/minutes process <url>` | Acknowledgement, then posts minutes | Now also checks StateStore; skips if already processed |
| `/minutes drive-status` | Watcher status, folder, pattern, count | Count sourced from StateStore instead of DriveWatcher dict |

### 5b. Log Messages

Operators monitoring logs will notice module name changes in state-related log entries:

| Before | After |
|--------|-------|
| `drive_watcher: Saved processed DB (10 entries)` | `state_store: Saved processing state (10 entries)` |
| `drive_watcher: Marked file X as failed` | `state_store: Marked rec_id=X as failed` |
| `minutes_bot: Skipping duplicate pipeline for craig:X (already processing)` | `state_store: Recording X already known (status=success), skipping` |

New log entries that did not exist before:

| Level | Message | When |
|-------|---------|------|
| INFO | `state_store: Migrated N entries from legacy processed_files.json` | First startup after upgrade |
| INFO | `state_store: Cleaned up N stale processing entries` | Startup, if entries were stuck |
| WARNING | `state_store: Recording {rec_id} already known, skipping` | Duplicate detection across paths |
| WARNING | `state_store: Disk write failed, in-memory state preserved` | Rare I/O failure |

### 5c. Verifying the System Works

After deployment, admins can confirm correct behavior by:

1. **Check processed count**: Run `/minutes drive-status`. The processed count should match the number of unique recordings.
2. **Check for duplicates**: Review the output forum channel. No recording should have more than one thread.
3. **Check logs after restart**: After a bot restart, look for `Loaded processing state: N entries`. The bot should NOT reprocess any of those N recordings.
4. **Check stale cleanup**: If the bot was killed mid-processing, look for `Cleaned up N stale processing entries` on restart, followed by the recording being retried in the next poll cycle.

---

## 6. Accessibility

Not applicable. This redesign has no user-facing interface changes. All existing accessibility characteristics of Discord's native UI (slash command interactions, embed rendering, screen reader support) remain unchanged.
