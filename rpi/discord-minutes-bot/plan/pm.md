# Product Requirements Document: Discord Meeting Auto-Minutes Generation System

**Version:** 1.0
**Date:** 2026-02-10
**Status:** Draft

---

## 1. Context and Why Now

Teams that hold regular meetings on Discord face a recurring, low-value task: manually writing meeting minutes. A typical 1-hour meeting requires 15-30 minutes of post-meeting summarization. For teams meeting weekly, that is 1-2 hours of labor per month spent on a task that can be fully automated.

Three technology shifts make this solvable now:

1. **Craig Bot multitrack recording** provides deterministic per-speaker audio tracks, eliminating the need for unreliable speaker diarization algorithms.
2. **faster-whisper large-v3** delivers Whisper-grade transcription accuracy locally on consumer GPUs at zero API cost.
3. **Claude API** produces structured, accurate meeting summaries from transcripts at approximately $0.09 per meeting.

No existing product combines free local transcription, multitrack speaker identification, LLM-structured minutes, and Discord-native automation. The closest commercial alternatives (NotesBot at $3-40/month, DiscMeet at $5-10/month) use single-track recording without deterministic speaker attribution and require ongoing subscriptions.

---

## 2. Users and Jobs to Be Done

### Primary Persona: Technical Team Lead

- Runs a small team (3-10 people) that meets on Discord weekly or biweekly
- Already uses Craig Bot for recording
- Has a workstation with an NVIDIA GPU (6GB+ VRAM)
- Comfortable running a Python bot locally
- Cost-sensitive; prefers self-hosted over SaaS subscriptions

**Job to be done:** "After my Discord meeting ends, I want structured meeting minutes posted to our channel without doing anything, so my team can immediately see decisions, action items, and who said what."

### Secondary Persona: Open-Source Community Organizer

- Runs community meetings on Discord for transparency
- Wants publicly posted, structured minutes for contributors who could not attend
- Has access to GPU-capable hardware (personal or contributor-donated)

**Job to be done:** "After our community call, I want minutes automatically posted so every contributor can see what was discussed and decided, even if they missed the call."

---

## 3. Success Metrics

### Leading Indicators

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| Pipeline completion rate | > 90% of meetings processed without manual intervention | Success/failure count per meeting |
| End-to-end processing time | < 15 minutes for a 1-hour meeting with 5 speakers | Timestamp delta: recording end to Discord post |
| Transcription usability | Speaker attribution correct for > 95% of utterances | Spot-check review of first 10 meetings |
| Minutes actionability | Decisions and action items accurately captured (subjective) | Human review of first 10 meetings |

### Lagging Indicators

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| Time saved per month | 1-2 hours (at 4 meetings/month) | Comparison to previous manual workflow |
| Cost per meeting | < $0.15 | Claude API usage tracking |
| Monthly maintenance effort | < 1 hour (excluding Craig breakage events) | Time tracking |
| System uptime during meeting hours | > 95% | Bot status monitoring |

---

## 4. User Stories and Acceptance Criteria

### US-1: Automatic Recording Detection

**As a** team lead, **I want** the bot to automatically detect when a Craig Bot recording ends in my channel, **so that** I do not need to trigger the minutes pipeline manually.

**Acceptance criteria:**
- The bot detects Craig Bot recording panel edits containing "Recording ended" via `on_raw_message_update`.
- The bot extracts recording ID and access key from the Craig panel payload.
- Detection works even if the bot was restarted after the recording panel was originally posted (no message cache dependency).
- False positives from other Craig panel edits (e.g., mid-recording updates) do not trigger the pipeline.

### US-2: Audio Download via Cook API

**As a** team lead, **I want** the bot to automatically download per-speaker audio tracks from Craig, **so that** I do not need to manually visit the Craig web dashboard.

**Acceptance criteria:**
- The bot calls the Craig Cook API (`POST /api/recording/{id}/cook`) requesting AAC format in a ZIP container.
- The bot calls the Users API (`GET /api/recording/{id}/users`) to get the speaker-to-track mapping.
- Per-speaker AAC files are extracted from the ZIP with speaker names resolved from filenames (pattern: `{track}-{username}.{format}`).
- Audio files are saved to a temporary directory that is cleaned up after processing.
- Download completes within 3 minutes for a typical 1-hour, 5-speaker meeting.

### US-3: Per-Speaker Transcription

**As a** team lead, **I want** each speaker's audio transcribed separately with timestamps, **so that** the final transcript correctly attributes every statement.

**Acceptance criteria:**
- Each speaker track is transcribed sequentially using faster-whisper large-v3 on the local GPU (float16, CUDA).
- Transcription output includes start timestamp, end timestamp, speaker name, and text for each segment.
- AAC files are processed directly by faster-whisper via PyAV without intermediate WAV conversion.
- The Whisper model is preloaded at bot startup and kept resident in VRAM.
- Total transcription time for 5 speakers x 1 hour each is under 10 minutes on an RTX 3060 (12GB).

### US-4: Timestamp-Merged Transcript

**As a** team lead, **I want** all speaker transcripts merged into a single chronological transcript, **so that** the conversation reads naturally.

**Acceptance criteria:**
- Segments from all speakers are sorted by start timestamp.
- Output format: `[HH:MM:SS] SpeakerName: text`
- Overlapping segments (multiple speakers talking at the same time) are ordered by start time, with ties broken by track number.

### US-5: LLM-Generated Structured Minutes

**As a** team lead, **I want** the merged transcript summarized into structured meeting minutes, **so that** I get actionable output without reading the full transcript.

**Acceptance criteria:**
- The merged transcript is sent to Claude API (Sonnet) with a configurable prompt template.
- Output is structured markdown containing: meeting metadata (date, participants, duration), summary (3-5 sentences), agenda/topics discussed, discussion details with speaker attribution, decisions with owners, action items with owners and deadlines, and concerns/risks.
- Claude API call includes retry logic (3 attempts, exponential backoff).
- Output is labeled as "AI-generated" to set appropriate trust expectations.

### US-6: Discord Posting

**As a** team member, **I want** the minutes posted to our Discord channel as both a summary embed and a detailed file, **so that** I can quickly scan the highlights or read the full minutes.

**Acceptance criteria:**
- A Discord embed is posted with: meeting date, participant list, duration, 3-5 line summary, and key decisions.
- The full markdown minutes are attached as a `.md` file.
- Optionally, the raw merged transcript is attached as a second file (configurable).
- The embed respects Discord's 4096-character description limit and 25-field limit.
- Output is posted to a configurable channel (may differ from the watched channel).

### US-7: Error Notification

**As a** team lead, **I want** to be notified in Discord when the pipeline fails, **so that** I can take manual action if needed.

**Acceptance criteria:**
- On pipeline failure at any stage, an error embed is posted to the output channel.
- The error embed includes: the failure stage name, error message, and a mention of the configured admin role.
- Errors are also written to a rotating log file for post-mortem analysis.

### US-8: Manual Fallback Command

**As a** team lead, **I want** a slash command to manually trigger the pipeline with a Craig URL, **so that** I can still get minutes when automatic detection fails.

**Acceptance criteria:**
- `/minutes process <craig_url>` accepts a Craig recording URL and triggers the full pipeline.
- The command validates the URL format before proceeding.
- This bypasses the detection stage but uses the same download, transcription, generation, and posting stages.

---

## 5. Functional Requirements Summary

### Stage 1: Recording Detection

| ID | Requirement | Priority |
|----|------------|----------|
| FR-1.1 | Detect Craig Bot recording panel edits via `on_raw_message_update` event | P0 |
| FR-1.2 | Extract recording ID and access key from raw JSON payload | P0 |
| FR-1.3 | Filter events to only the configured watch channel and Craig Bot ID (`272937604339466240`) | P0 |
| FR-1.4 | Provide `/minutes process <url>` manual fallback command | P1 |
| FR-1.5 | Provide `/minutes status` health check command | P1 |

### Stage 2: Audio Acquisition

| ID | Requirement | Priority |
|----|------------|----------|
| FR-2.1 | Call Craig Users API to get speaker-to-track mapping | P0 |
| FR-2.2 | Call Craig Cook API to request AAC ZIP download | P0 |
| FR-2.3 | Extract per-speaker audio files from ZIP | P0 |
| FR-2.4 | Save files to temporary directory with automatic cleanup | P0 |
| FR-2.5 | Post progress message to Discord ("Processing started, estimated completion: ~X min") | P1 |

### Stage 3: Transcription

| ID | Requirement | Priority |
|----|------------|----------|
| FR-3.1 | Transcribe each speaker track sequentially with faster-whisper large-v3 | P0 |
| FR-3.2 | Use CUDA float16 inference; configurable language (default: `ja`) | P0 |
| FR-3.3 | Preload Whisper model at bot startup; keep resident in VRAM | P0 |
| FR-3.4 | Produce timestamped segments per speaker | P0 |
| FR-3.5 | Update Discord progress per speaker ("Transcribing speaker 3/5...") | P2 |

### Stage 4: Transcript Merge

| ID | Requirement | Priority |
|----|------------|----------|
| FR-4.1 | Merge all speaker segments into chronological order by start timestamp | P0 |
| FR-4.2 | Output format: `[HH:MM:SS] Speaker: text` | P0 |

### Stage 5: Minutes Generation

| ID | Requirement | Priority |
|----|------------|----------|
| FR-5.1 | Send merged transcript to Claude API with configurable prompt template | P0 |
| FR-5.2 | Output structured markdown (metadata, summary, agenda, discussions, decisions, action items, risks) | P0 |
| FR-5.3 | Retry on API failure (3 attempts, exponential backoff) | P0 |

### Stage 6: Discord Output

| ID | Requirement | Priority |
|----|------------|----------|
| FR-6.1 | Post summary as Discord embed with configurable color | P0 |
| FR-6.2 | Attach full minutes as `.md` file | P0 |
| FR-6.3 | Optionally attach raw transcript (configurable) | P2 |
| FR-6.4 | Post error embed with admin role mention on pipeline failure | P0 |

### Configuration

| ID | Requirement | Priority |
|----|------------|----------|
| FR-7.1 | Load settings from `config.yaml` (channels, model, format) + `.env` (secrets) | P0 |
| FR-7.2 | Configurable: watch channel, output channel, Whisper model/language, LLM model, prompt template | P0 |

---

## 6. Non-Functional Requirements

### Performance

| Requirement | Target | Rationale |
|-------------|--------|-----------|
| End-to-end latency (1hr meeting, 5 speakers) | < 15 minutes | Users expect minutes within a reasonable window after meeting end |
| Transcription throughput | 10-20x real-time on RTX 3060 | Validated estimate for large-v3 float16 on Ampere GPU |
| Model cold start | < 30 seconds | Preloading at startup eliminates this from pipeline latency |
| Concurrent pipelines | 1 (single meeting at a time) | Single server/channel scope; no parallelism needed |

### Reliability

| Requirement | Target | Rationale |
|-------------|--------|-----------|
| Pipeline completion rate | > 90% | Craig API or network issues may cause occasional failures |
| API retry policy | 3 attempts, exponential backoff | Standard resilience for HTTP API calls |
| Temporary file cleanup | Guaranteed even on error | Use `tempfile.TemporaryDirectory` context manager |
| Log retention | Rotating file, 7-day retention | Sufficient for debugging without unbounded disk growth |

### Security and Privacy

| Requirement | Implementation |
|-------------|---------------|
| Secret management | API keys and bot token in `.env` file, excluded from version control via `.gitignore` |
| File permissions | `.env` restricted to owner (`chmod 600`) |
| Audio data lifecycle | Deleted from local disk immediately after pipeline completion |
| Data sent externally | Only the merged transcript text is sent to Claude API; audio never leaves the local machine |
| Bot permissions | Minimum required: Read Messages, Read Message History, Send Messages, Attach Files, Embed Links |
| Recording consent | Bot should post a notice when a recording is detected (participants should be aware of AI processing) |

### Observability

| Requirement | Implementation |
|-------------|---------------|
| Structured logging | Python `logging` module with rotating file handler |
| Per-stage timing | Log start/end time and duration for each pipeline stage |
| Craig payload logging | Log raw Craig message content for debugging format changes |
| Health check | `/minutes status` command reports uptime, last run, and pending errors |
| Error alerting | Discord embed with admin role mention on any pipeline failure |

### SLOs (Internal Targets)

| SLO | Target | Measurement Window |
|-----|--------|--------------------|
| Pipeline success rate | 90% | Rolling 30 days |
| Processing time (1hr meeting) | p95 < 15 minutes | Rolling 30 days |
| Bot availability during meeting hours | 95% | Rolling 30 days |
| Cost per meeting | < $0.15 | Per-meeting |

---

## 7. Cost Analysis

### Per-Meeting Cost (1-hour meeting, 5 speakers)

| Component | Cost | Notes |
|-----------|------|-------|
| Craig Bot recording | $0.00 | Free tier |
| faster-whisper transcription | $0.00 | Local GPU inference |
| Claude API (Sonnet) | ~$0.09 | ~30K input tokens (transcript) + ~2K output tokens (minutes) |
| Electricity (GPU inference ~10 min) | ~$0.01 | RTX 3060 at 170W, $0.12/kWh |
| Discord API | $0.00 | Free |
| **Total per meeting** | **~$0.10** | |

### Monthly Cost (4 meetings/month)

| Component | Monthly Cost |
|-----------|-------------|
| Claude API | ~$0.36 |
| Electricity | ~$0.04 |
| Infrastructure | $0.00 (local PC) |
| **Total monthly** | **~$0.40** |

### Comparison to Alternatives

| Solution | Monthly Cost | Speaker ID | Local Processing |
|----------|-------------|------------|------------------|
| This system | ~$0.40 | Deterministic (multitrack) | Yes |
| NotesBot | $3-40 | None (single-track) | No |
| DiscMeet | $5-10 | None (single-track) | No |
| Otter.ai | $17+ | Probabilistic (diarization) | No |

---

## 8. Out of Scope (v1)

The following are explicitly excluded from the initial release:

- **Multi-server or multi-channel support.** The bot watches one channel on one server.
- **Real-time transcription.** Minutes are generated after the meeting ends, not during.
- **Web UI or dashboard.** All interaction is through Discord commands and channel posts.
- **Database or persistent storage.** No meeting history, search, or analytics beyond log files.
- **Cloud deployment.** Runs on a local PC with a GPU. No Docker, no Kubernetes, no VPS.
- **Non-Craig recording sources.** The pluggable audio acquisition interface is designed for future extensibility, but only the Craig implementation ships in v1.
- **Automatic calendar integration.** No Google Calendar, Outlook, or scheduling system integration.
- **Transcript editing UI.** No way to correct transcription errors before minutes generation.
- **Multi-language detection.** Language is configured statically (default: Japanese). No per-meeting or per-speaker language switching.
- **Speaker voice enrollment or training.** Speaker identity comes from Craig track filenames, not voice recognition.
- **Meeting recording.** Craig Bot handles recording. This system does not join voice channels or capture audio.

---

## 9. Rollout Plan

### Phase 0: Craig Validation Sprint (Day 1, 2-4 hours)

**Goal:** Confirm the two highest-risk integration points before writing any pipeline code.

| Validation | Method | Go/No-Go Criterion |
|------------|--------|---------------------|
| Craig panel detection | Execute a real recording; capture the `on_raw_message_update` payload | "Recording ended" string is present and parseable |
| Cook API download | Call `POST /api/recording/{id}/cook?key={key}&format=aac&container=zip` | Returns a valid ZIP with per-speaker AAC files |
| Recording ID extraction | Parse the panel payload for recording ID and access key | Both values extractable via regex from raw JSON |

If Phase 0 fails: fall back to manual-upload-only scope (still delivers 80% of value).

### Phase 1: Bot Foundation + Craig Integration (Week 1)

- discord.py bot scaffold with event handlers
- Configuration management (`config.yaml` + `.env`)
- Craig panel detection via `on_raw_message_update`
- Craig Cook API client (users, cook, ZIP extraction)
- Manual fallback command (`/minutes process <url>`)
- Error handling framework and logging
- Unit tests for detection and Craig client modules

**Exit criteria:** Bot detects a real Craig recording end and downloads per-speaker audio files automatically.

### Phase 2: Audio Pipeline (Week 2)

- faster-whisper integration with model preloading
- Per-speaker sequential transcription (CUDA float16)
- Timestamp-based transcript merge
- Optional silence trimming via FFmpeg (if processing time exceeds targets)

**Exit criteria:** Bot produces a merged, speaker-attributed transcript from a real Craig recording.

### Phase 3: Minutes Generation + Posting (Week 3)

- Claude API integration with prompt template
- Structured minutes generation
- Discord embed builder (summary post)
- Markdown file attachment (full minutes)
- Error notification with admin role mention

**Exit criteria:** Full end-to-end pipeline produces and posts minutes from a real meeting.

### Phase 4: Hardening + Deployment (Week 4)

- End-to-end integration testing with multiple real meetings
- Retry logic and error recovery across all pipeline stages
- Progress notifications in Discord
- systemd user service for auto-start on Linux
- Setup documentation and troubleshooting guide

**Exit criteria:** System runs unattended for 3 consecutive meetings with > 90% success rate.

---

## 10. Risks and Mitigations

### Risk 1: Craig Bot Message Format Change (Probability: LOW-MEDIUM, Impact: HIGH)

Craig uses Discord Components V2 for its recording panel. The detection logic parses raw JSON payloads, which could change if Craig updates its UI.

**Mitigations:**
- Detection uses string matching on raw JSON (`"Recording ended"` in serialized components), not structural parsing, making it resilient to layout changes.
- All Craig-specific code is isolated in `detector.py` and `craig_client.py` for fast updates.
- Manual fallback command (`/minutes process <url>`) ensures the pipeline remains usable even if auto-detection breaks.
- Log all raw Craig payloads for rapid diagnosis when format changes occur.

### Risk 2: Craig Cook API Deprecation or Change (Probability: LOW, Impact: HIGH)

The Cook API is an internal API verified from Craig's source code, not a documented public interface. It could change without notice.

**Mitigations:**
- All Cook API calls are in a single module (`craig_client.py`) with response validation.
- HTTP status codes and response content types are checked before processing.
- The pluggable audio acquisition interface allows swapping to a different recording source without changing downstream stages.
- Monitor the Craig GitHub repository (`CraigChat/craig`) for changes to `cook.ts`.

### Risk 3: LLM Hallucination in Generated Minutes (Probability: MEDIUM, Impact: MEDIUM-HIGH)

Claude can fabricate action items, misattribute statements, or invent decisions that were never discussed.

**Mitigations:**
- All minutes are labeled "AI-generated" with a disclaimer.
- The raw merged transcript is optionally attached for verification.
- Prompt engineering explicitly instructs the model to only summarize what is in the transcript and to mark uncertain items.
- Users are expected to review minutes before treating them as official records.

### Risk 4: Local PC Unavailable During Meeting (Probability: MEDIUM, Impact: LOW)

If the PC is off, asleep, or disconnected during a meeting, the bot misses the recording-end event.

**Mitigations:**
- Auto-start via systemd user service on boot.
- `/minutes status` health check command for quick verification before meetings.
- Craig recordings remain available for 7 days; the manual fallback command can process missed meetings retroactively.
- Not a data loss event: the recording exists on Craig's servers regardless.

### Risk 5: Processing Time Exceeds Target for Long Meetings (Probability: MEDIUM, Impact: LOW)

A 2-hour meeting with 8 speakers produces ~16 hours of total audio. Sequential transcription at 10-15x real-time would take 60-90 minutes.

**Mitigations:**
- Post a progress message immediately with estimated completion time.
- Update progress per speaker during transcription.
- The 15-minute target applies to the typical case (1 hour, 5 speakers); longer meetings are proportional and expected.
- Optional silence trimming in Phase 4 can reduce audio volume 30-50% for passive speakers.

---

## 11. Open Questions

| ID | Question | Owner | Impact if Unresolved |
|----|----------|-------|----------------------|
| OQ-1 | Can the recording ID and access key be reliably extracted from the Craig panel edit payload, or do we need to capture them from the initial panel creation? | Phase 0 validation | Determines whether we need `on_message` for panel creation in addition to `on_raw_message_update` for panel edit |
| OQ-2 | Does Craig rate-limit the Cook API? If so, at what threshold? | Phase 0 validation | Could affect retry logic and download reliability |
| OQ-3 | What is the actual transcription accuracy of faster-whisper large-v3 for Japanese conversational speech recorded through Discord? | Phase 2 testing | If accuracy is too low, may need to evaluate alternative models or preprocessing |
| OQ-4 | Should the system support English or other languages from day one, or defer to post-v1? | Product decision | Affects prompt template design and Whisper language configuration |
| OQ-5 | What Discord bot permissions are actually required for reading Components V2 message edits? | Phase 0 validation | Determines bot permission configuration |
