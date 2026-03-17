# UX Design Document: Speaker Analytics

> Per-speaker talk time and character count visualization in meeting minutes Discord Embed.

## User Stories and Acceptance Criteria

### US-1: View speaker participation balance

**As a** meeting participant,
**I want to** see how much each person spoke in the meeting,
**so that** I can understand participation balance at a glance.

**Acceptance criteria:**

- [ ] The minutes Embed displays a bar graph showing each speaker's relative talk time
- [ ] Each line shows speaker name, visual bar, duration (M:SS), and character count
- [ ] Speakers are sorted by talk time descending (most active speaker first)
- [ ] The bar graph uses relative proportions (longest speaker = full bar)

### US-2: Disable statistics display

**As a** bot administrator,
**I want to** disable speaker statistics via configuration,
**so that** I can control whether this information appears in minutes.

**Acceptance criteria:**

- [ ] Setting `speaker_analytics.enabled: false` in `config.yaml` removes the field entirely
- [ ] When disabled, zero analytics code paths execute (no wasted computation)
- [ ] No Embed field is added when disabled
- [ ] Requires bot restart to take effect (not hot-reloadable)

---

## Display Design

### Embed Field Positioning

The speaker statistics appear as a single Embed field within the existing minutes Embed. The field order is:

```
+--------------------------------------------+
|  会議議事録 -- 2026-03-17 14:30             |  <- Embed title
|--------------------------------------------|
|  参加者                                     |  <- Field 1
|  Alice, Bob, Charlie                        |
|--------------------------------------------|
|  まとめ                                     |  <- Field 2
|  本会議では...                               |
|--------------------------------------------|
|  次のステップ                                |  <- Field 3
|  - [ ] Alice は...                          |
|--------------------------------------------|
|  📊 話者統計                                 |  <- Field 4 (this feature)
|  alice    ██████████ 5:23 1,234字           |
|  bob      ██████░░░░ 3:12   789字           |
|  charlie  ████░░░░░░ 2:01   456字           |
|--------------------------------------------|
|  詳細議事録は添付ファイルを参照               |  <- Footer
+--------------------------------------------+
```

- **Field name:** `📊 話者統計` (Unicode emoji + Japanese label)
- **Field inline:** `False` (full-width display)
- **Placement:** After "次のステップ" (next steps), before the footer
- **Conditional:** Only added when `speaker_stats` string is non-empty

### Integration Points

- `poster.py` `build_minutes_embed()` accepts an optional `speaker_stats: str | None` parameter
- `pipeline.py` computes statistics between transcription and merge stages, using pre-merge segments for accuracy
- The formatted string is passed through the pipeline as a plain text value

---

## Bar Graph Specification

### Line Format

Each speaker line follows this fixed-width pattern:

```
{name:<8s} {bar} {time:>5s} {chars:>7s}
```

Concrete example:

```
alice    ██████████ 5:23 1,234字
bob      ██████░░░░ 3:12   789字
charlie  ████░░░░░░ 2:01   456字
```

### Component Breakdown

| Component | Width | Alignment | Format | Example |
|-----------|-------|-----------|--------|---------|
| Speaker name | 8 chars (padded) | Left-aligned | Truncate at 7 + `...` if >8 | `alice   `, `verylon...` |
| Space | 1 char | -- | Literal space | ` ` |
| Bar graph | `bar_width` chars (default 10) | Left-aligned | `█` filled + `░` empty | `██████░░░░` |
| Space | 1 char | -- | Literal space | ` ` |
| Duration | 5 chars | Right-aligned | `M:SS` | ` 5:23` |
| Space | 1 char | -- | Literal space | ` ` |
| Character count | 7 chars | Right-aligned | `{n:,}字` | `1,234字` |

### Unicode Characters

| Character | Unicode | Name | Usage |
|-----------|---------|------|-------|
| `█` | U+2588 | FULL BLOCK | Filled portion of bar |
| `░` | U+2591 | LIGHT SHADE | Empty portion of bar |
| `…` | U+2026 | HORIZONTAL ELLIPSIS | Name truncation indicator |

### Proportional Scaling

The bar graph uses **relative proportions**, not absolute percentages:

1. Find the maximum `talk_time_sec` among all speakers
2. For each speaker: `ratio = talk_time_sec / max_time`
3. `filled_blocks = round(ratio * bar_width)`
4. The longest speaker always gets a full bar (`bar_width` filled blocks)
5. If `max_time <= 0`: fallback to `max_time = 1.0` to avoid division by zero

### Speaker Name Truncation

- Maximum display width: 8 characters
- Names longer than 8 characters: first 7 characters + `…` (ellipsis)
- Names 8 characters or shorter: displayed as-is, left-padded with spaces to 8 chars

Examples:
- `alice` -> `alice   ` (padded to 8)
- `verylongusername` -> `verylon…` (truncated to 7 + ellipsis)

---

## States

### State 1: Normal (2+ speakers)

The standard display. Multiple lines sorted by talk time descending.

```
📊 話者統計
alice    ██████████ 5:23 1,234字
bob      ██████░░░░ 3:12   789字
charlie  ████░░░░░░ 2:01   456字
```

**Trigger:** `calculate_speaker_stats()` returns 2+ `SpeakerStats` entries.

### State 2: Single Speaker

One speaker shown with a full bar (since they have 100% of the max time).

```
📊 話者統計
alice    ██████████ 5:23 1,234字
```

**Trigger:** `calculate_speaker_stats()` returns exactly 1 `SpeakerStats` entry.
**Behavior:** `ratio = time / time = 1.0`, so all blocks are filled.

### State 3: Empty (no segments)

No field is added to the Embed. The minutes Embed renders exactly as it would without the feature.

**Trigger:** `calculate_speaker_stats([])` returns `[]`, and `format_stats_embed([])` returns `""`.
**Pipeline behavior:** `speaker_stats_text` remains `None`; `build_minutes_embed()` skips the field.

### State 4: Disabled via Configuration

Completely absent from the pipeline. Zero code paths execute for analytics.

**Trigger:** `config.yaml` has `speaker_analytics.enabled: false`.
**Pipeline behavior:** The `if cfg.speaker_analytics.enabled:` guard in `pipeline.py` prevents any import or calculation. The `speaker_stats` parameter passed to `post_minutes()` remains `None`.

### State 5: Long Content (>1024 chars)

When the formatted output exceeds Discord's 1024-character Embed field value limit, the system applies recursive bar width reduction.

**Reduction sequence:** `10 -> 8 -> 6 -> 4` (decrements by 2 each step)

**Algorithm:**
1. Format with current `bar_width`
2. If `len(result) > max_chars` and `bar_width > 3`: retry with `bar_width - 2`
3. Recurse until either the output fits or `bar_width <= 3`
4. Final fallback: hard truncation at `max_chars` (`result[:1024]`)

**Example at bar_width=6:**
```
alice    ██████ 5:23 1,234字
bob      ████░░ 3:12   789字
```

---

## Overflow Handling

### Maximum Speakers

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_speakers` | 10 | Maximum number of individual speaker lines shown |

When total speakers exceed `max_speakers`:
- The top N speakers (by talk time) are shown individually
- A summary line `他{remaining}人` is appended

Example with 12 speakers and `max_speakers=10`:
```
user0    ██████████ 3:20    40字
user1    █████████░ 3:10    40字
...
user9    █░░░░░░░░░ 1:10    40字
他2人
```

### Character Limit Cascade

Discord Embed field values are limited to 1024 characters. The overflow handling cascade:

1. **Primary:** Render at `bar_width=10` -- if fits, done
2. **Reduction 1:** `bar_width=8` -- saves ~20 chars for 10 speakers
3. **Reduction 2:** `bar_width=6` -- saves ~40 chars total
4. **Reduction 3:** `bar_width=4` -- saves ~60 chars total
5. **Hard truncation:** Slice result to `max_chars` characters (last resort, may cut mid-line)

Additionally, `poster.py` applies its own `_truncate()` at 1024 chars with `...` suffix as a safety net.

---

## Configuration

### config.yaml

```yaml
speaker_analytics:
  enabled: true   # Set to false to disable entirely
```

### SpeakerAnalyticsConfig Dataclass

```python
@dataclass(frozen=True)
class SpeakerAnalyticsConfig:
    enabled: bool = True
```

### Environment Variable Override

Following the project's standard convention, the config value can be overridden via environment variable:

```
SPEAKER_ANALYTICS_ENABLED=false
```

### Behavior When Disabled

- No `speaker_analytics` module is imported in the pipeline
- No `calculate_speaker_stats()` or `format_stats_embed()` calls occur
- `speaker_stats_text` remains `None` throughout the pipeline
- `build_minutes_embed()` receives `speaker_stats=None` and skips the field
- Zero runtime cost

---

## Accessibility Notes

### Screen Reader Compatibility

- The bar graph is fully text-based, so screen readers will read the Unicode block characters
- Critically, **numeric values (duration and character count) accompany every bar**, ensuring the data is accessible even if the visual bar is not meaningful to non-visual users
- Speaker names are plaintext and readable

### Keyboard Navigation

- No interactive elements exist -- this is a read-only display within a Discord Embed
- No keyboard interaction is required or possible
- Users scroll through the Embed content using Discord's standard navigation

### Contrast and Readability

- `█` (U+2588 FULL BLOCK) and `░` (U+2591 LIGHT SHADE) provide strong visual contrast in both Discord light and dark themes
- Fixed-width padding (`{name:<8s}`, `{time:>5s}`, `{chars:>7s}`) maintains column alignment
- Discord does not guarantee monospace rendering in Embed fields (unlike code blocks), so minor misalignment is possible but does not affect data legibility since each line is self-contained

### Language

- All labels are in Japanese, consistent with the rest of the minutes Embed
- The `字` suffix (meaning "characters") is locale-appropriate for the target user base

---

## Known Limitations

### 1. Segment Overlap Inflates Total Duration

Craig Bot records separate audio tracks per speaker. When speakers talk simultaneously, each track captures the full segment independently. After transcription, the sum of all speakers' talk times can exceed the actual meeting duration.

**Impact:** The bar graph shows relative proportions, so the visual representation remains accurate. However, the absolute M:SS values may be slightly inflated compared to wall-clock time.

**Mitigation:** None currently. This is inherent to per-track recording and would require overlap detection to resolve.

### 2. No Absolute Percentage Display

The bar graph uses relative scaling (longest speaker = full bar). There is no percentage label showing each speaker's share of total talk time.

**Impact:** Users can visually compare speakers but cannot read exact percentages.

**Status:** Identified as Nice-to-Have in the original REQUEST.md. Deferred.

### 3. No Persistent Statistics History

Statistics are calculated fresh from segments on every pipeline run. There is no cross-meeting aggregation, trend tracking, or historical comparison.

**Impact:** Each meeting's statistics are standalone. Users cannot query patterns like "Who talked the most across the last 5 meetings?"

### 4. No Monospace Guarantee in Embed Fields

Discord Embed field values do not use a monospace font. Column alignment via Python format specifiers (`<8s`, `>5s`, `>7s`) produces approximate alignment that may vary across clients and platforms.

**Impact:** Columns may not align perfectly on all Discord clients.

**Mitigation:** Each line is self-contained with all data points (name, bar, time, chars), so misalignment does not cause data loss.

### 5. Pre-Merge Segment Usage

Statistics are calculated from pre-merge segments (before `merger.py` combines consecutive same-speaker segments). This is intentional -- merged segments inflate duration by including inter-segment gaps within the merge threshold.

**Impact:** More accurate talk time measurement, but the segment count in `SpeakerStats` reflects raw Whisper segments, not the merged transcript's speaker turns.

---

## Future Enhancements

### Percentage Display (Nice-to-Have, deferred)

Add an optional percentage column showing each speaker's share of total talk time:

```
alice    ██████████ 5:23 1,234字 (47%)
bob      ██████░░░░ 3:12   789字 (28%)
charlie  ████░░░░░░ 2:01   456字 (18%)
```

Consideration: Requires careful handling of the 1024-char limit since each line grows by ~5 characters.

### Markdown Table in Attachment

Include a formatted statistics table in the `.md` file attachment:

```markdown
## 話者統計

| 話者 | 発言時間 | 文字数 | 割合 |
|------|---------|--------|------|
| alice | 5:23 | 1,234 | 47% |
| bob | 3:12 | 789 | 28% |
```

### Per-Guild Toggle

Currently `speaker_analytics.enabled` is a global toggle. A per-guild override would allow different servers to have different preferences:

```yaml
discord:
  guilds:
    - guild_id: 123
      speaker_analytics: true
    - guild_id: 456
      speaker_analytics: false
```

### Cross-Meeting Trends

Integrate with `MinutesArchive` to enable queries like "show speaker balance trends for the last N meetings." Would require storing `SpeakerStats` alongside archived minutes.

### Overlap-Aware Duration

Detect overlapping segments across speakers and adjust talk time calculations to avoid double-counting simultaneous speech. This would make the absolute M:SS values more accurate without affecting relative proportions.
