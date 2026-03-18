# Implementation Roadmap: transcript-glossary

**Feature**: 用語辞書による文字起こし自動補正（Transcript Glossary）
**Complexity**: Simple
**Phases**: 3
**Total Tasks**: 12
**Status**: COMPLETED
**Research**: GO at 95% confidence (`rpi/transcript-glossary/research/RESEARCH.md`)

---

## Overview

Whisperの固有名詞・専門用語の誤認識を、ギルド単位のユーザー定義辞書で自動補正する機能。パイプラインの transcribe 後・merge 前に純粋関数で置換を適用する。既存の StateStore・Segment・Config・スラッシュコマンドパターンをそのまま活用し、新規外部依存なし。transcript-correction-ui（DEFER判定）の代替として、パイプライン再設計なしに80%のニーズをカバーする。

### Design Decisions

- **純粋関数**: `apply_glossary()` は入力を変更せず新しい Segment リストを返す（Segment は frozen dataclass）
- **大文字小文字**: デフォルトは case-insensitive（`re.sub` + `re.IGNORECASE`）。config で切替可能
- **永続化**: StateStore の `guild_settings.json` に `"glossary"` キーとして保存（template と同パターン）
- **正規表現安全**: ユーザー入力を `re.escape()` でエスケープし、正規表現インジェクションを防止

---

## Phase Overview

| Phase | Name | Tasks | Depends On | Estimated Time | Validation Gate |
|-------|------|-------|------------|----------------|-----------------|
| 1 | Core Module + Config | 6 | None | ~2h | `pytest tests/test_glossary.py tests/test_config.py` 全パス |
| 2 | Storage + Pipeline Integration | 4 | Phase 1 | ~1.5h | `pytest tests/test_state_store.py tests/test_pipeline.py` 全パス |
| 3 | Bot Commands | 2 | Phase 2 | ~1.5h | `pytest` 全テストスイートパス |

---

## Phase 1: Core Module + Config

**Goal**: 辞書適用の純粋関数 `apply_glossary()` と `TranscriptGlossaryConfig` を作成し、単体テストで動作を保証する。
**No dependencies. Can start immediately.**

### Tasks

#### 1.1: Create `src/glossary.py`
**Complexity**: Low
**File**: `src/glossary.py` (NEW, ~50 lines)

辞書ベースのテキスト置換を行う純粋関数モジュール。

```python
# Core function signature
def apply_glossary(
    segments: list[Segment],
    glossary: dict[str, str],
    case_sensitive: bool = False,
) -> list[Segment]:
    """Apply glossary replacements to segment text.

    Returns new Segment instances (frozen dataclass).
    Empty glossary returns segments unchanged (short-circuit).
    """
```

実装ポイント:
- Case-insensitive: `re.sub(re.escape(pattern), replacement, text, flags=re.IGNORECASE)`
- Case-sensitive: `text.replace(pattern, replacement)`
- 空の辞書は早期リターン（short-circuit）
- Segment は frozen dataclass のため、新しいインスタンスを生成して返す
- `re.escape()` でユーザー入力の正規表現メタ文字を安全にエスケープ

#### 1.2: Add `TranscriptGlossaryConfig` to `src/config.py`
**Complexity**: Low
**File**: `src/config.py` (MODIFY, +15 lines)

既存の `SpeakerAnalyticsConfig` と同パターンで追加。

```python
@dataclass(frozen=True)
class TranscriptGlossaryConfig:
    enabled: bool = True
    case_sensitive: bool = False
```

変更箇所:
- `TranscriptGlossaryConfig` dataclass 定義を追加（`CalendarConfig` の後）
- `Config` dataclass に `transcript_glossary: TranscriptGlossaryConfig` フィールドを追加
- `_SECTION_CLASSES` に `"transcript_glossary": TranscriptGlossaryConfig` を追加

#### 1.3: Add `transcript_glossary` section to `config.yaml`
**Complexity**: Low
**File**: `config.yaml` (MODIFY, +5 lines)

```yaml
transcript_glossary:
  # Enable dictionary-based correction of Whisper transcription errors
  enabled: true
  # Case-sensitive matching (false = case-insensitive, recommended)
  case_sensitive: false
```

**挿入位置**: `speaker_analytics:` セクションの後、`minutes_archive:` セクションの前

#### 1.4: Create `tests/test_glossary.py`
**Complexity**: Medium
**File**: `tests/test_glossary.py` (NEW, ~100 lines)

8+ テストケース:

| # | テスト | 検証内容 |
|---|--------|---------|
| 1 | `test_empty_glossary` | 空辞書 → セグメント変更なし |
| 2 | `test_empty_segments` | 空セグメントリスト → 空リスト返却 |
| 3 | `test_single_replacement` | 1エントリ辞書で正しく置換 |
| 4 | `test_multiple_replacements` | 複数エントリ辞書で全て置換 |
| 5 | `test_case_insensitive` | デフォルト（case_sensitive=False）で大小文字無視 |
| 6 | `test_case_sensitive` | case_sensitive=True で完全一致のみ |
| 7 | `test_no_match` | 辞書のパターンがテキストに存在しない → 変更なし |
| 8 | `test_immutability` | 元の Segment リストが変更されないことを検証 |
| 9 | `test_regex_escape` | `re` メタ文字（`.`, `*`, `(` 等）を含むパターンが安全に処理される |

#### 1.5: Update `tests/test_config.py`
**Complexity**: Low
**File**: `tests/test_config.py` (MODIFY, +10 lines)

- `TranscriptGlossaryConfig` のデフォルト値テスト（`enabled=True`, `case_sensitive=False`）
- YAML に `transcript_glossary: { enabled: false }` を設定した場合のテスト
- 既存の Config テストフィクスチャに `transcript_glossary` を追加

#### 1.6: Update `tests/test_pipeline.py`
**Complexity**: Low
**File**: `tests/test_pipeline.py` (MODIFY, +5 lines)

- `_make_config()` ヘルパーに `TranscriptGlossaryConfig()` を追加（既存テストが Config 構築エラーを起こさないようにする）

### 成功基準

- [ ] `src/glossary.py` が存在し、`apply_glossary()` が正しく動作する
- [ ] `TranscriptGlossaryConfig` が Config に追加され、YAML からロード可能
- [ ] `config.yaml` に `transcript_glossary:` セクションが存在する
- [ ] 全テストパス: `pytest tests/test_glossary.py tests/test_config.py tests/test_pipeline.py`

### 依存関係

- なし

### Validation Gate

```bash
pytest tests/test_glossary.py tests/test_config.py tests/test_pipeline.py -v
# test_glossary.py: 8+ tests passed
# test_config.py: existing + new tests passed
# test_pipeline.py: existing tests not broken by Config change
```

---

## Phase 2: Storage + Pipeline Integration

**Goal**: StateStore にギルド辞書の永続化メソッドを追加し、パイプラインの transcribe → merge 間に辞書適用を挿入する。
**Depends on**: Phase 1

### Tasks

#### 2.1: Add glossary methods to `src/state_store.py`
**Complexity**: Low
**File**: `src/state_store.py` (MODIFY, +20 lines)

既存の `get_guild_template()` / `set_guild_template()` と同一パターン。

```python
def get_guild_glossary(self, guild_id: int) -> dict[str, str]:
    """Return the glossary dict for a guild (empty dict if not set)."""
    settings = self._guild_settings.get(str(guild_id))
    if settings is None:
        return {}
    return dict(settings.get("glossary", {}))

def set_guild_glossary(self, guild_id: int, glossary: dict[str, str]) -> None:
    """Set the glossary for a guild."""
    key = str(guild_id)
    if key not in self._guild_settings:
        self._guild_settings[key] = {}
    self._guild_settings[key]["glossary"] = glossary
    self._flush_guild_settings()
```

格納先: `state/guild_settings.json` の `"glossary"` キー（template と並列）。

#### 2.2: Insert glossary application in `src/pipeline.py`
**Complexity**: Low
**File**: `src/pipeline.py` (MODIFY, +8 lines)

`run_pipeline_from_tracks()` 内、`_stage_transcribe()` 後（L83）、speaker_analytics（L86-91）の前に挿入。

```python
# Glossary correction (between transcribe and merge)
if cfg.transcript_glossary.enabled:
    guild_id = output_channel.guild.id if output_channel.guild else 0
    glossary = state_store.get_guild_glossary(guild_id)
    if glossary:
        from src.glossary import apply_glossary
        segments = apply_glossary(
            segments, glossary, cfg.transcript_glossary.case_sensitive,
        )
        logger.info("[glossary] Applied %d replacements for guild=%d", len(glossary), guild_id)
```

挿入位置の根拠:
- transcribe 後: セグメントが生成された直後
- speaker_analytics 前: 統計計算には補正済みテキストを使いたい
- merge 前: マージ時のテキスト結合に補正済みテキストが反映される

#### 2.3: Update `tests/test_state_store.py`
**Complexity**: Low
**File**: `tests/test_state_store.py` (MODIFY, +15 lines)

| # | テスト | 検証内容 |
|---|--------|---------|
| 1 | `test_get_guild_glossary_empty` | 未設定ギルド → 空 dict 返却 |
| 2 | `test_set_and_get_guild_glossary` | set → get でラウンドトリップ |
| 3 | `test_guild_glossary_persists` | flush 後に再ロードしても辞書が保持される |
| 4 | `test_guild_glossary_coexists_with_template` | テンプレートと辞書が同一ギルド設定に共存 |

#### 2.4: Update `tests/test_pipeline.py`
**Complexity**: Medium
**File**: `tests/test_pipeline.py` (MODIFY, +15 lines)

| # | テスト | 検証内容 |
|---|--------|---------|
| 1 | `test_glossary_applied_when_enabled` | `enabled=True` + 非空辞書 → セグメントテキストが補正される |
| 2 | `test_glossary_skipped_when_disabled` | `enabled=False` → セグメントテキスト変更なし |

### 成功基準

- [ ] StateStore が `get_guild_glossary()` / `set_guild_glossary()` を提供する
- [ ] パイプラインが transcribe → glossary → speaker_analytics → merge の順序で処理する
- [ ] 辞書が `guild_settings.json` に永続化される
- [ ] 全テストパス: `pytest tests/test_state_store.py tests/test_pipeline.py`

### 依存関係

- Phase 1 完了が必須（`TranscriptGlossaryConfig` と `apply_glossary` が必要）

### Validation Gate

```bash
pytest tests/test_state_store.py tests/test_pipeline.py -v
# state_store: glossary get/set/persist tests passed
# pipeline: glossary enabled/disabled tests passed
```

---

## Phase 3: Bot Commands

**Goal**: Discord スラッシュコマンドで辞書の CRUD 操作を提供する。
**Depends on**: Phase 2

### Tasks

#### 3.1: Add glossary commands to `bot.py`
**Complexity**: Medium
**File**: `bot.py` (MODIFY, +60 lines)

既存の `/minutes template-list` / `/minutes template-set` パターンに従い、glossary サブグループを追加。

**コマンド一覧**:

| コマンド | 説明 | 権限 | 応答 |
|---------|------|------|------|
| `/minutes glossary-add <wrong> <correct>` | 辞書エントリを追加 | `manage_guild` | ephemeral |
| `/minutes glossary-remove <wrong>` | 辞書エントリを削除 | `manage_guild` | ephemeral |
| `/minutes glossary-list` | 現在の辞書一覧を表示 | `manage_guild` | ephemeral (Embed) |

実装ポイント:
- 全コマンドに `@discord.app_commands.checks.has_permissions(manage_guild=True)` デコレータ
- 全応答は `ephemeral=True`
- `glossary-list` は Discord Embed で表示（フィールド: 誤認識 -> 正しい表記）
- 空辞書の場合は「辞書が空です」メッセージを表示
- `glossary-add` は StateStore から辞書を取得、追加、保存
- `glossary-remove` は該当キーが存在しない場合はエラーメッセージ

```python
# Command pattern (following existing template-set pattern)
@group.command(name="glossary-add", description="Add a glossary entry for transcript correction")
@discord.app_commands.checks.has_permissions(manage_guild=True)
@discord.app_commands.describe(wrong="Incorrect text from Whisper", correct="Correct replacement text")
async def glossary_add(interaction: discord.Interaction, wrong: str, correct: str) -> None:
    glossary = client.state_store.get_guild_glossary(interaction.guild_id)
    glossary[wrong] = correct
    client.state_store.set_guild_glossary(interaction.guild_id, glossary)
    await interaction.response.send_message(
        f"辞書に追加しました: `{wrong}` -> `{correct}`", ephemeral=True,
    )
```

#### 3.2: Add error handler for permission check
**Complexity**: Low
**File**: `bot.py` (MODIFY, included in 3.1 line count)

既存の `template_set` 権限チェックパターンを踏襲。`MissingPermissions` は既存の `tree.on_error` で処理されるか確認し、不足であれば個別ハンドラを追加。

### 成功基準

- [ ] `/minutes glossary-add <wrong> <correct>` が辞書エントリを追加する
- [ ] `/minutes glossary-remove <wrong>` が辞書エントリを削除する
- [ ] `/minutes glossary-list` が現在の辞書を Embed で表示する
- [ ] 全コマンドが `manage_guild` 権限を要求する
- [ ] 全応答がエフェメラルである
- [ ] 全テストスイートパス: `pytest`

### 依存関係

- Phase 2 完了が必須（StateStore の `get_guild_glossary` / `set_guild_glossary` が必要）

### Validation Gate

```bash
pytest -v
# All tests pass (including new glossary, config, state_store, pipeline tests)
# No regressions in existing test suite
```

---

## Dependency Graph

```
Phase 1 (Core Module + Config)
  |
  v
Phase 2 (Storage + Pipeline)
  |
  v
Phase 3 (Bot Commands)
```

全フェーズは直列依存。Phase 1 → 2 → 3 の順序で実行する。

---

## Files Changed Summary

| File | Action | Est. Lines | Phase |
|------|--------|-----------|-------|
| `src/glossary.py` | NEW | ~50 | 1 |
| `src/config.py` | MODIFY | +15 | 1 |
| `config.yaml` | MODIFY | +5 | 1 |
| `tests/test_glossary.py` | NEW | ~100 | 1 |
| `tests/test_config.py` | MODIFY | +10 | 1 |
| `tests/test_pipeline.py` | MODIFY | +5 (Phase 1), +15 (Phase 2) | 1, 2 |
| `src/state_store.py` | MODIFY | +20 | 2 |
| `src/pipeline.py` | MODIFY | +8 | 2 |
| `tests/test_state_store.py` | MODIFY | +15 | 2 |
| `bot.py` | MODIFY | +60 | 3 |

**Total**: ~150 lines new + ~130 lines changed + ~100 lines test

---

## Risk Management

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| 意図しない部分置換（短い語が長い語に含まれる） | Low | Low | v2 で `whole_words_only` オプション追加で対応。現状は辞書エントリの選定でユーザーが回避可能 |
| 正規表現インジェクション | Very Low | Medium | `re.escape()` で全メタ文字をエスケープ。テストで検証済み |
| guild_settings.json の肥大化 | Very Low | Low | 100エントリで~5KB。実用的な辞書サイズでは問題なし |
| 既存テスト破損（Config fixture） | Low | Medium | Phase 1.6 で `_make_config()` を先に更新。全パラメータにデフォルト値あり |
| パイプライン処理時間の増加 | Very Low | Low | O(n*m): 200セグメント * 100エントリ = 20,000回の文字列操作。1ms未満 |

---

## Test Plan

| Area | Tests | File | Phase |
|------|-------|------|-------|
| Glossary core (empty, single, multi, case, immutability, regex-escape) | 8+ | `tests/test_glossary.py` | 1 |
| Config loading (default, disabled, YAML) | 2-3 | `tests/test_config.py` | 1 |
| Pipeline fixture (Config not broken) | -- | `tests/test_pipeline.py` | 1 |
| StateStore glossary (get/set/persist/coexist) | 4 | `tests/test_state_store.py` | 2 |
| Pipeline integration (enabled/disabled) | 2 | `tests/test_pipeline.py` | 2 |
| **Total** | **16+** | | |

---

## Rollback Plan

用語辞書機能は全パラメータにデフォルト値を持つため、段階的なロールバックが可能。

### Level 1: ソフト無効化（最小限）

`config.yaml` で `transcript_glossary.enabled: false` を設定し Bot 再起動。辞書データは保持されるが適用されない。

### Level 2: 辞書データ削除

`state/guild_settings.json` から各ギルドの `"glossary"` キーを削除。他のギルド設定（template 等）には影響なし。

### Level 3: 完全ロールバック

```bash
git revert <merge-commit-hash>
# state/guild_settings.json の "glossary" キーは自動的に無視される
# Bot 再起動
```

### 安全性の根拠

- `TranscriptGlossaryConfig.enabled` のデフォルトは `True` だが、辞書が空なら `apply_glossary()` は即座にリターン（no-op）
- 新規パラメータは全て optional with defaults
- `guild_settings.json` に不明なキーがあっても他の機能に影響しない
- 既存の Segment dataclass は変更なし（新インスタンス生成のみ）
