# Product Requirements: Calendar Integration

**Feature Slug**: calendar-integration
**Date**: 2026-03-17
**Priority**: Ext-6 (Low)
**Size**: L (Medium-Complex)

---

## 1. Problem Statement

When meeting minutes are generated from Discord voice recordings, the output lacks contextual information about the meeting -- its official title, planned agenda, and expected attendees. Users must manually cross-reference Google Calendar to understand which meeting a set of minutes belongs to. This becomes especially cumbersome when multiple meetings happen on the same day or when minutes are reviewed weeks later.

---

## 2. Target Users

| Persona | Description | Frequency |
|---------|-------------|-----------|
| **Workspace Admin** | Google Workspace organization admin who schedules meetings in Google Calendar and records them on Discord | Daily |
| **Team Lead** | Reviews generated minutes and shares them with stakeholders; needs clear meeting identification | Weekly |
| **Casual Server Owner** | Individual Discord server owner using Google Calendar for personal meeting scheduling | Occasional |

### Out of Scope Users
- Users who do not use Google Calendar
- Users who use Discord for casual voice chats without scheduled meetings

---

## 3. User Stories

### Must-Have (P0)

| ID | Story | Acceptance Criteria |
|----|-------|---------------------|
| US-1 | As a team lead, I want the minutes to automatically show the Google Calendar event title so I can immediately identify which meeting the minutes belong to | Minutes embed and markdown contain the event title when a matching calendar event exists |
| US-2 | As a workspace admin, I want calendar event attendees and description included in the minutes prompt so the LLM can produce better-contextualized summaries | The Claude API prompt includes event_title, event_attendees, and event_description as template variables |
| US-3 | As an admin, I want to enable/disable calendar integration per config so I can control the feature without code changes | `config.yaml` has a `calendar:` section with `enabled: true/false`; when disabled, pipeline behaves identically to current behavior |
| US-4 | As a user, I want the pipeline to still work normally even if Calendar API fails so that minutes generation is never blocked by calendar issues | Calendar fetch failure logs a warning and continues with empty calendar data (graceful degradation) |

### Nice-to-Have (P1)

| ID | Story | Acceptance Criteria |
|----|-------|---------------------|
| US-5 | As a team lead, I want to see the calendar event title in the Discord embed so I can identify the meeting at a glance from the channel | Embed title includes event name when available |
| US-6 | As a workspace admin, I want to check calendar integration status via slash command so I can verify it is working | `/minutes calendar-status` shows enabled state, calendar ID, and last fetch result |

### Future (P2)

| ID | Story | Acceptance Criteria |
|----|-------|---------------------|
| US-7 | As a team lead, I want the bot to write a link to the posted minutes back to the calendar event so attendees can find the minutes from their calendar | Calendar event description is updated with a Discord message link (requires write scope) |
| US-8 | As an admin, I want to configure different calendars for different guilds so each server maps to its own team calendar | Per-guild `calendar_id` in guild config |

---

## 4. Feature Behavior

### 4.1 Happy Path

1. Recording ends in Discord voice channel
2. Pipeline starts (download -> transcribe -> merge)
3. **New Stage**: Before generation, the calendar client queries Google Calendar for events overlapping the recording time window
4. Best-matching event is selected (by time overlap)
5. Event metadata (title, attendees, description) is injected into the prompt template
6. Claude generates minutes with full meeting context
7. Minutes are posted with event title visible in the embed

### 4.2 No Calendar Event Found

Steps 1-3 proceed as above. When no matching event is found, all calendar template variables resolve to empty strings. The prompt template is designed to gracefully omit the calendar section when variables are empty. Minutes generation proceeds identically to current behavior.

### 4.3 Calendar API Failure

If the Calendar API call fails (network error, auth error, timeout), the error is logged as a warning. The pipeline continues with empty calendar data. No error is posted to the Discord channel for calendar-specific failures -- they are silent degradation.

### 4.4 Multiple Calendar Events

When multiple events overlap the recording window:
1. Events are scored by overlap duration (intersection of event time range and recording time range)
2. The event with the highest overlap is selected
3. Ties are broken by event creation time (newest wins)
4. Only one event's metadata is used (no multi-event merging)

---

## 5. Configuration

### New `calendar:` section in config.yaml

```yaml
calendar:
  enabled: false
  credentials_path: "credentials.json"  # same as google_drive
  calendar_id: "primary"                # or specific calendar ID
  timezone: "Asia/Tokyo"
  match_tolerance_minutes: 30           # extend search window by N minutes before/after recording
  api_timeout_sec: 10
  max_retries: 2
```

### Feature Flag

- `calendar.enabled: false` by default (opt-in)
- Enabling requires: (a) valid credentials.json with Calendar scope, (b) calendar shared with Service Account

---

## 6. Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Calendar event match rate | > 70% for scheduled meetings | Log `calendar_event_matched: true/false` per pipeline run |
| Pipeline reliability | 0% regressions from calendar feature | Zero additional pipeline failures attributable to calendar code |
| Calendar fetch latency | < 3 seconds P95 | Log fetch duration per pipeline run |
| User satisfaction | Positive feedback on meeting identification | Qualitative, tracked via Discord server feedback |

---

## 7. Rollout Plan

### Phase 0: Validation (Pre-Implementation)
- Validate Service Account calendar access
- Validate recording timestamp availability
- GO/NO-GO gate

### Phase 1: Core (Behind Feature Flag)
- `calendar.enabled: false` by default
- Implement calendar client, pipeline integration, template changes
- Internal testing with real meetings

### Phase 2: Enablement
- Enable for primary guild
- Monitor logs for 1 week
- Confirm match rates and latency

### Phase 3: Documentation
- Setup guide for Service Account calendar sharing
- Config documentation in config.yaml comments

---

## 8. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Service Account cannot access target calendar | Feature unusable | Phase 0 validation; clear setup documentation |
| Recording timestamps unavailable | Inaccurate event matching | Multi-strategy fallback (Whisper segments, pipeline timestamp) |
| Calendar API latency adds pipeline delay | User perceives slowdown | 10-second timeout; async fetch; graceful degradation |
| Scope change breaks Drive watcher | Existing feature regression | Shared credentials with additive scope; test backward compatibility |

---

## 9. Out of Scope (Explicitly)

- Microsoft Outlook / Office 365 calendar integration
- OAuth2 user authentication flow
- Writing back to calendar events (P2 future)
- Discord Scheduled Events integration (separate feature)
- Per-guild calendar configuration (P2 future)
- ICS file import
