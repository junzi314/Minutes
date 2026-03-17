# Implementation Plan: Calendar Integration

**Feature Slug**: calendar-integration
**Date**: 2026-03-17
**Estimated Total Effort**: 18-24 hours
**Prerequisite**: Phase 0 validation must pass before Phase 1 begins

---

## Phase Overview

| Phase | Name | Effort | Gate |
|-------|------|--------|------|
| 0 | Validation | 2-4h | All 4 conditions pass -> GO; any fail -> DEFER |
| 1 | Foundation | 4-6h | Tests pass; config loads; CalendarClient unit tests green |
| 2 | Pipeline Integration | 4-6h | End-to-end pipeline test with mock calendar; all existing tests pass |
| 3 | UX Polish | 3-4h | Slash command works; embed shows event title; template produces clean output |
| 4 | Testing & Documentation | 3-4h | Full test suite green; config.yaml documented; setup guide written |

---

## Phase 0: Validation (2-4 hours)

**Goal**: Confirm the technical prerequisites identified in the research report.

### Task 0.1: Validate Calendar API Access

**Objective**: Confirm that the existing `credentials.json` (Service Account) can access Google Calendar API.

**Steps**:
1. Write a standalone test script `scripts/test_calendar_access.py`
2. Load credentials from `credentials.json`
3. Build Calendar API v3 service with `calendar.readonly` scope
4. Call `Events.list` on the target calendar
5. Verify events are returned

**Acceptance**:
- HTTP 200 from Events.list
- At least one event returned (create a test event if needed)
- Script runs successfully and prints event titles

**Abort Condition**: If Service Account cannot access any calendar after attempting:
- Adding `calendar.readonly` scope to credentials
- Sharing calendar with Service Account email
-> DEFER the feature and consider Discord Scheduled Events alternative

### Task 0.2: Validate Credentials Scope Compatibility

**Objective**: Confirm that adding `calendar.readonly` scope does not break the existing Drive watcher.

**Steps**:
1. Create a Credentials object with both `drive.readonly` and `calendar.readonly` scopes
2. Build both Drive v3 and Calendar v3 services from the same credentials
3. Verify Drive `files().list()` still works
4. Verify Calendar `events().list()` works

**Acceptance**:
- Both API calls succeed
- Drive watcher integration test passes

### Task 0.3: Validate Recording Timestamp Estimation

**Objective**: Confirm that Whisper segment timestamps provide usable recording duration.

**Steps**:
1. Use an existing test recording
2. Run Whisper transcription
3. Check `max(segment.end)` for each speaker
4. Verify the maximum value represents approximate recording duration
5. Compare with known recording length

**Acceptance**:
- `max(segment.end)` is within 1 minute of actual recording duration
- Duration can be used to estimate recording start from pipeline execution time

### Task 0.4: Validate Calendar Sharing Setup

**Objective**: Document the realistic setup steps for sharing a calendar with a Service Account.

**Steps**:
1. Share a Google Calendar with the Service Account email
2. Set permission to "See all event details"
3. Verify Events.list returns events from the shared calendar
4. Time the process end-to-end

**Acceptance**:
- Process takes less than 15 minutes
- Steps are clearly documentable

### Phase 0 Gate

| Condition | Task | Result |
|-----------|------|--------|
| C1: Calendar API accessible | 0.1 | PASS/FAIL |
| C2: Scope compatibility | 0.2 | PASS/FAIL |
| C3: Timestamp estimation viable | 0.3 | PASS/FAIL |
| C4: Setup process realistic | 0.4 | PASS/FAIL |

**All PASS** -> Proceed to Phase 1
**Any FAIL** -> DEFER with documented reason

---

## Phase 1: Foundation (4-6 hours)

**Goal**: Build the core modules (config, client, errors) with full unit test coverage.

### Task 1.1: Add CalendarConfig to Config System

**Files**: `src/config.py`, `tests/test_config.py`, `config.yaml`

**Steps**:
1. Define `CalendarConfig` dataclass with fields:
   - `enabled: bool = False`
   - `credentials_path: str = "credentials.json"`
   - `calendar_id: str = "primary"`
   - `timezone: str = "Asia/Tokyo"`
   - `match_tolerance_minutes: int = 30`
   - `api_timeout_sec: int = 10`
   - `max_retries: int = 2`
2. Add `calendar: CalendarConfig` to `Config` dataclass
3. Register `"calendar": CalendarConfig` in `_SECTION_CLASSES`
4. Add validation rules in `_validate()`:
   - When enabled: `calendar_id` required, `match_tolerance_minutes >= 0`, `api_timeout_sec >= 1`
5. Add `calendar:` section to `config.yaml` with `enabled: false`
6. Write tests:
   - Default values
   - YAML parsing
   - Validation (enabled with missing calendar_id)
   - Environment variable override

**Acceptance**: `pytest tests/test_config.py` passes with all new tests green.

### Task 1.2: Add CalendarError to Error Hierarchy

**Files**: `src/errors.py`

**Steps**:
1. Add `CalendarError(MinutesBotError)` with `stage="calendar"`

**Acceptance**: Error can be instantiated and has correct stage attribute.

### Task 1.3: Implement CalendarClient

**Files**: `src/calendar_client.py` (new), `tests/test_calendar_client.py` (new)

**Steps**:
1. Define `CalendarEvent` and `CalendarFetchResult` dataclasses
2. Implement `CalendarClient.__init__(cfg: CalendarConfig)`
3. Implement `_build_service()` following `drive_watcher.py` pattern:
   - Load credentials from `cfg.credentials_path`
   - Scope: `["https://www.googleapis.com/auth/calendar.readonly"]`
   - Build: `build("calendar", "v3", credentials=credentials)`
   - Cache the service object
4. Implement `_list_events_sync(time_min, time_max)`:
   - Call `service.events().list(...)` with parameters from research report
   - Return raw event dicts
5. Implement `_parse_event(raw)`:
   - Handle both `dateTime` and `date` fields (all-day events)
   - Extract title, attendees (display name or email), description
   - Parse RFC3339 timestamps to timezone-aware datetime
6. Implement `_compute_overlap(event_start, event_end, rec_start, rec_end)`:
   - Return overlap in seconds (0 if no overlap)
7. Implement `fetch_event(recording_start, recording_end)`:
   - Extend window by `match_tolerance_minutes`
   - Call `_list_events_sync` via `asyncio.to_thread`
   - Score each event by overlap
   - Return best match or None
   - Catch all exceptions -> return CalendarFetchResult with error
   - Log fetch duration
8. Implement `estimate_recording_window(segments, pipeline_start, timezone_str)`:
   - Module-level function (not class method)
   - Use max segment end for duration
   - Fallback to 1-hour window if no segments

**Tests** (all using mocked Google API):
- `test_parse_event_basic`
- `test_parse_event_all_day`
- `test_parse_event_no_attendees`
- `test_parse_event_no_description`
- `test_compute_overlap_full`
- `test_compute_overlap_partial_start`
- `test_compute_overlap_partial_end`
- `test_compute_overlap_none`
- `test_fetch_event_single_match`
- `test_fetch_event_best_overlap_wins`
- `test_fetch_event_no_events`
- `test_fetch_event_api_error_graceful`
- `test_fetch_event_timeout_graceful`
- `test_estimate_recording_window_normal`
- `test_estimate_recording_window_empty_segments`

**Acceptance**: `pytest tests/test_calendar_client.py` all green. No Google API calls in tests (fully mocked).

---

## Phase 2: Pipeline Integration (4-6 hours)

**Goal**: Wire calendar data through the pipeline from fetch to generation to posting.

### Task 2.1: Extend Generator with Calendar Variables

**Files**: `src/generator.py`, `tests/test_generator.py`

**Steps**:
1. Add `event_title`, `event_attendees`, `event_description` parameters (default `""`) to:
   - `render_prompt()`
   - `generate()`
2. Add replacement entries to the `replacements` dict in `render_prompt()`
3. Forward parameters from `generate()` to `render_prompt()`
4. Write tests:
   - `test_render_prompt_with_calendar_vars`: template with `{event_title}` etc. gets substituted
   - `test_render_prompt_empty_calendar_vars`: empty strings leave template clean
   - `test_render_prompt_no_calendar_placeholders`: template without calendar vars works fine (backward compat)

**Acceptance**: All existing generator tests pass. New tests green.

### Task 2.2: Extend Poster with Event Title

**Files**: `src/poster.py`, `tests/test_poster.py`

**Steps**:
1. Add `event_title: str | None = None` parameter to `build_minutes_embed()`
2. If `event_title` is truthy, add embed field "会議名" after title, before participants
3. Add `event_title: str | None = None` parameter to `post_minutes()`; forward to `build_minutes_embed()`
4. Write tests:
   - `test_embed_with_event_title`: field appears
   - `test_embed_without_event_title`: field absent when None/empty

**Acceptance**: All existing poster tests pass. New tests green.

### Task 2.3: Insert Calendar Stage into Pipeline

**Files**: `src/pipeline.py`, `tests/test_pipeline.py`

**Steps**:
1. Import `CalendarClient`, `estimate_recording_window` from `calendar_client`
2. After Stage 3 (merge) and before Stage 4 (generate), add calendar fetch:
   - Check `cfg.calendar.enabled`
   - Call `estimate_recording_window(segments, datetime.now(), cfg.calendar.timezone)`
   - Instantiate `CalendarClient(cfg.calendar)`
   - Call `await calendar_client.fetch_event(rec_start, rec_end)`
   - Extract `event_title`, `event_attendees`, `event_description` (or empty strings)
3. Pass calendar variables to `generator.generate()`:
   - `event_title=event_title`
   - `event_attendees=event_attendees`
   - `event_description=event_description`
4. Pass `event_title` to `post_minutes()`
5. Write tests:
   - `test_pipeline_calendar_enabled_fetch_called`: mock CalendarClient, verify it is called
   - `test_pipeline_calendar_disabled_no_fetch`: verify CalendarClient is not instantiated
   - `test_pipeline_calendar_error_continues`: mock fetch to return error, verify pipeline completes

**Acceptance**: All existing pipeline tests pass. New tests green. `pytest` full suite green.

### Task 2.4: Update Prompt Template

**Files**: `prompts/minutes.txt`

**Steps**:
1. Add calendar variables to the meeting info section:
   ```
   - 会議名: {event_title}
   - カレンダー参加者: {event_attendees}
   - アジェンダ/説明: {event_description}
   ```
2. Add output rule 9: conditional omission of empty calendar fields
3. Update the output format section to show event title in header when available

**Acceptance**: Template renders correctly with both populated and empty calendar variables.

---

## Phase 3: UX Polish (3-4 hours)

**Goal**: Add slash command, refine embed formatting, handle edge cases.

### Task 3.1: Add /minutes calendar-status Command

**Files**: `bot.py`

**Steps**:
1. Add `minutes_calendar_status` command to the `group`
2. Show: enabled state, calendar_id, timezone, match_tolerance
3. When disabled: show message with config instruction
4. Ephemeral response

**Acceptance**: Command responds correctly in both enabled and disabled states.

### Task 3.2: Refine Embed Event Title Display

**Files**: `src/poster.py`

**Steps**:
1. Ensure event title field does not break embed length limits
2. Truncate event title to 256 chars (Discord embed field name + value limits)
3. Test with very long event titles

**Acceptance**: Embed renders correctly with normal and edge-case event titles.

### Task 3.3: Handle All-Day Events

**Files**: `src/calendar_client.py`, `tests/test_calendar_client.py`

**Steps**:
1. All-day events use `date` instead of `dateTime` in the Google Calendar API
2. Parse `date` field as start of day in configured timezone
3. Set end to start + 24 hours
4. These events should still match if the recording falls within that day

**Acceptance**: All-day event parsing test passes. Overlap computation works correctly.

### Task 3.4: Handle Timezone Edge Cases

**Files**: `src/calendar_client.py`, `tests/test_calendar_client.py`

**Steps**:
1. Ensure all datetime comparisons use timezone-aware objects
2. Convert Google Calendar RFC3339 timestamps to UTC for comparison
3. Convert recording window timestamps to UTC for comparison
4. Test with events in different timezones (UTC, JST, PST)

**Acceptance**: Timezone tests pass. No naive/aware datetime comparison warnings.

---

## Phase 4: Testing & Documentation (3-4 hours)

**Goal**: Full test coverage, documentation, and config comments.

### Task 4.1: Integration Test with Mock Calendar

**Files**: `tests/test_pipeline.py`

**Steps**:
1. Create an end-to-end pipeline test with calendar enabled
2. Mock all external APIs (Whisper, Claude, Calendar)
3. Verify the generated prompt includes calendar variables
4. Verify the posted embed includes event title

**Acceptance**: Integration test passes.

### Task 4.2: Run Full Test Suite

**Steps**:
1. `pytest` -- all tests green
2. No import errors from new modules
3. No regressions in existing tests
4. Verify test count increased by expected amount (~20-25 new tests)

**Acceptance**: `pytest` reports 0 failures, 0 errors.

### Task 4.3: Document Config

**Files**: `config.yaml`

**Steps**:
1. Add commented `calendar:` section with all fields documented
2. Include example calendar IDs
3. Note that credentials.json must have Calendar API scope

**Example**:
```yaml
calendar:
  # Enable Google Calendar integration for meeting context
  # Requires: Calendar API enabled in Google Cloud Console
  # Requires: Calendar shared with Service Account email
  enabled: false
  # Path to service account JSON key (same as google_drive)
  credentials_path: "credentials.json"
  # Google Calendar ID (find in Calendar Settings > Integrate calendar)
  # Use "primary" for the service account's own calendar
  calendar_id: "primary"
  # Timezone for recording time estimation
  timezone: "Asia/Tokyo"
  # Extend search window by N minutes before/after estimated recording time
  match_tolerance_minutes: 30
  # Timeout for Calendar API calls in seconds
  api_timeout_sec: 10
  # Retry attempts on Calendar API failure
  max_retries: 2
```

### Task 4.4: Write Setup Guide Comment in Config

**Files**: `config.yaml`

Add a comment block at the top of the calendar section with setup steps.

---

## File Change Summary

### New Files

| File | Lines (est.) | Description |
|------|-------------|-------------|
| `src/calendar_client.py` | ~150 | CalendarClient, CalendarEvent, CalendarFetchResult, estimate_recording_window |
| `tests/test_calendar_client.py` | ~180 | Unit tests for all calendar client functions |
| `scripts/test_calendar_access.py` | ~40 | Phase 0 validation script (not shipped) |

### Modified Files

| File | Lines Changed (est.) | Description |
|------|---------------------|-------------|
| `src/config.py` | +25 | CalendarConfig dataclass, _SECTION_CLASSES entry, Config field, validation |
| `src/errors.py` | +4 | CalendarError class |
| `src/generator.py` | +15 | 3 new parameters in render_prompt() and generate() |
| `src/poster.py` | +12 | event_title parameter in build_minutes_embed() and post_minutes() |
| `src/pipeline.py` | +30 | Calendar fetch stage, variable passing to generator and poster |
| `bot.py` | +20 | /minutes calendar-status slash command |
| `config.yaml` | +15 | calendar: section with comments |
| `prompts/minutes.txt` | +6 | Calendar template variables and conditional output rule |
| `tests/test_config.py` | +25 | CalendarConfig tests |
| `tests/test_generator.py` | +20 | Calendar variable rendering tests |
| `tests/test_pipeline.py` | +30 | Calendar pipeline integration tests |
| `tests/test_poster.py` | +15 | Event title embed tests |

### Total: ~3 new files, ~12 modified files, ~450 new lines + ~80 modified lines

---

## Risk Mitigation Checkpoints

### After Phase 1
- [ ] `pytest tests/test_config.py tests/test_calendar_client.py` all green
- [ ] No import cycles
- [ ] CalendarConfig defaults are sensible

### After Phase 2
- [ ] `pytest` full suite green (zero regressions)
- [ ] Calendar-disabled pipeline path unchanged
- [ ] Graceful degradation verified (mock API failure)

### After Phase 3
- [ ] Slash command works in test environment
- [ ] Embed formatting correct with/without event title
- [ ] Timezone edge cases handled

### After Phase 4
- [ ] `pytest` full suite green with new test count verified
- [ ] Config documentation complete
- [ ] Setup guide is accurate and actionable

---

## Rollback Procedure

1. **Immediate**: Set `calendar.enabled: false` in config.yaml, restart bot
2. **Code revert**: All changes are additive with default parameters; reverting `calendar_client.py` and the pipeline integration is sufficient
3. **No state cleanup**: Feature creates no persistent state (no DB migrations, no new files)
