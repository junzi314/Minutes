# Product Viability Analysis: Deduplication & Processed-DB Redesign

**Viability Score: HIGH**
**Recommendation: Proceed immediately. This is a P0 reliability fix for a production system.**

---

## 1. Context & Why Now

The Discord Minutes Bot is a set-and-forget automation tool that generates meeting minutes from Craig Bot recordings. It runs in production, processing real meetings for real teams. The bot currently has a **critical reliability defect**: recordings are reprocessed multiple times, flooding the output channel with duplicate minutes posts.

Evidence from production:
- `craig_PKgcDUSPh7v9` was reprocessed **6+ times over 4 days** (Mar 4 - Mar 8)
- `craig-xZH0rkeudmL1-2026-3-9` generated **2 duplicate posts** for the same recording
- `processed_files.json` shows only 10 entries despite far more processing runs having occurred
- `PermissionError: [Errno 13]` fires after every pipeline completion

A band-aid fix was applied in commit `40e8fdc` (retry with delay, read-modify-write, early "processing" mark), but the fundamental design flaws remain. The bug is not theoretical -- it is actively degrading user experience in production.

---

## 2. Users & Jobs To Be Done

### Primary User
Team members who participate in Discord voice meetings and rely on auto-generated minutes to track decisions, action items, and discussion history.

### Jobs To Be Done
1. **Review what was discussed**: After a meeting, users go to the minutes channel to see a clean summary. Duplicate posts make it unclear which is the "real" one.
2. **Trust the automation**: The bot should produce exactly one minutes post per recording, every time, without manual intervention. This is the core promise of the product.
3. **Set and forget**: Bot operators (the person who deployed it) should not need to monitor logs or clean up duplicate posts.

---

## 3. User Value Assessment

### Impact on End Users: **Severe**

| Symptom | User Impact |
|---------|------------|
| 6+ duplicate posts for one recording | Channel becomes noisy and confusing. Users cannot tell which post is authoritative. |
| Duplicates posted over 4 days | Creates the impression that meetings happened multiple times, or that the bot is broken. |
| PermissionError on every completion | Silent data loss -- processing state fails to persist, ensuring duplicates on next restart. |
| No cross-path dedup | If both Craig detection and Drive watcher see the same recording, two posts appear. |

This is not a cosmetic issue. Duplicate minutes posts in a forum channel actively erode the value proposition:
- **Noise**: Users must mentally filter which post to read.
- **Confusion**: Are these different meetings? Different versions? Did something change?
- **Lost trust**: Once users see duplicates, they stop trusting the channel as a source of truth. They revert to manual note-taking, which defeats the purpose entirely.

---

## 4. Product Impact Analysis

### 4a. Root Cause Decomposition (from code review)

I verified all five root causes identified in the REQUEST against the source code:

1. **Shared file conflict** (CONFIRMED): `DriveWatcher._save_processed_db()` at `drive_watcher.py:129` and `MinutesCache._save()` at `minutes_cache.py:41` both write to the same file (`processed_files.json`). Both do full-file read-modify-write. While the band-aid fix changed DriveWatcher to read-modify-write (preserving other sections), the fundamental race condition remains -- if both write within milliseconds of each other, the last writer wins and the first writer's changes are lost.

2. **WSL2 PermissionError** (CONFIRMED): Both `_save_processed_db` and `MinutesCache._save` now have retry-with-delay, but `time.sleep(0.5)` inside an async context blocks the event loop. This is both a reliability problem (retries may not be enough) and a performance problem (blocking sleep in async code).

3. **No cross-path deduplication** (CONFIRMED): `bot.py:131` uses `_processing_ids: set[str]` with keys like `"craig:{rec_id}"` and `"drive:{file_name}"`. These are independent namespaces. If a Craig recording `PKgcDUSPh7v9` arrives via both the Craig event detector AND as a file `craig_PKgcDUSPh7v9_*.aac.zip` in Drive, nothing correlates them as the same recording.

4. **Post-processing mark** (CONFIRMED): In `drive_watcher.py:500`, `_mark_processed` is called only after the full pipeline succeeds (including Discord posting). If the PermissionError on disk write fails all 3 retries, the in-memory dict is updated but not persisted. On next bot restart, the file will be reprocessed.

5. **Exception handler crash** (CONFIRMED): `_mark_failed` at `drive_watcher.py:186` calls `_save_processed_db`, which can raise (despite the retry logic, it swallows PermissionError but could still fail on other OSErrors). If `_mark_failed` itself fails, it does NOT crash the watch loop (the current code handles this correctly in the exception handler at lines 423-431). However, the disk state is still not persisted, meaning the file will be reprocessed on restart.

### 4b. Blast Radius

The deduplication failure affects **every recording processed by the bot**. It is not an edge case -- it is a systematic issue that will manifest whenever:
- The bot restarts (all in-memory state is lost, disk state may be incomplete)
- WSL2 file locking causes PermissionError (which happens "after every pipeline completion" per the request)
- A recording appears in both Craig events and the Drive folder

### 4c. Cost of Inaction

Each duplicate processing run incurs:
- **GPU time**: Whisper transcription on RTX 3060 (the most expensive pipeline stage)
- **API costs**: Claude API call for minutes generation (mitigated by the minutes cache, but only if the cache write itself succeeds -- which it may not, given the shared-file conflict)
- **Discord channel pollution**: Duplicate posts require manual cleanup
- **User trust erosion**: Cumulative and hard to recover

---

## 5. Strategic Alignment

The bot's core value proposition is **reliable, zero-touch automation**. The README and architecture are built around this: the bot watches for recordings, processes them automatically, and posts results. The user never needs to intervene.

Deduplication reliability is not a feature -- it is a **prerequisite for the product to function**. A minutes bot that produces duplicate minutes is worse than no bot at all, because it creates confusion while also consuming resources.

This fix aligns directly with the product's identity. There is no feature work that should take priority over making the existing feature work correctly.

---

## 6. Priority Assessment: **P0 -- Fix Immediately**

### Why P0 (not P1)?

- The bug is **active in production**, not theoretical
- It affects **every recording**, not a subset
- It **degrades the core value proposition** (reliable automation)
- The band-aid fix does not resolve the root causes
- Each day of delay means more duplicate posts and more trust erosion

### Comparison with other potential work

| Work Item | Priority | Rationale |
|-----------|----------|-----------|
| **Dedup redesign** | **P0** | Core reliability. Product is broken without it. |
| Forum thread name truncation (H1 from review) | P2 | Defensive fix, no production incidents yet. |
| Cross-guild Drive watcher support | P3 | Feature enhancement, current single-guild works. |
| Test coverage improvements | P3 | Quality investment, not blocking users. |

---

## 7. Product Concerns & Red Flags

### 7a. Scope Control

The REQUEST correctly scopes out audio processing, transcription, LLM generation, and Discord posting. This is good. The redesign should be purely about **state management and deduplication logic**. Watch for scope creep into adjacent systems.

### 7b. Migration Risk

The processed_files.json file contains real production data (10 entries of successfully processed recordings). The redesign must:
- Migrate existing data without loss
- Not re-trigger processing of already-processed files during migration
- Handle the case where the bot is upgraded while running (or at least document the required restart procedure)

### 7c. SQLite Consideration

The REQUEST mentions "SQLite acceptable if justified." From a product perspective:
- SQLite solves the concurrent-write and atomic-update problems inherently
- It adds a dependency but removes an entire class of bugs (file-level race conditions, WSL2 PermissionError on rapid sequential writes)
- For a single-process bot, SQLite is not overengineered -- it is the right tool for reliable state persistence
- However, a well-designed single-file approach with proper locking (e.g., `fcntl.flock` or atomic rename via temp file) could also work and avoids the dependency

The choice between SQLite and improved JSON file handling is an engineering decision, not a product one. Either approach is acceptable as long as it eliminates the duplicate posting.

### 7d. Cross-Path Dedup Strategy

The hardest product question is: **how should Craig-path and Drive-path recordings be correlated?**

Current state:
- Craig path key: `"craig:{rec_id}"` (e.g., `"craig:PKgcDUSPh7v9"`)
- Drive path key: `"drive:{file_name}"` (e.g., `"drive:craig_PKgcDUSPh7v9_2026-3-4.aac.zip"`)

The rec_id is embedded in the filename. A normalization layer that extracts the rec_id from both paths and uses it as the canonical dedup key would solve this. This is feasible because Craig filenames follow a predictable pattern (`craig[_-]{rec_id}[_-]{date}.aac.zip`).

### 7e. Observability Gap

The current system has no way to detect or alert on duplicate processing. Even after the fix, there should be:
- A log line when a duplicate is detected and skipped (already partially exists for in-memory dedup)
- A way to query processing history (the `/minutes drive-status` command shows count but not details)
- Consideration for a `/minutes history` command that shows recent processing results

This is a nice-to-have, not a blocker for the redesign itself.

### 7f. Testing the Fix

The fix must be verifiable. Specific test scenarios:
1. Bot restart mid-processing: file should not be reprocessed
2. Rapid sequential writes: state must persist correctly
3. Same recording via both Craig event and Drive folder: exactly one minutes post
4. PermissionError during state save: must not cause silent data loss that leads to reprocessing

---

## 8. Success Metrics

### Leading Indicators
- Zero PermissionError entries in logs after deployment
- Processed-files database entry count matches actual unique recordings processed
- No duplicate thread titles in the forum output channel

### Lagging Indicators
- Zero duplicate minutes posts over a 30-day observation period
- Bot uptime without manual intervention (target: indefinite)
- User trust (qualitative: team stops asking "why are there two posts?")

---

## 9. Recommendation

**Proceed with the dedup and processed-DB redesign immediately.**

This is the highest-priority work for the Discord Minutes Bot. The current system has a fundamental reliability defect that undermines the product's core value proposition. The band-aid fix in commit `40e8fdc` reduces the frequency of duplicates but does not eliminate them.

The scope is well-defined, the root causes are thoroughly understood, and the constraints are reasonable. The risk of the fix is low compared to the risk of inaction.

Suggested implementation approach (for engineering to refine):
1. Separate the minutes cache and processed-file DB into independent files (eliminates the shared-file conflict)
2. Use atomic writes (write to temp file, then rename) to eliminate partial-write corruption
3. Extract rec_id from Drive filenames to create a unified dedup key across both input paths
4. Mark files as "processing" on disk (not just in memory) before starting the pipeline, with status progression: processing -> success/error
5. Add integration tests that simulate the specific failure scenarios documented in the REQUEST
