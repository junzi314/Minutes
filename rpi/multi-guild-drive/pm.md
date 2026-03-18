# Product Viability Analysis: Multi-Guild Google Drive + Error Role

## 1. Product Viability Score: HIGH

This feature addresses a concrete, observable deficiency in multi-guild deployments. It is not speculative; the gap has clear symptoms (Drive-sourced minutes silently land in the wrong guild, error mentions resolve to nothing in non-primary guilds). The implementation scope is narrow and well-bounded.

---

## 2. Context and Why Now

The bot already invested significantly in multi-guild support across its core surface area:

| Capability | Multi-guild today? |
|---|---|
| Craig recording detection | Yes -- routes via `get_guild(guild_id)` |
| Slash commands (`/minutes process`, `/minutes status`) | Yes -- guild-scoped via `interaction.guild_id` |
| Template selection | Yes -- per-guild via StateStore |
| Minutes archive + search | Yes -- filtered by `guild_id` |
| **Google Drive watcher** | **No -- hardcoded to `guilds[0]`** |
| **Error mention role** | **No -- global `discord.error_mention_role_id`** |

The two remaining gaps are the only things preventing the bot from being fully multi-guild operational. Any operator who adds a second guild today will encounter both issues immediately:

- Drive-detected recordings always post to the first guild's output channel, regardless of which guild the recording belongs to.
- Error notifications mention a role ID from the primary guild, which does not exist in the secondary guild, causing Discord to render it as raw text (`<@&123>`) rather than a mention.

These are not edge cases. They are guaranteed failures for multi-guild operators.

---

## 3. Users and Jobs-to-be-Done

**Primary user**: The bot operator (system administrator) who runs this bot across two or more Discord servers.

**Job**: "I want every guild's recordings -- whether triggered by Craig detection or Google Drive -- to produce minutes in that guild's designated channel, with error notifications reaching the right people in each guild."

**Secondary user**: Guild members in non-primary servers. Their job is simply "I want my meeting minutes to appear in my server's channel," but today Drive-sourced minutes are silently diverted elsewhere.

---

## 4. User Value Assessment: HIGH

### Quantified impact

- **Google Drive routing fix**: Without this, any guild beyond `guilds[0]` gets zero minutes from the Drive watcher path. This is a 100% failure rate for secondary guilds using Drive.
- **Error role fix**: Without this, error mentions in secondary guilds are non-functional. Operators may not notice failures until users complain about missing minutes.

### Severity matrix

| Issue | Severity | Frequency | Detectability |
|---|---|---|---|
| Drive posts to wrong guild | High (data goes to wrong audience) | Every Drive detection | Low (silent misdirection) |
| Error mention broken in non-primary guild | Medium (notification fails) | Every error in non-primary guild | Medium (raw mention text visible) |

The Drive routing issue is particularly insidious because it is silent -- minutes appear in the wrong guild's channel without any error, and users in the intended guild see nothing. This is worse than an error; it is incorrect behavior that could go unnoticed.

---

## 5. Strategic Alignment: STRONG

The bot's mission is automated meeting minutes from voice recordings. Multi-guild is not a peripheral feature; it is a core architectural commitment that the team has already made (guild list in config, per-guild routing in Craig detection, guild-scoped slash commands, guild-filtered archive). The two remaining gaps undermine that commitment.

Completing multi-guild support also:

- Removes a known footgun for anyone following the documented config format (the YAML schema already implies multi-guild works, but Drive does not).
- Eliminates a class of "why is this happening?" support questions.
- Unblocks potential future features that assume multi-guild parity (e.g., per-guild Drive folder IDs, per-guild analytics dashboards).

---

## 6. Requirements Summary

### Functional Requirements

**FR-1**: Per-guild Google Drive folder configuration
- Each guild entry in `discord.guilds[]` may specify its own `google_drive.folder_id` and `google_drive.enabled` flag.
- Acceptance criteria: When two guilds are configured with different Drive folder IDs, files from folder A produce minutes in guild A's output channel, and files from folder B produce minutes in guild B's output channel.

**FR-2**: Per-guild error mention role
- Each guild entry in `discord.guilds[]` may specify its own `error_mention_role_id`.
- Acceptance criteria: When a pipeline error occurs for a guild with a configured role ID, the error embed mentions that guild's role, not the global one.

**FR-3**: Global fallback for error role
- The existing global `discord.error_mention_role_id` continues to work as a fallback when a guild does not specify its own.
- Acceptance criteria: A guild without `error_mention_role_id` in its config falls back to the global value. A guild with `error_mention_role_id: null` explicitly disables mentions.

**FR-4**: Backward compatibility for single-guild config
- The existing top-level `google_drive` section continues to function for single-guild deployments.
- Acceptance criteria: An operator who upgrades the bot without changing `config.yaml` sees identical behavior to the previous version.

**FR-5**: Multiple Drive watchers
- When multiple guilds have Drive enabled, the bot runs one `DriveWatcher` per guild, each polling its own folder.
- Acceptance criteria: All watchers run concurrently, each with independent polling and error handling.

### Non-Functional Requirements

**NFR-1 (Resource usage)**: Each additional DriveWatcher adds one asyncio task and one Google Drive API polling loop. At the default 30-second interval, adding 5 guilds means 5 API calls per 30 seconds -- well within Google Drive API quotas (1,000 requests per 100 seconds per user for service accounts).

**NFR-2 (Config validation)**: Invalid per-guild Drive configs (e.g., enabled but missing folder_id) must be caught at startup with a clear error message, not at runtime.

**NFR-3 (Observability)**: Each DriveWatcher's log messages must include the guild ID to distinguish which watcher produced the log line.

---

## 7. Scope

### In scope
- `GuildConfig` dataclass extension (add `error_mention_role_id`, `google_drive` sub-config)
- Config parser changes for new per-guild fields with backward compat
- `bot.py` on_ready: spawn per-guild DriveWatcher instances
- `pipeline.py`: resolve error role from guild config, then global fallback
- Config validation for new fields
- Unit tests for config parsing, fallback logic, and per-guild watcher setup

### Out of scope
- Per-guild Google Drive credentials (all guilds share the same service account) -- this is fine for an operator-owned bot
- Per-guild poll intervals or file patterns (use global defaults; can be added later if needed)
- Migration tooling for existing config files (the backward compat layer handles this automatically)
- UI/slash-command for runtime Drive config changes

---

## 8. Implementation Complexity Assessment

| Component | Changes needed | Complexity |
|---|---|---|
| `src/config.py` | Add fields to `GuildConfig`, update parser + validation | Low |
| `config.yaml` | Schema expansion with defaults | Low |
| `bot.py` `on_ready` | Loop over guilds, create per-guild DriveWatcher | Low-Medium |
| `src/pipeline.py` | Change 4 occurrences of `cfg.discord.error_mention_role_id` to guild-resolved lookup | Low |
| `src/drive_watcher.py` | No changes needed (already receives config via constructor) | None |
| Tests | Update config tests, add multi-guild Drive watcher tests | Medium |

Total estimated effort: 1-2 days of focused development, including tests.

The `DriveWatcher` class already accepts `GoogleDriveConfig` via its constructor and is completely decoupled from guild awareness. The fix is entirely in the orchestration layer (`bot.py`) and the config layer. This is a good sign -- it means the existing architecture was designed with this extensibility in mind, even if the wiring was not completed.

---

## 9. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Google API quota exceeded with many guild watchers | Low | Medium | Document maximum guild count; add jitter to poll intervals |
| Config migration confusion | Low | Low | Backward compat is automatic; document new format in config comments |
| Race condition between multiple watchers processing same file | Very Low | Low | StateStore dedup already handles this via `is_known`/`mark_processing` |
| Service account lacks access to guild-specific folders | Medium | Low | This is an operator config issue; validate at startup and log clear errors |

No blocking risks identified.

---

## 10. Priority Recommendation: P1 (High Priority, Next Sprint)

**Rationale**:

1. **Correctness over features**: This is a bug fix disguised as a feature. Multi-guild is already the documented and configured architecture, but it silently fails for two subsystems. Shipping new features on top of a broken multi-guild foundation creates compounding confusion.

2. **Low effort, high certainty**: The implementation touches well-understood, well-tested code paths. The `DriveWatcher` is already properly decoupled. The config system already supports per-guild fields. This is "finish the wiring" work, not design work.

3. **Zero user-facing UX changes**: No new slash commands, no new Discord interactions. This is invisible infrastructure that "just works."

4. **Unblocks future work**: Several items in the RPI backlog (per-guild templates, per-guild analytics) benefit from a proven multi-guild config pattern. Establishing the `GuildConfig` extension pattern now creates a reusable precedent.

---

## 11. Product Concerns and Red Flags

**No red flags identified.** Specific points considered:

- **Not over-engineering**: The feature request is scoped to exactly two known-broken capabilities. It does not introduce per-guild credentials, per-guild poll intervals, or other speculative extensions.
- **Not user-requested bloat**: This is operator-facing infrastructure, not feature creep. It fixes silent failures in an already-committed architecture.
- **Backward compatibility is natural**: The existing global `google_drive` section and `error_mention_role_id` map directly to fallback semantics. No forced migration.
- **Testing is straightforward**: The changes are in pure config parsing and orchestration logic, both of which are easy to unit test without Discord or Google API mocks.

The only concern worth noting is that the `pipeline.py` currently receives the error role ID from `cfg.discord.error_mention_role_id` (a global value). After this change, the pipeline needs to know which guild's role to use. Since `output_channel.guild.id` is available at all call sites, this resolution is straightforward -- but it does mean the pipeline functions need a way to look up per-guild config. The cleanest approach is to pass the resolved `error_mention_role_id` into the pipeline rather than having the pipeline resolve it, keeping the pipeline unaware of guild config details.
