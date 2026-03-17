# Product Requirements: speaker-analytics (Merge Readiness)

**Traceability**: R-83
**Status**: ~95% implemented -- merge-ready pending two minor gaps (C1, C2)
**Module**: `src/speaker_analytics.py` (new), with integration in `pipeline.py`, `poster.py`, `config.py`

---

## 1. Context and Why Now

The Discord Minutes Bot generates meeting minutes from Craig Bot recordings, but the output has always been text-only -- summaries, decisions, and action items. There is no visibility into *who* participated *how much*.

Meeting facilitators and team leads frequently want a quick, at-a-glance view of participation balance without leaving Discord. This matters because:

- **Unbalanced meetings are invisible today.** A facilitator cannot tell from the summary alone whether one person dominated the conversation or whether all attendees contributed.
- **Zero marginal cost.** The data already exists. Every `Segment` from faster-whisper carries `start`, `end`, `text`, and `speaker` fields. Aggregation is an O(N) scan over segments the pipeline already holds in memory.
- **No external dependencies.** No new APIs, no new infrastructure, no new Python packages. The feature is a pure computation + formatting layer on existing data.

This feature was listed as extension candidate Ext-3 in the original project roadmap (section 9: "today's extensions"). Implementation was completed ahead of other extensions because the data surface area is minimal and the integration surface is narrow (three touchpoints).

---

## 2. Users and Jobs-to-Be-Done

| User | Job | Outcome |
|------|-----|---------|
| Meeting facilitator | Understand participation balance after each meeting | Sees per-speaker bar chart in the minutes Embed without opening any files |
| Team lead / manager | Identify consistently quiet participants across recurring meetings | Notices at a glance who spoke less, can follow up offline |
| Bot administrator | Control whether statistics appear in the Embed | Toggles `speaker_analytics.enabled` in config.yaml |
| Meeting participant | Verify their own contribution was captured | Confirms their name, time, and character count in the stats field |

### User Stories

**US-1: Automatic stats display**
As a meeting facilitator, I want the minutes Embed to show per-speaker speaking time and character count, so that I can assess participation balance without additional tooling.

**US-2: Disable statistics**
As a Bot administrator, I want to set `speaker_analytics.enabled: false` in config.yaml, so that the stats field is entirely omitted when my team prefers a cleaner Embed.

**US-3: Visual comparison**
As a meeting participant, I want a bar chart that makes relative speaking proportions obvious, so that I can quickly see whether the meeting was balanced or dominated by one speaker.

**US-4: Large-meeting resilience**
As a user in a meeting with more than 10 participants, I want the stats display to remain readable, so that the Embed does not become cluttered or exceed Discord limits.

---

## 3. Functional Requirements

### FR-1: Per-speaker statistics aggregation
Calculate speaking time (seconds), character count, and segment count for each unique speaker from the list of `Segment` objects.

**Acceptance criteria:**
- [x] `calculate_speaker_stats(segments)` returns a list of `SpeakerStats` sorted by `talk_time_sec` descending
- [x] Empty input returns an empty list
- [x] Aggregation is O(N) in the number of segments
- [x] Zero talk time does not cause division-by-zero errors

### FR-2: Text bar graph formatting
Format aggregated stats as a fixed-width Unicode bar chart suitable for a Discord Embed field.

**Acceptance criteria:**
- [x] Bar uses Unicode block characters: filled (`U+2588`) and light shade (`U+2591`)
- [x] Speaker names truncated to 8 characters (7 + ellipsis for longer names)
- [x] Time displayed as `M:SS`, character count displayed with comma grouping and `字` suffix
- [x] Maximum 10 speakers shown; overflow displays as `他N人`
- [x] Output never exceeds `max_chars` (default 1024, Discord Embed field limit)
- [x] Recursive bar_width reduction when output exceeds max_chars

### FR-3: Pipeline integration
Invoke analytics between transcription and merge stages, passing the result to the poster.

**Acceptance criteria:**
- [x] Analytics runs on pre-merge segments (not merged transcript) for per-speaker accuracy
- [x] Conditional on `cfg.speaker_analytics.enabled`
- [x] When disabled, `speaker_stats` is `None` and no import occurs (lazy import)
- [x] `speaker_stats_text` passed as keyword argument to `post_minutes()`

### FR-4: Embed display
Add a `話者統計` field to the minutes Embed.

**Acceptance criteria:**
- [x] Field name is `📊 話者統計`
- [x] Field is `inline=False`
- [x] Positioned after "次のステップ", before footer
- [x] Field value truncated to 1024 characters via `_truncate()`
- [x] Field omitted entirely when `speaker_stats` is `None`

### FR-5: Configuration
Provide a config section to enable or disable the feature.

**Acceptance criteria:**
- [x] `SpeakerAnalyticsConfig(enabled: bool = True)` frozen dataclass in `config.py`
- [x] Registered in `_SECTION_CLASSES` for YAML loading
- [x] Accessible as `cfg.speaker_analytics.enabled` in the pipeline
- [ ] **C1 (gap):** `config.yaml` needs a `speaker_analytics:` section with `enabled: true` (2 lines)

### FR-6: Test coverage
Unit and integration tests for all layers.

**Acceptance criteria:**
- [x] 13 unit tests in `test_speaker_analytics.py` (empty input, single/multi speaker, sorting, char count, segment count, bar format, name truncation, max_speakers overflow, max_chars limit, comma formatting, zero talk time)
- [x] 2 integration tests in `test_poster.py` (embed with stats, embed without stats)
- [x] 2 integration tests in `test_pipeline.py` (stats enabled passes data, stats disabled passes None)
- [ ] **C2 (gap):** `test_config.py` should verify `SpeakerAnalyticsConfig` loads from YAML (~15 lines)

---

## 4. Non-Functional Requirements

### Performance
- **Aggregation complexity:** O(N) where N = total segment count. A typical 1-hour meeting produces ~500-2000 segments; aggregation takes <1ms.
- **Formatting complexity:** O(S) where S = unique speakers (typically <20). String formatting is negligible.
- **Pipeline impact:** No measurable increase in end-to-end latency. No blocking I/O, no API calls, no file I/O.

### Scale
- Handles meetings with any number of speakers. Display is capped at 10 with overflow notation.
- Recursive bar_width reduction ensures output fits within Discord's 1024-character Embed field limit regardless of input size.

### Reliability
- Feature failure is non-blocking. If `calculate_speaker_stats` or `format_stats_embed` raises, the pipeline should still produce minutes without the stats field. (Current implementation: exception would propagate; acceptable risk given the simplicity of the code path.)
- Zero-division guard: `max_time` floors to 1.0 when all speakers have 0 talk time.

### Privacy
- No new data is persisted. Statistics are computed in-memory and discarded after Embed posting.
- Speaker names are sourced from Craig Bot ZIP filenames (already exposed in the transcript).

### Security
- No new inputs from external users. All data flows from the existing trusted pipeline (faster-whisper segments).

### Observability
- No dedicated metrics or logging added. Stats computation is silent. Pipeline-level logging already covers the surrounding stages.

---

## 5. Scope

### In Scope (implemented)
- Per-speaker talk time aggregation from Segment data
- Per-speaker character count aggregation from Segment text
- Unicode text bar chart in Discord Embed field
- Configuration toggle (`speaker_analytics.enabled`)
- 17 tests across 3 test files

### Out of Scope (deferred)
- Markdown stats table in the `.md` minutes file attachment
- Percentage display (e.g., `42%`) alongside absolute values
- Historical comparison across meetings
- Real-time stats during recording
- Graph image generation (PNG/SVG)
- Per-command toggle (`/minutes process --no-stats`)
- Statistics data persistence or search

---

## 6. Business Value

| Dimension | Impact |
|-----------|--------|
| **User value** | Instant participation visibility without leaving Discord |
| **Cost** | Zero incremental cost -- no new API calls, no new infra, no new dependencies |
| **Engagement** | Visual stats make minutes more engaging; users more likely to review minutes |
| **Facilitation** | Enables data-driven meeting improvement (identify dominant speakers, encourage quiet participants) |
| **Differentiation** | Few meeting-minutes tools provide per-speaker analytics inline |

---

## 7. Success Metrics

| Metric | Type | Target | Measurement |
|--------|------|--------|-------------|
| Feature enabled in production config | Leading | 100% of guilds | `config.yaml` has `speaker_analytics.enabled: true` |
| Stats field present in posted Embeds | Leading | 100% of minutes with 2+ speakers | Spot-check Discord output |
| No pipeline failures caused by analytics | Lagging | 0 errors attributed to `speaker_analytics` | Log monitoring |
| Test pass rate | Leading | 17/17 tests green | `pytest` CI |

---

## 8. Rollout Plan

### Phase 1: Close gaps (est. 15 minutes)
1. **C1**: Add `speaker_analytics:` section to `config.yaml` (2 lines: section header + `enabled: true`)
2. **C2**: Add `SpeakerAnalyticsConfig` YAML loading test to `test_config.py` (~15 lines)
3. Run full test suite, confirm 17 speaker-analytics tests + new config test all pass

### Phase 2: Merge
1. Create PR from working tree changes
2. Reviewer checklist: confirm all 6 FRs, confirm config.yaml section present, confirm test count
3. Merge to main

### Phase 3: Production validation
1. Trigger a test recording in a non-production guild
2. Verify `📊 話者統計` field appears in the posted Embed
3. Verify disable toggle works (set `enabled: false`, reprocess, confirm field absent)

---

## 9. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Segment overlap inflates talk time.** If faster-whisper produces overlapping segments for the same speaker, `talk_time_sec` sums both, overcounting. | Low | Low | Accepted for v1. True overlap is rare with VAD enabled. Future: add overlap-aware duration calculation. |
| **Config discoverability.** New users may not know `speaker_analytics` exists in config.yaml if the section is missing (gap C1). | Medium | Low | C1 fix adds the section with a default-on value. |
| **Long speaker names in bar chart.** 8-char truncation may make similar names ambiguous (e.g., `tanaka_h` and `tanaka_k` both become `tanaka_…`). | Low | Low | Accepted. Discord usernames are typically unique within 7 characters. |
| **Embed total length budget.** Adding a stats field reduces space available for summary/decisions fields. | Low | Medium | Existing `max_embed_length` trimming logic in `build_minutes_embed` handles overshoot by trimming the summary field. |

---

## 10. Dependencies

- **Upstream**: None. Uses existing `Segment` dataclass from `src/transcriber.py`.
- **Downstream**: None. No other features depend on speaker analytics.
- **External**: None. No new APIs, packages, or services.

---

## 11. Open Questions

1. **Should the stats field be wrapped in a code block for monospace alignment?** Current implementation uses bare text. A `` ```\n...\n``` `` wrapper would ensure alignment but consumes 8 characters of the 1024 budget.
2. **Should percentage be added alongside absolute values?** Deferred to Nice-to-Have per REQUEST.md, but easy to add later.

---

## Appendix: Bar Graph Format

```
alice    ██████████ 5:23 1,234字
bob      ██████░░░░ 3:12   789字
charlie  ████░░░░░░ 2:01   456字
```

Each line: `{name:<8} {bar:10} {time:>5} {chars:>7}`
- Bar width scales linearly relative to the top speaker's talk time
- `█` = filled proportion, `░` = remaining proportion
- When >10 speakers: final line shows `他N人`
