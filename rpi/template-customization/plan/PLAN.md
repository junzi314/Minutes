# Implementation Roadmap: 議事録テンプレートカスタマイズ (Merge Checklist)

## Summary

| 項目 | 値 |
|------|-----|
| **Feature** | 議事録テンプレートカスタマイズ |
| **Traceability** | R-78 |
| **Complexity** | Low (残作業のみ) |
| **Implementation Status** | ~95% complete (unstaged working tree) |
| **Total Phases** | 4 |
| **Remaining Tasks** | 7 |
| **Research Recommendation** | GO (95% confidence) |
| **Risk Level** | Low |

> **NOTE**: This is a merge-readiness checklist, not a build plan. All core infrastructure
> (template engine, config, state persistence, slash commands, pipeline integration, tests)
> is already implemented and passing 250+ tests. The original 3-phase build plan was
> executed on 2026-03-17 (see `implement/IMPLEMENT.md`). This document tracks the
> remaining conditions and merge steps identified in the research report.

---

## Phase 1: Permission Gate (C1) — REQUIRED

**Goal**: `template-set` コマンドへの `manage_guild` 権限ゲートを追加し、任意のギルドメンバーによる無許可テンプレート変更を防止する。

### Tasks

| # | Task | File | Complexity | Depends |
|---|------|------|------------|---------|
| 1.1 | `template_set` に `@discord.app_commands.checks.has_permissions(manage_guild=True)` デコレータ追加 | `bot.py` | Low | — |
| 1.2 | `MissingPermissions` エラーハンドラ追加（既存の `tree.on_error` で対応可能か確認、不足なら個別ハンドラ追加） | `bot.py` | Low | 1.1 |
| 1.3 | 権限チェックの単体テスト追加 | `tests/` | Low | 1.1 |

### Implementation Details

#### Task 1.1: Permission decorator

`bot.py` の `template_set` コマンド定義（現在 line ~477）に1行追加:

```python
@group.command(name="template-set", description="Set the template for this guild")
@discord.app_commands.checks.has_permissions(manage_guild=True)  # NEW
@discord.app_commands.describe(name="Template name")
async def template_set(interaction: discord.Interaction, name: str) -> None:
    ...
```

#### Task 1.2: Error handler

discord.py の `CommandTree.on_error` が `MissingPermissions` を自動的にユーザーに通知するか確認。不十分な場合は `template_set.error` ハンドラを追加:

```python
@template_set.error
async def template_set_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message(
            "このコマンドには **サーバー管理** 権限が必要です。", ephemeral=True
        )
    else:
        raise error
```

#### Task 1.3: Permission test

テストファイルに権限チェック存在を検証するテストを追加。デコレータ検査（`template_set.checks` にパーミッションチェックが含まれることを確認）またはモック `Interaction` での拒否動作テスト。

### Success Criteria

- [ ] `manage_guild` 権限を持たないユーザーが `/minutes template-set` を実行するとエラーメッセージが表示される
- [ ] 権限を持つユーザーは従来通りテンプレートを変更できる
- [ ] 全 250+ テストがパスする

---

## Phase 2: UX Polish (C2) — RECOMMENDED

**Goal**: 情報表示系コマンドの応答をエフェメラルにし、チャンネルのノイズを削減する。

### Tasks

| # | Task | File | Complexity | Depends |
|---|------|------|------------|---------|
| 2.1 | `template-list` の応答に `ephemeral=True` を追加 | `bot.py` | Low | — |
| 2.2 | `template-set` が既に ephemeral であることを確認 | `bot.py` | Low | — |

### Implementation Details

#### Task 2.1: template-list ephemeral

`bot.py` の `template_list` コマンド（現在 line ~462）:

```python
# Before:
await interaction.response.send_message(embed=embed)

# After:
await interaction.response.send_message(embed=embed, ephemeral=True)
```

#### Task 2.2: Confirmation

`template_set` は既に `ephemeral=True` を使用している（確認済み）。変更不要。

### Success Criteria

- [ ] `/minutes template-list` の応答がコマンド実行者のみに表示される
- [ ] `/minutes template-set` の応答が引き続きエフェメラルである
- [ ] テスト回帰なし

---

## Phase 3: Merge & Validation

**Goal**: 全変更をステージングし、テスト通過を確認し、PRを作成する。

### Tasks

| # | Task | File | Complexity | Depends |
|---|------|------|------------|---------|
| 3.1 | 全テスト実行: `pytest` — 250+ パスを確認 | — | Low | Phase 1, 2 |
| 3.2 | 全変更ファイルの一貫性レビュー | 全対象ファイル | Low | 3.1 |
| 3.3 | ステージング + コミット | — | Low | 3.2 |
| 3.4 | PR作成（包括的な説明付き） | — | Low | 3.3 |

### Files to Stage

| File | Change Type | Content |
|------|-------------|---------|
| `src/generator.py` | MODIFY | TemplateInfo, metadata parser, multi-template cache, list_templates |
| `src/config.py` | MODIFY | GuildConfig.template field, _build_discord_section parsing |
| `src/state_store.py` | MODIFY | guild_settings persistence (get/set_guild_template) |
| `src/pipeline.py` | MODIFY | template_name propagation, _transcript_hash with template |
| `bot.py` | MODIFY | resolve_template, template-list, template-set, autocomplete, status, pipeline integration |
| `prompts/minutes.txt` | MODIFY | Metadata header (# name:, # description:) |
| `prompts/todo-focused.txt` | NEW | TODO重視テンプレート (46 lines) |
| `config.yaml` | MODIFY | template field documentation |
| `tests/test_generator.py` | MODIFY | Template listing, metadata, loading, rendering tests |
| `tests/test_config.py` | MODIFY | GuildConfig.template field tests |
| `tests/test_state_store.py` | MODIFY | Guild template persistence tests |
| `tests/test_pipeline.py` | MODIFY | _transcript_hash with template_name tests |
| `tests/test_poster.py` | MODIFY | (if modified) |

### Success Criteria

- [ ] `pytest` — 250+ テスト全数パス
- [ ] テンプレート関連の新規テスト 67+ アサーション（5ファイルに分散）がすべてパス
- [ ] `config.yaml` に `template` フィールドが未設定でも既存ギルドが影響を受けない（後方互換性）
- [ ] PR の説明に全変更ファイルと機能概要を含める

---

## Phase 4: Post-merge Follow-ups (SEPARATE PRs)

**Goal**: 非デフォルトテンプレートのUXを改善する。マージ後に独立したPRとして対応。

### F1: poster.py regex expansion (P3)

**Effort**: ~4行 + テスト2件

`src/poster.py` のセクション抽出パターンを拡張し、todo-focused テンプレートのセクション名にも対応する:

```python
# Before:
_SUMMARY_PATTERN = re.compile(
    r"## まとめ\s*\n(.*?)(?=\n## |\Z)", re.DOTALL
)
_DECISIONS_PATTERN = re.compile(
    r"## 推奨される次のステップ\s*\n(.*?)(?=\n## |\Z)", re.DOTALL
)

# After:
_SUMMARY_PATTERN = re.compile(
    r"## (?:まとめ|要約)\s*\n(.*?)(?=\n## |\Z)", re.DOTALL
)
_DECISIONS_PATTERN = re.compile(
    r"## (?:推奨される次のステップ|アクションアイテム\s*/\s*TODO)\s*\n(.*?)(?=\n## |\Z)", re.DOTALL
)
```

**現状の影響**: todo-focused テンプレート使用時、embed の「まとめ」「次のステップ」フィールドが空になる。`.md` ファイル添付には全内容が含まれるため graceful degradation であり、クラッシュは発生しない。

### F2: `/minutes template-preview <name>` command (P3)

**Effort**: ~40行

テンプレート選択前にサンプル出力を確認できるコマンド。Claude API呼び出しが必要なためコスト考慮あり。

### F3: Custom template variables (P4)

**Effort**: ~80行

標準5変数 (`{transcript}`, `{date}`, `{speakers}`, `{guild_name}`, `{channel_name}`) 以外のユーザー定義変数。

### F4: Template-specific embed formatting via metadata (P4)

**Effort**: ~60行

テンプレートメタデータコメントで embed 抽出セクション名を宣言可能にする。

---

## Dependency Chart

```
Phase 1 (Permission Gate)
  |
  v
Phase 2 (UX Polish)      [Phase 1 と並列可能だが順序推奨]
  |
  v
Phase 3 (Merge)
  |
  v
Phase 4 (Follow-ups)     [マージ後、独立PR]
```

Phase 1 と Phase 2 は技術的に並列実行可能。ただし Phase 1 (C1) はマージ必須条件のため先に完了すること。

---

## Risk Summary

| Risk | Impact | Probability | Status | Mitigation |
|------|--------|-------------|--------|------------|
| Permission gate 未実装のままマージ | Medium | — | **Fix required (Phase 1)** | `manage_guild` デコレータ追加 (1行) |
| poster.py embed 劣化（非デフォルトテンプレート） | Low | Medium | Known, acceptable | Graceful degradation。F1 で対応可能（post-merge） |
| テンプレートファイル内容変更が即座に反映されない | Low | Low | Documented | テンプレート*選択*変更は即座に反映。ファイル*内容*変更は Bot 再起動が必要 |
| キャッシュキー移行（初回のみ） | Low | Very Low | One-time | テンプレート変更後の最初のリクエストでキャッシュミス→再生成。データ損失なし |
| パストラバーサル攻撃 | High | Very Low | Mitigated | `_load_template()` が `..`, `/`, `\\` を含む名前を拒否 |

---

## Test Plan

| Area | Test Type | Status | Files | Assertions |
|------|-----------|--------|-------|------------|
| Template listing | Unit | PASS | test_generator.py | `TestListTemplates` |
| Template metadata parsing | Unit | PASS | test_generator.py | `TestParseTemplateMetadata` |
| Template loading + cache | Unit | PASS | test_generator.py | `TestLoadTemplate` |
| Path traversal prevention | Unit | PASS | test_generator.py | `_load_template` rejects `..`, `/`, `\\` |
| Prompt rendering with template | Unit | PASS | test_generator.py | `TestRenderPrompt` |
| GuildConfig.template field | Unit | PASS | test_config.py | default value + YAML parsing |
| Multi-guild template parsing | Unit | PASS | test_config.py | guilds list + legacy format |
| Guild template persistence | Unit | PASS | test_state_store.py | set/get + atomic write |
| Guild settings flush | Unit | PASS | test_state_store.py | guild_settings.json output |
| Cache key isolation | Unit | PASS | test_pipeline.py | template_name changes hash |
| Pipeline template propagation | Unit | PASS | test_pipeline.py | template_name in generate() |
| Archive template_name | Unit | PASS | test_minutes_archive.py | template_name column |
| **Permission gate (C1)** | **Unit** | **TODO** | **TBD** | **manage_guild check** |

**Current coverage**: 67 template-related assertions across 5 test files. 250+ total tests passing.

---

## Rollback Plan

テンプレートカスタマイズ機能は全パラメータにデフォルト値を持つため、段階的なロールバックが可能:

### Level 1: ギルド設定のみリセット（最小限）

```bash
rm state/guild_settings.json
# Bot再起動 → 全ギルドが GuildConfig.template (デフォルト "minutes") に戻る
```

### Level 2: コード変更のリバート（完全）

```bash
git revert <merge-commit-hash>
rm state/guild_settings.json
rm prompts/todo-focused.txt
# Bot再起動
```

### 安全性の根拠

- `GuildConfig.template` のデフォルト値は `"minutes"`（既存テンプレート）
- `resolve_template()` の最終フォールバックも `"minutes"`
- `guild_settings.json` が存在しなくてもエラーにならない（空 dict で初期化）
- 新規パラメータ (`template_name`) は全て optional with default `"minutes"`

---

## Implemented Components Reference

以下は既に実装・テスト済みのコンポーネント一覧（Research Report Appendix より）:

| Component | File | Lines | Status |
|-----------|------|-------|--------|
| `TemplateInfo` dataclass | `src/generator.py:19-27` | 9 | Implemented |
| `_parse_template_metadata()` | `src/generator.py:29-50` | 22 | Implemented |
| `_load_template()` + path traversal guard | `src/generator.py:84-102` | 19 | Implemented |
| `list_templates()` | `src/generator.py:104-118` | 15 | Implemented |
| `render_prompt()` with template_name | `src/generator.py:124-151` | 28 | Implemented |
| `generate()` with template_name | `src/generator.py:153-245` | 93 | Implemented |
| `GuildConfig.template` field | `src/config.py:39-47` | 1 | Implemented |
| Multi-guild format parsing | `src/config.py:275-298` | 24 | Implemented |
| `get_guild_template()` / `set_guild_template()` | `src/state_store.py:216-229` | 14 | Implemented |
| `_flush_guild_settings()` | `src/state_store.py:275-276` | 2 | Implemented |
| `resolve_template()` | `bot.py:223-234` | 12 | Implemented |
| `/minutes template-list` | `bot.py:462-475` | 14 | Implemented |
| `/minutes template-set` + autocomplete | `bot.py:477-502` | 26 | Implemented |
| `_transcript_hash()` with template | `src/pipeline.py:40-43` | 4 | Implemented |
| Pipeline template_name propagation | `src/pipeline.py:46-56, 238-248` | 20 | Implemented |
| `prompts/minutes.txt` metadata | `prompts/minutes.txt:1-2` | 2 | Updated |
| `prompts/todo-focused.txt` | `prompts/todo-focused.txt` | 46 | New file |
