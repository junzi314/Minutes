# Technical Specification: speaker-analytics

**Status**: ~95% implemented. This document reflects the actual codebase.

## 1. Overview

Aggregate per-speaker talk time, character count, and segment count from
Whisper transcription segments, then render a Unicode bar-graph inside the
Discord minutes Embed. The feature is gated by a single boolean config flag
(`speaker_analytics.enabled`, default `true`) and requires zero external
API calls or persistent state.

### Implementation Summary

| File | Type | Lines | Description |
|------|------|------:|-------------|
| `src/speaker_analytics.py` | New | 107 | `SpeakerStats` dataclass, `calculate_speaker_stats`, `format_stats_embed`, `_format_time` |
| `src/pipeline.py` | Modified | +7 | Conditional analytics call between transcribe and merge stages |
| `src/poster.py` | Modified | +10 | `speaker_stats` parameter on `build_minutes_embed` and `post_minutes`; embed field rendering |
| `src/config.py` | Modified | +5 | `SpeakerAnalyticsConfig` dataclass + `_SECTION_CLASSES` registration |
| `tests/test_speaker_analytics.py` | New | 138 | 13 unit tests (calculate + format) |
| `tests/test_poster.py` | Modified | +10 | 2 tests: embed with/without stats |
| `tests/test_pipeline.py` | Modified | +35 | 2 tests: enabled/disabled pipeline flow |

## 2. Architecture

### Data Flow

```
src/transcriber.py
  Transcriber.transcribe_all(tracks) -> list[Segment]
      |
      |  (pre-merge raw segments)
      v
src/speaker_analytics.py
  calculate_speaker_stats(segments) -> list[SpeakerStats]
  format_stats_embed(stats)         -> str
      |
      |  speaker_stats_text: str | None
      v
src/pipeline.py   run_pipeline_from_tracks()
  ... merge_transcripts(segments) ...
  ... generator.generate(transcript) ...
      |
      |  speaker_stats_text passed through
      v
src/poster.py
  post_minutes(speaker_stats=speaker_stats_text)
    -> build_minutes_embed(speaker_stats=...)
      -> embed.add_field(name="📊 話者統計", value=speaker_stats_text)
```

### Pipeline Placement (Pre-Merge)

Analytics runs on **raw segments** (between Stage 2: Transcribe and
Stage 3: Merge). This is intentional: the merger's gap-merge logic
extends `Segment.end` beyond actual speech boundaries, which would
inflate talk-time calculations. Pre-merge segments have accurate
per-utterance timings.

### Config Gate

```
src/config.py  SpeakerAnalyticsConfig.enabled (default: True)
      |
      v
src/pipeline.py  if cfg.speaker_analytics.enabled: ...
```

When disabled, no analytics code is imported or executed. The
`speaker_stats` parameter reaches `post_minutes` as `None`, and
no embed field is added.

## 3. Implementation Details

### 3.1 SpeakerStats Dataclass

```python
@dataclass(frozen=True)
class SpeakerStats:
    speaker: str          # Speaker username (from Segment.speaker)
    talk_time_sec: float  # Sum of (end - start) for all segments
    char_count: int       # Sum of len(text) for all segments
    segment_count: int    # Number of segments attributed to this speaker
```

Frozen dataclass ensures immutability after aggregation.

### 3.2 calculate_speaker_stats

**Signature**: `(segments: list[Segment]) -> list[SpeakerStats]`

**Algorithm** (single-pass, O(N)):

1. Return `[]` if input is empty.
2. Iterate over segments, accumulating into a `defaultdict` keyed by
   `speaker`:
   - `talk_time += seg.end - seg.start`
   - `chars += len(seg.text)`
   - `count += 1`
3. Build `SpeakerStats` objects from the accumulator.
4. Sort by `talk_time_sec` descending.

**Edge cases**:
- Empty input returns empty list.
- Single speaker returns a one-element list.
- Overlapping segments from the same speaker are summed (known limitation;
  segment overlap is rare in practice with VAD-filtered Whisper output).

### 3.3 format_stats_embed

**Signature**: `(stats, bar_width=10, max_speakers=10, max_chars=1024) -> str`

**Rendering rules**:

1. **Name truncation**: Speaker names exceeding 8 characters are truncated
   to 7 characters plus an ellipsis character. Names are left-aligned in
   an 8-character field.

2. **Bar graph**: Ratio is `talk_time / max_time` where `max_time` is the
   highest talk time among all speakers. Filled blocks are
   `round(ratio * bar_width)` of U+2588 (full block), remaining is
   U+2591 (light shade). The top speaker always gets a full bar.

3. **Time column**: Formatted as `M:SS` (right-aligned, 5 chars).

4. **Character count column**: Formatted with comma separator plus "字"
   suffix (right-aligned, 7 chars). Example: `1,234字`.

5. **Speaker overflow**: When `len(stats) > max_speakers`, only the top
   N speakers are shown, followed by a line "他M人" (M = remaining count).

6. **Length safety**: If the rendered string exceeds `max_chars`,
   `format_stats_embed` recursively retries with `bar_width - 2` (minimum 3).
   If still over after recursion, the result is hard-truncated at
   `max_chars`.

7. **Zero talk time**: `max_time` is floored to 1.0 to prevent
   division-by-zero. The bar renders as all empty blocks; time shows "0:00".

**Output example** (2 speakers):
```
alice    ██████████  1:00   100字
bob      █████░░░░░  0:30    50字
```

### 3.4 Pipeline Integration

Located in `src/pipeline.py`, `run_pipeline_from_tracks()`, lines 84-91:

```python
speaker_stats_text: str | None = None
if cfg.speaker_analytics.enabled:
    from src.speaker_analytics import calculate_speaker_stats, format_stats_embed
    stats = calculate_speaker_stats(segments)
    if stats:
        speaker_stats_text = format_stats_embed(stats)
```

The conditional import is intentional: when the feature is disabled, the
`src.speaker_analytics` module is never loaded. After the first enabled
invocation, Python's module cache ensures subsequent imports are free.

The `speaker_stats_text` variable is passed downstream to `post_minutes()`
via keyword argument.

### 3.5 Poster Integration

`build_minutes_embed()` accepts an optional `speaker_stats: str | None`
parameter. When non-None, it adds a field after "次のステップ" (next steps)
and before the footer:

```python
if speaker_stats:
    embed.add_field(
        name="\U0001f4ca 話者統計",
        value=_truncate(speaker_stats, 1024),
        inline=False,
    )
```

The value is truncated via the existing `_truncate()` helper to guarantee
Discord's 1024-character field value limit. The total embed length check
already present in `build_minutes_embed` handles overall embed size.

`post_minutes()` passes the parameter through to `build_minutes_embed()`.

## 4. Data Model

### SpeakerStats

| Field | Type | Description |
|-------|------|-------------|
| `speaker` | `str` | Speaker username extracted from Craig ZIP filenames |
| `talk_time_sec` | `float` | Cumulative talk time in seconds |
| `char_count` | `int` | Cumulative character count across all segments |
| `segment_count` | `int` | Number of transcription segments |

### SpeakerAnalyticsConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `True` | Enable/disable speaker statistics display |

Registered in `_SECTION_CLASSES` as `"speaker_analytics"`. Loaded from the
`speaker_analytics:` YAML section. Missing section uses dataclass defaults.
Environment variable override: `SPEAKER_ANALYTICS_ENABLED=true|false`.

## 5. Remaining Work

### C1 (Required): Add `speaker_analytics` section to config.yaml

The `SpeakerAnalyticsConfig` dataclass and `_SECTION_CLASSES` registration
are in place, but `config.yaml` does not yet contain the section. The feature
works because the dataclass default (`enabled=True`) applies when the YAML
section is absent. Adding the section makes the config self-documenting.

**Change to `config.yaml`** (add after the `google_drive:` section):

```yaml
speaker_analytics:
  # Enable per-speaker talk time and character count display in minutes embed
  enabled: true
```

### C2 (Recommended): Add config loading test to test_config.py

Verify that YAML loading produces a correct `SpeakerAnalyticsConfig` for
enabled, disabled, and missing-section cases.

**Tests to add to `tests/test_config.py`**:

```python
class TestSpeakerAnalyticsConfig:
    def test_speaker_analytics_default(self, tmp_path, monkeypatch):
        """Missing speaker_analytics section defaults to enabled=True."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            """)
        env_path = _write_env(tmp_path, "")
        cfg = load(str(cfg_path), str(env_path))
        assert cfg.speaker_analytics.enabled is True

    def test_speaker_analytics_disabled(self, tmp_path, monkeypatch):
        """Explicit enabled: false disables the feature."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            speaker_analytics:
              enabled: false
            """)
        env_path = _write_env(tmp_path, "")
        cfg = load(str(cfg_path), str(env_path))
        assert cfg.speaker_analytics.enabled is False

    def test_speaker_analytics_explicit_enabled(self, tmp_path, monkeypatch):
        """Explicit enabled: true works."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            speaker_analytics:
              enabled: true
            """)
        env_path = _write_env(tmp_path, "")
        cfg = load(str(cfg_path), str(env_path))
        assert cfg.speaker_analytics.enabled is True
```

## 6. Performance

| Metric | Value | Notes |
|--------|-------|-------|
| Time complexity | O(N) | Single pass over segments |
| Typical N | ~500 segments | 1-hour meeting with VAD |
| Wall-clock time | Sub-millisecond | Negligible vs. Whisper (~minutes) and Claude API (~seconds) |
| Memory overhead | O(S) | S = unique speaker count (typically < 20) |
| Pipeline timeout | 3600s | Analytics contribution: ~0% |

No external API calls, no I/O, no threading. Pure in-memory computation.

## 7. Testing

### Summary: 17 tests across 3 files

| File | Count | Scope |
|------|------:|-------|
| `tests/test_speaker_analytics.py` | 13 | Unit tests for `calculate_speaker_stats` and `format_stats_embed` |
| `tests/test_poster.py` | 2 | Embed construction with/without speaker stats |
| `tests/test_pipeline.py` | 2 | Pipeline flow: stats passed when enabled, `None` when disabled |

### test_speaker_analytics.py (13 tests)

**TestCalculateSpeakerStats** (5 tests):
- `test_empty_segments` -- empty input returns empty list
- `test_single_speaker` -- single speaker aggregation (time, chars, count)
- `test_multiple_speakers_sorted_by_time` -- multi-speaker sort by talk_time descending
- `test_char_count_accuracy` -- cumulative character count across segments
- `test_segment_count` -- per-speaker segment count

**TestFormatStatsEmbed** (8 tests):
- `test_empty_stats` -- empty input returns empty string
- `test_basic_format` -- two speakers, verifies names/times/chars/bar chars present
- `test_single_speaker_full_bar` -- one speaker gets 10 full blocks
- `test_max_speakers_truncation` -- 15 speakers with max_speakers=10 shows "他5人"
- `test_max_chars_limit` -- output stays within 1024 chars
- `test_long_speaker_name_truncated` -- 16-char name becomes 7 + ellipsis
- `test_char_count_with_comma` -- 1234 renders as "1,234字"
- `test_zero_talk_time` -- zero seconds does not cause division by zero

### test_poster.py (2 tests, within TestBuildMinutesEmbed)

- `test_embed_with_speaker_stats` -- stats text appears in "📊 話者統計" field
- `test_embed_without_speaker_stats` -- `None` stats produces no statistics field

### test_pipeline.py (2 tests, TestPipelineSpeakerAnalytics)

- `test_speaker_stats_passed_to_post_minutes` -- enabled config passes non-None stats containing speaker names
- `test_speaker_stats_disabled` -- disabled config passes `None` to `post_minutes`

## 8. Security

Not applicable. The feature:
- Processes only internal `Segment` data (no user input)
- Makes no external API calls
- Persists no data
- Has no authentication or authorization surface

## 9. Rollback Plan

### Level 1: Runtime disable (instant, zero-deploy)

Set in `config.yaml`:
```yaml
speaker_analytics:
  enabled: false
```

Restart the bot. Analytics code is not imported; `speaker_stats` is `None`
throughout the pipeline; no embed field is rendered. All other pipeline
behavior is unchanged.

### Level 2: Full code removal

1. Delete `src/speaker_analytics.py`
2. Delete `tests/test_speaker_analytics.py`
3. Revert additions in `src/pipeline.py` (lines 84-91, remove conditional
   analytics block; remove `speaker_stats=speaker_stats_text` from
   `post_minutes` call)
4. Revert `src/poster.py`: remove `speaker_stats` parameter from
   `build_minutes_embed` and `post_minutes`; remove the embed field block
5. Revert `src/config.py`: remove `SpeakerAnalyticsConfig` dataclass,
   `_SECTION_CLASSES` entry, and `Config.speaker_analytics` field
6. Remove `speaker_analytics:` section from `config.yaml`
7. Revert test additions in `test_poster.py` and `test_pipeline.py`

All changes are additive with optional parameters, so partial rollback
(e.g., removing only the pipeline integration) is also safe.
