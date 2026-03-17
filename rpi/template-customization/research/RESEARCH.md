# Research Report: 議事録テンプレートカスタマイズ (Template Customization)

**Feature Slug**: template-customization
**Date**: 2026-03-17
**Recommendation**: GO
**Confidence**: 95% (High)

---

## Executive Summary

**Recommendation: GO** -- Merge after adding `manage_guild` permission gate to `/minutes template-set`.

ギルドごとに議事録テンプレートをカスタマイズする機能は、現在の作業ツリー（unstaged changes）に **~95% 実装済み** であることが確認された。`generator.py` にテンプレートキャッシュ・メタデータ解析・パストラバーサルガード、`config.py` に `GuildConfig.template` フィールド、`state_store.py` に `guild_settings.json` 永続化、`bot.py` に `template-list`/`template-set` スラッシュコマンド+オートコンプリート、`pipeline.py` にテンプレート名のキャッシュキー統合がそれぞれ実装されている。250テスト全数パスを確認。既存機能への後方互換性に問題なし。マージに必要な唯一の条件は `template-set` コマンドへの `manage_guild` 権限ゲート追加（1行）であり、本レポートは merge readiness assessment として **GO** を推奨する。

---

## 1. Feature Overview

| 項目 | 値 |
|------|-----|
| **Feature Name** | 議事録テンプレートカスタマイズ (Template Customization) |
| **Type** | Enhancement（既存機能の拡張） |
| **Target Components** | generator.py, config.py, state_store.py, bot.py, pipeline.py, prompts/ |
| **Complexity** | Medium (Size M) |
| **Traceability** | R-78 |
| **Implementation Order** | Ext-2 |

### Goals

1. `prompts/` ディレクトリに複数テンプレートを配置し、ファイル追加のみで新テンプレートを利用可能にする
2. `config.yaml` でギルドごとにデフォルトテンプレートを指定可能にする
3. スラッシュコマンド (`/minutes template-list`, `/minutes template-set`) でランタイムにテンプレートを切替可能にする
4. テンプレート選択を即座に反映し、Bot再起動不要とする
5. デフォルトテンプレート (`minutes.txt`) の後方互換性を維持する

---

## 2. Requirements Summary

### Must-Have (ALL IMPLEMENTED)

1. **複数テンプレートの配置** -- `prompts/` ディレクトリ配下の `.txt` ファイルを自動検出。`list_templates()` でスキャン [R-78]
2. **ギルド別デフォルトテンプレート** -- `GuildConfig.template: str = "minutes"` フィールドによる config.yaml 設定。multi-guild / legacy 両形式対応 [R-78]
3. **`/minutes template-list` コマンド** -- Discord Embed で利用可能テンプレートを表示。現在選択中のテンプレートに `(現在)` マーカー付与 [R-78]
4. **`/minutes template-set <name>` コマンド** -- ギルドのテンプレートを変更。`state_store.set_guild_template()` で `guild_settings.json` に永続化。オートコンプリート対応 [R-78]
5. **3層テンプレート解決** -- `resolve_template()` 優先度: state_store override > GuildConfig.template > `"minutes"` default
6. **テンプレートのホットリロード** -- テンプレート選択変更は即座に反映（次回の議事録生成から適用、再起動不要）

### Nice-to-Have (NOT IMPLEMENTED, deferred)

- `/minutes template-preview <name>` -- サンプル出力のプレビュー表示
- カスタム変数 -- 標準5変数 (`{transcript}`, `{date}`, `{speakers}`, `{guild_name}`, `{channel_name}`) 以外のユーザー定義変数

### Non-Functional

- テンプレートファイルからのパストラバーサル防止（`..`, `/`, `\\` 含む名前を拒否）
- テンプレートメタデータ（`# name:`, `# description:` コメント形式）による表示名・説明文の管理
- テンプレート内容のメモリキャッシュ（同一テンプレートの再読み込み回避）

---

## 3. Product Analysis

### User Value: **High**

| 観点 | 評価 |
|------|------|
| **課題の深刻度** | 中-高。マルチギルド運用では各コミュニティの議事録ニーズが異なる（技術系: 詳細重視、ビジネス系: TODO重視、教育系: 要約重視）。単一テンプレートでは対応不可 |
| **影響範囲** | マルチギルド運用時に顕著な価値。単一ギルドでも会議種別（定例会議 vs ブレスト vs 1on1）による使い分けが有用 |
| **ユーザー体験** | スラッシュコマンドのオートコンプリートで直感的な操作。テンプレート追加はファイル配置のみでコード変更不要 |
| **即効性** | テンプレート変更は次回の議事録生成から即座に反映。全ギルドの既存設定に影響なし |

### Market Fit

議事録ツール市場では出力フォーマットのカスタマイズは標準的な機能。Otter.ai、Fireflies.ai 等の商用ツールは複数テンプレート対応を基本機能として提供しており、Discord Bot でのテンプレート切替は競争力の維持に必要。

### Strategic Alignment: **Full**

| 設計原則 | 適合性 | 根拠 |
|----------|--------|------|
| Pipeline-first | ✅ | `template_name` パラメータがパイプライン全体を通じて伝播。pipeline.py, generator.py の変更は最小限 |
| Async by default | ✅ | テンプレートロードは軽量な同期ファイル読み込み（メモリキャッシュあり）。API呼び出しは従来通り `asyncio.to_thread` で非同期 |
| Graceful degradation | ✅ | デフォルトテンプレート `minutes.txt` で後方互換。テンプレート未設定ギルドは自動的にデフォルトを使用 |
| Multi-guild support | ✅ | ギルドごとのテンプレート選択は multi-guild 設計原則に直結。`GuildConfig.template` + `state_store.get_guild_template()` の2層構造 |
| Minimal state | ✅ | 永続化は `guild_settings.json` の1ファイルのみ。StateStore の既存パターン（atomic write + in-memory mirror）を踏襲 |

### Product Viability Score: **8.5/10 -- STRONG GO**

### Concerns

1. **poster.py embed degradation** -- カスタムテンプレートが `## まとめ` / `## 推奨される次のステップ` セクション名を使わない場合、embed からの抽出が空になる。ただしクラッシュはせず、embed のフィールドが省略されるだけの graceful degradation
2. **Permission gate なし** -- 現在の `template-set` コマンドには `manage_guild` 権限ゲートがない。任意のギルドメンバーがテンプレートを変更可能

---

## 4. Technical Discovery

### Current State: ~95% Implemented

全コアインフラストラクチャがワーキングツリー（unstaged changes）に実装済みであることを確認した。

#### `src/generator.py` -- Template Engine (246 lines)

| Component | Status | Detail |
|-----------|--------|--------|
| `TemplateInfo` dataclass | ✅ Implemented | `name`, `display_name`, `description`, `path` の4フィールド |
| `_parse_template_metadata()` | ✅ Implemented | ファイル先頭の `# name:` / `# description:` コメントを解析 |
| `_load_template()` | ✅ Implemented | 名前によるテンプレートロード + キャッシュ + パストラバーサルガード (`..`, `/`, `\\` 検出) |
| `list_templates()` | ✅ Implemented | `prompts/*.txt` のスキャン + メタデータ抽出 + ソート済みリスト返却 |
| `render_prompt()` | ✅ Implemented | `template_name` パラメータ対応。`str.replace()` による5変数置換 |
| `generate()` | ✅ Implemented | `template_name` パラメータが `render_prompt()` に伝播 |
| Template cache | ✅ Implemented | `self._templates: dict[str, str]` でロード済みテンプレートをメモリ保持 |

#### `src/config.py` -- Guild Template Configuration (449 lines)

| Component | Status | Detail |
|-----------|--------|--------|
| `GuildConfig.template` field | ✅ Implemented | `template: str = "minutes"` -- frozen dataclass フィールド |
| Multi-guild format parsing | ✅ Implemented | `discord.guilds[i].template` を `GuildConfig` に渡す |
| Legacy format parsing | ✅ Implemented | `discord.template` を single guild の `GuildConfig` に渡す |

#### `src/state_store.py` -- Guild Settings Persistence (396 lines)

| Component | Status | Detail |
|-----------|--------|--------|
| `get_guild_template()` | ✅ Implemented | `guild_settings.json` からギルドのテンプレートオーバーライドを取得 |
| `set_guild_template()` | ✅ Implemented | テンプレート名を `guild_settings.json` にアトミック永続化 |
| `_flush_guild_settings()` | ✅ Implemented | 既存 StateStore パターンに準拠したアトミック書き込み |

#### `bot.py` -- Slash Commands & Template Resolution (630 lines)

| Component | Status | Detail |
|-----------|--------|--------|
| `resolve_template()` | ✅ Implemented | 3層優先度: state_store > GuildConfig > `"minutes"` default |
| `/minutes template-list` | ✅ Implemented | Embed 表示、現在のテンプレートに `(現在)` マーカー、フッターにヘルプ |
| `/minutes template-set <name>` | ✅ Implemented | バリデーション + `state_store.set_guild_template()` 呼び出し + 確認メッセージ |
| Autocomplete | ✅ Implemented | `template_name_autocomplete()` -- テンプレート名の部分一致フィルタ (max 25) |
| Status command | ✅ Implemented | `resolve_template()` の結果を表示 |
| Drive watcher integration | ✅ Implemented | `_on_drive_tracks` 内で `resolve_template()` 呼び出し |
| Craig pipeline integration | ✅ Implemented | `_launch_pipeline` 内で `resolve_template()` 呼び出し |

#### `src/pipeline.py` -- Template-Aware Pipeline (364 lines)

| Component | Status | Detail |
|-----------|--------|--------|
| `_transcript_hash()` | ✅ Implemented | `f"{template_name}:{transcript}"` -- テンプレート名をキャッシュキーに含む |
| `run_pipeline_from_tracks()` | ✅ Implemented | `template_name` パラメータを `generator.generate()` に伝播 |
| `run_pipeline()` | ✅ Implemented | `template_name` パラメータを `run_pipeline_from_tracks()` に伝播 |

#### Templates

| File | Status | Metadata |
|------|--------|----------|
| `prompts/minutes.txt` (50 lines) | ✅ Existing (updated) | `# name: 標準議事録` / `# description: Geminiメモ風の詳細フォーマット` |
| `prompts/todo-focused.txt` (46 lines) | ✅ New | `# name: TODO重視` / `# description: アクションアイテムとTODOを重視したフォーマット` |

#### Tests

250 テスト全数パス。テンプレート関連テストは以下のファイルに分散:

- `tests/test_generator.py` -- `TestListTemplates`, `TestParseTemplateMetadata`, `TestLoadTemplate`, `TestRenderPrompt` (34 occurrences)
- `tests/test_state_store.py` -- guild template persistence tests (21 occurrences)
- `tests/test_config.py` -- `GuildConfig.template` フィールド解析テスト (7 occurrences)
- `tests/test_pipeline.py` -- `_transcript_hash` with template_name テスト (3 occurrences)
- `tests/test_minutes_archive.py` -- template_name propagation (2 occurrences)

### Integration Points

```
config.yaml
  ↓ (discord.guilds[i].template: "minutes")
config.py (GuildConfig.template)
  ↓
bot.py (resolve_template: state_store → GuildConfig → "minutes")
  ↓
pipeline.py (template_name → _transcript_hash + generator.generate)
  ↓
generator.py (_load_template → render_prompt → Claude API)
  ↓
poster.py (regex extraction: ## まとめ, ## 推奨される次のステップ)

state_store.py (guild_settings.json ← /minutes template-set)
  ↑
bot.py (/minutes template-set → set_guild_template)
```

### Reusable Components

- `StateStore._flush()` pattern -- atomic write (`os.replace`) for `guild_settings.json`
- `_build_section()` / `_build_discord_section()` -- YAML to dataclass conversion with backward compat
- Slash command registration pattern in `register_commands()`
- `_parse_template_metadata()` -- reusable for any file-header metadata extraction

### Technical Constraints

- `GuildConfig` is frozen dataclass -- field addition is backward compatible (has default value)
- `MinutesGenerator` is single instance -- multi-template support via `_templates: dict[str, str]` cache (already implemented)
- Template cache keyed by name -- file content changes require Bot restart or cache eviction (documented behavior, acceptable)
- `poster.py` section extraction relies on fixed regex patterns (`## まとめ`, `## 推奨される次のステップ`) -- custom templates with different section names produce empty embed fields (graceful, not crash)

---

## 5. Technical Analysis

### Feasibility Score: **9/10**

Implementation is functionally complete. Two minor additions bring it to merge readiness.

### What is Already Implemented

The entire feature pipeline is operational:

1. Template files in `prompts/` with metadata comments
2. Config-level per-guild template setting
3. Runtime template override via StateStore
4. Slash commands for listing and setting templates
5. Template name propagation through the pipeline
6. Template-aware cache key to prevent cross-template cache hits
7. Path traversal protection in template loading
8. Autocomplete for template name input

### Remaining Work (2 items)

#### Condition 1 (Required): Permission Gate

Add `manage_guild` permission check to `/minutes template-set`. Without this, any guild member can change the template for the entire guild.

```python
# 1 line addition to template_set command
@discord.app_commands.checks.has_permissions(manage_guild=True)
```

Effort: ~1 line of code + 1 test case.

#### Condition 2 (Recommended): poster.py Regex Expansion

Current `poster.py` regex patterns extract `## まとめ` and `## 推奨される次のステップ` for embed display. The `todo-focused.txt` template uses `## 要約` instead of `## まとめ`, and `## アクションアイテム / TODO` instead of `## 推奨される次のステップ`. Adding alternate patterns would improve embed quality for non-default templates.

```python
# Expand patterns to match both template formats
_SUMMARY_PATTERN = re.compile(
    r"## (?:まとめ|要約)\s*\n(.*?)(?=\n## |\Z)", re.DOTALL
)
```

Effort: ~4 lines of regex modification. Optional -- current behavior is graceful degradation (empty fields, not crash).

### Complexity: **Medium (already absorbed)**

All complexity has already been absorbed in the working tree implementation. The remaining work is trivial.

| Component | Lines Changed | Lines Added | Risk |
|-----------|---------------|-------------|------|
| config.py | ~10 | +1 (GuildConfig.template field) | None |
| generator.py | ~75 | +80 (TemplateInfo, metadata, cache, list) | Low |
| state_store.py | ~20 | +20 (guild settings methods) | Low |
| bot.py | ~100 | +70 (resolve_template, 2 commands, autocomplete) | Low |
| pipeline.py | ~15 | +5 (template_name propagation, hash) | None |
| prompts/todo-focused.txt | -- | +46 (new file) | None |
| Tests | -- | ~60 (across 5 files) | None |
| **Total** | **~220** | **~280** | **Low** |

### Technical Risks

| Risk | Impact | Probability | Status | Mitigation |
|------|--------|-------------|--------|------------|
| poster.py embed degradation for non-standard templates | Low | Medium | Known, acceptable | Graceful degradation (empty fields). Optional regex expansion in Condition 2 |
| No permission gate on template-set | Medium | High | **Fix required** | Add `manage_guild` permission (Condition 1) |
| Template hot-reload not supported | Low | Low | Documented | Template *selection* changes immediately. Template *content* changes require restart. Acceptable for file-based templates |
| Cache key migration | Low | Very Low | One-time | First request after template change triggers a cache miss and regeneration. No data loss |
| Path traversal attempt | High | Very Low | Mitigated | `_load_template()` rejects `..`, `/`, `\\` in template names. `list_templates()` only scans `prompts/*.txt` |

### Alternatives Considered

| Approach | Assessment | Adopted |
|----------|-----------|---------|
| File-based templates + GuildConfig + StateStore | Simple, sufficient, backward compatible, already implemented | ✅ Recommended |
| SQLite for template settings | Over-engineered for ~1 setting per guild. Violates Minimal state principle | ❌ |
| config.yaml write-back for template changes | Dangerous (YAML comments lost, concurrent write risk) | ❌ |
| Discord Modal for template editing | Good UX but implementation complex, 2000-char limit | ❌ (future) |
| Template validation against required sections | Would restrict template freedom unnecessarily | ❌ (deferred) |

### Security Analysis

| Vector | Assessment |
|--------|-----------|
| **Path traversal** | Guarded: `_load_template()` rejects names containing `..`, `/`, `\\` |
| **Template injection** | Not applicable: templates are server-side files, not user-submitted content |
| **Unauthorized template change** | **Gap**: no permission gate on `template-set`. Fix in Condition 1 |
| **Template enumeration** | Acceptable: `template-list` shows all templates. Templates are not secrets |

### Rollback Plan

1. Delete `state/guild_settings.json` (removes all template overrides)
2. Restart bot (all guilds revert to `GuildConfig.template` default, which is `"minutes"`)
3. If full rollback needed: revert code changes; `prompts/minutes.txt` is unchanged and backward compatible

---

## 6. Strategic Recommendation

### Decision: **GO** -- Merge after adding permission gate

### Confidence: **95% (High)**

### Rationale

| Factor | Assessment |
|--------|-----------|
| **Implementation status** | ~95% complete in working tree. This is a merge readiness assessment, not a build decision |
| **Test coverage** | 250 tests pass. Template-related tests span 5 test files with 67 template-related assertions |
| **Risk of proceeding** | Very Low -- 1 line of permission gate code is the only required change |
| **Risk of NOT proceeding** | Low-Medium -- feature is implemented but not shipped, accumulating merge debt |
| **Technical feasibility** | 9/10 -- all components verified, patterns established, zero architectural changes needed |
| **Product value** | High for multi-guild; moderate for single-guild. Enables differentiated output per community |
| **Strategic alignment** | Full alignment with all 5 design principles. Multi-guild template customization is a natural extension |
| **Backward compatibility** | Perfect -- `GuildConfig.template` defaults to `"minutes"`, existing guilds unaffected |
| **Breaking changes** | Zero. All new parameters are optional with sensible defaults |

### Why This Should Ship Now

1. **Implementation is complete** -- delaying merge increases the risk of code drift and merge conflicts with other features in development
2. **Multi-guild users benefit immediately** -- different communities can use different formats without code changes
3. **Two templates already exist** -- `minutes.txt` (Gemini-style detailed format) and `todo-focused.txt` (action-item focused) provide immediate value
4. **Zero regression risk** -- all 250 tests pass, all new parameters have defaults, existing behavior preserved
5. **Foundation for future features** -- template preview, custom variables, and template-specific embed formatting build on this infrastructure

---

## 7. Conditions and Follow-ups

### Conditions (before merge)

| # | Condition | Type | Effort | Rationale |
|---|-----------|------|--------|-----------|
| C1 | Add `manage_guild` permission gate to `/minutes template-set` | Required | 1 line + 1 test | Prevents unauthorized template changes by any guild member |
| C2 | Consider making `/minutes template-list` response ephemeral | Recommended | 1 word change | Reduces channel noise; template list is informational, not collaborative |

### Follow-ups (after merge, separate PRs)

| # | Follow-up | Priority | Effort | Description |
|---|-----------|----------|--------|-------------|
| F1 | poster.py regex expansion | P3 (Low) | ~4 lines | Add alternate section name patterns (`## 要約`, `## アクションアイテム`) for improved embed display with non-default templates |
| F2 | `/minutes template-preview <name>` command | P3 (Low) | ~40 lines | Show a sample output excerpt for a template before selecting it |
| F3 | Custom template variables | P4 (Backlog) | ~80 lines | Allow templates to define additional variables beyond the standard 5 |
| F4 | Template-specific embed formatting | P4 (Backlog) | ~60 lines | Allow templates to declare which sections to extract for embed display via metadata comments |

---

## 8. Next Steps

Based on the GO recommendation:

1. **Apply Condition C1**: Add `@discord.app_commands.checks.has_permissions(manage_guild=True)` to the `template_set` command in `bot.py`
2. **Apply Condition C2** (optional): Add `ephemeral=True` to the `template-list` response
3. **Run full test suite**: `pytest` -- confirm 250+ tests pass
4. **Commit and create PR**: Stage all template-customization changes for review
5. **Post-merge**: Track F1-F4 follow-ups in separate issues

---

## Appendix: Code References

| File | Content | Status |
|------|---------|--------|
| `src/generator.py:19-27` | `TemplateInfo` dataclass (name, display_name, description, path) | Implemented |
| `src/generator.py:29-50` | `_parse_template_metadata()` -- header comment parsing | Implemented |
| `src/generator.py:84-102` | `_load_template()` -- name-based load with cache + path traversal guard | Implemented |
| `src/generator.py:104-118` | `list_templates()` -- directory scan + metadata extraction | Implemented |
| `src/generator.py:124-151` | `render_prompt()` -- template_name parameter, 5-variable replacement | Implemented |
| `src/generator.py:153-245` | `generate()` -- template_name propagation to render_prompt | Implemented |
| `src/config.py:39-47` | `GuildConfig` -- `template: str = "minutes"` field | Implemented |
| `src/config.py:275-289` | Multi-guild format parsing -- `template` field in guilds list | Implemented |
| `src/config.py:291-298` | Legacy format parsing -- `template` field for single guild | Implemented |
| `src/state_store.py:216-229` | `get_guild_template()` / `set_guild_template()` -- guild settings persistence | Implemented |
| `src/state_store.py:275-276` | `_flush_guild_settings()` -- atomic write | Implemented |
| `bot.py:223-234` | `resolve_template()` -- 3-tier priority resolution | Implemented |
| `bot.py:462-475` | `/minutes template-list` command | Implemented |
| `bot.py:477-502` | `/minutes template-set` command + autocomplete | Implemented |
| `src/pipeline.py:40-43` | `_transcript_hash()` -- template_name in cache key | Implemented |
| `src/pipeline.py:46-56` | `run_pipeline_from_tracks()` -- `template_name` parameter | Implemented |
| `src/pipeline.py:238-248` | `run_pipeline()` -- `template_name` parameter | Implemented |
| `src/poster.py:25-33` | Section extraction regexes -- `## まとめ`, `## 推奨される次のステップ` | Fixed patterns (graceful degradation) |
| `prompts/minutes.txt` | 標準議事録テンプレート (Gemini-style detailed) | Updated with metadata |
| `prompts/todo-focused.txt` | TODO重視テンプレート (action-item focused) | New file |
