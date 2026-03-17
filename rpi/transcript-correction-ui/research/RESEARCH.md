# Research Report: Transcript Correction UI

**Feature Slug**: transcript-correction-ui
**Date**: 2026-03-17
**Recommendation**: DEFER
**Confidence**: 85% (High)

---

## Executive Summary

**Recommendation: DEFER** -- This feature requests a browser-based Web UI for manually correcting Whisper transcription output before minutes generation. While the user value is real (correcting misrecognized proper nouns, technical terms), the implementation scope is fundamentally misaligned with the current project's architecture, deployment model, and design principles. The bot is a headless Discord-native Python application deployed via Docker/systemd on a single-GPU machine; adding a full Web application stack (HTTP server, REST API, HTML/CSS/JS frontend, authentication, audio streaming, session management) introduces an entirely new technology surface that dwarfs the existing codebase. More critically, the multi-user Discord context creates an unresolvable UX tension: who gets to edit, when is the edit window, and how does the bot communicate that corrections are pending? A lighter-weight alternative -- a Discord-native "dictionary" feature for automatic term replacement -- would deliver 80% of the value at 10% of the cost. This report recommends deferring the Web UI and instead pursuing a simpler Discord-native approach.

---

## Recommendation

- **Decision**: DEFER
- **Confidence**: High (85%)
- **Rationale**: The feature requires introducing a full Web application stack (HTTP server, frontend, auth, session management) into a headless Discord bot, violating the project's "minimal state" and pipeline-first design principles. The deployment complexity (exposing a web server from a single-GPU machine) and the UX friction (switching between Discord and a browser) significantly reduce the practical value. A Discord-native dictionary/glossary approach would address the core pain point (misrecognized terms) with far less complexity.

---

## 1. Feature Overview

| Item | Value |
|------|-------|
| **Feature Name** | Transcript Correction UI (文字起こし手動修正UI) |
| **Type** | New capability (new technology surface) |
| **Target Components** | New: `src/web/` (server, static assets). Modified: `pipeline.py`, `config.py`, `config.yaml` |
| **Complexity** | XL (as noted in REQUEST.md) |
| **Traceability** | R-79 |
| **Implementation Order** | Ext-7 (lowest priority extension) |
| **Origin** | Section 9: Future Extensions (explicitly out of scope in original plan) |

### Goals

1. Provide a browser-based UI to view and edit transcription segments before minutes generation
2. Allow correction of misrecognized proper nouns, technical terms, and speaker names
3. Integrate the corrected transcript back into the pipeline for improved minutes quality
4. Optionally sync audio playback with segment highlighting

---

## 2. Requirements Summary

### Must-Have (from REQUEST.md)

1. **Browser-based transcript correction UI** -- Left panel with segment list, right panel with edit area [R-79]
2. **Segment-level text editing** -- Edit individual segments with speaker, timestamp, and text [R-79]
3. **Pipeline continuation** -- After corrections, trigger minutes generation from the corrected transcript [R-79]

### Nice-to-Have

- Audio playback synchronized with segment highlighting
- Speaker name correction and merge
- Correction history tracking
- Segment split and merge operations

### Non-Functional

- Web server is optional (config.yaml toggle)
- Security: local network only, with auth token
- Temporary state: correction data deleted after generation

### API Surface (proposed in REQUEST.md)

- Local HTTP server (FastAPI or aiohttp)
  - `GET /transcripts/{id}` -- Retrieve transcript segments
  - `PUT /transcripts/{id}` -- Save corrected transcript
  - `POST /transcripts/{id}/generate` -- Trigger minutes generation

---

## 3. Product Analysis

### User Value: Medium

| Aspect | Assessment |
|--------|-----------|
| **Pain point severity** | Medium. Whisper large-v3 with VAD achieves ~95%+ accuracy for standard Japanese conversation. The primary failure modes are proper nouns (person names, product names, company names) and domain-specific jargon. These are real but narrow |
| **Frequency of need** | Low-Medium. Not every meeting requires correction. Only meetings with heavy jargon or new participants with unusual names would benefit |
| **Who benefits** | Power users who demand high accuracy in formal minutes. Casual Discord meeting groups are unlikely to invest time in manual correction |
| **Impact when needed** | High. A misrecognized person name propagated through the entire minutes document is highly visible and embarrassing |
| **Effort-to-value ratio** | Poor. Requiring users to open a browser, review every segment, make edits, and click generate adds 10-30 minutes of manual work to what is currently a fully automated 2-minute process |

### Product Vision Alignment: Weak

The core mission of this bot is **automated** meeting minutes from voice recordings. The word "automated" is the entire value proposition. A manual correction UI fundamentally undermines this by inserting a human-in-the-loop step that blocks the pipeline.

| Design Principle | Alignment | Rationale |
|-----------------|-----------|-----------|
| Pipeline-first | **Violated** | Introduces a blocking pause in the middle of the pipeline. The current flow is fire-and-forget; this requires wait-for-human semantics |
| Async by default | **Violated** | The pipeline must block and wait for human input. This is inherently synchronous from the pipeline's perspective |
| Graceful degradation | Neutral | Web UI is optional, but the "degraded" mode (no corrections) is actually the current working system |
| Multi-guild support | **Complicated** | Multiple concurrent corrections from different guilds require session management, concurrent editing, and timeout handling |
| Minimal state | **Violated** | Requires persisting in-flight transcripts, serving audio files, managing edit sessions, and tracking correction status |

### Priority Assessment: Low (Ext-7)

The REQUEST.md itself classifies this as the lowest-priority extension candidate. The original plan explicitly placed it in "future extensions (out of scope)." This classification is correct.

### Product Viability Score: 3.5/10 -- NOT RECOMMENDED

---

## 4. Technical Discovery

### Current Pipeline Architecture

The current pipeline is a clean 6-stage linear flow:

```
audio_acquisition -> transcription -> merge -> generation -> posting
     (Craig)        (faster-whisper)  (merger)   (Claude)    (Discord)
```

Key characteristics:
- **Fire-and-forget**: Pipeline launches via `asyncio.create_task()` in `_launch_pipeline()` and runs to completion
- **No intermediate state**: Segments exist only in memory during pipeline execution; they are never persisted
- **Temporary directory scoping**: Audio files live in a `tempfile.TemporaryDirectory` that is cleaned up when the pipeline finishes
- **Single-pass**: Each stage produces output consumed by the next; there is no loopback

### Where Correction Would Insert

A correction step would need to be inserted between Stage 3 (merge) and Stage 4 (generation):

```
transcription -> merge -> [CORRECTION UI] -> generation -> posting
                           ^^^^^^^^^^^^^^^^^
                           NEW: blocks pipeline
                           Requires: persist segments, serve web UI,
                           wait for user, resume pipeline
```

### Integration Points Affected

| Component | Current | Required Change |
|-----------|---------|-----------------|
| `pipeline.py` | Linear async flow, ~10 min max | Must pause indefinitely, resume on HTTP callback |
| `transcriber.py` | Returns `list[Segment]` in memory | Segments must be serialized to JSON and persisted |
| `merger.py` | Receives segments, returns transcript string | Must accept corrected segments (same interface, but from disk) |
| `bot.py` | Fire-and-forget pipeline launch | Must send "correction ready" link to Discord, handle timeout |
| `config.py` | No web config | New `WebConfig` dataclass (host, port, auth token, enabled flag) |
| `state_store.py` | Processing dedup + minutes cache | New: pending correction sessions with timeout tracking |

### Reusable Components

- `Segment` dataclass -- Already has `start`, `end`, `text`, `speaker` fields suitable for JSON serialization
- `merge_transcripts()` -- Can accept corrected segments without modification
- `StateStore` atomic write pattern -- Reusable for correction session persistence
- `config.py` section builder pattern -- `_build_section()` can build a `WebConfig` from YAML

### What Does NOT Exist

| Component | Status | Effort |
|-----------|--------|--------|
| HTTP server framework | Not installed | New dependency (FastAPI/aiohttp web) |
| REST API endpoints | None | ~200 lines |
| HTML/CSS/JS frontend | None | ~500-1000 lines (SPA with segment editor) |
| Audio file serving | None | Static file serving + CORS |
| Authentication middleware | None | Token-based auth |
| Session management | None | Pending corrections with timeout |
| Pipeline pause/resume | None | Fundamental architecture change |
| Transcript serialization | None | Segment list to/from JSON persistence |
| WebSocket or polling | None | For real-time status updates |

### Discord UI Alternative: What Could Work Natively

Discord provides limited but usable UI primitives:

| Component | Capability | Limit |
|-----------|------------|-------|
| **Modals (TextInput)** | Multi-line text editing | 4000 chars per field, 5 fields max, 3-second response timeout |
| **Buttons** | Action triggers | 5 per row, 5 rows max |
| **Select Menus** | Choose from list | 25 options max |
| **Embeds** | Rich display | 6000 chars total, 25 fields |
| **Files** | Attach transcript | 25 MB max |

A practical Discord-native approach could work for the most common correction need (term replacement):

1. After transcription, post the transcript as a `.md` file attachment
2. Offer a "Correct Terms" button that opens a modal
3. Modal has a text field for "find -> replace" pairs (e.g., `かいしゃ名 -> 会社名`)
4. Apply replacements to the transcript, then regenerate

This would not require a web server, new dependencies, or architectural changes.

---

## 5. Technical Analysis

### Feasibility Assessment

The feature is technically **feasible** but architecturally **expensive**. The core challenge is not "can we build a web UI" but "should we fundamentally change the application's architecture for an optional feature."

### Option A: Full Web UI (as described in REQUEST.md)

**Approach**: FastAPI server with static HTML/JS frontend, REST API, audio streaming.

| Aspect | Assessment |
|--------|-----------|
| **New dependencies** | fastapi, uvicorn (or aiohttp-web), jinja2 or static SPA |
| **New code surface** | ~1500-2500 lines (server + frontend + tests) |
| **Architecture impact** | High -- pipeline must support pause/resume, new state management |
| **Deployment impact** | High -- web server port exposure, TLS consideration, firewall rules |
| **Security surface** | Medium -- auth tokens, CORS, path traversal in static serving |
| **Testing complexity** | High -- integration tests for HTTP endpoints, frontend behavior |
| **Development effort** | 5-8 days (full-time developer) |
| **Maintenance burden** | High -- two technology stacks (Python bot + JS frontend) |

**Key technical challenges**:

1. **Pipeline pause/resume**: The current pipeline runs in a `tempfile.TemporaryDirectory` context manager. Pausing for human input means the temp directory must stay alive for hours/days. This breaks the current cleanup model.

2. **Concurrent sessions**: Multiple guilds may have corrections pending simultaneously. Each session needs its own state, audio files, and timeout management.

3. **Discord notification flow**: After transcription, the bot posts a link like `http://localhost:8080/edit/abc123?token=xyz`. Users click, edit in browser, click "Generate." The bot then resumes the pipeline. The 3-second Discord interaction timeout means the initial response must be immediate, and the actual generation happens asynchronously.

4. **Audio file lifetime**: If audio-sync playback is desired, audio files must persist for the duration of the edit session. Currently they are deleted immediately after transcription.

5. **Network accessibility**: The REQUEST.md suggests "local HTTP server." On a Docker/WSL2 deployment, "local" means the host machine's network. Discord users on different machines cannot access `localhost`. A reverse proxy, tunnel (ngrok), or public deployment would be needed for real multi-user scenarios.

### Option B: Discord-Native Dictionary/Glossary (Recommended Alternative)

**Approach**: A per-guild term dictionary that auto-corrects known misrecognitions before minutes generation.

| Aspect | Assessment |
|--------|-----------|
| **New dependencies** | None |
| **New code surface** | ~200-300 lines |
| **Architecture impact** | Minimal -- simple string replacement between merge and generate stages |
| **Deployment impact** | None |
| **Security surface** | None (Discord permissions model) |
| **Testing complexity** | Low -- unit tests for replacement logic |
| **Development effort** | 1-2 days |
| **Maintenance burden** | Minimal |

**How it works**:

1. `/minutes dictionary-add <wrong> <correct>` -- Adds a term pair (e.g., `かいしゃめい -> KaiSha名`)
2. `/minutes dictionary-list` -- Shows current dictionary
3. `/minutes dictionary-remove <wrong>` -- Removes a pair
4. Dictionary stored in `state/guild_settings.json` (existing pattern)
5. Applied automatically after `merge_transcripts()` and before `generator.generate()`

**Addresses 80% of the pain**: The most common Whisper errors are consistent -- the same proper noun is misrecognized the same way every time. A dictionary handles this without any manual intervention per meeting.

### Option C: Discord Modal-Based Correction (Middle Ground)

**Approach**: After transcription, post transcript preview with a "Correct" button. Button opens a Discord modal with the full transcript text for direct editing.

| Aspect | Assessment |
|--------|-----------|
| **Feasibility** | Partial -- Discord modals have a 4000-character total input limit across all fields. A 30-minute meeting transcript easily exceeds 10,000 characters |
| **UX quality** | Poor -- editing a large transcript in a Discord modal text field is a terrible experience |
| **Verdict** | Not viable for full transcript editing. Could work for a "corrections list" (find/replace pairs) |

### Complexity Assessment

| Option | Complexity | Value Delivered | Recommended |
|--------|-----------|-----------------|-------------|
| A: Full Web UI | XL (5-8 days) | High but niche | No (DEFER) |
| B: Dictionary/Glossary | S (1-2 days) | Medium, covers 80% of cases | Yes (separate feature) |
| C: Discord Modal | M (2-3 days) | Low (char limit makes it impractical) | No |

### Technical Risks

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Pipeline architecture overhaul required for pause/resume | High | Certain | Defer; design asynchronous pipeline v2 as a prerequisite |
| Web UI inaccessible from Docker/WSL2 to external users | High | High | Would need reverse proxy or tunnel; adds deployment complexity |
| Audio files persisted for edit session increase disk usage | Medium | Certain | Session timeout + cleanup; but adds state management complexity |
| Frontend JS maintenance burden on Python-focused team | Medium | High | Ongoing cost; no existing frontend expertise assumed |
| Security surface (token auth, CORS, static file serving) | Medium | Medium | Standard mitigations exist but must be implemented correctly |
| Concurrent edit sessions from multiple guilds | Medium | Medium | Session management with timeout; adds complexity to state_store |

---

## 6. Implementation Estimate (if proceeding with Option A)

### Phase Breakdown

| Phase | Effort | Description |
|-------|--------|-------------|
| 1. Architecture design | 1 day | Pipeline pause/resume design, session state model, API contract |
| 2. Backend (HTTP server + API) | 2 days | FastAPI server, 3 endpoints, auth middleware, session management |
| 3. Frontend (HTML/CSS/JS) | 2-3 days | Segment editor, audio player, save/generate buttons |
| 4. Pipeline integration | 1 day | Pause/resume, transcript serialization, temp directory management |
| 5. Config + tests | 1 day | WebConfig, integration tests, frontend smoke tests |
| **Total** | **7-8 days** | |

### Dependencies

| Dependency | Type | Status |
|------------|------|--------|
| FastAPI or aiohttp-web | External library | Not installed |
| uvicorn (ASGI server) | External library | Not installed |
| Frontend build tooling (optional) | Development tool | Not available |
| Pipeline v2 with async pause/resume | Internal prerequisite | Not designed |
| Network exposure (port forwarding/tunnel) | Infrastructure | Not configured |

---

## 7. Risks and Mitigations

### If We Proceed (Option A)

| # | Risk | Impact | Probability | Mitigation |
|---|------|--------|-------------|------------|
| R1 | Pipeline architecture becomes significantly more complex | High | Certain | Accept increased complexity; document thoroughly |
| R2 | Web UI is inaccessible in typical Docker/WSL2 deployment | High | High | Document network requirements; provide ngrok integration |
| R3 | Edit sessions left open indefinitely consume resources | Medium | Medium | Session timeout (e.g., 30 min) with automatic cleanup |
| R4 | Users rarely use the correction UI, making the investment wasted | Medium | Medium-High | Make it strictly optional; measure usage |
| R5 | Frontend code introduces new bug surface not covered by pytest | Medium | Medium | Basic Playwright/Selenium tests; or accept manual QA |
| R6 | Security vulnerability in web server (auth bypass, path traversal) | High | Low | Security review; limit to localhost; use established auth patterns |

### If We Defer (Recommended)

| # | Risk | Impact | Probability | Mitigation |
|---|------|--------|-------------|------------|
| R1 | Users with frequent misrecognition issues remain frustrated | Low-Medium | Medium | Implement Dictionary/Glossary feature (Option B) to address 80% of cases |
| R2 | Feature requested again in the future | Low | Medium | This report serves as a design reference; re-evaluate when pipeline v2 is designed |

---

## 8. Strategic Assessment

### Why DEFER (Not NO-GO)

This is not a permanent rejection. The feature has genuine value for power users who need high-accuracy minutes. However, the current timing and architecture are wrong:

1. **Architecture prerequisite**: The linear fire-and-forget pipeline must evolve to support pause/resume before any human-in-the-loop feature makes sense. This is a significant design effort that should be driven by multiple use cases, not just transcript correction.

2. **Priority mismatch**: At Ext-7 (lowest extension priority), this feature has the highest implementation cost (XL) and the lowest urgency. Other features in the backlog deliver more value per effort.

3. **Better alternative exists**: A Dictionary/Glossary feature (not currently in the REQUEST backlog) would address the most common correction need (recurring misrecognitions) with minimal architectural change.

4. **Technology surface expansion**: The project is a focused Python Discord bot. Adding a Web frontend doubles the technology surface and maintenance burden. This should be a deliberate strategic decision, not an incremental feature addition.

### Re-evaluation Triggers

Consider re-evaluating this feature when:

- The pipeline is redesigned to support async pause/resume (e.g., for a human-approval workflow)
- The bot is deployed with a web-accessible endpoint (e.g., a dashboard for configuration)
- Users express strong demand for per-meeting correction beyond what a dictionary provides
- The project gains frontend development capacity

---

## 9. Next Steps

Based on the DEFER recommendation:

1. **Do not implement the Web UI at this time**
2. **Consider creating a new REQUEST.md** for a "Transcript Dictionary/Glossary" feature (Option B) that addresses the core pain point with minimal complexity:
   - `/minutes dictionary-add <wrong> <correct>` slash command
   - Per-guild persistent dictionary in `guild_settings.json`
   - Automatic term replacement applied between merge and generation stages
   - Estimated effort: Size S (1-2 days)
3. **Archive this research** as a design reference for future pipeline v2 planning
4. **Track the Dictionary feature** as a separate Ext item with higher priority than Ext-7

---

## Appendix A: Discord UI Constraints Reference

| Component | Limit | Relevance |
|-----------|-------|-----------|
| Modal TextInput | 4000 chars per field, 5 fields max | Too small for full transcript editing |
| Modal response | 3-second timeout for initial response | Must defer/acknowledge immediately |
| Button rows | 5 buttons per row, 5 rows max (25 total) | Sufficient for navigation controls |
| Select Menu | 25 options max | Could show top-25 segments for selection |
| Embed | 6000 chars total, 25 fields | Sufficient for displaying transcript excerpts |
| File attachment | 25 MB max | Sufficient for transcript and audio files |
| Interaction timeout | 15 minutes for component interactions | Limits the edit window in Discord-native approach |

## Appendix B: Alternative Feature Comparison

| Criterion | Web UI (Option A) | Dictionary (Option B) | Discord Modal (Option C) |
|-----------|-------------------|----------------------|-------------------------|
| Addresses misrecognized terms | Yes (manual per-meeting) | Yes (automatic per-term) | Partially (char limit) |
| Addresses speaker name errors | Yes | Partially (name mapping) | No |
| Addresses segment timing errors | Yes | No | No |
| Requires new tech stack | Yes (HTTP + JS) | No | No |
| Works in Docker/WSL2 | Requires port exposure | Yes | Yes |
| Per-meeting effort | 10-30 min | 0 (after setup) | 5-10 min |
| Implementation effort | XL (5-8 days) | S (1-2 days) | M (2-3 days) |
| Maintenance burden | High | Minimal | Low |
| Architecture impact | High (pause/resume) | Minimal (string replace) | Low (button/modal) |
| Multi-guild support | Complex (sessions) | Simple (per-guild dict) | Simple (interaction scoped) |

## Appendix C: Codebase Integration Analysis

### Current Segment Data Model

```python
@dataclass(frozen=True)
class Segment:
    start: float    # seconds from recording start
    end: float      # seconds from recording end
    text: str       # transcribed text
    speaker: str    # speaker username from Craig ZIP filename
```

This model is already suitable for JSON serialization. No model changes needed for any option.

### Pipeline Insertion Point

```python
# In run_pipeline_from_tracks() -- pipeline.py lines 82-97

# Stage 2: Transcribe
segments = await _stage_transcribe(transcriber, tracks)

# [CORRECTION WOULD INSERT HERE]
# Option A: Persist segments, serve UI, wait for callback
# Option B: Apply dictionary replacements to segment.text
# Option C: Show Discord modal with transcript excerpt

# Stage 3: Merge transcript
transcript = merge_transcripts(segments, cfg.merger)
```

For Option B (Dictionary), the insertion is trivial:

```python
# Apply dictionary corrections (2 lines)
if dictionary:
    segments = apply_dictionary(segments, dictionary)
```

For Option A (Web UI), the insertion requires fundamental restructuring:

```python
# Persist segments to disk
session_id = persist_segments(segments, tracks)
# Notify user via Discord
await notify_correction_ready(output_channel, session_id)
# Wait for HTTP callback (indefinite, with timeout)
corrected_segments = await wait_for_correction(session_id, timeout=1800)
# Resume pipeline
segments = corrected_segments
```

This contrast illustrates why the Dictionary approach is strongly preferred from an engineering perspective.
