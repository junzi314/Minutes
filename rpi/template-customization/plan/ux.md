# UX Design: Template Customization (Implemented)

Per-guild meeting minutes template selection via Discord slash commands.
Guilds choose which prompt template shapes their generated minutes.

---

## 1. User Stories and Acceptance Criteria

### US-1: View available templates

**As** a guild member, **I want** to see all available templates and which one is active, **so that** I can decide whether to switch.

**Acceptance Criteria:**
- `/minutes template-list` returns a Discord Embed listing every `.txt` file in `prompts/`.
- Each entry shows `display_name` (from `# name:` header) and `description` (from `# description:` header).
- The currently active template is marked with `(現在)` after its display name.
- Footer text instructs the user how to change: `/minutes template-set <名前> で変更`.

### US-2: Change template

**As** a guild member, **I want** to change the minutes template for my server, **so that** future minutes follow a different format.

**Acceptance Criteria:**
- `/minutes template-set` provides an autocomplete dropdown populated from `prompts/*.txt`.
- Autocomplete filters by partial match on the template `name` (file stem), case-insensitive.
- On valid selection, the choice persists in `state/guild_settings.json` keyed by guild ID.
- Confirmation is ephemeral: visible only to the invoking user.
- On invalid name (typed manually, bypassing autocomplete), an ephemeral error message is returned with guidance to run `/minutes template-list`.

### US-3: Confirm current template

**As** a guild member, **I want** to check which template is active without listing all templates, **so that** I can quickly verify the configuration.

**Acceptance Criteria:**
- `/minutes status` includes a `**Template**: {name}` line.
- The value reflects the resolution chain: state_store override, then config.yaml `template` field, then `"minutes"` default.

### US-4: Add a new template (admin, no UI)

**As** a server administrator, **I want** to add a custom prompt template by dropping a file, **so that** I do not need code changes to introduce new formats.

**Acceptance Criteria:**
- A new `.txt` file placed in `prompts/` appears in `/minutes template-list` without restart.
- The file must start with `# name:` and `# description:` header comments for proper display.
- Missing headers degrade gracefully: file stem is used as display name, description shows "説明なし".
- Template variables `{transcript}`, `{date}`, `{speakers}`, `{guild_name}`, `{channel_name}` are replaced via simple string substitution (no `str.format()`).

### US-5: Minutes generated with selected template

**As** a guild member, **I want** generated minutes to use whichever template my server selected, **so that** the output matches our preferred format.

**Acceptance Criteria:**
- Both auto-detected (Craig recording end) and manual (`/minutes process`) pipelines resolve the guild's template.
- Google Drive watcher pipeline also resolves the template for its configured guild.
- Cache key includes `template_name`, so the same transcript processed with different templates produces distinct cached results.
- Switching templates does not invalidate old cache entries; they remain usable if the original template is re-selected.

---

## 2. User Flows

### Flow 1: View Available Templates

```
1. User types:  /minutes template-list
2. Discord sends the interaction to the bot.
3. Bot calls generator.list_templates(), which scans prompts/*.txt (sorted).
4. Bot calls resolve_template(guild_id) to determine the current template.
5. Bot constructs a Discord Embed:
     Title:  利用可能なテンプレート
     Color:  0x5865F2 (Discord blurple)
     Fields: One per template (inline=False)
       Name:  "{display_name}" or "{display_name} (現在)" for the active one
       Value: description text, or "説明なし" if absent
     Footer: /minutes template-set <名前> で変更
6. Bot sends the embed as a public (non-ephemeral) message.
```

### Flow 2: Change Template

```
1. User types:  /minutes template-set
2. Discord triggers autocomplete on the "name" parameter.
3. Bot returns up to 25 Choice objects:
     name  = display_name  (shown in dropdown)
     value = file stem      (sent to handler)
   Filtered: current.lower() in t.name.lower()
4. User selects a template from the dropdown (or types manually).
5. Bot validates the name against the set of available template names.
6a. VALID:
     Bot calls state_store.set_guild_template(guild_id, name).
     state/guild_settings.json is atomically updated.
     Bot responds (ephemeral):
       "テンプレートを **{name}** に変更しました。次回の議事録生成から適用されます。"
6b. INVALID:
     Bot responds (ephemeral):
       "テンプレート `{name}` は見つかりません。
        `/minutes template-list` で利用可能なテンプレートを確認してください。"
```

### Flow 3: Check Current Template via Status

```
1. User types:  /minutes status
2. Bot resolves the template name for the guild:
     Priority: state_store override -> GuildConfig.template -> "minutes"
3. Bot assembles status lines including:
     **Template**: {resolved_template_name}
4. Bot sends the full status message (ephemeral).
```

### Flow 4: Add New Template (Admin, No UI)

```
1. Admin creates a new file:  prompts/{name}.txt
2. File starts with metadata comments:
     # name: 表示名
     # description: テンプレートの説明
3. File body contains the prompt with placeholder variables:
     {transcript}, {date}, {speakers}, {guild_name}, {channel_name}
4. On next /minutes template-list call, the new template appears automatically.
   - list_templates() scans the directory each time it is called.
   - _load_template() lazily caches template content on first generation use.
5. No bot restart is required for the template to become selectable.
   The template file content is cached after its first use in generation.
```

### Flow 5: Minutes Generation with Selected Template

```
1. Pipeline trigger fires (auto-detection, /minutes process, or Drive watcher).
2. bot.resolve_template(guild_id) resolves the template name:
     a. state_store.get_guild_template(guild_id) -- slash command override
     b. guild_cfg.template                       -- config.yaml per-guild default
     c. "minutes"                                -- hardcoded fallback
3. template_name is passed to run_pipeline() or run_pipeline_from_tracks().
4. Pipeline computes cache key:  sha256("{template_name}:{transcript}")
5. On cache miss, generator.generate() is called with template_name.
   generator._load_template() loads and caches the template file content.
   render_prompt() fills in variables via str.replace().
6. Generated minutes are posted as Embed + .md attachment to the output channel.
7. Minutes are archived with template_name metadata (if archive is enabled).
```

---

## 3. UI Components

### 3.1 Template List Embed

| Property | Value |
|----------|-------|
| Type | Discord Embed |
| Title | 利用可能なテンプレート |
| Color | 0x5865F2 |
| Fields | One per template, `inline=False` |
| Field Name | `{display_name}` or `{display_name} (現在)` |
| Field Value | Description string or "説明なし" |
| Footer | `/minutes template-set <名前> で変更` |
| Ephemeral | No (visible to all channel members) |

Example with current templates:

```
+--------------------------------------------------+
| 利用可能なテンプレート                               |
|                                                  |
| 標準議事録 (現在)                                   |
| Geminiメモ風の詳細フォーマット（まとめ・詳細・次のステップ）|
|                                                  |
| TODO重視                                          |
| アクションアイテムとTODOを重視したフォーマット           |
|                                                  |
| /minutes template-set <名前> で変更                 |
+--------------------------------------------------+
```

### 3.2 Autocomplete Dropdown

| Property | Value |
|----------|-------|
| Trigger | User typing in the `name` parameter of `/minutes template-set` |
| Source | `generator.list_templates()` |
| Display | `t.display_name` (e.g., "標準議事録") |
| Value sent | `t.name` (file stem, e.g., "minutes") |
| Max entries | 25 (Discord API limit) |
| Filter | `current.lower() in t.name.lower()` on the file stem |

### 3.3 Ephemeral Messages

All state-changing and error responses use ephemeral messages (visible only to the command invoker):

| Scenario | Message |
|----------|---------|
| Template changed | テンプレートを **{name}** に変更しました。次回の議事録生成から適用されます。 |
| Invalid name | テンプレート \`{name}\` は見つかりません。\n\`/minutes template-list\` で利用可能なテンプレートを確認してください。 |
| Status check | (Multi-line status including **Template**: {name}) |

### 3.4 Minutes Output Embed (affected by template choice)

The `poster.py` embed builder extracts sections using fixed regex patterns:
- `## まとめ` for the summary field
- `## 推奨される次のステップ` for the next-steps field

These headings match the `minutes` (standard) template. Other templates that use different headings (e.g., `todo-focused` uses `## 要約` and `## アクションアイテム / TODO`) result in those embed fields being empty. The full markdown file attachment always contains the complete output regardless of template.

---

## 4. States

### 4.1 Normal States

| State | Trigger | Display |
|-------|---------|---------|
| Template list loaded | `/minutes template-list` | Embed with all templates, active one marked |
| Template changed | `/minutes template-set {valid}` | Ephemeral confirmation |
| Status with template | `/minutes status` | Status lines including Template field |
| Generation with template | Pipeline runs | Minutes generated using selected template's prompt |

### 4.2 Empty States

| State | Trigger | Display |
|-------|---------|---------|
| No templates found | `prompts/` directory missing or empty; generator not loaded | Embed with title but no fields |
| No guild override set | `state_store.get_guild_template()` returns None | Falls back to `GuildConfig.template`, then to `"minutes"` |
| No autocomplete results | User types text that matches no template name | Empty dropdown |

### 4.3 Error States

| State | Trigger | Display |
|-------|---------|---------|
| Invalid template name | User bypasses autocomplete and types a non-existent name | Ephemeral: "テンプレート \`{name}\` は見つかりません。..." |
| Path traversal attempt | Template name contains `..`, `/`, or `\` | `GenerationError` raised; error embed posted to channel |
| Template file deleted after selection | Selected template no longer on disk at generation time | `GenerationError("Template not found: {name}")` |
| Generator not loaded | `load()` never called or `prompts_dir` is None | `list_templates()` returns empty list; `_load_template()` raises `GenerationError` |
| guild_settings.json corrupt | JSON parse failure on startup | `_load_json` returns `{}`, all guilds fall back to defaults |

---

## 5. Accessibility

### 5.1 Command Discoverability

- All template commands live under the `/minutes` command group, consistent with existing commands (`status`, `process`, `drive-status`, `search`).
- The autocomplete dropdown for `/minutes template-set` prevents typos and teaches users what templates exist without requiring them to run `template-list` first.
- The template-list embed footer explicitly tells users how to change: `/minutes template-set <名前> で変更`.

### 5.2 Error Recovery Guidance

- Every error message includes actionable next steps. The invalid-name error directs users to `/minutes template-list`.
- Path traversal and missing-template errors are caught at the generator level and surfaced as error embeds in the output channel, so the team is aware a pipeline failed.

### 5.3 Keyboard and Screen Reader Considerations

- Discord slash commands are fully keyboard-navigable: users can type `/minutes template` and arrow through completions.
- Autocomplete choices display the human-readable `display_name`, not the file stem, making selection intuitive.
- Embed fields use semantic names (not icons or decorative text), which screen readers can parse.
- No images, color-only indicators, or interactive buttons are used; all information is conveyed through text.

### 5.4 Contrast and Readability

- The embed color (0x5865F2, Discord blurple) provides sufficient contrast against both light and dark Discord themes.
- Template descriptions use plain text, not code blocks, for natural reading.
- The `(現在)` marker is appended as text, not as an emoji, ensuring consistent rendering across platforms and assistive technologies.

---

## 6. Known Issues

### 6.1 Embed Heading Mismatch (Graceful Degradation)

`poster.py` uses hardcoded regex patterns to extract `## まとめ` and `## 推奨される次のステップ` sections for the Discord Embed preview. Templates that use different section headings (like `todo-focused` which uses `## 要約` and `## アクションアイテム / TODO`) result in those embed fields being empty.

**Impact:** The embed preview is less informative for non-standard templates. The full `.md` file attachment is always complete and correct.

**Severity:** Low. Functional but suboptimal.

**Mitigation path:** Allow templates to declare their embed-extractable heading names in metadata comments, e.g.:
```
# embed_summary: 要約
# embed_actions: アクションアイテム / TODO
```

### 6.2 No Permission Gate

Currently any guild member can run `/minutes template-set` and change the template for the entire server. There is no `manage_guild` or similar permission check.

**Impact:** A non-admin user could change the template without the team's knowledge.

**Severity:** Medium in shared servers, low in small teams.

**Mitigation path:** Add `@discord.app_commands.checks.has_permissions(manage_guild=True)` decorator to the `template_set` command. This is a one-line change.

### 6.3 Template List is Not Ephemeral

`/minutes template-list` sends its response as a public message visible to all channel members. In active channels this may create noise.

**Impact:** Low. The embed is informational and relatively compact.

**Mitigation path:** Add `ephemeral=True` to the `send_message` call. However, keeping it public can also be useful for team coordination ("here are our options").

---

## 7. Future Enhancements

### 7.1 Template Preview Command

Add `/minutes template-preview <name>` that generates a short sample using a canned transcript snippet (50-100 words), so users can see the output format before committing to a template change.

### 7.2 Dynamic Embed Section Extraction

Allow each template to declare which `##` headings should be extracted for the Discord Embed via metadata comments:

```
# embed_fields: 要約, アクションアイテム / TODO
```

This would eliminate the heading mismatch issue (6.1) for all current and future templates.

### 7.3 Custom Template Variables

Allow guilds to define custom variables (e.g., `{project_name}`, `{team}`) that are substituted into templates alongside the built-in variables. Stored in `guild_settings.json`.

### 7.4 Template Versioning

Track which template version was used for each set of minutes (currently only the template name is stored in the archive). This would support auditing when templates are edited over time.

### 7.5 In-Discord Template Editing

A `/minutes template-edit` flow using Discord modals (text input forms) for small template adjustments without SSH/file access. Limited by Discord's 4000-character modal input limit, which is tight for full prompt templates.

---

## 8. Data Flow Summary

```
config.yaml                  state/guild_settings.json
  discord.guilds[].template     {guild_id}.template
         |                            |
         v                            v
   GuildConfig.template    state_store.get_guild_template()
         |                            |
         +---------- resolve_template(guild_id) ----------+
                              |                           |
                     (override wins)              (fallback chain)
                              |
                              v
                       template_name: str
                              |
              +---------------+----------------+
              |               |                |
              v               v                v
       _load_template()  _transcript_hash()  archive.store()
              |               |                |
              v               v                v
       prompt text      cache key           metadata
              |
              v
       render_prompt() -> Claude API -> minutes_md
```
