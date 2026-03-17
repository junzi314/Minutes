# Product Requirements: Multilingual Support (日英混在対応)

## Context & Why Now

The Discord Minutes Bot currently ships with `language: "ja"` hardcoded in `config.yaml`.
When meetings contain English phrases, technical terms, or fully bilingual discussion,
Whisper forces all output through the Japanese decoder. This produces garbled roman-script
segments and reduced transcription accuracy, which cascades into lower-quality
Claude-generated minutes.

Why now: the core machinery for auto-detect already exists in the codebase (merged in the
initial implementation). The only production gap is that `config.yaml` still defaults to
`"ja"`, meaning no user benefits from the capability unless they manually edit the config.
Shipping the default change is a single-line, zero-risk-to-code operation, but it needs a
deliberate GPU validation gate before rollout.

### Implementation status

| Component | Status | Evidence |
|-----------|--------|----------|
| `transcriber.py` auto-detect logic | IMPLEMENTED | Line 80: `language = None if self._cfg.language == "auto" else self._cfg.language` |
| `config.py` validation for "auto" | IMPLEMENTED | Lines 27-32: `VALID_WHISPER_LANGUAGES` includes "auto"; lines 344-348 reject invalid codes |
| Unit tests (4 cases) | IMPLEMENTED | `test_auto_language_passes_none`, `test_explicit_language_passes_through`, `test_invalid_whisper_language_rejected`, `test_auto_whisper_language_accepted` |
| `config.yaml` default | **NOT YET CHANGED** | Line 41 still reads `language: "ja"` |

## Users & Jobs To Be Done

### US-1: Guild administrator / meeting organizer

**As a** guild administrator running meetings with bilingual participants,
**I want** English terms, proper nouns, and code references transcribed verbatim
**so that** the auto-generated minutes are trustworthy without manual post-editing.

**Acceptance criteria:**
- `language: "auto"` in `config.yaml` causes Whisper to auto-detect language per file.
- English words in a mixed Japanese-English recording appear as roman script, not katakana transliteration.
- Processing time with auto-detect remains within 2x of ja-fixed on the same recording.

### US-2: Meeting participant reading posted minutes

**As a** meeting participant searching posted minutes for a specific English topic,
**I want** the term to appear verbatim in the transcript and summary
**so that** I can find it with a simple text search in Discord.

**Acceptance criteria:**
- English technical terms discussed in a bilingual meeting appear in the minutes in their original script.

### US-3: Bot operator (self-hosted)

**As a** self-hosted bot operator,
**I want** the shipped default config to handle mixed-language meetings out of the box
**so that** I do not need to understand Whisper internals to get good results.

**Acceptance criteria:**
- A fresh clone with no user edits uses `language: "auto"`.
- Operators who previously set `language: "ja"` explicitly in their `config.yaml` see no change in behavior.

## Success Metrics

| Type | Metric | Target |
|------|--------|--------|
| Leading | Auto-detect wall-clock time vs. ja-fixed (measured on reference bilingual recording) | Within 2x |
| Leading | Manual spot-check: English tokens in bilingual recording correctly transcribed | 90%+ of identifiable English phrases |
| Lagging | Operator complaints about transcription quality after default change | Zero regressions within 2 weeks of deploy |

## Functional Requirements

### FR-1: Change config.yaml default to "auto" [IN SCOPE]

Change `config.yaml` line 41 from `language: "ja"` to `language: "auto"`.
Update the adjacent YAML comment to clarify the new default.

**Acceptance criteria:**
1. A fresh clone with no user edits uses `language: "auto"`.
2. `transcriber.py` passes `language=None` to faster-whisper, triggering built-in language detection.
3. Existing deployments with explicit `language: "ja"` in their config.yaml continue to behave identically (YAML values override dataclass defaults).

### FR-2: WhisperConfig validation for "auto" [ALREADY IMPLEMENTED]

`config.py` lines 27-32 include `"auto"` in `VALID_WHISPER_LANGUAGES`.
Lines 344-348 reject invalid codes.

**Acceptance criteria (verified by existing tests):**
1. `language: "auto"` passes validation. (`test_auto_whisper_language_accepted`)
2. `language: "xyz"` raises `ConfigError`. (`test_invalid_whisper_language_rejected`)

### FR-3: Auto-detect passthrough to Whisper [ALREADY IMPLEMENTED]

`transcriber.py` line 80 maps `"auto"` to `None` for the Whisper API call.

**Acceptance criteria (verified by existing tests):**
1. Config `language="auto"` causes `language=None` in the Whisper call. (`test_auto_language_passes_none`)
2. Config `language="en"` passes `"en"` unchanged. (`test_explicit_language_passes_through`)

## Non-Functional Requirements

| Category | Requirement |
|----------|-------------|
| Performance | `language="auto"` processing time within 2x of `language="ja"` on the same recording. Validated by GPU benchmark before rollout. |
| SLA | End-to-end pipeline (download through posting) completes within the existing 15-minute target for a typical 60-minute meeting. |
| Backward compatibility | Existing `config.yaml` files with explicit `language: "ja"` behave identically. Only the shipped default and dataclass default change. |
| Observability | Detected language and probability already logged per file (`transcriber.py` line 119: `lang=%s, prob=%.2f`). No additional instrumentation needed. |
| Security / Privacy | No change. Audio data flow is unchanged. No new external calls. |

## Scope

### IN (this iteration)

1. Change `config.yaml` line 41 from `language: "ja"` to `language: "auto"`.
2. Optionally update the `WhisperConfig` dataclass default from `"ja"` to `"auto"` (`config.py` line 79) so new deployments without a config file also get auto-detect.
3. Update the YAML comment on line 40 to clarify the new default.
4. GPU validation: run a real bilingual recording through the pipeline with `language: "auto"` and confirm accuracy and latency before merging.

### OUT (backlogged)

| Item | Rationale | Effort estimate |
|------|-----------|-----------------|
| Per-guild language override via `StateStore` | Pattern exists (`get_guild_template`), but no user has requested per-guild language. | ~60-80 lines, 2-3 hours |
| `/minutes language <lang>` slash command | Depends on per-guild override. No demand yet. | ~40 lines, 1-2 hours |
| Multilingual prompt templates (non-Japanese output) | The minutes prompt (`prompts/minutes.txt`) is hardcoded to produce Japanese output. Auto-language output requires new prompt templates. | Separate REQUEST.md |
| Per-segment language tagging in output | Whisper returns `info.language` per file, not per segment, in the current API usage. Segment-level detection needs a different transcription strategy. | Research needed |
| `Segment` dataclass language field | Useful for future analytics but not required for the default-change scope. | ~20 lines |

## Rollout Plan

| Phase | Action | Gate |
|-------|--------|------|
| 0. Validate | Run pipeline on a real bilingual recording with `language: "auto"` on the RTX 3060 deployment. Measure wall-clock time vs. `"ja"` baseline. Spot-check English token accuracy. | Latency within 2x AND English tokens correctly transcribed |
| 1. Deploy | Change `config.yaml` default to `"auto"`. Commit and deploy. | Phase 0 gate passed |
| 2. Monitor | Watch transcription logs for 1 week. Check `info.language` and `info.language_probability` values for unexpected language detection. | No accuracy regressions reported |
| 3. Backlog review | If per-guild language override is requested, implement using `StateStore` guild settings pattern. | User request received |

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Auto-detect misidentifies language on short or noisy segments, producing worse output than ja-fixed | Medium | Medium | Phase 0 validation gate. Operator can revert to `language: "ja"` in their config.yaml at any time (one-line change). |
| Performance degradation exceeds 2x on long recordings, pushing past 15-min SLA | Low | High | Benchmark in Phase 0. If exceeded, keep `"ja"` as default and document `"auto"` as opt-in only. |
| Japanese prompt + English transcript produces inconsistent minutes language | Low | Low | The prompt explicitly instructs Claude to output in Japanese. Claude handles mixed-language input well. Monitor output quality in Phase 2. |
| Existing deployments silently pick up new default on update if they rely on dataclass default rather than explicit YAML | Low | Low | The shipped `config.yaml` has always had an explicit `language:` line. Only users who deleted the line would be affected, and auto-detect is the better default for them anyway. |

## Open Questions

1. **Should the `WhisperConfig` dataclass default also change?** Changing both the YAML and the dataclass default (`config.py` line 79) ensures consistency. Users who omit the `whisper.language` key entirely get auto-detect. Recommended: yes, change both.
2. **Segment-level language detection**: faster-whisper can return detected language info per file. Is there value in a future second pass with segment-level detection for analytics? Deferred -- no current user need.
3. **Prompt language auto-switching**: Should the minutes output language follow the detected transcript language instead of always outputting Japanese? Deferred -- separate feature requiring new prompt templates and its own REQUEST.md.
