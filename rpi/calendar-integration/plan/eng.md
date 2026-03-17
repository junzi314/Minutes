# Engineering Specification: Calendar Integration

**Feature Slug**: calendar-integration
**Date**: 2026-03-17
**Size**: L (Medium-Complex)
**Estimated LOC**: ~450 (new) + ~80 (modified)

---

## 1. Architecture Overview

### Integration Point in Pipeline

```
run_pipeline_from_tracks()
  |
  Stage 2: Transcribe (transcriber.py)
  |
  Stage 3: Merge (merger.py)
  |
  Stage 3.5: Calendar Fetch (NEW - calendar_client.py)  <-- inserted here
  |
  Stage 4: Generate (generator.py) -- receives calendar context
  |
  Stage 5: Post (poster.py) -- embed includes event title
```

Calendar fetch is inserted between merge and generate because:
- It needs no data from transcription (only recording timestamps)
- It must complete before generation (to inject template variables)
- It can run concurrently with transcript formatting (optimization opportunity)

### Module Dependency Graph

```
config.py (CalendarConfig)
    |
    v
calendar_client.py (CalendarClient) <-- new module
    |
    v
pipeline.py (orchestration) -- calls calendar_client, passes result to generator
    |
    +-- generator.py (render_prompt with new variables)
    +-- poster.py (embed with event title field)
```

---

## 2. New Module: `src/calendar_client.py`

### Data Structures

```python
@dataclass(frozen=True)
class CalendarEvent:
    """Metadata from a Google Calendar event."""
    title: str
    attendees: list[str]        # email addresses or display names
    description: str
    start: datetime             # timezone-aware (UTC)
    end: datetime               # timezone-aware (UTC)
    calendar_id: str
    event_id: str


@dataclass(frozen=True)
class CalendarFetchResult:
    """Result of a calendar lookup for a recording time window."""
    event: CalendarEvent | None
    candidates_count: int       # how many events were in the time window
    fetch_duration_sec: float
    error: str | None = None    # non-None if fetch failed (graceful degradation)
```

### CalendarClient Class

```python
class CalendarClient:
    """Google Calendar API client for fetching events by time window.

    Authentication reuses the Service Account pattern from DriveWatcher.
    The Google API client is synchronous, so all API calls are wrapped
    in asyncio.to_thread().
    """

    def __init__(self, cfg: CalendarConfig) -> None:
        self._cfg = cfg
        self._service: Any = None  # cached API service

    def _build_service(self) -> Any:
        """Build and cache the Google Calendar API v3 service.

        Reuses credentials.json with calendar.readonly scope.
        Pattern follows drive_watcher.py:_build_service().
        """
        ...

    def _list_events_sync(
        self, time_min: datetime, time_max: datetime
    ) -> list[dict]:
        """Call Events.list synchronously. Run in executor."""
        ...

    async def fetch_event(
        self,
        recording_start: datetime,
        recording_end: datetime,
    ) -> CalendarFetchResult:
        """Find the best-matching calendar event for a recording.

        1. Extends time window by match_tolerance_minutes in each direction
        2. Queries Events.list with timeMin/timeMax
        3. Scores events by overlap duration with recording window
        4. Returns the highest-scoring event, or None

        Never raises -- returns CalendarFetchResult with error field on failure.
        """
        ...

    @staticmethod
    def _compute_overlap(
        event_start: datetime,
        event_end: datetime,
        rec_start: datetime,
        rec_end: datetime,
    ) -> float:
        """Compute overlap in seconds between two time ranges."""
        ...

    @staticmethod
    def _parse_event(raw: dict) -> CalendarEvent:
        """Parse a Google Calendar API event resource into CalendarEvent."""
        ...
```

### Events.list API Parameters

```python
events = service.events().list(
    calendarId=cfg.calendar_id,
    timeMin=time_min.isoformat(),    # RFC3339
    timeMax=time_max.isoformat(),    # RFC3339
    singleEvents=True,               # expand recurring events
    orderBy="startTime",
    maxResults=10,
).execute()
```

### Error Handling Strategy

`fetch_event()` catches all exceptions internally and returns a `CalendarFetchResult` with `event=None` and `error` set. This ensures the pipeline never fails due to calendar issues.

```python
async def fetch_event(self, ...) -> CalendarFetchResult:
    try:
        ...
    except Exception as exc:
        logger.warning("Calendar fetch failed: %s", exc)
        return CalendarFetchResult(
            event=None, candidates_count=0,
            fetch_duration_sec=elapsed, error=str(exc),
        )
```

---

## 3. Configuration Changes: `src/config.py`

### New CalendarConfig Dataclass

```python
@dataclass(frozen=True)
class CalendarConfig:
    enabled: bool = False
    credentials_path: str = "credentials.json"
    calendar_id: str = "primary"
    timezone: str = "Asia/Tokyo"
    match_tolerance_minutes: int = 30
    api_timeout_sec: int = 10
    max_retries: int = 2
```

### Registration

Add to `_SECTION_CLASSES`:

```python
_SECTION_CLASSES: dict[str, type] = {
    ...
    "calendar": CalendarConfig,
}
```

Add to `Config` dataclass:

```python
@dataclass(frozen=True)
class Config:
    ...
    calendar: CalendarConfig
```

### Environment Variable Overrides

Following existing convention, these env vars will work automatically:
- `CALENDAR_ENABLED=true`
- `CALENDAR_CREDENTIALS_PATH=/path/to/creds.json`
- `CALENDAR_CALENDAR_ID=team@group.calendar.google.com`
- `CALENDAR_TIMEZONE=UTC`
- `CALENDAR_MATCH_TOLERANCE_MINUTES=60`
- `CALENDAR_API_TIMEOUT_SEC=15`
- `CALENDAR_MAX_RETRIES=3`

### Validation

Add to `_validate()`:

```python
if cfg.calendar.enabled:
    if not cfg.calendar.calendar_id:
        errors.append("calendar.calendar_id is required when calendar.enabled is true")
    if cfg.calendar.match_tolerance_minutes < 0:
        errors.append("calendar.match_tolerance_minutes must be >= 0")
    if cfg.calendar.api_timeout_sec < 1:
        errors.append("calendar.api_timeout_sec must be >= 1")
```

---

## 4. Recording Timestamp Strategy

### Problem

`DetectedRecording` and `run_pipeline_from_tracks()` lack recording start/end timestamps. The current `date_str` is `datetime.now()` at pipeline execution time, not recording time.

### Solution: Multi-Strategy Fallback

Three strategies, tried in order:

| Priority | Strategy | Source | Accuracy |
|----------|----------|--------|----------|
| 1 | Whisper segment boundaries | `min(seg.start)` / `max(seg.end)` from transcription | High (relative within recording; needs anchor) |
| 2 | Pipeline execution timestamp | `datetime.now()` at pipeline start | Medium (minutes of delay from recording end) |
| 3 | File modification timestamp | ZIP file mtime (Drive watcher path) | Low-Medium |

### Implementation

The recording time window is derived from Whisper segment data combined with the pipeline timestamp:

```python
def estimate_recording_window(
    segments: list[Segment],
    pipeline_start: datetime,
    timezone_str: str = "Asia/Tokyo",
) -> tuple[datetime, datetime]:
    """Estimate recording start/end from Whisper segments.

    Whisper segment timestamps are relative to the start of each audio file.
    The maximum segment.end gives us the recording duration. Combined with
    the pipeline execution time (which is close to recording end), we can
    estimate the absolute recording window.

    Returns (estimated_start, estimated_end) as timezone-aware datetimes.
    """
    if not segments:
        # Fallback: assume 1-hour meeting ending now
        tz = ZoneInfo(timezone_str)
        end = pipeline_start.replace(tzinfo=tz)
        start = end - timedelta(hours=1)
        return start, end

    # Recording duration from Whisper segments
    max_end = max(seg.end for seg in segments)
    duration = timedelta(seconds=max_end)

    tz = ZoneInfo(timezone_str)
    estimated_end = pipeline_start.replace(tzinfo=tz)
    estimated_start = estimated_end - duration

    return estimated_start, estimated_end
```

This approach:
- Uses real recording duration from Whisper (reliable)
- Anchors to pipeline execution time (close to recording end, within minutes)
- Combined with `match_tolerance_minutes` config (default 30 min), provides adequate search window

---

## 5. Pipeline Integration: `src/pipeline.py`

### Changes to `run_pipeline_from_tracks()`

```python
async def run_pipeline_from_tracks(
    tracks: list[SpeakerAudio],
    cfg: Config,
    transcriber: Transcriber,
    generator: MinutesGenerator,
    output_channel: OutputChannel,
    state_store: StateStore,
    source_label: str = "unknown",
    template_name: str = "minutes",
    archive: MinutesArchive | None = None,
) -> None:
    ...
    # After Stage 3 (merge), before Stage 4 (generate):

    # Stage 3.5: Calendar event fetch (optional, non-blocking)
    event_title = ""
    event_attendees = ""
    event_description = ""

    if cfg.calendar.enabled:
        from src.calendar_client import CalendarClient, estimate_recording_window

        rec_start, rec_end = estimate_recording_window(
            segments, datetime.now(), cfg.calendar.timezone,
        )
        calendar_client = CalendarClient(cfg.calendar)
        result = await calendar_client.fetch_event(rec_start, rec_end)

        if result.event:
            event_title = result.event.title
            event_attendees = ", ".join(result.event.attendees)
            event_description = result.event.description
            logger.info(
                "Calendar event matched: '%s' (overlap with %d candidates)",
                event_title, result.candidates_count,
            )
        elif result.error:
            logger.warning("Calendar fetch error: %s", result.error)
        else:
            logger.info("No calendar event found for recording window")

    # Stage 4: Generate (pass calendar variables)
    minutes_md = await generator.generate(
        transcript=transcript,
        date=date_str,
        speakers=speakers_str,
        guild_name=guild_name,
        channel_name=output_channel.name,
        template_name=template_name,
        event_title=event_title,
        event_attendees=event_attendees,
        event_description=event_description,
    )
```

---

## 6. Generator Changes: `src/generator.py`

### `render_prompt()` -- Add Calendar Variables

```python
def render_prompt(
    self,
    transcript: str,
    date: str,
    speakers: str,
    guild_name: str = "",
    channel_name: str = "",
    template_name: str = "minutes",
    event_title: str = "",           # NEW
    event_attendees: str = "",       # NEW
    event_description: str = "",     # NEW
) -> str:
    template = self._load_template(template_name)
    replacements = {
        "{transcript}": transcript,
        "{date}": date,
        "{speakers}": speakers,
        "{guild_name}": guild_name,
        "{channel_name}": channel_name,
        "{event_title}": event_title,             # NEW
        "{event_attendees}": event_attendees,      # NEW
        "{event_description}": event_description,  # NEW
    }
    result = template
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)
    return result
```

### `generate()` -- Pass Through Calendar Variables

Same parameter additions as `render_prompt()`, forwarded directly.

### Backward Compatibility

If a template does not contain `{event_title}` / `{event_attendees}` / `{event_description}`, the replacements are no-ops (replacing a substring that does not exist is safe). Existing templates work unchanged.

---

## 7. Poster Changes: `src/poster.py`

### `build_minutes_embed()` -- Optional Event Title Field

```python
def build_minutes_embed(
    minutes_md: str,
    date: str,
    speakers: str,
    cfg: PosterConfig,
    speaker_stats: str | None = None,
    event_title: str | None = None,       # NEW
) -> discord.Embed:
    ...
    # After title, before participants:
    if event_title:
        embed.add_field(
            name="会議名",
            value=event_title,
            inline=False,
        )
    ...
```

### `post_minutes()` -- Pass Event Title

```python
async def post_minutes(
    ...
    event_title: str | None = None,    # NEW
) -> discord.Message:
    embed = build_minutes_embed(
        ..., event_title=event_title,
    )
    ...
```

---

## 8. Prompt Template Changes: `prompts/minutes.txt`

### Addition to Meeting Info Section

```
## 会議情報
- 日時: {date}
- サーバー: {guild_name}
- チャンネル: {channel_name}
- 参加者: {speakers}
- 会議名: {event_title}
- カレンダー参加者: {event_attendees}
- アジェンダ/説明: {event_description}
```

### Additional Instruction

Add to output rules:

```
9. 会議名・カレンダー参加者・アジェンダが空欄の場合は、その項目を出力に含めないでください。
   会議名がある場合は、議事録のタイトルに会議名を含めてください。
```

---

## 9. Error Hierarchy: `src/errors.py`

### New Exception

```python
class CalendarError(MinutesBotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, stage="calendar")
```

Note: `CalendarError` is defined for completeness and logging, but `CalendarClient.fetch_event()` catches all exceptions internally and never raises. Other modules (e.g., future calendar write-back) may use it.

---

## 10. Bot Entry Point: `bot.py`

### Initialization

No new initialization required at startup. `CalendarClient` is instantiated per-pipeline-run (stateless). The Google API service is cached within the client instance.

### New Slash Command: `/minutes calendar-status`

```python
@group.command(name="calendar-status", description="Show calendar integration status")
async def minutes_calendar_status(interaction: discord.Interaction) -> None:
    cal_cfg = client.cfg.calendar
    if not cal_cfg.enabled:
        await interaction.response.send_message(
            "カレンダー連携は**無効**です。\nconfig.yamlの `calendar.enabled` を `true` に設定してください。",
            ephemeral=True,
        )
        return

    lines = [
        f"**カレンダー連携**: 有効",
        f"**カレンダーID**: `{cal_cfg.calendar_id}`",
        f"**タイムゾーン**: {cal_cfg.timezone}",
        f"**マッチ許容範囲**: 前後{cal_cfg.match_tolerance_minutes}分",
    ]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)
```

---

## 11. Test Plan

### Unit Tests: `tests/test_calendar_client.py` (~150 lines)

| Test | Description |
|------|-------------|
| `test_parse_event_basic` | Parse a standard Google Calendar event response |
| `test_parse_event_all_day` | Handle all-day events (date vs dateTime) |
| `test_parse_event_no_attendees` | Event with no attendees list |
| `test_parse_event_no_description` | Event with empty description |
| `test_compute_overlap_full` | Event fully contains recording |
| `test_compute_overlap_partial` | Partial overlap (event starts before, ends during) |
| `test_compute_overlap_none` | No overlap (disjoint time ranges) |
| `test_fetch_event_single_match` | Single event in time window |
| `test_fetch_event_multiple_best_overlap` | Multiple events, best overlap selected |
| `test_fetch_event_no_match` | No events in time window |
| `test_fetch_event_api_error` | API failure returns graceful result |
| `test_fetch_event_timeout` | Timeout returns graceful result |
| `test_estimate_recording_window` | Timestamp estimation from Whisper segments |
| `test_estimate_recording_window_empty` | Empty segments fallback |

### Unit Tests: `tests/test_generator.py` additions (~20 lines)

| Test | Description |
|------|-------------|
| `test_render_prompt_with_calendar_vars` | Calendar variables are correctly substituted |
| `test_render_prompt_empty_calendar_vars` | Empty calendar variables produce clean output |
| `test_render_prompt_backward_compat` | Template without calendar vars works unchanged |

### Unit Tests: `tests/test_pipeline.py` additions (~30 lines)

| Test | Description |
|------|-------------|
| `test_pipeline_with_calendar_enabled` | Calendar fetch is called when enabled |
| `test_pipeline_with_calendar_disabled` | Calendar fetch is skipped when disabled |
| `test_pipeline_calendar_error_continues` | Pipeline continues on calendar failure |

### Unit Tests: `tests/test_poster.py` additions (~15 lines)

| Test | Description |
|------|-------------|
| `test_embed_with_event_title` | Event title field appears in embed |
| `test_embed_without_event_title` | No event title field when None/empty |

### Unit Tests: `tests/test_config.py` additions (~20 lines)

| Test | Description |
|------|-------------|
| `test_calendar_config_defaults` | Default values are correct |
| `test_calendar_config_from_yaml` | YAML parsing works |
| `test_calendar_config_validation` | Validation catches invalid values |
| `test_calendar_config_env_override` | Environment variable overrides work |

---

## 12. Dependencies

### External (No New Packages)

| Package | Version | Purpose | Status |
|---------|---------|---------|--------|
| `google-api-python-client` | 2.189.0 | Calendar API calls | Already installed |
| `google-auth` | 2.48.0 | Service Account auth | Already installed |

### Internal

| Module | Change Type | Scope |
|--------|-------------|-------|
| `src/calendar_client.py` | **New** | CalendarClient, CalendarEvent, CalendarFetchResult, estimate_recording_window |
| `src/config.py` | Modified | Add CalendarConfig, register in _SECTION_CLASSES, add to Config, add validation |
| `src/errors.py` | Modified | Add CalendarError |
| `src/pipeline.py` | Modified | Insert calendar fetch stage, pass variables to generator and poster |
| `src/generator.py` | Modified | Add event_title/event_attendees/event_description parameters |
| `src/poster.py` | Modified | Add event_title parameter to embed and post functions |
| `bot.py` | Modified | Add /minutes calendar-status command |
| `config.yaml` | Modified | Add calendar: section |
| `prompts/minutes.txt` | Modified | Add calendar template variables and conditional output rule |

---

## 13. Performance Considerations

| Concern | Mitigation |
|---------|------------|
| Calendar API latency | 10-second timeout; async execution; does not block transcription |
| API quota | 1M calls/day free tier; 1 call per pipeline run; effectively unlimited |
| Memory | CalendarClient is stateless (created per run); Google API service object is lightweight |
| Concurrency | Single CalendarClient per pipeline run; no shared mutable state |

---

## 14. Security Considerations

| Concern | Mitigation |
|---------|------------|
| Calendar data exposure | Calendar data is used only in the prompt (not logged at INFO level); event details appear in generated minutes which are already guild-scoped |
| Scope creep | Calendar scope is `readonly`; no write access to user calendars |
| Credentials | Same `credentials.json` as Drive watcher; no new credential files |
| Input sanitization | Calendar event fields are used in string replacement (same as other template vars); no code execution risk |

---

## 15. Rollback Strategy

1. Set `calendar.enabled: false` in config.yaml -- immediate feature disable
2. No database migrations or state changes to revert
3. No changes to existing module signatures (all new parameters have defaults)
4. All modified functions maintain backward compatibility via default arguments
