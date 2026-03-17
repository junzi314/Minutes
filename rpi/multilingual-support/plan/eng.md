# Technical Specification: Multilingual Support (日英混在対応)

**Traceability**: R-84
**Status**: Phase 1 code complete (all infrastructure exists), activation pending config change
**Last Updated**: 2026-03-17

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Phase 1: Activate Auto-Detection](#phase-1-activate-auto-detection)
3. [Phase 2: Per-Guild Language Override (Backlog)](#phase-2-per-guild-language-override-backlog)
4. [API Contracts (Phase 2)](#api-contracts-phase-2)
5. [Test Strategy](#test-strategy)
6. [Performance Analysis](#performance-analysis)
7. [Risk Matrix](#risk-matrix)
8. [Rollback Plan](#rollback-plan)

---

## Architecture Overview

### Current Data Flow (No Code Changes Required)

The multilingual support infrastructure is fully implemented. The data flow from
configuration through to Whisper invocation already handles `"auto"`, `"ja"`,
`"en"`, and 14 other language codes.

```
config.yaml                        src/config.py                  src/transcriber.py
+-----------------------+          +-------------------------+     +----------------------------+
| whisper:              |          | WhisperConfig           |     | transcribe_file()          |
|   language: "auto"  -+--------->|   language: str = "ja"  +---->|   language = None          |
|                       |   load() | VALID_WHISPER_LANGUAGES |     |     if cfg.language=="auto"|
+-----------------------+          |   _validate()           |     |   else cfg.language        |
                                   +-------------------------+     +----------+-----------------+
                                                                              |
                                                                   WhisperModel.transcribe(
                                                                     language=None  # auto-detect
                                                                   )
                                                                              |
                                                                   TranscriptionInfo
                                                                     .language        (logged)
                                                                     .language_probability (logged)
```

### What Already Exists in Code

| Component | Location | What It Does |
|-----------|----------|--------------|
| Language whitelist | `src/config.py:27-32` | `VALID_WHISPER_LANGUAGES` frozenset with 17 entries including `"auto"` |
| Validation | `src/config.py:344-348` | Rejects unknown language codes at startup |
| Auto-detect logic | `src/transcriber.py:80` | `language = None if self._cfg.language == "auto" else self._cfg.language` |
| WhisperConfig default | `src/config.py:79` | `language: str = "ja"` (Python-level default, backward compatible) |
| Config comment | `config.yaml:40` | Documents `"ja"`, `"en"`, `"auto"` options |
| Unit test: auto -> None | `tests/test_transcriber.py:115-128` | Verifies `language="auto"` passes `None` to Whisper |
| Unit test: explicit passthrough | `tests/test_transcriber.py:130-143` | Verifies `language="en"` passes `"en"` unchanged |
| Unit test: invalid rejected | `tests/test_config.py:216-232` | Verifies `language="xyz"` raises `ConfigError` |
| Unit test: auto accepted | `tests/test_config.py:234-250` | Verifies `language="auto"` passes validation |

### Unmodified Stages (No Changes Needed)

The following pipeline stages are language-agnostic and require zero modifications:

| Stage | Module | Why No Change |
|-------|--------|---------------|
| Audio Acquisition | `craig_client.py` | Downloads raw audio; language irrelevant |
| Merging | `merger.py` | Merges by timestamp/speaker; language-agnostic |
| Generation | `generator.py` | Claude API handles any language in transcript |
| Posting | `poster.py` | Renders markdown; encoding-neutral |

---

## Phase 1: Activate Auto-Detection

**Scope**: Single line change in `config.yaml`. No code changes.
**Effort**: < 1 minute. Requires bot restart.

### The Change

File: `config.yaml`, line 41

```yaml
# Before:
  language: "ja"

# After:
  language: "auto"
```

### Why This Works Without Code Changes

1. `WhisperConfig.language` already accepts any string from YAML
2. `_validate()` already checks against `VALID_WHISPER_LANGUAGES` which includes `"auto"`
3. `transcribe_file()` already converts `"auto"` to `None` before calling `WhisperModel.transcribe()`
4. The Python-level default (`language: str = "ja"`) remains `"ja"`, so any deployment that omits the YAML field gets the safe backward-compatible behavior

### Activation Procedure

```bash
# 1. Edit config.yaml
sed -i 's/language: "ja"/language: "auto"/' config.yaml

# 2. Restart bot
sudo systemctl restart discord-minutes-bot
# or: docker compose restart
```

### Verification

```bash
# Check logs for language detection output per speaker track
grep "lang=" logs/bot.log | tail -5
# Expected: "Transcribed 1-alice.aac: 42 segments in 18.3s (lang=ja, prob=0.97)"
# or mixed: "Transcribed 2-bob.aac: 15 segments in 8.1s (lang=en, prob=0.92)"
```

---

## Phase 2: Per-Guild Language Override (Backlog)

**Status**: Designed, not yet implemented.
**Scope**: Allow each guild to override the global `whisper.language` via a slash command.
**Estimated size**: ~60-80 lines of new code across 4 files.

### Design Pattern

This follows the **exact same pattern** as the existing template override system:

| Concern | Template Override (exists) | Language Override (new) |
|---------|---------------------------|------------------------|
| Storage | `state_store.get_guild_template()` | `state_store.get_guild_language()` |
| Resolution | `bot.resolve_template(guild_id)` | `bot.resolve_language(guild_id)` |
| Command | `/minutes template-set <name>` | `/minutes language <lang>` |
| Priority | state_store > GuildConfig > "minutes" | state_store > config.yaml > "ja" |
| Persistence | `state/guild_settings.json` | `state/guild_settings.json` (same file) |

### Component Changes

#### 1. `src/state_store.py` (~10-15 new lines)

Add two methods to the `StateStore` class, mirroring `get_guild_template` / `set_guild_template`:

```python
def get_guild_language(self, guild_id: int) -> str | None:
    """Return the language override for a guild, or None."""
    settings = self._guild_settings.get(str(guild_id))
    if settings is None:
        return None
    return settings.get("language")

def set_guild_language(self, guild_id: int, language: str) -> None:
    """Set the transcription language for a guild."""
    key = str(guild_id)
    if key not in self._guild_settings:
        self._guild_settings[key] = {}
    self._guild_settings[key]["language"] = language
    self._flush_guild_settings()
```

Storage format in `state/guild_settings.json`:
```json
{
  "1027141726340657243": {
    "template": "todo-focused",
    "language": "auto"
  }
}
```

#### 2. `src/transcriber.py` (~5 new lines)

Add an optional `language_override` parameter to `transcribe_file()`:

```python
def transcribe_file(
    self,
    audio_path: Path,
    speaker_name: str,
    language_override: str | None = None,
) -> list[Segment]:
    # ...
    effective_lang = language_override if language_override is not None else self._cfg.language
    language = None if effective_lang == "auto" else effective_lang
    segments_iter, info = self._model.transcribe(
        str(path),
        language=language,
        beam_size=self._cfg.beam_size,
        vad_filter=self._cfg.vad_filter,
    )
```

Also update `transcribe_all()` to accept and forward the override:

```python
def transcribe_all(
    self,
    tracks: list[SpeakerAudio],
    language_override: str | None = None,
) -> list[Segment]:
    # ...
    for i, track in enumerate(tracks, 1):
        segments = self.transcribe_file(
            track.file_path,
            speaker_name=track.speaker.username,
            language_override=language_override,
        )
        all_segments.extend(segments)
```

#### 3. `bot.py` (~30-40 new lines)

Add resolution method (mirrors `resolve_template`):

```python
def resolve_language(self, guild_id: int) -> str:
    """Resolve transcription language for a guild.

    Priority: state_store override -> config.yaml whisper.language
    """
    override = self.state_store.get_guild_language(guild_id)
    if override:
        return override
    return self.cfg.whisper.language
```

Add slash command to the `register_commands` function:

```python
@group.command(name="language", description="Set transcription language for this guild")
@discord.app_commands.describe(lang="Language code: ja, en, auto, zh, ko, etc.")
async def minutes_language(interaction: discord.Interaction, lang: str) -> None:
    from src.config import VALID_WHISPER_LANGUAGES
    if lang not in VALID_WHISPER_LANGUAGES:
        await interaction.response.send_message(
            f"無効な言語コード `{lang}` です。\n"
            f"有効な値: {', '.join(sorted(VALID_WHISPER_LANGUAGES))}",
            ephemeral=True,
        )
        return
    client.state_store.set_guild_language(interaction.guild_id or 0, lang)
    await interaction.response.send_message(
        f"文字起こし言語を **{lang}** に変更しました。次回の処理から適用されます。",
        ephemeral=True,
    )

@minutes_language.autocomplete("lang")
async def language_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[discord.app_commands.Choice[str]]:
    from src.config import VALID_WHISPER_LANGUAGES
    labels = {"auto": "auto (自動検出)", "ja": "ja (日本語)", "en": "en (English)",
              "zh": "zh (中文)", "ko": "ko (한국어)"}
    return [
        discord.app_commands.Choice(
            name=labels.get(lang, lang), value=lang
        )
        for lang in sorted(VALID_WHISPER_LANGUAGES)
        if current.lower() in lang.lower()
    ][:25]
```

Update `/minutes status` to show current language:

```python
lines.append(f"**Language**: {client.resolve_language(interaction.guild_id or 0)}")
```

#### 4. `src/pipeline.py` (~5-10 new lines)

Thread the resolved language through to the transcriber:

```python
async def run_pipeline_from_tracks(
    # ... existing params ...
    language_override: str | None = None,   # NEW
) -> None:
    # ...
    # Stage 2: Transcribe
    segments = await _stage_transcribe(transcriber, tracks, language_override)

async def _stage_transcribe(
    transcriber: Transcriber,
    tracks: list[SpeakerAudio],
    language_override: str | None = None,   # NEW
) -> list[Segment]:
    segments = await asyncio.to_thread(
        transcriber.transcribe_all, tracks, language_override
    )
    return segments
```

Callers (`bot.py` drive watcher callback, `_launch_pipeline`) resolve the language
and pass it through.

### Data Flow (Phase 2)

```
/minutes language auto        state/guild_settings.json
        |                              |
        v                              v
bot.resolve_language(guild_id) -----> state_store.get_guild_language()
        |                                    |
        | (fallback)                         | (if None)
        +---> cfg.whisper.language           |
              |                              |
              v                              v
   pipeline.run_pipeline_from_tracks(language_override="auto")
              |
              v
   transcriber.transcribe_all(tracks, language_override="auto")
              |
              v
   transcriber.transcribe_file(..., language_override="auto")
              |
              v
   WhisperModel.transcribe(language=None)
```

---

## API Contracts (Phase 2)

### StateStore

```python
class StateStore:
    def get_guild_language(self, guild_id: int) -> str | None:
        """Return language override for a guild, or None if not set."""
        ...

    def set_guild_language(self, guild_id: int, language: str) -> None:
        """Persist language override. Caller must validate beforehand."""
        ...
```

### Transcriber

```python
class Transcriber:
    def transcribe_file(
        self,
        audio_path: Path,
        speaker_name: str,
        language_override: str | None = None,   # NEW optional param
    ) -> list[Segment]:
        """language_override takes precedence over self._cfg.language."""
        ...

    def transcribe_all(
        self,
        tracks: list[SpeakerAudio],
        language_override: str | None = None,   # NEW optional param
    ) -> list[Segment]:
        """Forwards language_override to each transcribe_file call."""
        ...
```

### Pipeline

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
    language_override: str | None = None,   # NEW optional param
) -> None:
    ...
```

### Bot

```python
class MinutesBot(discord.Client):
    def resolve_language(self, guild_id: int) -> str:
        """Priority: state_store override -> config.yaml whisper.language."""
        ...
```

### Slash Command

```
/minutes language <lang>
  lang: str  (autocomplete from VALID_WHISPER_LANGUAGES)
  Response: confirmation message (ephemeral)
  Side effect: writes to state/guild_settings.json
```

---

## Test Strategy

### Existing Tests (Phase 1 -- All Passing)

| Test | File | Line | Validates |
|------|------|------|-----------|
| `test_auto_language_passes_none` | `tests/test_transcriber.py` | 115 | `"auto"` -> `language=None` in Whisper call |
| `test_explicit_language_passes_through` | `tests/test_transcriber.py` | 130 | `"en"` -> `language="en"` unchanged |
| `test_invalid_whisper_language_rejected` | `tests/test_config.py` | 216 | `"xyz"` raises `ConfigError` |
| `test_auto_whisper_language_accepted` | `tests/test_config.py` | 234 | `"auto"` passes validation |

### Phase 1 Activation Validation (Manual)

| Step | Command | Expected |
|------|---------|----------|
| Run unit tests | `pytest tests/test_transcriber.py tests/test_config.py -v` | All pass |
| Change config | Set `language: "auto"` in `config.yaml` | No error |
| Start bot | `python3 bot.py` | Starts without ConfigError |
| Process recording | Trigger or use `/minutes process <url>` | Log shows `lang=` per track |
| Check timing | Compare log timestamps | Auto mode <= 2x of ja-fixed baseline |

### Phase 2 Tests (To Be Written)

| Test | File | Validates |
|------|------|-----------|
| `test_get_guild_language_default_none` | `tests/test_state_store.py` | Returns `None` when no override set |
| `test_set_and_get_guild_language` | `tests/test_state_store.py` | Round-trip persistence |
| `test_language_override_takes_precedence` | `tests/test_transcriber.py` | `language_override="en"` overrides `cfg.language="ja"` |
| `test_language_override_none_uses_config` | `tests/test_transcriber.py` | `language_override=None` falls back to `cfg.language` |
| `test_resolve_language_priority` | `tests/test_pipeline.py` or bot test | state_store > config.yaml chain |

---

## Performance Analysis

### Auto-Detection Overhead

faster-whisper's auto-detection uses the first 30 seconds of audio to identify the
language. This adds a constant overhead per speaker track, independent of total
audio length.

| Scenario | Tracks | Baseline (ja fixed) | Auto-Detect | Overhead |
|----------|--------|---------------------|-------------|----------|
| 2 speakers, 10min | 2 | ~2 min | ~2 min 4s | +2-3s |
| 5 speakers, 15min | 5 | ~5 min | ~5 min 10s | +5-15s |
| 5 speakers, 60min | 5 | ~15 min | ~15 min 10s | +5-15s |
| 10 speakers, 30min | 10 | ~10 min | ~10 min 20s | +10-30s |

**Key insight**: The overhead is per-track (constant ~1-3s), not per-minute-of-audio.
For typical meetings (2-5 speakers), the overhead is negligible relative to the
total transcription time. Well within the 15-minute SLA for any realistic scenario.

### VRAM Impact

No additional VRAM usage. Auto-detection reuses the same model weights already
loaded in GPU memory. The only difference is an extra forward pass on 30s of audio.

### Quality Trade-off

| Scenario | `language="ja"` | `language="auto"` |
|----------|-----------------|-------------------|
| Pure Japanese | Optimal | ~Equivalent (>95% detection accuracy for ja) |
| Pure English | Poor (forced ja tokenizer) | Optimal |
| Mixed ja/en | Compromised (English rendered as katakana) | Good (detects dominant language) |

**Caveat**: Whisper auto-detect selects one language per file, not per segment. In a
mixed-language meeting, the detected language will be the dominant language of each
speaker track. This is acceptable because Craig provides per-speaker files, so a
Japanese speaker's file will detect as `ja` and an English speaker's file as `en`.

---

## Risk Matrix

| # | Risk | Impact | Probability | Mitigation | Owner |
|---|------|--------|-------------|------------|-------|
| R1 | Auto-detect adds latency beyond SLA | High | Very Low | Overhead is 1-3s/track; SLA is 15min. Validated by performance analysis above. Rollback: revert config.yaml to `"ja"`. | Ops |
| R2 | Japanese misdetected as Chinese for short tracks | Low | Low | VAD filter already excludes short silence-only segments. Whisper large-v3 ja detection accuracy >95%. Monitor `lang=` log output after activation. | Dev |
| R3 | Phase 2 language override bypasses validation | Medium | Low | `set_guild_language()` callers (slash command) validate against `VALID_WHISPER_LANGUAGES` before writing. Add assertion in `set_guild_language()` as defense-in-depth. | Dev |
| R4 | guild_settings.json corruption | Medium | Very Low | Same atomic write pattern (`_flush()` with `os.replace`) already proven reliable for template overrides. DrvFs warning already emitted. | Ops |
| R5 | Existing tests break from Phase 1 activation | High | None | Phase 1 is a config-only change. Python default remains `"ja"`. All existing tests use explicit `WhisperConfig(language="ja", ...)` -- they never read config.yaml. | Dev |
| R6 | Phase 2 `transcribe_all` signature change breaks callers | Medium | Low | New parameter is optional with `None` default. All existing callers continue to work without modification. | Dev |

---

## Rollback Plan

### Phase 1 Rollback (Config Only)

If auto-detection causes quality or performance issues after activation:

```bash
# 1. Revert config.yaml (1 line)
sed -i 's/language: "auto"/language: "ja"/' config.yaml

# 2. Restart bot
sudo systemctl restart discord-minutes-bot
```

**Recovery time**: < 1 minute.
**Data impact**: None. Already-generated minutes are cached in `state/minutes_cache.json`
and are not affected.

### Phase 2 Rollback (Code)

If per-guild language override causes issues:

1. **Immediate**: Clear guild language settings via `state/guild_settings.json`
   (remove `"language"` keys from each guild entry). Bot falls back to config.yaml value.

2. **Full revert**: `git revert <commit>` the Phase 2 commit.
   - New optional parameters have defaults, so reverting is safe
   - `guild_settings.json` retains `"language"` keys but they are ignored by the reverted code

### Feature Flag Approach (Alternative)

No feature flag is needed because:
- Phase 1 is a config value change (the flag IS the config value)
- Phase 2's per-guild override is inherently opt-in (guilds that don't run
  `/minutes language` get the global config value)

---

## Appendix: File Change Summary

### Phase 1

| File | Change | Lines |
|------|--------|-------|
| `config.yaml` | `language: "ja"` -> `language: "auto"` | 1 |

### Phase 2 (Estimated)

| File | Change | Lines |
|------|--------|-------|
| `src/state_store.py` | `get_guild_language()`, `set_guild_language()` | ~12 |
| `src/transcriber.py` | `language_override` param on `transcribe_file()`, `transcribe_all()` | ~8 |
| `src/pipeline.py` | Thread `language_override` through `run_pipeline_from_tracks`, `_stage_transcribe` | ~8 |
| `bot.py` | `resolve_language()`, `/minutes language` command + autocomplete, status line | ~35 |
| `tests/test_state_store.py` | 2 new tests | ~15 |
| `tests/test_transcriber.py` | 2 new tests | ~20 |
| **Total** | | **~98** |
