# UX Design: Multilingual Support (日英混在対応)

## Overview

This feature changes the Whisper transcription default from fixed-language (`"ja"`) to auto-detect (`"auto"`), improving recognition of English terms in mixed Japanese-English meetings. The change is transparent to end users -- no new UI surfaces, no workflow changes. Admins interact only through `config.yaml`.

---

## User Stories and Acceptance Criteria

### US-1: End user experiences improved transcription (transparent)

**As a** meeting participant,
**I want** English terms and proper nouns spoken during a mixed-language meeting to be transcribed accurately,
**So that** I can trust the generated minutes without manually correcting terminology.

**Acceptance criteria:**
- After the default changes to `"auto"`, mixed-language meetings produce better English-term recognition with no user action required.
- The Discord Embed format, file attachments, and posting flow remain identical.
- Processing completes within the existing 15-minute SLA (auto-detect adds at most 2x latency vs. fixed language).

### US-2: Admin selects language mode via config (config change)

**As a** bot administrator,
**I want to** choose between `"ja"`, `"en"`, `"auto"`, or other ISO 639-1 codes in `config.yaml`,
**So that** I can tune transcription for my server's meeting language profile.

**Acceptance criteria:**
- Valid values: any code in `VALID_WHISPER_LANGUAGES` (17 codes including `"auto"`).
- Invalid values produce a clear `ConfigError` at bot startup listing valid options.
- Changing the value requires only editing one line and restarting the bot.

---

## User Flows

### Flow 1: Default behavior after config change (end user -- zero interaction)

```
[Before]
1. Meeting recorded via Craig Bot
2. Bot transcribes with language="ja" (hardcoded)
3. English terms often garbled (e.g., "Kubernetes" -> "クーバネティス")
4. Minutes posted to Discord

[After -- transparent]
1. Meeting recorded via Craig Bot
2. Bot transcribes with language="auto" (Whisper auto-detects per segment)
3. English terms recognized correctly (e.g., "Kubernetes")
4. Minutes posted to Discord -- same Embed, same .md file, same flow
```

No user sees a settings screen, a language picker, or any new UI element.

### Flow 2: Admin changes language setting

```
1. Admin opens config.yaml
2. Edits whisper.language value ("ja" | "en" | "auto" | other ISO 639-1)
3. Restarts the bot (systemctl restart / docker compose restart)
4. Next recording uses the new setting
```

### Flow 3: Admin sets an invalid language value

```
1. Admin sets whisper.language: "xyz"
2. Bot startup fails immediately
3. Log output shows:
     ConfigError: Configuration validation failed:
       - whisper.language 'xyz' is not valid. Choose from: ['ar', 'auto', 'de', ...]
4. Admin corrects the value and restarts
```

---

## States and Transitions

### Language Config States

```
                    config.yaml edit + restart
  [ja (fixed)] <-----------------------------> [auto (detect)]
       |                                              |
       |         config.yaml edit + restart            |
       +---------------> [en (fixed)] <---------------+
       |                                              |
       +----------> [other ISO 639-1] <---------------+
```

All transitions require: edit config.yaml, then restart the bot. There is no runtime state change.

### Transcription Behavior by State

| Config value | Whisper `language` param | Behavior |
|---|---|---|
| `"ja"` | `"ja"` | Fixed Japanese. Fastest. Best for Japanese-only meetings. |
| `"en"` | `"en"` | Fixed English. Best for English-only meetings. |
| `"auto"` | `None` | Auto-detect per file. Slight latency overhead. Best for mixed-language. |
| Other ISO code | Passed through | Fixed to that language. |

### Pipeline Stage Impact

Only Stage 3 (transcription) is affected. All other stages -- audio acquisition, merging, generation, posting -- are unchanged. The Claude API generation stage already handles multilingual input naturally.

---

## Error States

### E-1: Invalid language code at startup

- **Trigger**: Admin sets `whisper.language` to a value not in `VALID_WHISPER_LANGUAGES`.
- **Behavior**: Bot refuses to start. `ConfigError` raised with actionable message.
- **Recovery**: Edit config.yaml to a valid value, restart.
- **User impact**: None (bot was not running).

### E-2: Auto-detect produces unexpected language

- **Trigger**: `language: "auto"` and Whisper misidentifies the dominant language (e.g., detects Korean instead of Japanese for a quiet segment).
- **Behavior**: Transcription completes but some segments may have lower quality.
- **Visibility**: Detected language and probability are logged (`lang=ko, prob=0.45`). No user-facing error.
- **Recovery**: Admin can switch to fixed language if auto-detect consistently underperforms for their use case.

### E-3: Auto-detect increases processing time beyond SLA

- **Trigger**: `language: "auto"` adds overhead for long recordings.
- **Behavior**: Pipeline timeout (default 3600s) still applies. If exceeded, standard timeout error is posted.
- **Recovery**: Admin switches to fixed language. Acceptance criterion: auto should be within 2x of fixed-language time.

### E-4: No speech detected

- **Trigger**: Recording contains only silence or noise regardless of language setting.
- **Behavior**: Whisper returns zero segments. Existing empty-transcript handling applies (error posted to output channel).
- **Recovery**: No action needed. Same behavior as before this change.

---

## Discord Embed Output (Unchanged)

The posted Embed and attached files are identical regardless of language setting:

```
+------------------------------------------+
|  会議議事録 -- 2026/03/17                  |
|                                          |
|  参加者: user1, user2, user3             |
|                                          |
|  まとめ:                                  |
|  (summary text, may contain both         |
|   Japanese and English naturally)        |
|                                          |
|  次のステップ:                             |
|  - Action item 1                         |
|  - Action item 2                         |
|                                          |
|  詳細議事録は添付ファイルを参照              |
+------------------------------------------+
|  [minutes_2026-03-17.md]                 |
|  [transcript_2026-03-17.md]              |
+------------------------------------------+
```

No language indicator is added to the Embed. The minutes language follows the transcript content naturally -- Claude API handles multilingual input without additional prompting.

---

## Future UX: Per-Guild Language Command (Backlogged)

This section is a design sketch for the Nice-to-Have `/minutes language` command, not part of the current implementation.

### Proposed Command

```
/minutes language <lang>
```

- **Parameters**: `lang` -- autocomplete from `VALID_WHISPER_LANGUAGES` list.
- **Permission**: Requires `manage_guild` permission (guild admin only).
- **Scope**: Per-guild override stored in `state/` persistent store.
- **Precedence**: Guild override > config.yaml default.

### Interaction Flow

```
Admin: /minutes language auto
Bot:   Language setting updated to "auto" for this server.
       (Previously: "ja")

Admin: /minutes language reset
Bot:   Language setting reset to config default ("auto").
```

### Proposed Subcommands

| Command | Description |
|---|---|
| `/minutes language <code>` | Set guild language override |
| `/minutes language reset` | Remove override, fall back to config.yaml default |
| `/minutes language` (no args) | Show current effective language for this guild |

### Design Notes

- Follows the existing pattern established by `/minutes template-set <name>` and `/minutes template-list`.
- Ephemeral responses (visible only to the admin who ran the command).
- Autocomplete for the `lang` parameter to prevent invalid input.
- Changes take effect on the next recording -- no retroactive reprocessing.

---

## Accessibility Notes

### Language Handling in Discord Embeds

- Discord does not support `lang` HTML attributes in Embeds. Screen readers will read Embed text in whatever language the user's client is set to. This is a Discord platform constraint, not something this feature can address.
- The Embed field names remain in Japanese (`参加者`, `まとめ`, `次のステップ`) regardless of the transcription language. This is consistent with the current design where the bot UI language is Japanese.
- Attached `.md` files are UTF-8 encoded and render correctly for all supported languages in Discord's file preview.

### Configuration Accessibility

- `config.yaml` is a plain text file editable with any text editor or screen reader.
- Error messages from validation are plain ASCII/English text written to stdout/log, compatible with all terminal screen readers.

### Contrast and Visual

- No visual changes to Discord Embeds. Existing embed color (`0x5865F2`, Discord blurple) and formatting are preserved.
- No new UI elements are introduced in the current scope.

### Keyboard Navigation

- Not applicable for the current scope (config file change only).
- For the backlogged `/minutes language` command: Discord slash commands are fully keyboard-navigable by default. Autocomplete suggestions are accessible via arrow keys and Enter.
