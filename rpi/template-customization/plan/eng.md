# Technical Specification: Template Customization

## 1. Overview

Per-guild prompt template selection for the minutes generation pipeline. Guild administrators can switch between prompt templates (e.g., standard minutes vs. TODO-focused) via slash commands, and the choice persists across bot restarts.

**Implementation status**: ~95% complete. All core functionality is implemented and tested. Two items remain (C1: permission gate, C2: poster.py regex expansion).

**Scope of change**: 7 source files modified, 1 new prompt file, ~220 lines added, 250 tests passing.

---

## 2. Architecture

### Component Map

```
config.yaml
  | discord.guilds[i].template: "minutes"
  v
config.py (GuildConfig.template: str = "minutes")
  |
  v
bot.py (resolve_template: state_store -> GuildConfig -> "minutes")
  |
  v
pipeline.py (template_name -> _transcript_hash + generator.generate)
  |
  v
generator.py (_load_template -> render_prompt -> Claude API)
  |
  v
poster.py (regex extraction: ## まとめ, ## 推奨される次のステップ)

state_store.py (guild_settings.json <-- /minutes template-set)
  ^
  |
bot.py (/minutes template-set -> set_guild_template)
```

### Data Flow

1. **Config time** -- `config.yaml` loads into `GuildConfig.template` (default: `"minutes"`)
2. **Runtime override** -- `/minutes template-set <name>` calls `StateStore.set_guild_template()`, which writes `state/guild_settings.json`
3. **Template resolution** -- `MinutesBot.resolve_template(guild_id)` checks: state_store override -> `GuildConfig.template` -> `"minutes"` fallback
4. **Pipeline propagation** -- `template_name` threaded through `run_pipeline()` -> `run_pipeline_from_tracks()` -> `generator.generate()`
5. **Cache isolation** -- `_transcript_hash(transcript, template_name)` hashes `f"{template_name}:{transcript}"` with SHA-256, ensuring the same transcript processed with different templates yields distinct cache entries
6. **Template loading** -- `_load_template(name)` validates the name (path traversal check), reads `prompts/{name}.txt`, caches in `self._templates` dict
7. **Prompt rendering** -- `render_prompt()` performs `str.replace()` for 5 placeholder variables
8. **Embed extraction** -- `poster.py` uses regex to extract sections for the Discord embed; degrades gracefully (empty string) when headings do not match

### Files Changed

| File | Description |
|------|-------------|
| `src/config.py` | `GuildConfig.template` field; parsed in both multi-guild and legacy config formats |
| `src/generator.py` | `TemplateInfo` dataclass, `_parse_template_metadata()`, multi-template cache, `list_templates()`, `template_name` params on `render_prompt` / `generate` |
| `src/state_store.py` | `guild_settings.json` load/flush; `get_guild_template()` / `set_guild_template()` |
| `bot.py` | `resolve_template()`, `template-list` / `template-set` commands with autocomplete, template propagation in `_launch_pipeline` + Drive watcher, status display |
| `src/pipeline.py` | `template_name` parameter on both entry points; `_transcript_hash` includes template name |
| `src/poster.py` | No changes yet (C2 pending) |
| `prompts/minutes.txt` | Added metadata header (`# name:`, `# description:`) |
| `prompts/todo-focused.txt` | New template file (46 lines) |

---

## 3. API Specification

### Slash Commands

#### `/minutes template-list`

Lists all available templates from `prompts/*.txt`. Displays as a Discord Embed with the current guild's active template marked.

- **Permissions**: none (read-only)
- **Response**: Embed (public)

#### `/minutes template-set <name>`

Sets the active template for the current guild. Persists in `state/guild_settings.json`.

- **Parameters**: `name` (str) -- template file stem; autocomplete-enabled
- **Validation**: name must exist in `list_templates()` result set
- **Permissions**: none currently (**gap -- see C1**)
- **Response**: ephemeral confirmation or error
- **Autocomplete**: `template_name_autocomplete` filters `list_templates()` by substring match, returns up to 25 choices

#### `/minutes status`

Extended to display the resolved template name for the current guild.

### Internal APIs

#### `MinutesGenerator`

```python
def list_templates(self) -> list[TemplateInfo]
def render_prompt(..., template_name: str = "minutes") -> str
async def generate(..., template_name: str = "minutes") -> str
```

#### `StateStore`

```python
def get_guild_template(self, guild_id: int) -> str | None
def set_guild_template(self, guild_id: int, template_name: str) -> None
```

#### `MinutesBot`

```python
def resolve_template(self, guild_id: int) -> str
```

---

## 4. Data Model

### `state/guild_settings.json`

```json
{
  "1027141726340657243": {
    "template": "todo-focused"
  }
}
```

- Keys are `str(guild_id)`
- Values are dicts with a `"template"` key
- Atomic writes via `.tmp` + `os.replace()` (same pattern as `processing.json`)
- Missing file or missing key returns `None` (falls through to config/default)

### Template Metadata Format

File: `prompts/{name}.txt`

```
# name: 表示名
# description: 説明文
(prompt body with {transcript}, {date}, {speakers}, {guild_name}, {channel_name} placeholders)
```

- Parsed by `_parse_template_metadata()`: reads `#`-prefixed lines from file start, extracts `# name:` and `# description:` values
- Parsing stops at the first non-`#` line
- Both fields are optional; `display_name` falls back to file stem, `description` falls back to empty string

### config.yaml

```yaml
discord:
  guilds:
    - guild_id: 123
      watch_channel_id: 456
      output_channel_id: 789
      template: "todo-focused"   # optional, default: "minutes"
```

Legacy single-guild format also supported with the `template` field.

---

## 5. Implementation Details

### 3-Tier Template Resolution

```python
def resolve_template(self, guild_id: int) -> str:
    # 1. Runtime override (slash command)
    override = self.state_store.get_guild_template(guild_id)
    if override:
        return override
    # 2. Static config
    guild_cfg = self.cfg.discord.get_guild(guild_id)
    if guild_cfg:
        return guild_cfg.template
    # 3. Hard-coded default
    return "minutes"
```

### Template Loading and Caching

`MinutesGenerator._templates` is a `dict[str, str]` mapping template name to file content. Templates are loaded lazily on first use and cached for the process lifetime. The default template is loaded eagerly in `load()`.

```python
def _load_template(self, name: str) -> str:
    if name in self._templates:
        return self._templates[name]
    if ".." in name or "/" in name or "\\" in name:
        raise GenerationError(f"Invalid template name: {name}")
    path = self._prompts_dir / f"{name}.txt"
    if not path.exists():
        raise GenerationError(f"Template not found: {name}")
    content = path.read_text(encoding="utf-8")
    self._templates[name] = content
    return content
```

### Cache Key Isolation

The minutes cache key incorporates the template name as a prefix, ensuring the same transcript yields different cache entries when processed with different templates:

```python
def _transcript_hash(transcript: str, template_name: str = "minutes") -> str:
    key = f"{template_name}:{transcript}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
```

### Prompt Rendering

Uses `str.replace()` instead of `str.format()` to avoid breakage from literal braces in user-supplied values (guild names, transcript text):

```python
replacements = {
    "{transcript}": transcript,
    "{date}": date,
    "{speakers}": speakers,
    "{guild_name}": guild_name,
    "{channel_name}": channel_name,
}
result = template
for placeholder, value in replacements.items():
    result = result.replace(placeholder, value)
```

---

## 6. Remaining Work

### C1 (Required): Permission Gate on `/minutes template-set`

Currently any guild member can change the template. Add `manage_guild` permission check.

**Change in `bot.py`**:

```python
@group.command(name="template-set", description="Set the template for this guild")
@discord.app_commands.checks.has_permissions(manage_guild=True)  # ADD THIS
@discord.app_commands.describe(name="Template name")
async def template_set(interaction: discord.Interaction, name: str) -> None:
    ...
```

**Error handler** (add after the command definition):

```python
@template_set.error
async def template_set_error(
    interaction: discord.Interaction, error: discord.app_commands.AppCommandError
) -> None:
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message(
            "テンプレートの変更には「サーバー管理」権限が必要です。",
            ephemeral=True,
        )
    else:
        raise error
```

**Test**: 1 new test case asserting the decorator is present (inspect the command's checks list).

**Effort**: ~15 lines of code, ~10 lines of test.

### C2 (Recommended): poster.py Regex Expansion

The current section extraction patterns in `poster.py` only match the headings used by the default `minutes` template. The `todo-focused` template uses different section headings, so its embed will have empty summary/decisions fields (graceful degradation, but suboptimal UX).

**Change in `poster.py`** (lines 25-30):

```python
# Before
_SUMMARY_PATTERN = re.compile(
    r"## まとめ\s*\n(.*?)(?=\n## |\Z)", re.DOTALL
)
_DECISIONS_PATTERN = re.compile(
    r"## 推奨される次のステップ\s*\n(.*?)(?=\n## |\Z)", re.DOTALL
)

# After
_SUMMARY_PATTERN = re.compile(
    r"## (?:まとめ|要約)\s*\n(.*?)(?=\n## |\Z)", re.DOTALL
)
_DECISIONS_PATTERN = re.compile(
    r"## (?:推奨される次のステップ|アクションアイテム\s*/\s*TODO)\s*\n(.*?)(?=\n## |\Z)", re.DOTALL
)
```

**Tests**: 2 new test cases in `tests/test_poster.py` asserting extraction works for both template heading styles.

**Effort**: ~4 lines changed, ~20 lines of test.

---

## 7. Security

| Threat | Mitigation | Status |
|--------|-----------|--------|
| Path traversal via template name | `_load_template()` rejects names containing `..`, `/`, `\` | Implemented |
| Template injection | Not applicable -- templates are server-side files only; no user-supplied template content | N/A |
| Unauthorized template change | Needs `manage_guild` permission check on `/minutes template-set` | **Gap (C1)** |
| Template enumeration | `template-list` is public; templates are not secrets | Acceptable |
| Denial of service via large template | Templates are local files under operator control; file I/O is bounded | Acceptable |

---

## 8. Testing

250 tests pass (including template-related tests across 5 files).

| File | Template-Related Tests | Coverage |
|------|----------------------|----------|
| `tests/test_generator.py` | `TestListTemplates` (2 tests), `TestParseTemplateMetadata` (3 tests), `TestLoadTemplate` (3 tests: not found, path traversal, caching), `test_render_with_template_name` | Template loading, caching, metadata parsing, path traversal prevention, multi-template rendering |
| `tests/test_state_store.py` | `test_guild_template_default_none`, `test_guild_template_set_get`, `test_guild_template_overwrite`, `test_guild_template_persistence`, `test_guild_template_multiple_guilds` (5 tests) | Guild settings CRUD, atomic persistence, reload-after-restart, multi-guild isolation |
| `tests/test_config.py` | `test_guild_config_template_default`, `test_guild_config_template_from_yaml` (2 tests) | Config parsing for both default and explicit template values |
| `tests/test_pipeline.py` | `test_transcript_hash_includes_template`, `test_transcript_hash_default_template` (2 tests) | Cache key isolation by template name |
| `tests/test_minutes_archive.py` | `template_name` propagation in `store()` call (1 assertion) | Archive records include template_name |

---

## 9. Rollback Plan

1. **Soft rollback** (remove overrides only): Delete `state/guild_settings.json`, restart bot. All guilds revert to their `config.yaml` template (or `"minutes"` default).
2. **Full rollback** (revert feature): `git revert` the feature commits. Delete `state/guild_settings.json` and `prompts/todo-focused.txt`. The `prompts/minutes.txt` metadata header lines are inert (treated as prompt text by Claude) and do not affect behavior, but can be removed for cleanliness.
3. **Backward compatibility**: The default template name `"minutes"` and the unchanged `prompts/minutes.txt` prompt body ensure that guilds without explicit template configuration produce identical output to the pre-feature baseline.
