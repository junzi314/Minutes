# PLAN: Discord Meeting Auto-Minutes Generation System

**Date:** 2026-02-10
**Feature Slug:** discord-minutes-bot
**RPI Phase:** Plan (Step 3)
**Research Decision:** CONDITIONAL GO (High Confidence)
**Condition:** Validate Craig detection + Cook API with live recording (Phase 0)

---

## Implementation Overview

| Phase | Name | Goal | Tasks | Duration | Complexity |
|-------|------|------|-------|----------|------------|
| 0 | Craig Validation Sprint | Confirm Craig detection and Cook API work with a live recording | 7 | ~2-4 hours | Low |
| 1 | Bot Foundation + Craig Integration | Bot connects, detects Craig recording end, downloads audio | 12 | 5-7 working days | Medium |
| 2 | Transcription Pipeline | Transcribe per-speaker audio and merge into chronological transcript | 7 | 4-5 working days | Medium |
| 3 | Minutes Generation + Discord Posting | Generate structured minutes via Claude API and post to Discord | 9 | 3-4 working days | Low-Medium |
| 4 | Integration Testing + Hardening | Production-ready error handling, logging, deployment | 10 | 4-5 working days | Medium |
| **Total** | | | **45** | **16.5-21.5 working days** | **Medium** |

### Complexity Distribution

| Complexity | Task Count | Percentage |
|------------|-----------|------------|
| Low | 14 | 31% |
| Medium | 22 | 49% |
| High | 9 | 20% |

---

## Phase 0: Craig Validation Sprint

**Goal:** Confirm Craig detection and Cook API work with a live recording before writing any code.

**Prerequisites:**
- Craig Bot invited to target Discord server
- Discord Bot application created with valid token in `.env`
- Access to a Discord voice channel for test recording

### Task List

| # | Task | Complexity | Files Created/Modified | Notes |
|---|------|-----------|----------------------|-------|
| 0.1 | Perform a real Craig recording on target Discord server | Low | -- | Execute `/join` in voice channel, speak briefly, then `/stop` |
| 0.2 | Capture raw MESSAGE_UPDATE gateway payload when recording panel edits to "Recording ended" | Medium | `tests/fixtures/craig_panel_ended.json` | Use Discord gateway logger or bot debug mode; save full raw JSON |
| 0.3 | Extract recording ID and access key from the captured payload | Low | -- | Document the extraction regex pattern that works against real data |
| 0.4 | Test Cook API with curl | Medium | -- | `curl -X POST "https://craig.chat/api/recording/{id}/cook?key={key}&format=aac&container=zip"` |
| 0.5 | Verify ZIP contains per-speaker AAC files with `{track}-{username}.aac` naming | Low | -- | Inspect ZIP contents; confirm naming convention matches `1-shake344.aac` pattern |
| 0.6 | Test Users API with curl | Low | -- | `curl "https://craig.chat/api/recording/{id}/users?key={key}"` ; save response structure |
| 0.7 | Document actual Craig domain and API behavior | Low | -- | Confirm whether `craig.chat` or `craig.horse` or another domain is the active API host |

### Task Dependency Chart

```
0.1 ──> 0.2 ──> 0.3
  │               │
  └──> 0.4 ──> 0.5
  │
  └──> 0.6

0.3 + 0.5 + 0.6 ──> 0.7
```

Tasks 0.4 and 0.6 can run in parallel once 0.1 completes. Task 0.2 requires observing the gateway event during 0.1. Task 0.7 is a documentation pass after all validation tasks complete.

### Success Criteria

- [ ] Real Craig recording completed on target server
- [ ] Raw gateway payload saved to `tests/fixtures/craig_panel_ended.json`
- [ ] Recording ID and access key successfully extracted from payload using regex
- [ ] Cook API returns HTTP 200 with a valid ZIP file
- [ ] ZIP contains per-speaker AAC files matching `{track}-{username}.aac` naming
- [ ] Users API returns JSON with user/track mapping
- [ ] Active Craig API domain documented

### GO/NO-GO Gate

| Outcome | Action |
|---------|--------|
| Cook API returns valid ZIP with per-speaker tracks | **GO** -- proceed to Phase 1 with full automatic pipeline scope |
| Cook API fails but gateway payload captured | **PARTIAL GO** -- proceed to Phase 1 with manual-upload-only scope; revisit Cook API later |
| Cook API fails AND gateway payload not capturable | **SCOPE REDUCTION** -- build manual-upload-only pipeline; defer automatic detection |
| Craig Bot unavailable on target server | **BLOCK** -- resolve Craig Bot access before proceeding |

---

## Phase 1: Bot Foundation + Craig Integration

**Goal:** Bot connects to Discord, detects Craig recording end, downloads audio via Cook API.

**Prerequisites:**
- Phase 0 completed with GO decision
- `tests/fixtures/craig_panel_ended.json` captured
- discord.py, PyYAML, python-dotenv installed (`pip install discord.py pyyaml python-dotenv`)

### Task List

| # | Task | Complexity | Files Created/Modified | Notes |
|---|------|-----------|----------------------|-------|
| 1.1 | Project scaffolding: venv, requirements.txt, .gitignore, directory structure | Low | `requirements.txt`, `.gitignore` (update), `src/__init__.py`, `tests/__init__.py`, `prompts/` (dir), `logs/` (dir) | Pin discord.py, pyyaml, python-dotenv versions |
| 1.2 | Exception hierarchy | Low | `src/errors.py` | `MinutesBotError` base + `DetectionError`, `DownloadError`, `CookError`, `ExtractionError`, `ConfigError` |
| 1.3 | Config dataclass + YAML/.env loader + validation | Medium | `src/config.py`, `config.yaml` | Dataclass with fields for discord, craig, whisper, llm, output sections; env var interpolation; validation of required fields |
| 1.4 | Default configuration file | Low | `config.yaml` | All settings from requirements.md Section 5 with sensible defaults |
| 1.5 | Discord client setup, logging bootstrap, startup sequence | Medium | `bot.py` | discord.py Client with intents (messages, message_content, guilds); structured logging to rotating file + console; model preload hook (placeholder for Phase 2) |
| 1.6 | `parse_recording_ended()` -- extract DetectedRecording from raw payload | High | `src/detector.py` | `DetectedRecording` dataclass (recording_id, access_key, channel_id, message_id, guild_id); JSON string match for "Recording ended"; regex extraction of recording ID and access key from components |
| 1.7 | Unit tests for detector using Phase 0 fixtures | Medium | `tests/test_detector.py` | Test: valid ended payload, non-Craig message, Craig message that is not "ended", malformed payload, missing fields |
| 1.8 | `AudioSource` abstract base class | Medium | `src/audio_source.py` | ABC with `async fetch_speakers(recording) -> list[Speaker]` and `async download(recording) -> list[AudioTrack]` methods; `Speaker` and `AudioTrack` dataclasses |
| 1.9 | `CraigClient` implementing AudioSource | High | `src/craig_client.py` | Users API call, Cook API call (format=aac, container=zip), ZIP download with timeout, `extract_tracks()` for ZIP parsing, URL construction, aiohttp session management |
| 1.10 | Unit tests for CraigClient | Medium | `tests/test_craig_client.py` | Test URL construction, ZIP extraction with mock data, HTTP error handling; integration test with aioresponses for Users API and Cook API |
| 1.11 | Register `on_raw_message_update` handler in bot.py | Medium | `bot.py` (modify) | Filter by Craig Bot ID + watched channel; delegate to `detector.parse_recording_ended()`; on success, log detection and trigger pipeline stub |
| 1.12 | End-to-end smoke test: detect real Craig recording end, download ZIP, list extracted files | High | -- | Manual test: start Craig recording, stop, verify bot detects, downloads, and extracts files; document results |

### Task Dependency Chart

```
1.1 ──> 1.2 ──> 1.3 ──> 1.5 ──> 1.11 ──> 1.12
         │       │
         │       └──> 1.4
         │
         └──> 1.6 ──> 1.7
         │
         └──> 1.8 ──> 1.9 ──> 1.10 ──> 1.12

1.6 + 1.8 can be parallelized (no dependency between them)
1.7 + 1.9 can be parallelized (test detector while building craig_client)
1.11 depends on 1.5 (bot.py) + 1.6 (detector)
1.12 depends on 1.11 + 1.9 (handler registered + craig_client working)
```

### Parallel Work Opportunities

| Group A (Detection) | Group B (Craig API) | Group C (Bot Infrastructure) |
|---------------------|--------------------|-----------------------------|
| 1.6 detector.py | 1.8 audio_source.py | 1.1 scaffolding |
| 1.7 test_detector.py | 1.9 craig_client.py | 1.2 errors.py |
| | 1.10 test_craig_client.py | 1.3 config.py |
| | | 1.4 config.yaml |
| | | 1.5 bot.py |

Group C must be done first (foundation). Groups A and B can proceed in parallel after 1.2 (errors.py) is complete.

### Success Criteria

- [ ] Bot connects to Discord and stays online for 10+ minutes without disconnecting
- [ ] `/minutes status` slash command responds with bot uptime and health info
- [ ] Bot detects Craig recording end in the watched channel via `on_raw_message_update`
- [ ] Bot extracts recording ID and access key from the Craig panel edit payload
- [ ] Bot downloads recording as AAC ZIP via Cook API
- [ ] Bot extracts per-speaker AAC files from the ZIP with correct speaker names
- [ ] Configuration loads from `config.yaml` with `.env` variable interpolation
- [ ] Errors logged to rotating file and reported to console
- [ ] Unit tests pass for `detector` module (5+ test cases)
- [ ] Unit tests pass for `craig_client` module (5+ test cases)
- [ ] End-to-end smoke test succeeds with a real Craig recording

### Dependencies

| Dependency | Type | Status |
|-----------|------|--------|
| Phase 0 complete (fixtures captured) | Internal | Required |
| discord.py >= 2.3 | PyPI | Install at phase start |
| PyYAML >= 6.0 | PyPI | Install at phase start |
| python-dotenv >= 1.0 | PyPI | Install at phase start |
| aiohttp (transitive via discord.py) | PyPI | Installed with discord.py |
| Discord Bot with MESSAGE_CONTENT intent | External | Verify at phase start |

---

## Phase 2: Transcription Pipeline

**Goal:** Transcribe per-speaker audio files and merge into a unified chronological transcript.

**Prerequisites:**
- Phase 1 complete (bot detects recordings and downloads audio)
- faster-whisper installed (`pip install faster-whisper`)
- CUDA working (verified via `nvidia-smi` and `python -c "import torch; print(torch.cuda.is_available())"`)
- `samples/1-shake344.aac` available for testing

### Task List

| # | Task | Complexity | Files Created/Modified | Notes |
|---|------|-----------|----------------------|-------|
| 2.1 | Install faster-whisper + verify CUDA/GPU access | Medium | `requirements.txt` (update) | Verify: `from faster_whisper import WhisperModel; model = WhisperModel("large-v3", device="cuda", compute_type="float16")` loads without error |
| 2.2 | `Transcriber` class -- model lifecycle management | High | `src/transcriber.py` | Load model at startup, keep resident in VRAM; `transcribe_file(path, speaker_name) -> list[Segment]` with Segment dataclass (start, end, text, speaker); `transcribe_all(tracks: list[AudioTrack]) -> list[Segment]` for sequential per-speaker processing; language="ja" default; configurable model/device/compute_type from config |
| 2.3 | GPU test using sample AAC file | Medium | `tests/test_transcriber.py` | Test: model loads in <30s, transcribes `samples/1-shake344.aac` producing non-empty segments with Japanese text, segments have valid timestamps; skip if no GPU available |
| 2.4 | `merge_transcripts()` -- pure function for transcript merging | Medium | `src/merger.py` | Sort all segments from all speakers by start timestamp; merge adjacent same-speaker segments within configurable gap threshold (default 1.0s); format output as `[HH:MM:SS] Speaker: text` lines; return merged transcript string |
| 2.5 | Unit tests for merger | Low | `tests/test_merger.py` | Test: two speakers interleaved, single speaker, empty input, gap merging threshold, timestamp formatting, segment ordering |
| 2.6 | Wire transcriber + merger into pipeline (partial) | Medium | `src/pipeline.py` | Pipeline stages: detect, download, transcribe, merge, log output; temp directory management with `tempfile.TemporaryDirectory`; progress logging per speaker; cleanup on success or failure |
| 2.7 | Test with real Craig recording: verify transcript quality and speaker attribution | High | -- | Manual test: full pipeline from Craig detection through transcript output; evaluate Japanese text quality and speaker label accuracy |

### Task Dependency Chart

```
2.1 ──> 2.2 ──> 2.3
                  │
                  └──> 2.6 ──> 2.7

2.4 ──> 2.5 ──> 2.6

2.2 and 2.4 can be parallelized (no dependency)
2.3 and 2.5 can be parallelized (test independently)
2.6 depends on both 2.2 (transcriber) and 2.4 (merger)
2.7 depends on 2.6 (pipeline wired up)
```

### Parallel Work Opportunities

| Group A (Transcription) | Group B (Merging) |
|------------------------|-------------------|
| 2.1 Install + verify CUDA | 2.4 merger.py |
| 2.2 transcriber.py | 2.5 test_merger.py |
| 2.3 test_transcriber.py | |

Groups A and B are independent. Task 2.6 merges both groups. Task 2.7 is the integration test.

### Success Criteria

- [ ] faster-whisper loads `large-v3` model in <30 seconds on RTX 3060
- [ ] Model uses GPU with float16 compute type (confirmed via VRAM usage)
- [ ] `samples/1-shake344.aac` transcribes successfully with Japanese text output
- [ ] Transcription produces segments with valid start/end timestamps
- [ ] Merged transcript shows correct chronological order across multiple speakers
- [ ] Merged transcript uses `[HH:MM:SS] Speaker: text` format
- [ ] Adjacent same-speaker segments within gap threshold are merged
- [ ] Pipeline produces merged transcript from a real Craig recording
- [ ] Unit tests pass for `transcriber` module (3+ test cases)
- [ ] Unit tests pass for `merger` module (5+ test cases)

### Dependencies

| Dependency | Type | Status |
|-----------|------|--------|
| Phase 1 complete (detection + download working) | Internal | Required |
| faster-whisper >= 1.0 | PyPI | Install at phase start |
| CUDA Toolkit + cuDNN | System | Verify at phase start |
| RTX 3060 with 12GB VRAM available | Hardware | Confirmed |
| `samples/1-shake344.aac` | Local file | Available |

---

## Phase 3: Minutes Generation + Discord Posting

**Goal:** Generate structured minutes via Claude API and post to Discord as an embed with a file attachment.

**Prerequisites:**
- Phase 2 complete (transcription pipeline produces merged transcript)
- anthropic SDK installed (`pip install anthropic`)
- Valid Anthropic API key in `.env`

### Task List

| # | Task | Complexity | Files Created/Modified | Notes |
|---|------|-----------|----------------------|-------|
| 3.1 | LLM prompt template with variable substitution | Medium | `prompts/minutes.txt` | Template variables: `{transcript}`, `{date}`, `{speakers}`, `{guild_name}`, `{channel_name}`; output structure matches requirements.md Section 3.6 (summary, agenda, discussion, decisions, action items, risks) |
| 3.2 | `MinutesGenerator` class -- prompt rendering + Anthropic API call | Medium | `src/generator.py` | Load template from file; render with variables; call `anthropic.Anthropic().messages.create()` with claude-sonnet model; extract text response; retry with exponential backoff (max 3 attempts); configurable model/max_tokens from config |
| 3.3 | Unit tests for generator | Medium | `tests/test_generator.py` | Mock Anthropic client; test prompt template rendering with all variables; test retry logic on API error; test response extraction; test empty transcript handling |
| 3.4 | `MinutesPoster` class -- embed formatting + Discord posting | Medium | `src/poster.py` | Build Discord embed (title, date, participants, summary, decisions); handle 4096-char embed description limit with truncation; attach full markdown as `.md` file; post error embed with admin mention on failure; status message updates ("Processing...", "Transcribing...", "Generating minutes...") |
| 3.5 | Unit tests for poster | Low | `tests/test_poster.py` | Test embed field population; test 4096-char chunking/truncation; test markdown file generation; test error embed formatting |
| 3.6 | Full pipeline orchestrator | High | `src/pipeline.py` (modify) | Complete pipeline: detect, download, transcribe, merge, generate, post; temp directory lifecycle; error boundary around each stage with stage-specific error reporting; cleanup on success or failure; logging at each stage |
| 3.7 | `/minutes process <url>` slash command (manual fallback) | Medium | `bot.py` (modify) | Accept Craig recording URL; parse recording ID and access key from URL; feed into pipeline starting at download stage; respond with progress updates |
| 3.8 | `/minutes status` slash command (health check) | Low | `bot.py` (modify) | Report: bot uptime, last successful pipeline run timestamp, model loaded status, GPU available, config summary |
| 3.9 | End-to-end test: full pipeline from Craig detection to Discord posting | High | -- | Manual test: real Craig recording, automatic detection, full pipeline, verify minutes quality and embed formatting |

### Task Dependency Chart

```
3.1 ──> 3.2 ──> 3.3
                  │
                  └──> 3.6 ──> 3.7 ──> 3.9

3.4 ──> 3.5 ──> 3.6

3.8 is independent (can be done anytime)

3.2 and 3.4 can be parallelized (generator and poster are independent)
3.3 and 3.5 can be parallelized (test independently)
3.6 merges generator + poster into pipeline
3.7 depends on 3.6 (pipeline complete)
3.9 depends on 3.6 + 3.7 (full pipeline + manual command)
```

### Parallel Work Opportunities

| Group A (Generation) | Group B (Posting) | Group C (Bot Commands) |
|---------------------|-------------------|----------------------|
| 3.1 prompt template | 3.4 poster.py | 3.8 /minutes status |
| 3.2 generator.py | 3.5 test_poster.py | |
| 3.3 test_generator.py | | |

Groups A and B are independent. Group C is fully independent. Task 3.6 merges A and B. Tasks 3.7 and 3.9 are sequential after 3.6.

### Success Criteria

- [ ] Prompt template renders correctly with all variable substitutions
- [ ] Claude API generates structured Japanese minutes from a real transcript
- [ ] Minutes contain all required sections: summary, agenda, discussion, decisions, action items, risks
- [ ] Minutes posted as Discord embed with title, date, participants, and summary
- [ ] Embed respects 4096-character description limit
- [ ] Full markdown file attached to the Discord message
- [ ] `/minutes process <url>` command accepts a Craig URL and runs the full pipeline
- [ ] `/minutes status` command reports bot health
- [ ] Error embed posted with admin mention when pipeline fails
- [ ] Unit tests pass for `generator` module (4+ test cases)
- [ ] Unit tests pass for `poster` module (4+ test cases)

### Dependencies

| Dependency | Type | Status |
|-----------|------|--------|
| Phase 2 complete (transcription pipeline working) | Internal | Required |
| anthropic >= 0.40 | PyPI | Install at phase start |
| Valid ANTHROPIC_API_KEY in `.env` | Secret | Verify at phase start |
| Claude claude-sonnet-4-5-20250929 model access | External | Verify with test API call |

---

## Phase 4: Integration Testing + Hardening

**Goal:** Production-ready error handling, logging, deployment configuration, and real-world validation.

**Prerequisites:**
- Phase 3 complete (full pipeline works end-to-end)
- Access to at least 3 real meeting recordings for testing

### Task List

| # | Task | Complexity | Files Created/Modified | Notes |
|---|------|-----------|----------------------|-------|
| 4.1 | Comprehensive retry logic for all external calls | Medium | `src/craig_client.py` (modify), `src/generator.py` (modify), `src/poster.py` (modify) | Exponential backoff: max 3 retries for Craig API, Anthropic API, Discord API; configurable retry count and base delay; distinguish retryable vs non-retryable errors |
| 4.2 | Progress feedback: status message updates during pipeline | Medium | `src/pipeline.py` (modify), `src/poster.py` (modify) | Edit a single Discord message with progress: "Downloading audio...", "Transcribing speaker 2/5 (username)...", "Generating minutes...", "Posting results..." |
| 4.3 | Error reporting: formatted error embeds with admin mention | Low | `src/poster.py` (modify) | Error embed with: failure stage, error message (sanitized), timestamp, admin role mention; log full stack trace to file |
| 4.4 | Logging: RotatingFileHandler, structured format, sensitive data masking | Medium | `bot.py` (modify), `src/config.py` (modify) | RotatingFileHandler (10MB per file, 5 backups); structured format: `{timestamp} [{level}] {module}: {message}`; mask API keys, bot tokens, access keys in log output |
| 4.5 | Integration tests: happy path with mocks, failure at each stage | High | `tests/test_pipeline.py` | Test: full pipeline with all external calls mocked; test failure at each stage (detection, download, transcription, generation, posting); verify error handling and cleanup at each failure point |
| 4.6 | Real-world testing: run against 3+ actual meetings | High | -- | Process 3+ real meetings; evaluate: transcript accuracy, minutes quality, speaker attribution, processing time, error handling; document issues found |
| 4.7 | Performance profiling: measure actual processing times per stage | Low | -- | Log timing for each stage; compare against 15-minute target for 1-hour meeting; identify bottlenecks; document baseline performance |
| 4.8 | Optional: FFmpeg silence trimming for long recordings | Medium | `src/transcriber.py` (modify) | Optional preprocessing step: detect silence >5s, trim to 0.5s; configurable enable/disable in config.yaml; measure time savings |
| 4.9 | systemd user service configuration | Low | `discord-minutes-bot.service` | ExecStart, WorkingDirectory, Restart=on-failure, RestartSec=10; install instructions for `systemctl --user` |
| 4.10 | README.md: setup guide, configuration reference, troubleshooting | Medium | `README.md` | Prerequisites, installation steps, configuration reference (all config.yaml fields), Discord bot setup, first-run checklist, troubleshooting guide, architecture overview |

### Task Dependency Chart

```
4.1 ──> 4.5
4.2 ──> 4.5
4.3 ──> 4.5
4.4 ──> 4.5

4.5 ──> 4.6 ──> 4.7

4.8 is independent (optional enhancement)
4.9 is independent (deployment config)
4.10 is independent (documentation)

4.1, 4.2, 4.3, 4.4 can all be parallelized (independent hardening tasks)
4.8, 4.9, 4.10 can be parallelized with each other and with 4.1-4.4
4.5 depends on 4.1-4.4 (integration tests need retry logic, progress, errors, logging)
4.6 depends on 4.5 (real-world testing after integration tests pass)
4.7 depends on 4.6 (profiling during real-world tests)
```

### Parallel Work Opportunities

| Group A (Hardening) | Group B (Testing) | Group C (Independent) |
|--------------------|-------------------|-----------------------|
| 4.1 Retry logic | 4.5 Integration tests | 4.8 Silence trimming |
| 4.2 Progress feedback | 4.6 Real-world testing | 4.9 systemd service |
| 4.3 Error reporting | 4.7 Performance profiling | 4.10 README.md |
| 4.4 Logging | | |

Group A tasks are independent of each other and can all be done in parallel. Group B is sequential. Group C is independent of everything else.

### Success Criteria

- [ ] All external API calls retry up to 3 times with exponential backoff
- [ ] Pipeline posts progress updates to Discord during processing
- [ ] Pipeline failures produce formatted error embeds with admin mention
- [ ] Logs rotate at 10MB with 5 backup files retained
- [ ] Sensitive data (API keys, tokens) masked in all log output
- [ ] Integration tests pass: happy path and failure at each pipeline stage
- [ ] Pipeline completes successfully for 3+ real meetings
- [ ] Processing time <15 minutes for a 1-hour meeting with 5 speakers
- [ ] Errors reported clearly to Discord with actionable information
- [ ] Bot auto-starts on system boot via systemd
- [ ] README.md provides complete setup and troubleshooting guide
- [ ] All unit and integration tests pass

### Dependencies

| Dependency | Type | Status |
|-----------|------|--------|
| Phase 3 complete (full pipeline working) | Internal | Required |
| 3+ real meeting recordings available | External | Schedule during phase |
| FFmpeg (for optional silence trimming) | System | Install if implementing 4.8 |

---

## Risk Checkpoints

### Phase Boundary Gates

Each phase boundary requires a GO/NO-GO decision before proceeding.

| Gate | Location | GO Criteria | NO-GO Action |
|------|----------|-------------|-------------|
| Gate 0 | After Phase 0 | Cook API returns valid ZIP; gateway payload captured | Reduce scope to manual-upload-only pipeline |
| Gate 1 | After Phase 1 | Bot detects Craig recording end; downloads and extracts audio; unit tests pass | Debug detection/download; do not proceed to transcription until audio pipeline works |
| Gate 2 | After Phase 2 | faster-whisper transcribes sample audio; merged transcript is readable; real recording produces usable output | Evaluate: model size downgrade, language settings, audio quality issues |
| Gate 3 | After Phase 3 | Claude API generates structured minutes; embed posts correctly; manual command works | Debug API integration; verify prompt template; check Discord permissions |
| Gate 4 | After Phase 4 | 3+ real meetings processed successfully; <15 min processing time; error handling verified | Address specific failures; extend hardening phase if needed |

### Risk Monitoring During Development

| Risk | Monitor | Trigger | Response |
|------|---------|---------|----------|
| Craig API format change | Test fixture comparison on each Phase 1 run | Payload structure differs from `craig_panel_ended.json` | Update detector regex; capture new fixture |
| VRAM exhaustion | `nvidia-smi` during transcription | VRAM usage >10GB | Reduce batch size; consider medium model |
| Transcription quality | Manual review of first 3 transcriptions | >30% of content is garbled or incorrect | Test language parameter; evaluate model alternatives; check audio quality |
| Claude API cost spike | Monitor Anthropic dashboard | >$1 per meeting | Reduce transcript length sent to API; summarize before sending |
| Discord rate limits | HTTP 429 responses in logs | Any 429 during posting | Increase retry delay; batch embed updates |

---

## Total Estimates

### Task Summary

| Phase | Low | Medium | High | Total Tasks | Duration |
|-------|-----|--------|------|------------|----------|
| Phase 0 | 5 | 2 | 0 | 7 | 2-4 hours |
| Phase 1 | 3 | 6 | 3 | 12 | 5-7 days |
| Phase 2 | 1 | 4 | 2 | 7 | 4-5 days |
| Phase 3 | 2 | 5 | 2 | 9 | 3-4 days |
| Phase 4 | 3 | 5 | 2 | 10 | 4-5 days |
| **Total** | **14** | **22** | **9** | **45** | **16.5-21.5 days** |

### Calendar Estimate

Assuming 5 working days per week with focused development time:

| Week | Phase | Milestone |
|------|-------|-----------|
| Week 1, Day 1 | Phase 0 | Craig validation sprint complete; GO/NO-GO decision made |
| Week 1, Days 2-5 | Phase 1 (start) | Scaffolding, config, errors, bot skeleton, detector |
| Week 2, Days 1-3 | Phase 1 (end) | Craig client, handler, smoke test |
| Week 2, Days 4-5 | Phase 2 (start) | faster-whisper install, transcriber class |
| Week 3, Days 1-3 | Phase 2 (end) | Merger, pipeline wiring, real recording test |
| Week 3, Days 4-5 | Phase 3 (start) | Prompt template, generator, poster |
| Week 4, Days 1-2 | Phase 3 (end) | Pipeline orchestrator, slash commands, e2e test |
| Week 4, Days 3-5 | Phase 4 (start) | Retry logic, progress feedback, logging, integration tests |
| Week 5, Days 1-3 | Phase 4 (end) | Real-world testing, profiling, systemd, README |

**Realistic total: 4-5 weeks** of focused part-time development.

---

## Validation Gates

### What Must Be True Before Each Phase

#### Before Phase 1

| # | Condition | Verification Method |
|---|-----------|-------------------|
| V1.1 | Phase 0 GO decision recorded | Written confirmation in sprint notes |
| V1.2 | `tests/fixtures/craig_panel_ended.json` exists and contains valid payload | File exists; JSON is parseable; contains "Recording ended" text |
| V1.3 | Craig API domain confirmed | Documented which domain (craig.chat / craig.horse) works |
| V1.4 | Cook API response format understood | ZIP structure documented; AAC files confirmed |
| V1.5 | discord.py installed in venv | `python -c "import discord; print(discord.__version__)"` succeeds |

#### Before Phase 2

| # | Condition | Verification Method |
|---|-----------|-------------------|
| V2.1 | Bot stays online for 10+ minutes | Observe bot status in Discord |
| V2.2 | Bot detects Craig recording end | Trigger real recording; verify bot log shows detection |
| V2.3 | Bot downloads AAC ZIP via Cook API | Verify extracted files in temp directory |
| V2.4 | Unit tests pass for detector and craig_client | `pytest tests/test_detector.py tests/test_craig_client.py` all green |
| V2.5 | faster-whisper installed with CUDA support | `python -c "from faster_whisper import WhisperModel"` succeeds |
| V2.6 | GPU accessible | `nvidia-smi` shows GPU; VRAM available >6GB |

#### Before Phase 3

| # | Condition | Verification Method |
|---|-----------|-------------------|
| V3.1 | faster-whisper loads large-v3 on GPU | Model loads in <30s; VRAM usage increases |
| V3.2 | Sample AAC transcribes with Japanese output | `samples/1-shake344.aac` produces Japanese text segments |
| V3.3 | Merged transcript format correct | Output matches `[HH:MM:SS] Speaker: text` format |
| V3.4 | Real Craig recording transcribes end-to-end | Full pipeline: detect, download, transcribe, merge, output text |
| V3.5 | anthropic SDK installed | `python -c "import anthropic"` succeeds |
| V3.6 | Anthropic API key valid | Test API call returns a response |

#### Before Phase 4

| # | Condition | Verification Method |
|---|-----------|-------------------|
| V4.1 | Claude API generates structured minutes | Minutes contain all required sections |
| V4.2 | Discord embed posts correctly | Embed visible in output channel with correct formatting |
| V4.3 | Markdown file attached | `.md` file downloadable from Discord message |
| V4.4 | `/minutes process <url>` works | Manual command processes a Craig URL end-to-end |
| V4.5 | `/minutes status` works | Command returns bot health information |
| V4.6 | All unit tests pass | `pytest` runs clean across all test files |

---

## File Creation Summary

### Complete list of files created across all phases

| Phase | File | Type | Description |
|-------|------|------|-------------|
| 0 | `tests/fixtures/craig_panel_ended.json` | Test fixture | Captured raw gateway payload for Craig "Recording ended" |
| 1 | `requirements.txt` | Config | Pinned Python dependencies |
| 1 | `.gitignore` (update) | Config | Add logs/, __pycache__/, .env, *.pyc patterns |
| 1 | `src/__init__.py` | Module | Package init |
| 1 | `tests/__init__.py` | Module | Test package init |
| 1 | `src/errors.py` | Source | Exception hierarchy |
| 1 | `src/config.py` | Source | Config dataclass + loader |
| 1 | `config.yaml` | Config | Default configuration |
| 1 | `bot.py` | Source | Discord client entry point |
| 1 | `src/detector.py` | Source | Craig recording-end detection |
| 1 | `tests/test_detector.py` | Test | Detector unit tests |
| 1 | `src/audio_source.py` | Source | AudioSource ABC |
| 1 | `src/craig_client.py` | Source | Craig API client |
| 1 | `tests/test_craig_client.py` | Test | Craig client unit tests |
| 2 | `src/transcriber.py` | Source | faster-whisper wrapper |
| 2 | `tests/test_transcriber.py` | Test | Transcriber tests (GPU) |
| 2 | `src/merger.py` | Source | Transcript merge logic |
| 2 | `tests/test_merger.py` | Test | Merger unit tests |
| 2 | `src/pipeline.py` | Source | Pipeline orchestrator (partial) |
| 3 | `prompts/minutes.txt` | Template | LLM prompt template |
| 3 | `src/generator.py` | Source | Claude API minutes generator |
| 3 | `tests/test_generator.py` | Test | Generator unit tests |
| 3 | `src/poster.py` | Source | Discord embed + file poster |
| 3 | `tests/test_poster.py` | Test | Poster unit tests |
| 3 | `src/pipeline.py` (modify) | Source | Full pipeline orchestrator |
| 3 | `bot.py` (modify) | Source | Add slash commands |
| 4 | `tests/test_pipeline.py` | Test | Integration tests |
| 4 | `discord-minutes-bot.service` | Config | systemd user service file |
| 4 | `README.md` | Docs | Setup guide and reference |

### Final Project Structure

```
discord-minutes-bot/
├── bot.py                          # Entry point: Discord client, event handlers, slash commands
├── config.yaml                     # Default configuration
├── .env                            # Secrets (DISCORD_BOT_TOKEN, ANTHROPIC_API_KEY)
├── requirements.txt                # Pinned dependencies
├── discord-minutes-bot.service     # systemd user service
├── README.md                       # Setup guide, config reference, troubleshooting
├── src/
│   ├── __init__.py
│   ├── errors.py                   # Exception hierarchy
│   ├── config.py                   # Config dataclass + YAML/.env loader
│   ├── detector.py                 # Craig recording-end detection
│   ├── audio_source.py             # AudioSource ABC
│   ├── craig_client.py             # Craig API client (Users + Cook + ZIP)
│   ├── transcriber.py              # faster-whisper wrapper
│   ├── merger.py                   # Transcript merge (timestamp-based)
│   ├── generator.py                # Claude API minutes generation
│   ├── poster.py                   # Discord embed + file posting
│   └── pipeline.py                 # Full pipeline orchestrator
├── prompts/
│   └── minutes.txt                 # LLM prompt template
├── tests/
│   ├── __init__.py
│   ├── fixtures/
│   │   └── craig_panel_ended.json  # Captured Craig gateway payload
│   ├── test_detector.py            # Detector unit tests
│   ├── test_craig_client.py        # Craig client unit tests
│   ├── test_transcriber.py         # Transcriber GPU tests
│   ├── test_merger.py              # Merger unit tests
│   ├── test_generator.py           # Generator unit tests
│   ├── test_poster.py              # Poster unit tests
│   └── test_pipeline.py            # Integration tests
├── samples/
│   └── 1-shake344.aac              # Real Craig output for development
├── logs/                           # Runtime logs (gitignored)
├── docs/
│   └── requirements.md             # Original requirements document
└── rpi/
    └── discord-minutes-bot/
        ├── REQUEST.md
        ├── research/
        │   └── RESEARCH.md
        └── plan/
            └── PLAN.md             # This file
```

---

## Appendix A: Key Technical Decisions (from Research)

These decisions were established during the research phase and inform the implementation plan.

| Decision | Choice | Rationale | Source |
|----------|--------|-----------|--------|
| Detection event | `on_raw_message_update` (not `on_message_edit`) | Works without message cache; robust across bot restarts | RESEARCH.md P3-2 |
| HTTP client | aiohttp | Transitive dependency of discord.py; async-native | RESEARCH.md P3-5 |
| Audio format | AAC (not FLAC) | 5-7x smaller; negligible quality difference for speech-to-text | RESEARCH.md P3-3 |
| WAV conversion | Skipped | faster-whisper decodes AAC directly via PyAV | RESEARCH.md P3-4 |
| Config format | YAML + `.env` | YAML for structure, `.env` for secrets | RESEARCH.md P3-5 |
| LLM SDK | anthropic (direct, not LangChain) | Single prompt-response call; LangChain adds weight without value | RESEARCH.md P3-5 |
| Temp file management | `tempfile.TemporaryDirectory` | Ensures cleanup even on errors | RESEARCH.md P3-5 |
| Model lifecycle | Preload at startup, keep resident in VRAM | Eliminates 15-30s cold start per pipeline run | RESEARCH.md P3-5 |
| Containerization | None (venv only) | GPU passthrough complexity; single-user tool | RESEARCH.md Section 9 |

## Appendix B: Requirements Corrections Applied

The original requirements document (`docs/requirements.md`) contains assumptions that conflict with verified Craig Bot behavior. This plan incorporates the corrected understanding from RESEARCH.md P3-9.

| Original Assumption | Corrected Understanding | Plan Impact |
|---------------------|------------------------|-------------|
| Craig posts download link to channel at recording end | Craig sends DM at recording start; edits recording panel at end | Detection uses `on_raw_message_update` on panel edit, not `on_message` for new message |
| Monitor "Bot ID / embed content" | Craig panel is Components V2, not an embed | Parse raw JSON payload, not embed fields |
| Download URL leads to audio files | Download URL leads to web dashboard | Use Cook API for programmatic download |
| FLAC/Ogg to WAV conversion required | faster-whisper accepts AAC/FLAC/Ogg directly via PyAV | No WAV conversion step; FFmpeg optional |
| FFmpeg required for format conversion | FFmpeg optional (only for silence trimming) | FFmpeg deferred to Phase 4 as optional enhancement |

## Appendix C: External References

- Craig Bot source code: https://github.com/CraigChat/craig
- Research report: `/home/junzi/projects/discord-minutes-bot/rpi/discord-minutes-bot/research/RESEARCH.md`
- Product viability analysis: `/home/junzi/projects/discord-minutes-bot/reports/product-viability-analysis.md`
- Requirements document: `/home/junzi/projects/discord-minutes-bot/docs/requirements.md`
- Feature request: `/home/junzi/projects/discord-minutes-bot/rpi/discord-minutes-bot/REQUEST.md`
