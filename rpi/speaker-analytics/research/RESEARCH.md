# Research Report: 話者別発言量・発言時間可視化 (Speaker Analytics)

**Feature Slug**: speaker-analytics
**Date**: 2026-03-17
**Recommendation**: GO
**Confidence**: 95% (High)

---

## Executive Summary

**Recommendation: GO** -- Merge after adding `speaker_analytics:` section to `config.yaml`.

話者ごとの発言時間・文字数を集計し、Discord Embedにテキスト棒グラフ（Unicode `█`/`░`）として表示する機能は、現在の作業ツリー（unstaged changes）に **~95% 実装済み** であることが確認された。`src/speaker_analytics.py` に `SpeakerStats` dataclass・集計関数・フォーマット関数（106行）、`pipeline.py` に条件付き呼び出し、`poster.py` に `"📊 話者統計"` Embedフィールド、`config.py` に `SpeakerAnalyticsConfig(enabled=True)` frozen dataclass がそれぞれ実装されている。13 + 2 + 2 = 17テスト全数パスを確認。既存機能への後方互換性に問題なし。マージに必要な唯一の条件は `config.yaml` への `speaker_analytics:` セクション追加（2行）であり、本レポートは merge readiness assessment として **GO** を推奨する。

---

## 1. Feature Overview

| 項目 | 値 |
|------|-----|
| **Feature Name** | 話者別発言量・発言時間可視化 (Speaker Analytics) |
| **Type** | Enhancement（パイプライン上のアナリティクスレイヤー追加） |
| **Target Components** | `src/speaker_analytics.py` (new), `src/pipeline.py`, `src/poster.py`, `src/config.py` |
| **Complexity** | Medium (Size M) -- ただし ~95% 実装済み |
| **Traceability** | R-83 |
| **Implementation Order** | Ext-3 |

### Goals

1. 話者ごとの発言時間（秒）を Segment の start/end から集計する
2. 話者ごとの発言文字数を Segment の text から集計する
3. Discord Embed に Unicode テキスト棒グラフとして可視化する（`"📊 話者統計"` フィールド）
4. `config.yaml` で有効/無効を制御可能にする
5. パイプラインのパフォーマンスに影響を与えない（O(N) 軽量処理）

---

## 2. Requirements Summary

### Must-Have [R-83] (ALL IMPLEMENTED)

1. **話者ごとの発言時間集計** -- `Segment.start` / `Segment.end` から talk_time_sec を算出
2. **話者ごとの発言文字数集計** -- `Segment.text` の `len()` から char_count を算出
3. **Discord Embed テキスト棒グラフ** -- Unicode ブロック文字 (`█` `░`) による相対的な発言量の可視化

### Nice-to-Have [R-83] (DEFERRED)

4. 議事録 Markdown ファイルにも統計テーブルを追加
5. 発言割合（%）表示

### Non-Functional

- O(N) 集計処理 -- パイプライン SLA への影響なし
- 外部ライブラリ追加不要（標準ライブラリのみ使用: `collections.defaultdict`, `dataclasses`）
- 新規永続化データなし（毎回新規計算）
- `config.yaml` の `speaker_analytics.enabled: false` で完全にバイパス可能

---

## 3. Product Analysis

### User Value: **Medium-High**

| 観点 | 評価 |
|------|------|
| **課題の深刻度** | 中。会議のファシリテーターが参加者の発言バランスを客観的に把握する手段がない |
| **影響範囲** | 全議事録利用者。特に定例会議でのバランス改善に有用 |
| **ユーザー体験** | 視覚的な棒グラフが議事録 Embed の知覚価値を大幅に向上。ゼロクリックで情報が得られる |
| **即効性** | 設定変更なしで次回の議事録生成から自動表示（`enabled: true` がデフォルト） |

### Market Fit

議事録・会議分析ツール市場において、話者別統計は標準的な機能。Otter.ai、Fireflies.ai、Notta 等の商用ツールは参加者別の発言時間表示を基本機能として提供しており、Discord Bot での実装は競争力の維持に必要。

### Strategic Alignment: **Full**

| 設計原則 | 適合性 | 根拠 |
|----------|--------|------|
| Pipeline-first | ✅ | 純関数として transcribe/merge ステージ間に挿入。パイプラインの依存関係に影響なし |
| Async by default | ✅ | 同期 O(N) 集計を非同期パイプライン内で実行。sub-millisecond で完了するためブロッキングなし |
| Graceful degradation | ✅ | `enabled: false` で完全バイパス。空の stats → Embed フィールド省略。全パスで安全 |
| Multi-guild support | ✅ | グローバル設定（全ギルド共通）。ギルド別カスタマイズの需要が出た場合は将来対応 |
| Minimal state | ✅ | 永続化データなし。毎回 Segment リストから新規計算 |

### Product Viability Score: **8/10 -- STRONG GO**

低工数・高可視性の ROI が良い機能。外部 API 呼び出し・新規依存なしでユーザー価値を追加できる。

### Concerns

1. **Segment overlap による集計精度** -- Craig の per-track 録音では、話者時間の合計が会議の実時間を超えることがある。ただし棒グラフは相対比率を表示するため、ユーザーへの実害はなし

---

## 4. Technical Discovery

### Current State: ~95% Implemented

全コアインフラストラクチャがワーキングツリー（unstaged changes）に実装済みであることを確認した。

#### `src/speaker_analytics.py` (106 lines, new file)

| Component | Status | Detail |
|-----------|--------|--------|
| `SpeakerStats` dataclass | ✅ Implemented | `speaker`, `talk_time_sec`, `char_count`, `segment_count` -- frozen dataclass |
| `calculate_speaker_stats()` | ✅ Implemented | O(N) 集計、`talk_time_sec` 降順ソート。空入力 → 空リスト |
| `_format_time()` | ✅ Implemented | 秒数を `M:SS` 形式にフォーマット |
| `format_stats_embed()` | ✅ Implemented | Unicode 棒グラフ (`█`/`░`)、8文字名前切り詰め（7 + `…`）、再帰的 `bar_width` 縮小、`max_speakers=10`、`"他N人"` overflow 表記 |

#### `src/pipeline.py` (lines 84-91)

| Component | Status | Detail |
|-----------|--------|--------|
| Analytics call | ✅ Implemented | `_stage_transcribe` と `merge_transcripts` の間に挿入 |
| Conditional on config | ✅ Implemented | `if cfg.speaker_analytics.enabled` でガード |
| Conditional import | ✅ Implemented | `from src.speaker_analytics import ...` -- 有効時のみインポート |
| Pass to poster | ✅ Implemented | `speaker_stats=speaker_stats_text` として `post_minutes()` に伝播 |

#### `src/poster.py` (lines 51-98, 190-197)

| Component | Status | Detail |
|-----------|--------|--------|
| `build_minutes_embed()` | ✅ Implemented | `speaker_stats: str \| None = None` -- optional パラメータ |
| `"📊 話者統計"` field | ✅ Implemented | `embed.add_field(name="📊 話者統計", value=..., inline=False)` -- decisions の後、footer の前に配置 |
| Truncation | ✅ Implemented | `_truncate(speaker_stats, 1024)` -- Discord Embed field 上限への対応 |
| `post_minutes()` | ✅ Implemented | `speaker_stats: str \| None = None` パラメータを `build_minutes_embed` に伝播 |

#### `src/config.py` (lines 136-138, 157, 166-177)

| Component | Status | Detail |
|-----------|--------|--------|
| `SpeakerAnalyticsConfig` | ✅ Implemented | `enabled: bool = True` -- frozen dataclass。デフォルト有効 |
| `Config` integration | ✅ Implemented | `speaker_analytics: SpeakerAnalyticsConfig` フィールド |
| `_SECTION_CLASSES` | ✅ Implemented | `"speaker_analytics": SpeakerAnalyticsConfig` として YAML ローダーに登録 |

#### Segment dataclass (`src/transcriber.py`, lines 19-26)

```python
@dataclass(frozen=True)
class Segment:
    start: float   # 開始時間（秒）
    end: float     # 終了時間（秒）
    text: str      # 文字起こしテキスト
    speaker: str   # 話者名
```

必要なフィールドすべて存在。変更不要。

#### Tests (17 total, all passing)

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_speaker_analytics.py` | 13 | `calculate_speaker_stats` (5): empty, single, multi-sorted, char_count, segment_count / `format_stats_embed` (8): empty, basic, single-speaker-full-bar, max_speakers, max_chars, long-name, comma, zero-time |
| `tests/test_poster.py` | 2 | `test_embed_with_speaker_stats`, `test_embed_without_speaker_stats` |
| `tests/test_pipeline.py` | 2 | `test_speaker_stats_passed_to_post_minutes`, `test_speaker_stats_disabled` |

全テストスイート: 248 passed / 2 failed (GPU-specific, pre-existing, unrelated)。

### Integration Points

```
src/transcriber.py (Segment[])
  | (pre-merge raw segments)
  v
src/pipeline.py (calculate_speaker_stats -> format_stats_embed)
  | (speaker_stats_text: str | None)
  v
src/poster.py (build_minutes_embed -> "📊 話者統計" field)

src/config.py (SpeakerAnalyticsConfig.enabled)
  |
  v
src/pipeline.py (if cfg.speaker_analytics.enabled)
```

**Key design decision**: Analytics は pre-merge segments を使用する（post-merge ではない）。これは merge が `gap_merge_threshold_sec` 以下のギャップを結合して `end` を実際の発話境界を超えて延長するため、pre-merge の方が正確な talk_time を算出できるため。

### Reusable Components

- `Segment` dataclass -- そのまま利用（start/end/speaker/text）
- Config loading pattern -- `_SECTION_CLASSES` への登録のみで YAML ローダーが自動処理
- Embed field addition pattern -- `build_minutes_embed()` の既存パターンに準拠
- テストヘルパー -- `_seg()` helper function for Segment creation

### Code Conflicts: None

- 既存関数シグネチャに optional パラメータ追加のみ
- 新規モジュール `src/speaker_analytics.py` で集計ロジックを分離
- 既存テストへの breaking change なし

### Remaining Gaps (2 items)

| # | Gap | Type | Effort | Detail |
|---|-----|------|--------|--------|
| G1 | `config.yaml` に `speaker_analytics:` セクションがない | Documentation/Discoverability | 2 lines | ユーザーが無効化オプションを発見できない。デフォルト `enabled: true` は config.py で定義済みのため動作には影響しないが、YAML に明示することで可視性を確保する |
| G2 | `tests/test_config.py` に `SpeakerAnalyticsConfig` ローディングテストがない | Test coverage | ~15 lines | YAML から `SpeakerAnalyticsConfig` を正しくロードできることの検証が欠如 |

---

## 5. Technical Analysis

### Feasibility Score: **9/10**

全コードが実装済み・テスト済み。2 件の軽微な polish 項目のみ残存。

### What is Already Implemented

機能パイプライン全体が動作可能:

1. `SpeakerStats` dataclass with aggregation fields
2. O(N) aggregation sorted by talk_time descending
3. Unicode bar graph formatting with recursive width reduction
4. 8-char name truncation with ellipsis
5. `max_speakers=10` with overflow indicator (`"他N人"`)
6. Config-driven enable/disable
7. Conditional import in pipeline (module cached after first import)
8. Embed field with 1024-char truncation safety
9. 17 unit tests covering core logic, integration, and edge cases

### Key Technical Decisions (Verified Correct)

1. **Pre-merge segments**: Analytics は `_stage_transcribe` の直後、`merge_transcripts` の直前で実行。merge は `gap_merge_threshold_sec` 以下のギャップを結合して `end` を延長するため、pre-merge の raw segments の方が正確な talk_time を算出できる

2. **1024-char limit handling**: 3層の防御:
   - (a) `format_stats_embed()` の再帰的 `bar_width` 縮小（10 → 8 → 6 → 4 まで）
   - (b) `format_stats_embed()` の最終 `result[:max_chars]` ハード切り詰め
   - (c) `poster.py` の `_truncate(speaker_stats, 1024)` -- 最終安全弁

3. **Edge cases**:
   - 空 segments → 空リスト → 空文字列 → Embed フィールド省略
   - 1話者 → 全ブロック filled (`█` * bar_width)
   - 長い名前 → 7文字 + `…`（例: `verylon…`）
   - talk_time ゼロ → `max_time` を 1.0 にクランプ（ゼロ除算回避）

4. **Conditional import**: `from src.speaker_analytics import ...` を `if cfg.speaker_analytics.enabled` ブロック内で実行。標準的なパターンで、モジュールは初回インポート後キャッシュされる

5. **スレッドセーフティ**: 純関数 + immutable データ (frozen dataclass)。sub-millisecond で完了するため threading の懸念なし

### Complexity: **Medium (already absorbed)**

すべての複雑性はワーキングツリーの実装で吸収済み。残存作業は trivial。

| Component | Lines Changed | Lines Added | Risk |
|-----------|---------------|-------------|------|
| `src/speaker_analytics.py` | -- | +106 (new file) | None |
| `src/pipeline.py` | ~7 | +7 (conditional analytics call) | None |
| `src/poster.py` | ~10 | +10 (speaker_stats parameter + field) | None |
| `src/config.py` | ~5 | +5 (SpeakerAnalyticsConfig + registration) | None |
| `tests/test_speaker_analytics.py` | -- | +138 (new file, 13 tests) | None |
| `tests/test_poster.py` | -- | +10 (2 tests) | None |
| `tests/test_pipeline.py` | -- | +35 (2 tests) | None |
| **Total** | **~22** | **~311** | **None** |

### Technical Risks

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Segment overlap → talk_time > 実会議時間 | Low | Medium | Craig の per-track 録音に内在する制限。棒グラフは相対比率を表示するため、絶対値の不正確さはユーザーに影響しない |
| Embed total length exceeded (4000 chars) | Low | Very Low | 統計フィールドは ~200-400 chars を追加。既存の `max_embed_length` 超過時の summary 切り詰めロジックが対応。poster.py の pre-existing issue |
| `config.yaml` に `speaker_analytics:` セクションがない | Low | High | ユーザーが無効化オプションを発見できない。2行の YAML 追加で解決 (Condition C1) |
| 将来の Segment schema 変更 | Low | Low | frozen dataclass のため、フィールド変更はテストで即座に検出される |
| Unicode 棒グラフの表示差異 | Very Low | Very Low | `█` (`U+2588`) と `░` (`U+2591`) は Discord の全プラットフォーム（デスクトップ、モバイル、Web）で広くサポート済み |

### Alternatives Considered

| Approach | Assessment | Adopted |
|----------|-----------|---------|
| 独立モジュール `speaker_analytics.py` + pipeline injection | シンプル、テスト容易、分離明確 | ✅ Recommended |
| merger.py 内に集計ロジック統合 | 責務混在（merge + analytics）。SRP 違反 | ❌ |
| Post-merge transcript からの再解析 | テキスト解析が不正確（タイムスタンプ文字列の再パース必要）。Segment 直接利用の方が正確 | ❌ |
| Matplotlib/PIL による画像グラフ生成 | 外部依存追加。Docker イメージ肥大化。Discord Embed のテキストフィールドで十分 | ❌ (future) |

---

## 6. Strategic Recommendation

### Decision: **GO** -- Merge after adding config.yaml section

### Confidence: **95% (High)**

### Rationale

| Factor | Assessment |
|--------|-----------|
| **Implementation status** | ~95% complete in working tree. This is a merge readiness assessment, not a build decision |
| **Test coverage** | 17 tests across 3 files. All passing. Core logic, integration, and edge cases covered |
| **Risk of proceeding** | Very Low -- 2 lines of YAML addition is the only required change |
| **Risk of NOT proceeding** | Low -- feature is implemented but not shipped, accumulating merge debt with other in-flight changes |
| **Technical feasibility** | 9/10 -- all components verified, patterns established, zero architectural changes needed |
| **Product value** | Medium-High -- actionable participation insights at zero incremental cost (no API calls, no dependencies) |
| **Strategic alignment** | Full alignment with all 5 design principles |
| **Backward compatibility** | Perfect -- `SpeakerAnalyticsConfig(enabled=True)` is the default, existing deployments automatically gain the feature |
| **Breaking changes** | Zero. All new parameters are optional with sensible defaults |

### Why This Should Ship Now

1. **Implementation is complete** -- delaying merge increases the risk of code drift and merge conflicts with other features in development (template-customization, minutes-archive)
2. **Zero configuration required** -- default `enabled: true` means existing deployments gain speaker statistics automatically
3. **Zero cost** -- no API calls, no external dependencies, no persistent state. Sub-millisecond O(N) computation
4. **Visual value** -- Unicode bar graph in Embed significantly enhances the perceived quality of the bot's output
5. **Clean disable path** -- `speaker_analytics.enabled: false` in config.yaml completely bypasses the feature

---

## 7. Conditions and Follow-ups

### Conditions (before merge)

| # | Condition | Type | Effort | Rationale |
|---|-----------|------|--------|-----------|
| C1 | Add `speaker_analytics:` section to `config.yaml` with `enabled: true` and comment | Required | 2 lines | Discoverability: users need to see the option exists to disable it. Without this, the feature is invisible in config |
| C2 | Add `SpeakerAnalyticsConfig` YAML loading test in `tests/test_config.py` | Recommended | ~15 lines | Ensures config.yaml `speaker_analytics:` section is correctly parsed into `SpeakerAnalyticsConfig`. Standard test coverage gap |

### Condition C1 Detail

```yaml
speaker_analytics:
  # Enable per-speaker talk time and character count display in minutes embed
  enabled: true
```

### Condition C2 Detail

Test that `config.yaml` with `speaker_analytics: { enabled: false }` produces `SpeakerAnalyticsConfig(enabled=False)`, and that missing section produces default `SpeakerAnalyticsConfig(enabled=True)`.

### Follow-ups (after merge, separate PRs)

| # | Follow-up | Priority | Effort | Description |
|---|-----------|----------|--------|-------------|
| F1 | Percentage display in bar graph | P3 (Low) | ~10 lines | Add `42%` suffix to each bar line. Requires minor `format_stats_embed` modification |
| F2 | Markdown stats table in minutes file | P4 (Backlog) | ~30 lines | Add tabular stats to the .md attachment. New `format_stats_markdown()` function |
| F3 | Per-guild enable/disable | P4 (Backlog) | ~20 lines | `state_store.get_guild_setting()` pattern (same as template-customization) |

---

## 8. Next Steps

Based on the GO recommendation:

1. **Apply Condition C1**: Add `speaker_analytics:` section to `config.yaml`
2. **Apply Condition C2**: Add `SpeakerAnalyticsConfig` loading test in `tests/test_config.py`
3. **Run full test suite**: `pytest` -- confirm 248+ tests pass
4. **Commit and create PR**: Stage all speaker-analytics changes for review
5. **Post-merge**: Track F1-F3 follow-ups in separate issues

---

## Appendix: Code References

| File | Content | Status |
|------|---------|--------|
| `src/speaker_analytics.py:11-18` | `SpeakerStats` dataclass (speaker, talk_time_sec, char_count, segment_count) | Implemented |
| `src/speaker_analytics.py:21-51` | `calculate_speaker_stats()` -- O(N) aggregation, sorted by talk_time desc | Implemented |
| `src/speaker_analytics.py:54-58` | `_format_time()` -- seconds to M:SS | Implemented |
| `src/speaker_analytics.py:61-106` | `format_stats_embed()` -- Unicode bar graph with recursive width reduction | Implemented |
| `src/pipeline.py:84-91` | Conditional analytics call between transcribe and merge | Implemented |
| `src/poster.py:51-57` | `build_minutes_embed()` with `speaker_stats: str \| None` parameter | Implemented |
| `src/poster.py:92-98` | `"📊 話者統計"` Embed field addition | Implemented |
| `src/poster.py:190-197` | `post_minutes()` with `speaker_stats` parameter | Implemented |
| `src/config.py:136-138` | `SpeakerAnalyticsConfig(enabled=True)` frozen dataclass | Implemented |
| `src/config.py:157` | `Config.speaker_analytics` field | Implemented |
| `src/config.py:175` | `_SECTION_CLASSES` registration | Implemented |
| `src/transcriber.py:19-26` | `Segment` dataclass (start, end, text, speaker) | Existing |
| `tests/test_speaker_analytics.py` | 13 unit tests (5 calculate + 8 format) | Implemented |
| `tests/test_poster.py:135-145` | 2 embed integration tests (with/without stats) | Implemented |
| `tests/test_pipeline.py:452-524` | 2 pipeline flow tests (enabled/disabled) | Implemented |
| `config.yaml` | Missing `speaker_analytics:` section | Gap (C1) |
| `tests/test_config.py` | Missing `SpeakerAnalyticsConfig` loading test | Gap (C2) |
