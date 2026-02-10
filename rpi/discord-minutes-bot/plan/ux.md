# UX Design Document: Discord Meeting Auto-Minutes Bot

**Date:** 2026-02-10
**System:** Discord Meeting Auto-Minutes Generation Bot
**Interface:** Discord messages, embeds, slash commands, and file attachments
**Primary Language:** Japanese (configurable)

---

## 1. User Stories and Acceptance Criteria

### US-1: Automatic Minutes from Craig Recording (Primary Flow)

**As** a meeting organizer who uses Craig Bot,
**I want** meeting minutes to be generated and posted automatically when a recording ends,
**so that** I do not have to manually summarize the meeting.

**Acceptance Criteria:**
- Given a Craig Bot recording that has ended in the watched channel, when the bot detects the "Recording ended" panel edit, then the bot posts a status message within 5 seconds of detection.
- Given the pipeline is processing, when each stage completes, then the status message is edited to reflect the current stage and progress.
- Given the pipeline completes successfully, then an embed with the minutes summary and a markdown file attachment with the full minutes are posted to the output channel.
- Given the pipeline completes successfully, then the status message is edited to "Minutes posted." with the total processing time.

### US-2: Manual Minutes from Craig URL (Fallback)

**As** a user whose automatic detection failed or who has a Craig URL from another source,
**I want** to trigger minutes generation by providing a Craig recording URL,
**so that** I can still get minutes when the automatic flow does not work.

**Acceptance Criteria:**
- Given I run `/minutes process <craig_url>`, when the URL is a valid Craig recording URL, then the bot responds with a deferred "Processing..." message and starts the pipeline.
- Given I run `/minutes process <craig_url>`, when the URL is invalid or inaccessible, then the bot responds with an error message within 3 seconds explaining the failure.
- Given the pipeline started from a manual command completes, then the output is identical to the automatic flow (embed + file attachment).

### US-3: Bot Health Check

**As** a server administrator,
**I want** to check whether the minutes bot is healthy and operational,
**so that** I can verify it will work before our next meeting.

**Acceptance Criteria:**
- Given I run `/minutes status`, then the bot responds within 3 seconds with an ephemeral message showing: bot uptime, last successful pipeline run (timestamp and recording ID), Whisper model loaded status, GPU name and VRAM usage, and watched channel name.
- Given the bot has never completed a pipeline run, then the "last successful run" field displays "None" instead of a timestamp.

### US-4: Error Visibility

**As** a server administrator,
**I want** to be notified when minutes generation fails,
**so that** I can take corrective action such as retrying manually.

**Acceptance Criteria:**
- Given a pipeline failure at any stage, then the bot posts a red error embed in the output channel that identifies the failure stage, error message, recording ID, and a hint to use `/minutes process` for manual retry.
- Given the `error_mention_role` configuration is set, then the error embed mentions that role.
- Given the status message was posted before the failure, then the status message is edited to indicate the failure.

### US-5: Minutes Review

**As** a meeting participant,
**I want** to read the minutes summary directly in Discord without opening a file,
**so that** I can quickly review decisions and action items.

**Acceptance Criteria:**
- Given minutes have been posted, then the embed contains: meeting date in the title, participant names, meeting duration, a summary section, and a decisions section.
- Given minutes have been posted, then the full minutes markdown file is attached to the same message for detailed review.
- Given the embed description exceeds 4096 characters (Discord limit), then the description is truncated at a natural break point with a note to see the attached file for full details.

---

## 2. User Flows

### 2.1 Automatic Flow (Zero-Interaction)

```
User starts Craig Bot       User stops Craig Bot       Bot detects
recording with /join   -->  recording with /stop  -->  "Recording ended"
                            or /leave                  panel edit
                                                           |
                                                           v
                                                  Bot posts status message:
                                                  "Processing recording..."
                                                           |
                            +------------------------------+
                            |
                            v
                   Status message updates:
                   "Downloading audio from Craig..."
                            |
                            v
                   "Transcribing speaker 1/5 (tanaka)..."
                            |
                            v
                   "Transcribing speaker 2/5 (suzuki)..."
                            |
                            v
                   "Generating minutes..."
                            |
                            v
                   Bot posts minutes embed + markdown file
                            |
                            v
                   Status message edited:
                   "Minutes posted. (Processing time: 12m 34s)"
```

**States:**
- **Idle:** Bot is online, monitoring the watched channel. No visual indicator in Discord (silent monitoring).
- **Detecting:** Transient internal state. The bot identifies a Craig panel edit and validates it.
- **Downloading:** Bot has posted the status message. Craig Cook API is being called.
- **Transcribing:** Audio tracks are being processed sequentially by faster-whisper. Status shows per-speaker progress.
- **Generating:** Merged transcript has been sent to Claude API. Status shows "Generating minutes..."
- **Posting:** Embed and file are being sent to Discord. Brief state, typically under 5 seconds.
- **Complete:** Status message updated to final state. Pipeline resources cleaned up.
- **Failed:** Error embed posted. Status message updated to indicate failure. See section 6.

### 2.2 Manual Fallback Flow

```
User runs:
/minutes process https://craig.chat/rec/abc123?key=XYZDEF
        |
        v
Bot responds with deferred message:
"Processing recording abc123..."
        |
        v
(Same pipeline stages as automatic flow)
        |
        v
Bot posts minutes embed + markdown file
        |
        v
Bot edits deferred response:
"Minutes posted. (Processing time: 8m 12s)"
```

**States:** Same as automatic flow, starting from the Downloading state.

**Error state:** If the URL is invalid, the bot responds immediately (not deferred) with an ephemeral error message. No status message is posted to the channel.

### 2.3 Health Check Flow

```
User runs:
/minutes status
        |
        v
Bot responds with ephemeral embed (only visible to the user):
+------------------------------------------+
| Minutes Bot Status                        |
|                                          |
| Status: Online                           |
| Uptime: 3d 14h 22m                      |
| Last Run: 2026-02-07 15:23 (rec:718299) |
| Model: large-v3 (loaded)                |
| GPU: RTX 3060 (4.2 / 12.0 GB used)     |
| Watching: #meeting-voice                 |
+------------------------------------------+
```

**States:**
- **Healthy:** All fields show normal values. Embed color is green (#57F287).
- **Degraded:** Model not loaded or GPU unavailable. Embed color is yellow (#FEE75C). Specific field indicates the issue.
- **Error:** Bot is unable to check its own status (unlikely but possible if GPU query fails). Falls back to a plain text message with whatever information is available.

### 2.4 Error Recovery Flow

```
Pipeline fails at any stage
        |
        v
Bot posts error embed to output channel
(red, with failure details and retry hint)
        |
        v
Bot edits status message:
"Processing failed. See error details below."
        |
        v
User reads error embed
        |
        v
User decides to retry:
/minutes process <url>
        |
        v
(Manual flow begins)
```

---

## 3. Discord Message Specifications

### 3.1 Status Message (During Processing)

**Type:** Regular message (not embed), edited in place as processing progresses.

**Why plain text:** Status updates are transient operational messages. Using an embed would draw visual weight equal to the final minutes, which is the message that should stand out in the channel. Plain text keeps the status visually subordinate.

**Initial state (posted on detection):**

```
Processing recording...
Downloading audio from Craig...
```

**Transcription progress (edited):**

```
Processing recording...
Transcribing speaker 2/5 (suzuki)...
```

**Minutes generation (edited):**

```
Processing recording...
Generating minutes...
```

**Final state (edited after minutes are posted):**

```
Minutes posted. (Processing time: 12m 34s)
```

**Character budget:** Status messages are short by design. Maximum expected length is under 100 characters.

**Edit frequency:** Status message is edited once per pipeline stage transition and once per speaker during transcription. For a 5-speaker meeting, this is approximately 8 edits over 10-15 minutes. This is well within Discord's rate limit of 5 edits per 5 seconds per channel.

### 3.2 Minutes Embed (Final Output)

**Type:** Rich embed with file attachment.

| Embed Property | Value | Notes |
|---------------|-------|-------|
| Title | `Meeting Minutes -- 2026/02/07` | Date in YYYY/MM/DD format; localized title configurable |
| Color | `#5865F2` (Discord blurple) | Configurable via `output.embed_color` |
| Description | Summary + Decisions (see below) | Max 4096 characters (Discord limit) |
| Field 1 | **Name:** `Participants` **Value:** `tanaka, suzuki, sato` | Inline: true |
| Field 2 | **Name:** `Duration` **Value:** `58 min` | Inline: true |
| Footer | `Full minutes attached | AI-generated -- verify important details` | Always present as disclaimer |
| Timestamp | ISO 8601 timestamp of meeting end | Displayed by Discord in local time |

**Description content structure:**

```
## Summary
The project progress review confirmed all sprint tasks are on track.
Key milestone dates were set for the next two weeks.

## Decisions
- Deadline for XX set to 2/14 (owner: suzuki)
- Review of YY scheduled for next Monday (owner: sato)
```

**Truncation behavior:** If the combined summary and decisions text exceeds 3800 characters (leaving 296 characters of buffer below the 4096 limit), the description is truncated at the last complete bullet point or paragraph before the limit, and the following line is appended:

```
(Truncated -- see attached file for full minutes)
```

**File attachment (same message):**
- Filename: `minutes-2026-02-07.md`
- Content: Full structured minutes in markdown (see requirements.md section 3.6 for the template)
- Size: Typically 2-10 KB for a 1-hour meeting. Well under Discord's 25 MB limit.

**Optional second attachment (configurable):**
- Filename: `transcript-2026-02-07.txt`
- Content: Raw timestamped transcript
- Controlled by `output.include_transcript` config flag (default: false)

### 3.3 Error Embed

**Type:** Rich embed.

| Embed Property | Value | Notes |
|---------------|-------|-------|
| Title | `Minutes Generation Failed` | Static text |
| Color | `#ED4245` (Discord red) | Always red for errors |
| Description | Error message with context (see below) | Max 4096 characters |
| Footer | `Use /minutes process <url> to retry manually.` | Always present |

**Description content structure:**

```
**Stage:** Audio Download
**Error:** Connection timed out after 300 seconds.
**Recording:** 718299383

@admin-role
```

**Error message mapping by stage:**

| Stage | Example Error | User-Facing Message |
|-------|--------------|-------------------|
| Detection | Recording ID extraction failed | `Could not extract recording information from the Craig panel. The message format may have changed.` |
| Download | HTTP 404 from Cook API | `Audio download failed. The recording may have expired or the Craig API returned an error (HTTP 404).` |
| Download | Timeout | `Audio download timed out after {timeout} seconds. The recording may be too large or the Craig server may be slow.` |
| Transcription | CUDA out of memory | `Transcription failed due to GPU memory error. The audio track may be unusually long.` |
| Transcription | Model not loaded | `Whisper model is not loaded. The bot may need to be restarted.` |
| Generation | Claude API error | `Minutes generation failed. The Claude API returned an error: {error_detail}.` |
| Generation | Token limit exceeded | `The transcript is too long for the LLM context window ({token_count} tokens). Consider shorter meetings or reducing the number of speakers.` |
| Posting | Discord API error | `Failed to post minutes to Discord: {error_detail}.` |

**Role mention:** If `error_mention_role` is configured, the role is mentioned in the description text. The mention uses Discord's role mention syntax (`<@&ROLE_ID>`), which renders as a clickable, highlighted mention and sends a notification to users with that role.

### 3.4 Health Check Response

**Type:** Rich embed, ephemeral (only visible to the command user).

| Embed Property | Value | Notes |
|---------------|-------|-------|
| Title | `Minutes Bot Status` | Static text |
| Color | `#57F287` (green) if healthy, `#FEE75C` (yellow) if degraded | Dynamic |
| Fields | See below | All fields inline: false |

**Fields:**

| Field Name | Example Value | Notes |
|-----------|--------------|-------|
| `Status` | `Online` | Always "Online" if the bot can respond |
| `Uptime` | `3d 14h 22m` | Time since bot process started |
| `Last Successful Run` | `2026-02-07 15:23 JST (recording: 718299383)` | "None" if no successful runs |
| `Whisper Model` | `large-v3 (loaded, VRAM: 3.1 GB)` | Or "Not loaded" with yellow color |
| `GPU` | `NVIDIA RTX 3060 (4.2 / 12.0 GB VRAM)` | Or "No GPU detected" with yellow color |
| `Watched Channel` | `#meeting-voice` | Channel mention format |
| `Output Channel` | `#meeting-minutes` | Channel mention format |

---

## 4. Slash Command Specifications

### 4.1 Command: `/minutes process`

| Property | Value |
|---------|-------|
| Name | `minutes` (group) `process` (subcommand) |
| Description | `Process a Craig Bot recording URL and generate meeting minutes.` |
| Parameter 1 | **Name:** `url` **Type:** String **Required:** Yes **Description:** `Craig recording URL (e.g., https://craig.chat/rec/abc123?key=XYZDEF)` |
| Default Permission | Enabled for all users who can see the channel |
| Response Type | Deferred (bot sends "thinking..." immediately, then follows up with the status message) |

**Response behavior:**
1. Bot validates the URL format immediately. If invalid, responds with an ephemeral error (not visible to others): `Invalid Craig URL format. Expected: https://craig.chat/rec/{id}?key={key}`
2. If valid, bot defers the response and posts a status message to the output channel.
3. The interaction response is edited to link to the status message: `Processing started. Follow progress: [link to status message]`
4. Pipeline runs. Status message is edited as described in section 3.1.
5. On completion, the interaction response is edited to: `Minutes posted. (Processing time: Xm Ys)`

**URL validation regex:** `https?://(craig\.chat|craig\.horse)/rec/[a-zA-Z0-9]+\?key=[a-zA-Z0-9]+`

The bot accepts both `craig.chat` and `craig.horse` domains, as Craig uses multiple domains.

### 4.2 Command: `/minutes status`

| Property | Value |
|---------|-------|
| Name | `minutes` (group) `status` (subcommand) |
| Description | `Show bot health status and last pipeline run.` |
| Parameters | None |
| Default Permission | Enabled for all users who can see the channel |
| Response Type | Ephemeral (only visible to the command user) |

**Response behavior:**
1. Bot gathers system information (uptime, GPU status, model status, last run info).
2. Bot responds with an ephemeral embed as described in section 3.4.
3. Response time target: under 3 seconds.

### 4.3 Command Group Registration

All commands are registered under the `minutes` command group:

```
/minutes process <url>   -- Generate minutes from a Craig URL
/minutes status          -- Show bot health status
```

**Rationale for command group:** Using a group (`/minutes`) rather than separate top-level commands (`/process-minutes`, `/minutes-status`) keeps the command namespace clean and groups related functionality. Discord autocomplete will show all subcommands when a user types `/minutes`.

---

## 5. Progress Feedback Design

### 5.1 Design Principles

1. **Single editable message:** All progress updates edit one message rather than posting new messages. This avoids cluttering the channel with transient status information.
2. **Stage-level granularity:** Progress is reported at the pipeline stage level (downloading, transcribing, generating), not at percentage level. Percentage-based progress bars are unreliable because transcription time per speaker is unpredictable.
3. **Speaker-level transcription progress:** During the transcription stage (the longest stage), progress is reported per speaker (`Transcribing speaker 2/5 (suzuki)...`). This gives users a meaningful sense of progress without false precision.
4. **Time estimate on first message:** The initial status message includes a rough time estimate based on the number of speakers and total audio duration (obtained from Craig's Users API and Duration API before downloading). Format: `Estimated: ~X minutes`.

### 5.2 Status Message Lifecycle

| Pipeline Stage | Status Message Content | Timing |
|---------------|----------------------|--------|
| Detection complete | `Processing recording {id}...\nEstimated: ~{est} minutes\nDownloading audio from Craig...` | Immediately after detection (within 5 seconds) |
| Download complete | `Processing recording {id}...\nEstimated: ~{est} minutes\nTranscribing speaker 1/{total} ({username})...` | After ZIP downloaded and extracted |
| Each speaker transcribed | `Processing recording {id}...\nEstimated: ~{est} minutes\nTranscribing speaker {n}/{total} ({username})...` | After each speaker's track completes |
| All transcription complete | `Processing recording {id}...\nEstimated: ~{est} minutes\nGenerating minutes...` | After transcript merge |
| Minutes posted | `Minutes posted. (Processing time: {mm}m {ss}s)` | After embed and file are sent |
| Pipeline failed | `Processing failed. See error details below.` | On any unrecoverable error |

### 5.3 Time Estimation Formula

```
estimated_minutes = (total_audio_duration_seconds / 10) / 60 + 3
```

Where:
- `total_audio_duration_seconds` = sum of all speaker track durations (from Craig Duration/Users API)
- Division by 10 = approximate real-time factor for faster-whisper large-v3 on RTX 3060
- `+ 3` = fixed overhead for download, generation, and posting

The estimate is rounded up to the nearest minute and displayed as `~X minutes`. This is intentionally conservative to avoid user frustration when processing takes longer than estimated.

---

## 6. Error States

### 6.1 Error Classification

| Category | Severity | User Impact | Bot Behavior |
|----------|----------|-------------|-------------|
| **Detection errors** | Low | User unaware; meeting not processed | Log the error. No message posted (the bot does not know a meeting happened). Discoverable via `/minutes status` if the bot logged a detection attempt. |
| **Download errors** | Medium | Pipeline started but no output | Post error embed. Edit status message to indicate failure. |
| **Transcription errors** | Medium-High | Pipeline started, audio downloaded but no output | Post error embed. Edit status message. Temp files cleaned up. |
| **Generation errors** | Medium | Transcript exists but no minutes | Post error embed. Optionally attach the raw transcript so it is not lost. |
| **Posting errors** | Low | Minutes generated but not visible | Retry posting up to 3 times with exponential backoff. If all retries fail, log the error and save the minutes file locally. |
| **Configuration errors** | High | Bot cannot start or cannot function | Log to stderr/file at startup. Bot does not come online with broken config. |

### 6.2 Detailed Error Scenarios

**E1: Craig panel format changed**
- Trigger: `on_raw_message_update` fires but recording ID/key extraction fails.
- User sees: Nothing (silent failure). The pipeline does not start.
- Admin sees: Warning logged to file with the raw payload for debugging.
- Recovery: Admin updates extraction regex. Or user uses `/minutes process` with the URL from Craig's DM.

**E2: Craig Cook API returns HTTP error**
- Trigger: POST to `/api/recording/{id}/cook` returns 4xx or 5xx.
- User sees: Error embed stating "Audio download failed" with the HTTP status code.
- Recovery: User waits and retries with `/minutes process`. If the recording expired (7-day limit), no recovery is possible.

**E3: Craig Cook API timeout**
- Trigger: Cook API does not respond within `craig.download_timeout` seconds (default: 300).
- User sees: Error embed stating "Audio download timed out."
- Recovery: User retries with `/minutes process`. If persistent, admin increases timeout in config.

**E4: ZIP file corrupt or empty**
- Trigger: Downloaded ZIP cannot be opened or contains no audio tracks.
- User sees: Error embed stating "Downloaded audio file is corrupt or empty."
- Recovery: User retries. If persistent, the Craig recording may be damaged.

**E5: GPU out of memory during transcription**
- Trigger: faster-whisper raises CUDA OOM error.
- User sees: Error embed stating "Transcription failed due to GPU memory error."
- Recovery: Admin checks for competing GPU processes. If a single track is too long, silence trimming (future feature) would help.

**E6: Whisper model not loaded**
- Trigger: Transcription requested but model failed to load at startup.
- User sees: Error embed stating "Whisper model is not loaded."
- Recovery: Admin restarts the bot. `/minutes status` shows model status.

**E7: Claude API rate limit or error**
- Trigger: Anthropic API returns 429 or 5xx.
- User sees: Error embed after 3 retry attempts stating "Minutes generation failed" with the API error.
- Recovery: Automatic retry with exponential backoff (up to 3 attempts). If all fail, user can retry later with `/minutes process`.

**E8: Transcript too long for LLM context**
- Trigger: Merged transcript exceeds Claude's context window.
- User sees: Error embed stating "Transcript is too long for the LLM context window."
- Recovery: Future improvement could split the transcript into chunks. For now, user must accept partial processing or shorter meetings.

**E9: Discord embed exceeds character limits**
- Trigger: Generated minutes summary exceeds 4096 characters for embed description.
- User sees: Truncated embed with a note to see the attached file. This is not an error; it is handled gracefully.
- Recovery: Not needed. The full minutes are in the attached file.

**E10: Bot was offline when recording ended**
- Trigger: Bot was not running when Craig's panel was edited to "Recording ended."
- User sees: Nothing. The edit event is not retroactively delivered when the bot comes back online.
- Recovery: User uses `/minutes process` with the Craig URL from their DM. `/minutes status` shows the bot's uptime, which helps identify if it was offline.

**E11: Duplicate processing**
- Trigger: Craig panel edit fires multiple `on_raw_message_update` events for the same recording (possible during network reconnection), or user runs `/minutes process` for a recording already being processed.
- User sees: For duplicate detection events, the bot ignores the duplicate (tracked via an in-memory set of active recording IDs). For manual commands during active processing, the bot responds with an ephemeral message: `Recording {id} is already being processed. Check the status message in #{channel}.`
- Recovery: Not needed. Deduplication is automatic.

**E12: Bot lacks Discord permissions**
- Trigger: Bot cannot send messages, attach files, or embed links in the output channel.
- User sees: No output (messages fail silently from the user's perspective).
- Admin sees: Discord API error logged to file. If the error occurs during pipeline, the status message update fails.
- Recovery: Admin grants the bot the required permissions: Read Messages, Send Messages, Embed Links, Attach Files, Read Message History.

---

## 7. Edge Cases

### 7.1 Very Short Meetings (Under 2 Minutes)

- The pipeline runs normally. Faster-whisper and Claude handle short transcripts.
- The LLM prompt should instruct Claude to produce proportionally shorter minutes. If the transcript is under 100 characters, the minutes summary may simply state: "Brief session with minimal discussion. No decisions or action items identified."

### 7.2 Single Speaker

- If only one participant spoke (e.g., a presentation), the minutes embed still lists only that participant.
- The transcript has no interleaving needed. The LLM should be prompted to adapt the format (no "discussion" section, focus on content summary).

### 7.3 Very Long Meetings (Over 2 Hours)

- Processing time may exceed 30 minutes. The time estimate in the status message sets user expectations.
- The status message per-speaker progress updates prevent the appearance of the bot being stuck.
- No timeout on overall pipeline execution. The bot processes until complete or until an error occurs.

### 7.4 Many Speakers (Over 10)

- Transcription is sequential, so time scales linearly with speaker count.
- The embed participant list may become long. If participants exceed 10, the field value is truncated: `tanaka, suzuki, sato, ... and 8 others (13 total)`.
- The full participant list is in the attached minutes file.

### 7.5 Bot Restart During Processing

- All in-progress pipeline state is lost. The status message remains in Discord but is no longer being updated.
- On restart, the bot does not attempt to resume. The stale status message remains as-is.
- The user can retry with `/minutes process` after the bot is back online.
- Temp files from the interrupted pipeline persist until the OS cleans `/tmp` or until the next bot startup (which can include a temp directory cleanup routine).

### 7.6 Multiple Recordings End Simultaneously

- The system processes one recording at a time (single-channel, single-server design).
- If a second recording ends while the first is processing, the second detection event is queued in memory and processed after the first pipeline completes.
- The queue holds at most one pending recording. If a third event arrives while one is processing and one is queued, the oldest queued event is replaced (with a warning log). This is an unlikely scenario for a single-server deployment.

### 7.7 Craig Recording with No Audio (All Tracks Silent)

- faster-whisper produces an empty transcript.
- The LLM receives an empty transcript and should generate a minutes document stating: "No speech was detected in the recording."
- The bot still posts the embed and file, with the summary reflecting the empty content.

---

## 8. Accessibility Notes

### 8.1 Embed Structure for Screen Readers

Discord clients expose embed content to assistive technologies in the following reading order: title, description, fields (in order), footer. The minutes embed is structured to follow this natural reading order:

1. **Title:** Identifies the document type and date. Reads as: "Meeting Minutes, 2026/02/07."
2. **Description:** Contains the substantive content (summary and decisions). This is the most important section and is encountered early in the reading order.
3. **Fields:** Metadata (participants, duration) in inline fields. These provide context after the main content.
4. **Footer:** Disclaimer about AI generation. Encountered last, as appropriate for a caveat.

### 8.2 Text Content over Visual Decoration

- The embed does not rely on emoji alone to convey meaning. Field names use plain text labels (`Participants`, `Duration`) rather than emoji-only labels.
- Emoji used in the requirements spec examples (e.g., the checkmark, clock, and people icons) are decorative. In the implementation, if emoji are used in field names, they are paired with text labels (e.g., `Participants` rather than just a people emoji).
- Color is not the sole indicator of message type. Error embeds have the title "Minutes Generation Failed" in addition to red color. Success embeds have "Meeting Minutes" in the title. Health check embeds have "Minutes Bot Status" in the title.

### 8.3 File Attachment as Primary Content

- The full minutes are always available as a markdown text file attachment. This ensures that users who cannot easily navigate embeds (due to assistive technology limitations or client differences) have access to the complete content in a universally accessible plain-text format.
- The markdown file uses heading hierarchy (`#`, `##`, `###`) that maps to document structure, which assistive technologies can navigate.

### 8.4 Ephemeral Messages

- The `/minutes status` response is ephemeral (visible only to the invoking user). This prevents status check spam in channels, but it also means the response is not persistable or sharable. This is an intentional tradeoff: health checks are transient queries, not reference material.

### 8.5 Keyboard Navigation

- All user interaction is via slash commands, which are fully keyboard-navigable in all Discord clients (desktop, web, mobile).
- No bot-posted buttons or interactive components are used. This eliminates the need for mouse interaction and avoids accessibility issues with Discord's component rendering.
- Users can navigate to the minutes embed and attached file using standard Discord keyboard shortcuts (arrow keys in message list, Enter to expand attachments).

### 8.6 Contrast and Readability

- Embed colors are chosen from Discord's own design system:
  - Blurple (`#5865F2`) for standard minutes: meets WCAG AA contrast against Discord's dark theme background (`#36393f`). Contrast ratio: 3.09:1 for the embed sidebar indicator, which is decorative, not textual.
  - Red (`#ED4245`) for errors: clearly distinguishable from blurple.
  - Green (`#57F287`) for healthy status: clearly distinguishable from both blurple and red.
  - Yellow (`#FEE75C`) for degraded status: clearly distinguishable from the above.
- All substantive content is in Discord's default text color (white/light gray on dark theme, black on light theme), which Discord itself ensures meets contrast requirements.
- The bot does not use custom text formatting that would reduce readability (e.g., no light gray text for important content, no extremely long lines without breaks).

---

## 9. Localization Notes

### 9.1 Language Configuration

The bot's output language is determined by the LLM prompt, not by hardcoded strings in the bot code. The prompt template (`prompts/minutes.txt`) instructs Claude to generate minutes in the target language.

**Default language:** Japanese. The prompt template and embed labels are written in Japanese for the default configuration.

### 9.2 Configurable Text Strings

The following user-facing strings should be externalized (not hardcoded) so they can be localized by editing a single configuration file or locale file:

| String ID | Default (Japanese) | English Equivalent |
|-----------|-------------------|-------------------|
| `embed.title_prefix` | `Meeting Minutes` | `Meeting Minutes` |
| `embed.field.participants` | `Participants` | `Participants` |
| `embed.field.duration` | `Duration` | `Duration` |
| `embed.footer.disclaimer` | `Full minutes attached \| AI-generated -- verify important details` | `Full minutes attached \| AI-generated -- verify important details` |
| `status.processing` | `Processing recording...` | `Processing recording...` |
| `status.downloading` | `Downloading audio from Craig...` | `Downloading audio from Craig...` |
| `status.transcribing` | `Transcribing speaker {n}/{total} ({username})...` | `Transcribing speaker {n}/{total} ({username})...` |
| `status.generating` | `Generating minutes...` | `Generating minutes...` |
| `status.complete` | `Minutes posted. (Processing time: {time})` | `Minutes posted. (Processing time: {time})` |
| `status.failed` | `Processing failed. See error details below.` | `Processing failed. See error details below.` |
| `error.title` | `Minutes Generation Failed` | `Minutes Generation Failed` |
| `error.footer` | `Use /minutes process <url> to retry manually.` | `Use /minutes process <url> to retry manually.` |
| `health.title` | `Minutes Bot Status` | `Minutes Bot Status` |

### 9.3 Date and Time Formatting

- Dates in embed titles use `YYYY/MM/DD` format (e.g., `2026/02/07`), which is standard in Japanese locale and unambiguous internationally.
- Timestamps in health check and error messages use `YYYY-MM-DD HH:MM` with timezone abbreviation (e.g., `2026-02-07 15:23 JST`).
- Duration is displayed in minutes (e.g., `58 min`), which is universally understood.

### 9.4 Minutes Content Language

The minutes content itself (summary, decisions, action items) is generated by Claude in the language specified in the prompt template. Changing the meeting minutes language requires editing the prompt template, not the bot code. The prompt should instruct the LLM to:
- Write in the target language.
- Use the target language's conventions for names (e.g., family name first in Japanese).
- Format dates according to the target locale.

---

## 10. Discord API Constraints Reference

This section documents Discord API limits that directly affect the bot's message design.

| Constraint | Limit | Impact on Design |
|-----------|-------|-----------------|
| Embed description | 4,096 characters | Minutes summary must be truncated if too long. Full content goes in attached file. |
| Embed title | 256 characters | Not a practical concern; titles are short. |
| Embed field name | 256 characters | Not a practical concern; field names are short labels. |
| Embed field value | 1,024 characters | Participant list must be truncated if too many speakers (see edge case 7.4). |
| Embed footer text | 2,048 characters | Not a practical concern; footer is a short disclaimer. |
| Embed fields count | 25 maximum | Only 2 fields used (Participants, Duration). Well within limit. |
| Total embed size | 6,000 characters | Sum of title + description + fields + footer. Monitor in implementation. |
| File attachment size | 25 MB (standard), 500 MB (Nitro server) | Minutes markdown files are 2-10 KB. Not a concern. |
| Message edit rate limit | 5 per 5 seconds per channel | Status message edits are well within this limit (one edit per pipeline stage). |
| Slash command description | 100 characters | Command descriptions must be concise. |
| Slash command option description | 100 characters | Parameter descriptions must be concise. |
| Ephemeral message lifetime | Visible until client restart or dismissal | Health check responses are ephemeral; user cannot reference them later. Acceptable for transient status queries. |

---

## 11. Message Examples (Rendered)

### 11.1 Minutes Embed (Japanese Default)

```
+----------------------------------------------------------+
| Meeting Minutes -- 2026/02/07                             |
|                                                          |
| Participants: tanaka, suzuki, sato     Duration: 58 min  |
|                                                          |
| ## Summary                                               |
| Reviewed project progress and set milestone dates for    |
| the next sprint. All current tasks are on schedule.      |
| Key deadline set for February 14.                        |
|                                                          |
| ## Decisions                                             |
| - XX deadline set to 2/14 (owner: suzuki)                |
| - YY review scheduled for next Monday (owner: sato)      |
|                                                          |
| Full minutes attached | AI-generated -- verify important |
| details                                                  |
|                                                          |
| [📎 minutes-2026-02-07.md]                               |
+----------------------------------------------------------+
```

### 11.2 Error Embed

```
+----------------------------------------------------------+
| [RED] Minutes Generation Failed                          |
|                                                          |
| **Stage:** Audio Download                                |
| **Error:** Connection timed out after 300 seconds.       |
| **Recording:** 718299383                                 |
|                                                          |
| @admin                                                   |
|                                                          |
| Use /minutes process <url> to retry manually.            |
+----------------------------------------------------------+
```

### 11.3 Health Check Embed (Healthy)

```
+----------------------------------------------------------+
| [GREEN] Minutes Bot Status                               |
|                                                          |
| Status          Online                                   |
| Uptime          3d 14h 22m                               |
| Last Run        2026-02-07 15:23 JST (recording: 718299) |
| Whisper Model   large-v3 (loaded, VRAM: 3.1 GB)         |
| GPU             NVIDIA RTX 3060 (4.2 / 12.0 GB VRAM)    |
| Watching        #meeting-voice                           |
| Output          #meeting-minutes                         |
+----------------------------------------------------------+
```

### 11.4 Health Check Embed (Degraded)

```
+----------------------------------------------------------+
| [YELLOW] Minutes Bot Status                              |
|                                                          |
| Status          Online (degraded)                        |
| Uptime          0d 0h 3m                                 |
| Last Run        None                                     |
| Whisper Model   large-v3 (NOT LOADED -- CUDA error)      |
| GPU             NVIDIA RTX 3060 (not accessible)         |
| Watching        #meeting-voice                           |
| Output          #meeting-minutes                         |
+----------------------------------------------------------+
```

---

## 12. Required Discord Bot Permissions

The bot requires the following permissions to deliver the UX described in this document:

| Permission | Reason |
|-----------|--------|
| Read Messages / View Channels | Monitor the watched channel for Craig panel edits |
| Read Message History | Access message content from `on_raw_message_update` events |
| Send Messages | Post status messages, minutes, and error messages |
| Embed Links | Post rich embeds (minutes, errors, health check) |
| Attach Files | Attach the minutes markdown file |
| Use Slash Commands | Register and respond to `/minutes` commands |

**Privileged Gateway Intents required:**
- `Message Content Intent` -- Required to read Craig Bot's message content and components for recording detection.

**Not required:**
- Manage Messages (the bot edits only its own messages)
- Mention Everyone (role mentions use the `<@&ROLE_ID>` syntax which does not require this permission)
- Administrator (never grant this to a bot with limited scope)
