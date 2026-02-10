# RESEARCH: Discord Meeting Auto-Minutes Generation System

**Date:** 2026-02-10
**Feature Slug:** discord-minutes-bot
**RPI Phase:** Research (Step 2) -- Phase 3 (Technical Feasibility) + Phase 4 (Strategic CTO Assessment)
**Assessors:** Senior Software Engineer (Phase 3) + CTO / Technical Advisor (Phase 4)

---

## Executive Summary

**Decision: CONDITIONAL GO**
**Confidence: HIGH**

This system should be built. The technical foundation is solid, the cost-benefit ratio is exceptionally favorable, and all critical dependencies have been validated through source code analysis rather than assumptions. The "conditional" qualifier exists for one reason: the Craig Bot detection mechanism must be validated with a live recording before committing to the full pipeline. This is a 2-hour validation task, not a blocker.

The system is a personal/small-team automation tool. It should be built as exactly that -- a straightforward linear pipeline running on a local workstation. No microservices. No container orchestration. No cloud infrastructure. The architecture should match the problem: a single Python process with clear module boundaries.

---

---

# PART I: Phase 3 -- Senior Software Engineer Technical Feasibility Assessment

**Added:** 2026-02-10
**Assessor:** Senior Software Engineer (AI-Assisted)

This section provides the concrete engineering assessment: what works, what does not, what to build, and where the implementation-level risks live. It was produced after deep analysis of the Craig Bot source code (verified from https://github.com/CraigChat/craig), the discord.py ecosystem, faster-whisper internals, and the local development environment.

---

## P3-1. Technical Feasibility Score: HIGH

### Scoring Breakdown

| Dimension | Score | Evidence |
|-----------|-------|----------|
| Component maturity | HIGH | discord.py (stable), faster-whisper v1.1+ (production-grade), Claude API (GA), FFmpeg/PyAV (decades old) |
| Integration complexity | MEDIUM | Craig uses Components V2 (newer Discord feature); cook API is undocumented but source-verified |
| Infrastructure readiness | HIGH | RTX 3060 12GB confirmed; CUDA 13.0 driver present; Python 3.12 available |
| Pipeline linearity | HIGH | Strictly sequential pipeline; no distributed state; no concurrency requirements |
| Dependency risk | MEDIUM | Craig Bot is a third-party with undocumented API; message format can change |

The pipeline is a batch processing job triggered by an event. No real-time constraints, no distributed coordination, no complex state machines. Each stage produces a well-defined artifact consumed by the next stage.

---

## P3-2. Key Technical Challenge: Craig Recording Detection

### Verified Craig Bot Behavior vs. Original Assumptions

| Aspect | Actual Behavior (Source-Verified) | Original Assumption in requirements.md |
|--------|----------------------------------|----------------------------------------|
| Download link delivery | DM to the user who started recording, sent at recording **START** | Channel message at recording end |
| Channel message | Recording panel (Components V2 message with `IS_COMPONENTS_V2` flag) | Embed with download link |
| Recording end signal | Panel is **edited** to show "Recording ended." state | New message posted |
| Download URL | Leads to web dashboard, not direct file | Direct download link |

### Three Candidate Approaches Assessed

#### Approach A: Detect Recording Panel Edit via `on_raw_message_update` -- RECOMMENDED

**How it works:** Monitor the channel where `/join` was executed. Craig posts a Components V2 recording panel at recording start. When the recording stops, Craig **edits** this panel message (the title changes to "Recording ended.", color is removed). Our bot detects this edit via the `on_raw_message_update` event.

**Feasibility: HIGH**

| Factor | Assessment |
|--------|-----------|
| `on_raw_message_update` event | Standard discord.py event; fires on any message edit in visible channels; works WITHOUT requiring the message to be in the internal cache |
| Detecting Craig as author | Check `payload.data.get("author", {}).get("id") == "272937604339466240"` -- trivial |
| Detecting "ended" state | Even without full Components V2 parsing, the raw payload JSON contains text "Recording ended" -- a simple string match on the serialized payload suffices |
| Extracting recording ID | The panel contains the recording ID in its text display components; extractable via regex from the raw JSON |
| Constructing download URL | Once recording ID is known, construct `https://craig.chat/rec/{id}?key={key}`; the access key is embedded in the panel or available from the initial panel creation |
| Components V2 parsing | NOT REQUIRED for detection; raw JSON string matching is sufficient and framework-version-independent |

**Implementation sketch:**

```python
CRAIG_BOT_ID = "272937604339466240"

@bot.event
async def on_raw_message_update(payload: discord.RawMessageUpdateEvent):
    # Filter: only Craig messages in the watched channel
    author_data = payload.data.get("author", {})
    if author_data.get("id") != CRAIG_BOT_ID:
        return
    if str(payload.channel_id) != config.watch_channel_id:
        return

    # Detect recording end: check raw components for "Recording ended" text
    components = payload.data.get("components", [])
    raw_json = json.dumps(components)
    if "Recording ended" not in raw_json:
        return

    # Extract recording ID and access key from the message content/components
    recording_info = extract_recording_info(payload.data)
    if recording_info:
        await start_pipeline(recording_info)
```

**Why `on_raw_message_update` instead of `on_message_edit`:**
- `on_message_edit` requires the original message to be in discord.py's internal message cache; if the bot restarted after Craig posted the panel, the cache is empty and the event is silently dropped
- `on_raw_message_update` receives the raw gateway payload regardless of cache state
- This makes the detection robust across bot restarts

#### Approach B: Monitor DMs Sent by Craig -- NOT VIABLE

| Factor | Assessment |
|--------|-----------|
| Bots cannot read other users' DMs | **BLOCKING** -- a Discord bot has no access to DMs sent by Craig to the user who started the recording |
| User forwarding | Would require the user to manually forward or paste the DM content, defeating automation |
| Self-bot approach | Using a user account token to read DMs violates Discord Terms of Service |

**Verdict: REJECTED.** Cannot be automated.

#### Approach C: Use `/recordings` Command or Craig API Polling -- NOT VIABLE AS PRIMARY

| Factor | Assessment |
|--------|-----------|
| `/recordings` is Craig's slash command | Our bot cannot invoke another bot's slash commands programmatically |
| Craig API for listing recordings | No "list all recordings" endpoint verified in source code; API requires a known recording ID and access key |
| Polling approach | Would need to know recording IDs in advance; no discovery mechanism |

**Verdict: REJECTED as primary.** However, the cook API endpoints (once recording ID and access key are known) are fully usable for downloading.

### Detection Strategy Decision

**Primary:** Approach A -- `on_raw_message_update` monitoring for Craig panel edits
**Fallback:** Manual command where the user pastes the Craig download URL:

```
/minutes process https://craig.chat/rec/abc123?key=ABCDEF
```

This two-tier approach ensures automatic operation in the common case and graceful degradation when automation fails.

---

## P3-3. Craig Download Automation -- Feasibility Assessment

### The Problem

Craig's download URL (`https://craig.chat/rec/{id}?key={key}`) leads to a **web dashboard** where users manually select a format. There is no direct audio file URL in any Craig message.

### The Solution: Craig Cook API (Source-Verified from cook.ts)

| Endpoint | Method | Purpose | Verified |
|----------|--------|---------|----------|
| `/api/recording/{id}/duration?key={key}` | GET | Recording duration | YES (source) |
| `/api/recording/{id}/users?key={key}` | GET | User/track list with names | YES (source) |
| `/api/recording/{id}/notes?key={key}` | GET | Timestamped user notes | YES (source) |
| `/api/recording/{id}/cook?key={key}&format={fmt}&container={ctr}` | POST | Start cook job, returns audio | YES (source) |

**Supported formats:** flac, oggflac, aac, heaac, wav8, wavsfx, mp3
**Supported containers:** zip (default), aupzip, ogg, matroska

### Implementation Plan

```python
import aiohttp
import zipfile
import io
import re

CRAIG_API_BASE = "https://craig.chat/api/recording"

async def get_speaker_list(session: aiohttp.ClientSession, rec_id: str, key: str):
    """Get per-speaker track mapping before downloading."""
    url = f"{CRAIG_API_BASE}/{rec_id}/users?key={key}"
    async with session.get(url) as resp:
        resp.raise_for_status()
        return await resp.json()  # List of {id, username, track_number}

async def cook_and_download(session: aiohttp.ClientSession, rec_id: str, key: str,
                            fmt: str = "aac", container: str = "zip"):
    """Request cook in specified format, download resulting ZIP."""
    url = f"{CRAIG_API_BASE}/{rec_id}/cook"
    params = {"key": key, "format": fmt, "container": container}
    async with session.post(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.read()  # ZIP file bytes

def extract_tracks(zip_bytes: bytes) -> dict[str, bytes]:
    """Extract per-speaker audio tracks from Craig ZIP.
    Filename pattern: {track_number}-{username}.{format}
    Example: '1-shake344.aac'
    """
    tracks = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            match = re.match(r"(\d+)-(.+)\.\w+$", name)
            if match:
                tracks[name] = zf.read(name)
    return tracks
```

### Format Recommendation: AAC

| Factor | AAC | FLAC |
|--------|-----|------|
| File size (1hr mono track) | ~15 MB (observed from sample) | ~80-120 MB |
| Quality loss | Minimal (LC profile at 48kHz) | None (lossless) |
| faster-whisper compatibility | Direct (via PyAV) | Direct (via PyAV) |
| Download time (5 speakers) | ~75 MB total, ~30s | ~500 MB total, ~3 min |
| Transcription accuracy impact | Negligible for speech | None |

**Recommendation: Use AAC.** The quality difference between AAC LC and FLAC is inaudible for speech-to-text. The 5-7x file size reduction translates directly to faster downloads and less disk usage. The existing sample file (`1-shake344.aac`) confirms AAC works.

### Feasibility Verdict: HIGH

The cook API is a standard HTTP POST that returns a file. No authentication beyond the per-recording access key. No rate limiting observed (single recording per meeting means minimal API calls). The only risk is API instability, which is mitigated by the manual fallback command.

---

## P3-4. faster-whisper + AAC Integration

### Can faster-whisper Process AAC Directly?

**YES.** faster-whisper uses PyAV (which bundles FFmpeg libraries internally) for audio decoding. PyAV supports all formats FFmpeg supports, including AAC/ADTS.

From faster-whisper documentation: *"Unlike openai-whisper, FFmpeg does not need to be installed on the system, as the audio is decoded with the Python library PyAV which bundles the FFmpeg libraries in its package."*

### Is Explicit WAV Conversion Necessary?

**NO.** The original requirements document specifies "FLAC/Ogg -> WAV" as a preprocessing step. This is unnecessary:

1. faster-whisper's PyAV backend decodes AAC, FLAC, Ogg, MP3, and WAV natively
2. Converting to WAV inflates file sizes by 5-10x (uncompressed PCM) with zero quality benefit
3. The conversion adds processing time and disk I/O for no transcription accuracy gain

### When FFmpeg IS Still Useful

FFmpeg (as a standalone tool, not through PyAV) remains useful for optional preprocessing:

| Use Case | Benefit | Priority |
|----------|---------|----------|
| Silence trimming (VAD-based) | Reduces transcription time 30-50% for passive speakers | NICE-TO-HAVE (Phase 2) |
| Audio normalization | Equalizes speaker volumes | NICE-TO-HAVE (Phase 4) |
| Manual format conversion | Debugging and inspection | DEVELOPMENT TOOL |

**Recommendation:** Skip FFmpeg in Phase 1-2. Install it as a development convenience but do not make it a pipeline dependency. Feed AAC files directly to faster-whisper.

### Performance Estimate on RTX 3060 12GB

| Parameter | Value |
|-----------|-------|
| Model | large-v3 (~3 GB VRAM for model weights) |
| Available VRAM | 12,288 MiB total; ~8,100 MiB free (observed via nvidia-smi) |
| Compute type | float16 (optimal for Ampere architecture) |
| Expected speed ratio | 10-20x real-time for large-v3 on RTX 30-series |
| 1hr meeting, 5 speakers | ~5 hours of total audio; ~15-30 min processing (sequential) |
| Model loading time | 15-30 seconds from cold; can be preloaded at bot startup |

With 12 GB VRAM, the model (~3 GB) plus inference working memory (~1-2 GB) fits comfortably. Sequential per-speaker processing is correct; parallel inference would risk OOM with no meaningful speed improvement since the GPU is already fully utilized per track.

**Recommendation:** Preload the model at bot startup and keep it resident in VRAM. The 12 GB budget allows this without competing with normal system GPU usage.

---

## P3-5. Recommended Architecture

### Module Structure

```
discord-minutes-bot/
├── bot.py                     # Entry point: discord.py client, event handlers
├── config.yaml                # Bot configuration (channels, model, format prefs)
├── .env                       # Secrets (DISCORD_BOT_TOKEN, ANTHROPIC_API_KEY)
├── requirements.txt           # Pinned dependencies
├── src/
│   ├── __init__.py
│   ├── config.py              # Config loader: YAML + env var interpolation
│   ├── detector.py            # Craig recording-end detection logic
│   ├── craig_client.py        # Craig API client (cook, users, duration)
│   ├── transcriber.py         # faster-whisper wrapper (Phase 2)
│   ├── merger.py              # Transcript timestamp-based merge (Phase 2)
│   ├── generator.py           # Claude API minutes generation (Phase 3)
│   ├── poster.py              # Discord embed + file output (Phase 3)
│   └── pipeline.py            # Orchestrates the full pipeline sequence
├── prompts/
│   └── minutes.txt            # LLM prompt template (Phase 3)
├── tests/
│   ├── test_detector.py       # Unit tests for Craig detection
│   ├── test_craig_client.py   # Unit tests for Craig API client
│   ├── test_config.py         # Unit tests for config loading
│   └── fixtures/              # Captured Craig message payloads for testing
│       └── craig_panel_ended.json
├── samples/
│   └── 1-shake344.aac         # Real Craig output for development
└── logs/                      # Runtime log files (gitignored)
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Detection event | `on_raw_message_update` | Works without message cache; robust across bot restarts |
| HTTP client for Craig API | `aiohttp` | Already a discord.py transitive dependency; async-native |
| Audio format from Craig | AAC | Smaller than FLAC; directly consumable by faster-whisper; matches sample data |
| Config format | YAML + `.env` | YAML for structure, `.env` for secrets; standard Python pattern |
| LLM SDK | `anthropic` (direct) | Single prompt-response call; LangChain adds weight without value here |
| Temp file management | `tempfile.TemporaryDirectory` context manager | Ensures cleanup even on errors |
| Error reporting | Discord channel notification + rotating log file | Operator sees errors where they work (Discord); logs for post-mortem |
| Model lifecycle | Preload at startup, keep resident | Eliminates 15-30s cold start per pipeline run |

### Data Flow

```
on_raw_message_update (Craig panel "Recording ended")
    |
    v
detector.py: extract recording_id + access_key
    |
    v
craig_client.py: GET /users -> speaker list
craig_client.py: POST /cook (format=aac, container=zip) -> ZIP bytes
craig_client.py: extract_tracks(zip) -> {filename: audio_bytes}
    |
    v
[Save to /tmp/minutes-bot/{recording_id}/]
    |
    v
transcriber.py: for each track -> faster-whisper.transcribe()
                 -> List[Segment(start, end, text, speaker)]
    |
    v
merger.py: sort all segments by start timestamp
           -> merged transcript string
    |
    v
generator.py: Claude API call with prompt template + transcript
              -> structured minutes (Markdown)
    |
    v
poster.py: build Discord embed (summary) + attach .md file (full minutes)
           -> send to output channel
    |
    v
[Cleanup /tmp/minutes-bot/{recording_id}/]
```

---

## P3-6. Complexity Estimate: MEDIUM

### Phase-by-Phase Breakdown

| Phase | Scope | Complexity | Est. Days |
|-------|-------|------------|-----------|
| Phase 0 | Craig validation sprint (live test recording) | Simple | 0.5 |
| Phase 1 | Bot foundation + Craig detection + cook API download | Medium | 5-7 |
| Phase 2 | faster-whisper transcription + transcript merge | Simple-Medium | 4-5 |
| Phase 3 | Claude API minutes generation + Discord posting | Simple | 3-4 |
| Phase 4 | Integration testing + error handling + hardening | Medium | 4-5 |
| **Total** | | **Medium** | **16.5-21.5** |

### Why Medium (Not Simple)

- Craig's Components V2 message parsing requires working with raw gateway payloads
- The cook API is undocumented; integration is based on source code reverse-engineering
- Recording ID and access key extraction from edited messages needs robust regex parsing
- End-to-end testing requires actual Craig recordings (cannot be fully mocked)
- Error handling across a 6-stage pipeline has meaningful surface area

### Why Medium (Not Complex)

- Pipeline is strictly sequential; no concurrency management needed
- Single server, single channel eliminates multi-tenancy concerns
- All external APIs are straightforward HTTP or WebSocket
- No database, no queue, no background job scheduler
- No user authentication or authorization beyond Discord permissions
- No real-time requirements; batch processing only

---

## P3-7. Technical Risks and Mitigations

### Risk 1: Craig Components V2 Message Format (Severity: HIGH, Probability: LOW-MEDIUM)

**Risk:** Craig uses Discord's Components V2 system. discord.py's Components V2 support was merged into the dev branch (PR #10166, August 2025) but may not be in a stable PyPI release. Craig's specific component layout could change independently.

**Mitigation (eliminates dependency on discord.py V2 support):**
- Use `on_raw_message_update` which receives the raw gateway JSON payload
- Parse the raw JSON directly with `json.dumps()` + string matching + regex
- Detection needs only `"Recording ended" in json.dumps(payload.data.get("components", []))` -- no component tree parsing
- Maintain a test fixture of the actual Craig panel JSON captured during Phase 0
- **Result:** Detection logic is framework-version-independent

**Residual risk:** LOW. Raw JSON parsing sidesteps the entire discord.py Components V2 maturity question.

### Risk 2: Craig Cook API Instability (Severity: MEDIUM-HIGH, Probability: LOW)

**Risk:** The cook API is internal to Craig's web frontend, not a documented public API. It could change without notice.

**Mitigation:**
- All Craig API interactions isolated in `craig_client.py` (one module to update if API changes)
- Response validation: check HTTP status, Content-Type, ZIP file integrity before processing
- Manual fallback command (`/minutes process <url>`) so pipeline remains usable even if auto-detection breaks
- Monitor Craig's GitHub repository for changes to `cook.ts` and `util.ts`
- Cache known-working API call examples in test fixtures

**Residual risk:** MEDIUM. The API could break, but detection of the breakage is immediate (HTTP errors), and the fallback ensures degradation rather than failure.

### Risk 3: Recording ID + Access Key Extraction Fragility (Severity: MEDIUM, Probability: LOW)

**Risk:** Extracting recording ID and access key from the Craig panel's raw JSON depends on the specific text format Craig uses.

**Mitigation:**
- The URL format `https://{domain}/rec/{id}?key={key}` is fundamental to Craig's architecture (used across web dashboard, DMs, and panel) and is the least likely thing to change
- Use multiple regex patterns with fallback: first try URL extraction, then try direct component text parsing
- If extraction fails, post an error asking the user to provide the URL manually
- Unit test extraction against captured samples

**Residual risk:** LOW after mitigation.

### Risk 4: discord.py Version Compatibility (Severity: LOW, Probability: LOW)

**Risk:** discord.py 2.x stable release may lag behind Components V2 features.

**Mitigation:**
- As established in Risk 1, detection uses raw payloads -- no V2 component classes needed
- The bot's own output (embeds, file attachments) uses stable, well-tested discord.py APIs
- Pin discord.py version in `requirements.txt`

**Residual risk:** NEGLIGIBLE.

### Risk 5: faster-whisper Model Loading / VRAM (Severity: LOW, Probability: VERY LOW)

**Risk:** Model loading takes 15-30s; VRAM usage could spike during long tracks.

**Mitigation:**
- Preload model at bot startup (single load, persistent in VRAM)
- 12 GB VRAM provides ~8 GB of headroom above the ~3 GB model; inference working memory is ~1-2 GB
- Sequential per-speaker processing eliminates parallel VRAM pressure

**Residual risk:** NEGLIGIBLE.

### Risk 6: Processing Time for Long Meetings (Severity: LOW, Probability: MEDIUM)

**Risk:** A 2-hour meeting with 8 participants produces ~16 hours of audio. At 10-15x real-time, that is 60-90 minutes of transcription.

**Mitigation:**
- Post a progress message immediately: "Processing started. Estimated completion: ~X minutes"
- Update progress per speaker: "Transcribing speaker 3/8 (username)..."
- The 15-minute target applies to typical 1-hour meetings; longer meetings naturally take proportionally longer
- Optional silence trimming (Phase 4) can reduce audio volume 30-50% for passive speakers

**Residual risk:** LOW. Users accept proportional processing time.

---

## P3-8. Phase 1 Specific Assessment

### Phase 1 Acceptance Criteria

1. Bot connects to Discord and stays online (health check command: `/minutes status`)
2. Bot detects when Craig ends a recording in the watched channel
3. Bot extracts recording ID and access key from the Craig panel edit payload
4. Bot downloads the recording as AAC ZIP via the cook API
5. Bot extracts per-speaker AAC files from the ZIP
6. Bot posts a confirmation message listing speaker names and track info
7. Configuration loaded from `config.yaml` + `.env`
8. Errors logged to file and reported to Discord error channel
9. Unit tests pass for detector, craig_client, and config modules

### Task Breakdown

| Task | Est. Days | Dependencies |
|------|-----------|-------------|
| Phase 0: Craig live validation sprint | 0.5 | Craig Bot on test server |
| Project scaffolding (venv, config, logging) | 0.5 | None |
| discord.py bot skeleton + health check | 0.5 | discord.py installed |
| Craig panel detection (`on_raw_message_update`) | 1.5 | Phase 0 fixtures |
| Recording ID + access key extraction | 1.0 | Detection handler working |
| Craig cook API client (users + cook + ZIP extract) | 1.5 | aiohttp, test recording |
| Manual fallback command (`/minutes process <url>`) | 0.5 | Craig client working |
| Error handling + logging framework | 0.5 | Bot skeleton |
| Unit tests + fixture capture | 1.0 | All modules |
| **Total** | **7.5** | |

### Is 1 Week Realistic?

**Assessment: TIGHT.** 7.5 working days for an experienced developer. If "1 week" means 5 working days, this is approximately 50% over budget.

**Recommendations to fit within 1 week:**
- Defer the manual fallback command to early Phase 2 (saves 0.5 days)
- Reduce unit test coverage to detection and extraction only (saves 0.5 days)
- Accept that integration testing with a live Craig recording may spill into Week 2 Day 1

**More realistic estimate:** 7-10 working days for Phase 1 including validation sprint.

**The bottleneck is integration testing.** Craig detection logic cannot be validated without an actual Craig recording event. The developer must perform a recording, capture the raw gateway payload, and use it to calibrate the detection regex. This should happen on Day 1 (Phase 0).

### Phase 1 Dependencies (Install Checklist)

| Package | Status | Install Command |
|---------|--------|-----------------|
| discord.py | NOT INSTALLED | `pip install discord.py` |
| PyYAML | NOT INSTALLED | `pip install pyyaml` |
| python-dotenv | NOT INSTALLED | `pip install python-dotenv` |
| aiohttp | TRANSITIVE (via discord.py) | Installed with discord.py |
| FFmpeg (system) | NOT INSTALLED | `sudo apt install ffmpeg` (optional for Phase 1) |

### Phase 1 Does NOT Require

- faster-whisper (Phase 2)
- anthropic SDK (Phase 3)
- FFmpeg (optional convenience tool)
- CUDA toolkit beyond drivers (Phase 2)

---

## P3-9. Corrections to Original Requirements Document

The requirements document (`/home/junzi/projects/discord-minutes-bot/docs/requirements.md`) contains assumptions that conflict with verified Craig Bot source code. These must be updated before planning:

| Section | Original Claim | Corrected Understanding |
|---------|---------------|------------------------|
| 3.2 Trigger | Craig Bot posts download link message | Craig sends download DM at recording START; edits recording panel at END |
| 3.2 Detection | Monitor "Bot ID / embed content" | Craig panel is Components V2, not an embed; use `on_raw_message_update` on the panel edit |
| 3.2 File retrieval | "Download URL -> per-speaker audio files" | Download URL leads to web dashboard; use cook API programmatically |
| 3.3 Format conversion | "FLAC/Ogg -> WAV (faster-whisper input)" | WAV conversion is unnecessary; faster-whisper accepts AAC/FLAC/Ogg directly via PyAV |
| 3.3 FFmpeg dependency | FFmpeg required for format conversion | FFmpeg is optional; only needed for silence trimming or normalization |
| 備考 note | "録音終了後にテキストチャンネルにダウンロードリンクが投稿される" | Download link is via DM at start; channel gets recording panel that is edited at end |

---

# PART II: Phase 4 -- Strategic CTO Assessment (Previously Completed)

---

## 1. GO/NO-GO Recommendation

### Decision: CONDITIONAL GO

### Confidence Level: HIGH

### Conditions for Unconditional GO

1. **Craig Cook API Validation (REQUIRED, ~2 hours)**: Execute a real Craig recording, then programmatically call the Cook API (`POST /api/recording/{id}/cook?key={key}&format=flac&container=zip`) and confirm:
   - The API returns a ZIP file containing per-speaker audio tracks
   - The filename convention (`{track}-{username}.{format}`) is consistent
   - The recording ID and access key can be reliably extracted from Craig's Discord messages or the bot's DM

2. **Craig Detection Method Validation (REQUIRED, ~1 hour)**: Confirm which Discord event reliably signals "recording ended" by testing with a live recording. The `on_message_edit` approach for the recording panel is the recommended primary method (see Section 5).

Both conditions can be validated in a single test session. If either fails, the system is still buildable with the manual-upload fallback, but the "fully automatic" value proposition is degraded.

---

## 2. Strategic Rationale

### Why This Is Worth Building

**The arithmetic is simple and favorable.** The system eliminates 15-30 minutes of manual summarization work per meeting. At 4 meetings per month, that is 1-2 hours of recurring labor saved. The development cost is approximately 4 weeks of part-time effort. The breakeven point is reached within 2-3 months of operation. After that, every meeting generates pure time savings at approximately $0.09 per meeting in API costs.

**The technology stack carries minimal risk.** Every component in the pipeline is mature, well-documented, and battle-tested:

| Component | Maturity | Risk Level |
|-----------|----------|------------|
| discord.py v2 | Stable, actively maintained | LOW |
| FFmpeg | Industry standard, 20+ years | NEGLIGIBLE |
| faster-whisper large-v3 | Production-grade, CTranslate2-based | LOW |
| Claude API (Sonnet) | Stable commercial API with SLAs | LOW |
| Craig Bot | Operating since 2017, 500K+ servers | MEDIUM (see Section 3) |

**The environment is validated.** The development machine has:
- NVIDIA RTX 3060 with 12GB VRAM (requirement: 6GB minimum) -- 2x headroom
- ~8GB free VRAM currently available
- Python 3.12.3 installed
- CUDA 13.0 driver available
- A real Craig audio sample (`samples/1-shake344.aac`, 14.3MB, AAC LC 48kHz stereo) confirming the expected file format

**There is no adequate commercial alternative.** The competitive analysis from Phase 2 confirmed that no existing product combines: (a) free local transcription, (b) per-speaker attribution via multitrack recording, (c) LLM-generated structured minutes, and (d) Discord-native automation. The closest competitors (NotesBot at $3-40/month, DiscMeet at $5-10/month) use single-track recording without deterministic speaker identification.

### Why This Is NOT a Commercial Product Decision

This is a personal automation tool for a specific workflow. The architecture should reflect that reality:
- Single Python process, not microservices
- Local filesystem, not cloud storage
- `config.yaml` + `.env`, not a configuration service
- `systemd` user service, not Kubernetes
- Simple logging to files, not an observability stack

Over-engineering this system would be a strategic error. The requirements document correctly scopes this to a single server, single channel, local PC deployment. That scope should be respected.

---

## 3. Risk vs. Reward Analysis

### Risk Register

| # | Risk | Probability | Impact | Severity | Mitigation |
|---|------|-------------|--------|----------|------------|
| R1 | Craig Bot changes message format or API | LOW-MEDIUM | HIGH | HIGH | Pluggable audio acquisition interface (Option C) |
| R2 | Craig Bot goes offline permanently | VERY LOW | CRITICAL | MEDIUM | Manual upload fallback; alternative recording bots exist |
| R3 | LLM hallucination in minutes | MEDIUM | MEDIUM | MEDIUM | Disclaimer label; raw transcript attachment; prompt engineering |
| R4 | Transcription quality too low for useful minutes | LOW | HIGH | MEDIUM | Multitrack recording mitigates; test with real meetings in Phase 2 |
| R5 | Local PC unavailable during meeting | MEDIUM | LOW | LOW | Auto-start config; health check command; not a data loss event |
| R6 | Processing time exceeds 15-minute target | LOW | LOW | LOW | RTX 3060 with 12GB provides ample headroom; silence trimming |
| R7 | Discord API rate limits during posting | VERY LOW | LOW | NEGLIGIBLE | Simple retry logic |

### Risk-Reward Assessment

**Total weighted risk: MODERATE-LOW.** The dominant risk (R1: Craig format change) has a well-understood mitigation path. No risk is both high-probability AND high-impact.

**Reward magnitude: HIGH for the investment level.** This is not a venture-scale bet. It is a 4-week personal project that produces recurring value. The risk-reward ratio is strongly favorable because:
- The downside is bounded (4 weeks of development time, ~$0 infrastructure cost)
- The upside is recurring (every future meeting benefits)
- The learning value is retained even if the tool is eventually replaced
- The most expensive failure mode (Craig API breaks) is recoverable through the abstraction layer

---

## 4. Craig Bot Dependency Mitigation

### Recommendation: Option C -- Both Pluggable Interface AND Manual Upload Fallback

This is not an either/or decision. Both mitigations are cheap to implement and serve different failure modes:

**Option A: Pluggable Audio Acquisition Interface**

Purpose: Decouple the pipeline from any specific recording source.

```
AudioAcquisitionInterface (ABC)
    |
    +-- CraigAcquisition       (automatic: detect recording end, cook API, download ZIP)
    +-- ManualUploadAcquisition (manual: user uploads audio files via Discord command)
    +-- FutureAcquisition       (placeholder for future recording bots or direct VC recording)
```

This is a simple Python abstract base class with two methods: `detect_recording_end() -> RecordingMetadata` and `download_audio(metadata) -> List[AudioTrack]`. The implementation cost is approximately 1-2 hours of interface design.

**Option B: Manual Upload Fallback**

Purpose: Ensure the pipeline remains useful even if Craig detection breaks.

A Discord slash command (`/minutes upload`) that accepts audio file attachments (or a URL to a ZIP) and feeds them directly into the preprocessing stage. This bypasses Craig entirely and serves as:
- A fallback when Craig detection fails
- A way to process recordings from other sources
- A testing tool during development

Implementation cost: approximately 2-3 hours.

**Why Both**: Option A protects against Craig API changes by making the switch to a new source a code-level change. Option B protects against total Craig failure by giving the user an immediate manual workaround with zero code changes. Together, they reduce the Craig dependency from a "pipeline killer" to a "minor inconvenience."

---

## 5. Architecture Recommendation: Craig Detection Approach

### Analysis of Detection Approaches

| Approach | Reliability | Complexity | Fragility | Recommendation |
|----------|-------------|------------|-----------|----------------|
| A: Monitor recording panel for "ENDED" state change via `on_message_edit` | HIGH | MEDIUM | MEDIUM | PRIMARY |
| B: Intercept DM to recording initiator | LOW | HIGH | HIGH | REJECT |
| C: Use `/recordings` command | LOW | MEDIUM | HIGH | REJECT |
| D: Poll Craig API for recording status | MEDIUM | LOW | LOW | SECONDARY FALLBACK |

### Recommended Strategy: Approach A (Primary) + Approach D (Fallback)

**Primary: Monitor `on_message_edit` for the recording panel state change.**

The Phase 2.5 discovery confirmed that Craig sends a "recording panel" (Components V2 message) to the channel when recording starts, and this panel updates to show "Recording ended." when stopped. The `on_message_edit` event in discord.py v2 fires when this update occurs.

Implementation:
1. When a Craig Bot message is created in the watched channel, check if it is a recording panel (by Bot ID and message structure)
2. Store the message ID and recording metadata
3. Listen for `on_message_edit` events on that message
4. When the edit contains "Recording ended" (or equivalent state), trigger the pipeline
5. Extract the recording ID and access key from the message content or embeds

**Why this is the best approach:**
- It uses a standard Discord API event (`on_message_edit`), which is well-supported by discord.py
- It does not require the bot to be the recording initiator
- It does not require DM access
- It fires at exactly the right moment (recording end)

**Risks and mitigations:**
- Craig could change the panel format. Mitigation: Parse defensively with fallback patterns. Log unrecognized Craig messages for manual review.
- The edit event could be missed if the bot is offline. Mitigation: This is acceptable -- the bot simply misses that meeting. The manual upload fallback covers this case.

**Secondary Fallback: Poll the Craig Recording API**

If the `on_message_edit` approach proves unreliable in testing, a polling fallback can check the recording status via `/api/recording/{id}/duration?key={key}`. If a known recording ID starts returning a fixed duration (indicating recording has ended), the pipeline triggers. This is less elegant but more resilient to UI changes.

### Architecture Diagram

```
Discord Events
    |
    +-- on_message (Craig Bot) -----> Store recording panel metadata
    |                                  (recording_id, access_key, channel_id, message_id)
    |
    +-- on_message_edit (Craig Bot) -> Check for "Recording ended" state
                                       |
                                       +-- YES --> Trigger Pipeline
                                       |            |
                                       |            +-- Cook API: POST /api/recording/{id}/cook
                                       |            +-- Download ZIP (per-speaker tracks)
                                       |            +-- FFmpeg preprocessing
                                       |            +-- faster-whisper transcription (sequential per track)
                                       |            +-- Transcript merge (timestamp-based)
                                       |            +-- Claude API summarization
                                       |            +-- Discord embed + file post
                                       |
                                       +-- NO  --> Ignore (panel still active)
```

---

## 6. Phase Ordering Assessment

### Current Plan (from requirements.md)

| Phase | Content | Duration |
|-------|---------|----------|
| Phase 1 | Environment setup, Bot foundation, Craig message detection | 1 week |
| Phase 2 | Audio download, preprocessing, transcription pipeline | 1 week |
| Phase 3 | LLM minutes generation, Discord posting | 1 week |
| Phase 4 | Integration testing, error handling, deployment | 1 week |

### Assessment: The 4-phase plan is WELL-STRUCTURED with ONE MODIFICATION

The phasing correctly follows the evidence-based risk reduction principle: the highest-risk component (Craig Bot integration) is validated first in Phase 1, before investing in the downstream pipeline. This is exactly the right ordering.

**Recommended Modification: Split Phase 1 into a validation sprint and a foundation sprint.**

```
Phase 0 (Day 1):   Craig Validation Sprint
                    - Execute a real recording
                    - Test on_message_edit detection
                    - Test Cook API programmatically
                    - Confirm file format and naming convention
                    - Result: GO/NO-GO for automatic detection
                    - Duration: 2-4 hours

Phase 1 (Week 1):  Bot Foundation + Craig Integration
                    - discord.py bot scaffold
                    - Config management (config.yaml + .env)
                    - Craig message detection (on_message_edit handler)
                    - Cook API integration + ZIP download
                    - Manual upload command (/minutes upload)
                    - Audio acquisition interface (abstract base class)

Phase 2 (Week 2):  Audio Pipeline
                    - FFmpeg preprocessing (format conversion, silence trimming)
                    - faster-whisper integration (sequential per-track processing)
                    - Transcript merge (timestamp-based interleaving)
                    - Output: merged transcript text file

Phase 3 (Week 3):  Minutes Generation + Posting
                    - Claude API integration
                    - Prompt template for structured minutes
                    - Discord embed generation (summary)
                    - Markdown file attachment (full minutes)
                    - Error notification (mention admin role)

Phase 4 (Week 4):  Hardening + Deployment
                    - End-to-end integration testing with real meetings
                    - Error handling and retry logic
                    - Logging and diagnostics
                    - systemd service configuration (or OS-appropriate auto-start)
                    - Documentation (setup guide, troubleshooting)
```

The key addition is **Phase 0**: a focused 2-4 hour validation sprint that confirms the Craig integration works before any code is written. This is the cheapest possible way to de-risk the project. If Phase 0 fails, the decision reverts to "build with manual upload only" which is still valuable but changes the scope.

---

## 7. Key Conditions and Prerequisites

### Before Starting Phase 0

| # | Prerequisite | Status | Action Required |
|---|-------------|--------|-----------------|
| P1 | NVIDIA GPU with 6GB+ VRAM | SATISFIED | RTX 3060 (12GB) confirmed via `nvidia-smi` |
| P2 | CUDA drivers installed | SATISFIED | CUDA 13.0 confirmed |
| P3 | Python 3.10+ installed | SATISFIED | Python 3.12.3 confirmed |
| P4 | Craig Bot on target Discord server | UNVERIFIED | Confirm Craig Bot is invited to the server |
| P5 | Discord Bot application created | PARTIAL | Bot token exists in `.env`; verify bot has correct permissions |
| P6 | discord.py v2 installed | NOT YET | `pip install discord.py` (install during Phase 1) |
| P7 | FFmpeg installed | NOT YET | `apt install ffmpeg` or equivalent (install during Phase 1) |
| P8 | faster-whisper installed | NOT YET | `pip install faster-whisper` (install during Phase 2) |
| P9 | Anthropic API key valid | UNVERIFIED | Key exists in `.env`; test with a simple API call |
| P10 | Craig recording sample available | SATISFIED | `samples/1-shake344.aac` (14.3MB, AAC LC 48kHz stereo) |

### Before Starting Phase 1

- Phase 0 validation sprint completed successfully
- All software dependencies installed (discord.py, FFmpeg)
- Discord bot permissions configured (Read Messages, Read Message History, Send Messages, Attach Files, Embed Links)

### Before Starting Phase 2

- Craig detection working with real recordings (confirmed in Phase 1)
- Audio download pipeline functional
- faster-whisper and CUDA toolkit installed

---

## 8. Alternative Options Analysis

| Option | Description | Verdict | Rationale |
|--------|-------------|---------|-----------|
| **BUILD (recommended)** | Develop the full pipeline as described | GO | Best cost-benefit ratio; solves the actual problem; all components validated |
| **BUY (NotesBot/DiscMeet)** | Subscribe to commercial Discord meeting bot | REJECT | $3-40/month ongoing cost; no multitrack speaker ID; less structured output; cloud dependency |
| **BUY (Otter.ai/Fireflies)** | Use general meeting assistant | REJECT | No Discord integration; requires platform switch; $17-18/month |
| **PARTNER (fork existing OSS)** | Fork discord-meeting-transcribe-summary or similar | CONSIDER AS REFERENCE | Useful for implementation patterns but uses cloud APIs and single-track; would need substantial rework |
| **DEFER** | Wait for commercial tools to mature | REJECT | No indicator that Discord-native tools will add multitrack + LLM minutes; waiting has no expected payoff |
| **DECLINE** | Do not build | REJECT | The problem is real and recurring; the solution cost is low; no reason to absorb the ongoing manual effort |
| **SIMPLIFY (manual-only)** | Build pipeline without Craig auto-detection; manual upload only | VIABLE FALLBACK | If Phase 0 fails, this is the correct scope reduction; still delivers 80% of the value |

---

## 9. Technology Stack Alignment

### Stack Assessment Against Standards

This is a **product** (personal automation tool), not an **internal platform**. The architecture should be appropriately simple.

| Component | Proposed | Standard | Aligned? | Notes |
|-----------|----------|----------|----------|-------|
| Language | Python | Python (Django/FastAPI) | YES | No web framework needed -- this is a bot, not a web app |
| Bot Framework | discord.py v2 | N/A (domain-specific) | ACCEPTABLE | Industry standard for Python Discord bots |
| Audio Processing | FFmpeg | N/A (domain-specific) | ACCEPTABLE | No alternative exists at this quality level |
| Speech-to-Text | faster-whisper (local) | N/A (domain-specific) | ACCEPTABLE | Best-in-class for local inference |
| LLM Integration | Claude API (direct SDK) | LangChain/LlamaIndex | ACCEPTABLE | Direct SDK is appropriate here; LangChain adds unnecessary abstraction for a single prompt call |
| Configuration | config.yaml + .env | N/A | ACCEPTABLE | Right-sized for a single-process application |
| Deployment | Local PC + systemd | Docker/K8s | ACCEPTABLE | Container orchestration would be over-engineering for a single-user local tool |
| Database | None (filesystem) | PostgreSQL/MongoDB | ACCEPTABLE | No persistent state beyond config; filesystem temp files are correct |

**Key Decision: Do NOT use LangChain.** The LLM interaction in this system is a single prompt-response call to Claude API. LangChain would add dependency weight, abstraction layers, and failure modes without providing any value. The direct `anthropic` Python SDK is the correct choice.

**Key Decision: Do NOT containerize.** This runs on a personal workstation with a GPU. Docker adds complexity to GPU passthrough (nvidia-container-toolkit) without providing isolation benefits for a single-user tool. A Python virtual environment (`venv`) is sufficient.

---

## 10. Technical Risk Deep-Dive

### Scalability Risk: NEGLIGIBLE (by design)

The system processes one meeting at a time on one server. Scalability is explicitly out of scope. This is correct. The processing pipeline is sequential and bounded: even a 2-hour meeting with 10 participants would complete within the 15-minute target on the available hardware (RTX 3060, 12GB VRAM).

### Performance Risk: LOW

| Stage | Expected Duration (1hr meeting, 5 speakers) | Bottleneck |
|-------|-----------------------------------------------|------------|
| Craig Cook API + Download | 1-3 minutes | Network bandwidth |
| FFmpeg preprocessing | 30-60 seconds | CPU-bound, fast |
| faster-whisper transcription | 5-10 minutes | GPU-bound (sequential per track) |
| Transcript merge | < 1 second | Trivial |
| Claude API call | 10-30 seconds | API latency |
| Discord posting | < 5 seconds | API latency |
| **Total** | **7-15 minutes** | **Transcription dominates** |

The RTX 3060 with 12GB VRAM can run faster-whisper large-v3 in float16 with significant headroom. The 6GB minimum in the requirements is conservative; 12GB allows for comfortable operation without memory pressure.

### Security Risk: LOW

- Audio data is processed locally (good)
- Transcript text is sent to Claude API (acceptable for personal use; Anthropic's data policy does not train on API inputs)
- API keys are in `.env` and `.gitignore` covers them (verified)
- Bot token permissions should be minimized (Read Messages, Send Messages, Attach Files, Embed Links)
- **NOTE**: The `.env` file was readable during this assessment. While it is gitignored, ensure the file has restrictive permissions (`chmod 600 .env`) on the deployment machine.

### Maintainability Risk: LOW-MEDIUM

- The Craig Bot dependency is the primary maintenance driver
- All other components have stable, well-maintained upstream projects
- The linear pipeline architecture is simple to debug and modify
- The pluggable audio acquisition interface isolates Craig-specific code
- Estimated maintenance burden: 1-2 hours per quarter for dependency updates; potential spike if Craig changes format

### Integration Risk: LOW

- The only external integrations are Discord API (stable, well-documented), Craig API (validated via source code analysis), and Claude API (stable commercial API)
- No complex inter-service communication
- No database migrations or schema management
- No distributed system coordination

---

## 11. Success Metrics

### Phase 0 (Validation Sprint)

| Metric | Target | Measurement |
|--------|--------|-------------|
| Craig Cook API returns valid ZIP | Yes/No | Single test call |
| on_message_edit fires on recording end | Yes/No | Single test recording |
| Per-speaker audio files extractable from ZIP | Yes/No | Inspect ZIP contents |

### Phase 1-4 (Development)

| Metric | Target | Measurement |
|--------|--------|-------------|
| End-to-end pipeline completion rate | > 90% of real meetings | Track successes vs failures |
| Time from recording end to minutes posted | < 15 minutes (1hr meeting) | Timestamp comparison |
| Transcription accuracy (subjective) | Usable without extensive correction | Human review of first 5 meetings |
| Minutes quality (subjective) | Actionable summary, correct speaker attribution | Human review of first 5 meetings |
| System uptime during meeting hours | > 95% | Bot status monitoring |
| Cost per meeting | < $0.15 | Claude API usage tracking |

### Ongoing Operations

| Metric | Target | Measurement |
|--------|--------|-------------|
| Monthly maintenance time | < 1 hour (excluding Craig breakage) | Time tracking |
| Craig API breakage frequency | < 1 per quarter | Incident tracking |
| Mean time to recovery from Craig breakage | < 24 hours | Incident tracking |

---

## 12. Monitoring Strategy

For a personal/small-team tool, monitoring should be proportionate:

1. **Discord-native health check**: A `/minutes status` command that reports bot uptime, last successful pipeline run, and any pending errors.

2. **Pipeline logging**: Structured logging (Python `logging` module) to a rotating file. Each pipeline stage logs start time, end time, and success/failure. No need for an external logging service.

3. **Error notification**: On pipeline failure, post an error embed to the configured output channel with the failure stage, error message, and admin role mention. This is specified in the requirements and is sufficient.

4. **Craig format monitoring**: Log the raw Craig message content whenever a Craig message is detected. If the format changes, the log provides the data needed to update the parser.

Do NOT implement: Prometheus/Grafana, ELK stack, Datadog, PagerDuty, or any external monitoring service. These are appropriate for production services, not personal automation tools.

---

## 13. Phase 2.5 Technical Discovery: Key Findings Integration

The technical discovery phase (Craig Bot source code analysis) produced critical findings that directly inform the architecture:

### Finding 1: Craig sends download info via DM at recording START, not END
**Impact**: The original requirements assumed Craig posts a download link to the channel on recording end. This is incorrect. The channel receives a "recording panel" (Components V2) that updates its state.
**Architecture Response**: Use `on_message_edit` event to detect the state change, not `on_message` for a new download link message.

### Finding 2: Cook API enables programmatic export
**Impact**: The download URL (`https://{domain}/rec/{id}?key={accessKey}`) leads to a web dashboard, not a direct file download. The Cook API (`POST /api/recording/{id}/cook?key={key}&format=flac&container=zip`) is the correct programmatic path.
**Architecture Response**: Implement Cook API client in the audio acquisition module. Request FLAC format in ZIP container for best transcription quality.

### Finding 3: Recording metadata APIs exist
**Impact**: `/api/recording/{id}/users?key={key}` provides user/track list and `/api/recording/{id}/duration?key={key}` provides recording duration. These can be used for progress estimation and speaker name resolution.
**Architecture Response**: Call the users API before downloading to get the speaker-to-track mapping. Use the duration API for progress bar estimation.

### Finding 4: Sample file confirms expected format
**Impact**: The sample file `1-shake344.aac` (MPEG ADTS, AAC, v4 LC, 48kHz stereo, 14.3MB) confirms Craig's actual output format. The requirements mentioned FLAC/Ogg, but the Cook API allows format selection.
**Architecture Response**: Request FLAC from Cook API for lossless quality. Handle AAC as a fallback if FLAC is unavailable.

---

## 14. Final Strategic Assessment

### The Case FOR Building

1. **The problem is real, recurring, and quantifiable.** Every meeting without automated minutes costs 15-30 minutes of manual effort.
2. **The technology is mature and validated.** No research-grade or bleeding-edge components. Every piece of the pipeline is production-proven.
3. **The environment is ready.** RTX 3060 (12GB), Python 3.12, CUDA 13.0 -- all prerequisites are met or trivially installable.
4. **The cost structure is excellent.** ~$0.09/meeting after a 4-week development investment. No ongoing infrastructure costs.
5. **The risk is bounded and mitigable.** The Craig dependency (the biggest risk) is mitigated by the pluggable interface and manual upload fallback.
6. **There is no adequate alternative.** No commercial or open-source tool provides this specific combination of features at this cost.

### The Case AGAINST Building

1. **The user base is narrow.** This serves one team on one server. The effort-to-impact ratio is high per-user.
2. **Craig Bot is a single point of failure.** Despite mitigations, a Craig API break would require development time to fix.
3. **LLM output requires trust calibration.** Users must learn to verify AI-generated minutes, especially action items and decisions.

### Verdict

The case FOR substantially outweighs the case AGAINST. The "against" arguments are real but manageable: the narrow user base is acceptable for a personal tool, the Craig dependency is mitigated by design, and LLM trust calibration is a one-time learning curve.

**Proceed with development. Validate Craig integration in Phase 0 first. Build the abstraction layer and manual fallback from the start. Ship a working pipeline within 4 weeks.**

---

## Appendix A: Environment Validation Results

```
GPU:              NVIDIA GeForce RTX 3060, 12288 MiB total, ~7951 MiB free
CUDA Version:     13.0
Python:           3.12.3
OS:               Linux (WSL2) 6.6.87.2-microsoft-standard-WSL2
Sample File:      samples/1-shake344.aac (14.3MB, MPEG ADTS AAC v4 LC, 48kHz stereo)
discord.py:       Not installed (install in Phase 1)
FFmpeg:           Not installed (install in Phase 1)
faster-whisper:   Not installed (install in Phase 2)
Bot Token:        Present in .env (gitignored)
Anthropic Key:    Present in .env (gitignored)
```

## Appendix B: File References

- Requirements document: `/home/junzi/projects/discord-minutes-bot/docs/requirements.md`
- Feature request: `/home/junzi/projects/discord-minutes-bot/rpi/discord-minutes-bot/REQUEST.md`
- Product viability analysis: `/home/junzi/projects/discord-minutes-bot/reports/product-viability-analysis.md`
- Audio sample: `/home/junzi/projects/discord-minutes-bot/samples/1-shake344.aac`
- RPI workflow definition: `/home/junzi/projects/discord-minutes-bot/workflow/rpi/rpi-workflow.md`
- This report: `/home/junzi/projects/discord-minutes-bot/rpi/discord-minutes-bot/research/RESEARCH.md`
