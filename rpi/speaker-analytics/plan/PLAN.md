# Implementation Roadmap: speaker-analytics

**Feature**: 話者別発言量・発言時間可視化
**Complexity**: Simple-Medium
**Phases**: 4
**Total Tasks**: 9
**Status**: ~95% implemented (merge checklist, not build plan)
**Research**: GO at 95% confidence (`rpi/speaker-analytics/research/RESEARCH.md`)

---

## Overview

話者ごとの発言時間・文字数を集計し、Discord EmbedにUnicode棒グラフ（`█`/`░`）として表示する機能。コアモジュール106行、17テスト全数パス。ワーキングツリーに実装済みのため、本ロードマップは **マージに必要な残作業と検証手順** を定義する。

### What is Already Implemented

| Component | File | Status |
|-----------|------|--------|
| `SpeakerStats` dataclass + `calculate_speaker_stats()` | `src/speaker_analytics.py` | Done (106 lines) |
| `format_stats_embed()` Unicode棒グラフ | `src/speaker_analytics.py` | Done |
| `SpeakerAnalyticsConfig(enabled=True)` | `src/config.py` | Done |
| `_SECTION_CLASSES` 登録 | `src/config.py` | Done |
| Pipeline条件付き呼び出し | `src/pipeline.py` (lines 84-91) | Done |
| `build_minutes_embed()` + `post_minutes()` 拡張 | `src/poster.py` | Done |
| Unit tests (13) + Integration tests (4) | `tests/test_speaker_analytics.py`, `test_poster.py`, `test_pipeline.py` | Done (17 total) |

### Remaining Gaps

| # | Gap | Type | Effort | Phase |
|---|-----|------|--------|-------|
| C1 | `config.yaml` に `speaker_analytics:` セクションがない | Required | 2 lines | Phase 1 |
| C2 | `tests/test_config.py` に YAML ローディングテストがない | Recommended | ~15 lines | Phase 2 |

---

## Phase 1: Config Documentation (C1) -- REQUIRED

**Goal**: `config.yaml` に `speaker_analytics:` セクションを追加し、機能の存在をユーザーに発見可能にする

### Tasks

| # | タスク | ファイル | 複雑度 |
|---|--------|---------|--------|
| 1.1 | `speaker_analytics:` セクション追加（`enabled: true` + コメント） | `config.yaml` | Low |
| 1.2 | `enabled: true` の動作確認（既存テストで暗黙に検証済み） | -- | Low |
| 1.3 | `enabled: false` に変更して統計フィールドが省略されることを確認 | -- | Low |

### config.yaml 追加内容

```yaml
speaker_analytics:
  # Enable per-speaker talk time and character count display in minutes embed
  enabled: true
```

**挿入位置**: `poster:` セクションの後、`pipeline:` セクションの前

### 成功基準
- [ ] `config.yaml` に `speaker_analytics:` セクションが存在する
- [ ] Bot が YAML から `enabled: true` を正しく読み込む
- [ ] Bot が YAML から `enabled: false` を正しく読み込む（統計フィールド省略）

### 依存関係
- なし

---

## Phase 2: Test Coverage (C2) -- RECOMMENDED

**Goal**: `SpeakerAnalyticsConfig` の YAML ローディングテストを追加し、Config テストカバレッジのギャップを埋める

### Tasks

| # | タスク | ファイル | 複雑度 |
|---|--------|---------|--------|
| 2.1 | YAML に `speaker_analytics: { enabled: false }` → `cfg.speaker_analytics.enabled == False` を検証するテスト | `tests/test_config.py` | Low |
| 2.2 | YAML に `speaker_analytics:` セクション未記載 → デフォルト `enabled == True` を検証するテスト | `tests/test_config.py` | Low |

### 成功基準
- [ ] `SpeakerAnalyticsConfig` YAML ローディングテストが追加されている
- [ ] デフォルト値テストが追加されている
- [ ] 全テストスイート（250+）がパスする

### 依存関係
- Phase 1 完了が望ましい（テストで `config.yaml` の実際のセクションを参照するため）

---

## Phase 3: Merge & Validation

**Goal**: 全変更をコミットし、PRを作成する

### Tasks

| # | タスク | ファイル | 複雑度 |
|---|--------|---------|--------|
| 3.1 | 全テストスイート実行: `pytest` | -- | Low |
| 3.2 | 全変更ファイルのレビュー | -- | Low |
| 3.3 | speaker-analytics 関連変更をステージ＆コミット | -- | Low |
| 3.4 | PR作成 | -- | Low |

### コミット対象ファイル

| ファイル | 変更種別 |
|---------|---------|
| `src/speaker_analytics.py` | 新規 |
| `src/config.py` | 修正（追加のみ） |
| `src/pipeline.py` | 修正（追加のみ） |
| `src/poster.py` | 修正（optional パラメータ追加） |
| `config.yaml` | 修正（セクション追加） |
| `tests/test_speaker_analytics.py` | 新規 |
| `tests/test_config.py` | 修正（テスト追加） |
| `tests/test_poster.py` | 修正（テスト追加） |
| `tests/test_pipeline.py` | 修正（テスト追加） |

### 成功基準
- [ ] 全テストパス（GPU関連の pre-existing failure を除く）
- [ ] PR に上記全ファイルが含まれる
- [ ] 後方互換（`enabled=true` デフォルト、既存ユーザーのconfig変更不要）
- [ ] breaking change なし

### Validation Gate

```bash
pytest
# 250+ passed (GPU-specific pre-existing failures を除く)
# speaker-analytics 関連 17+ テスト全パス
```

### 依存関係
- Phase 1 完了が必須、Phase 2 完了が望ましい

---

## Phase 4: Post-merge Follow-ups (SEPARATE PRs)

**Goal**: 将来の拡張を個別PRで段階的に追加する

| # | Follow-up | Priority | Effort | Description |
|---|-----------|----------|--------|-------------|
| F1 | 発言割合（%）表示 | P3 (Low) | ~10 lines | 各バー行に `42%` サフィックスを追加。`format_stats_embed` の軽微な修正 |
| F2 | Markdown統計テーブル | P4 (Backlog) | ~30 lines | .md添付ファイルに表形式の統計を追加。新規 `format_stats_markdown()` 関数 |
| F3 | ギルド別 enable/disable | P4 (Backlog) | ~20 lines | `state_store.get_guild_setting()` パターン（template-customization と同一） |

---

## 変更影響範囲

| ファイル | 変更種別 | 影響 |
|---------|---------|------|
| `src/speaker_analytics.py` | 新規 | なし |
| `src/config.py` | 修正（追加のみ） | Config構造拡張 |
| `src/pipeline.py` | 修正（追加のみ） | パイプラインに集計ステップ追加 |
| `src/poster.py` | 修正（optionalパラメータ追加） | Embed構築拡張 |
| `config.yaml` | 修正（セクション追加） | なし |
| `tests/test_speaker_analytics.py` | 新規 | なし |
| `tests/test_config.py` | 修正（テスト追加） | Config fixture更新 |
| `tests/test_poster.py` | 修正（テスト追加） | なし |
| `tests/test_pipeline.py` | 修正（テスト追加） | Config fixture更新 |

---

## リスク管理

| リスク | 確率 | 影響 | 対策 |
|--------|------|------|------|
| Segment overlap → talk_time > 実会議時間 | 中 | 低 | Craig per-track 録音の内在的制限。棒グラフは相対比率表示のため影響なし |
| `config.yaml` に `speaker_analytics:` セクション未追加 | 高 | 低 | Phase 1 (C1) で解決。デフォルト `enabled: true` のため動作自体には影響しないが発見性に問題 |
| Embed total length 超過 (4000 chars) | 極低 | 低 | 統計フィールドは ~200-400 chars。既存 poster.py の summary 切り詰めロジックが対応 |
| Embed field 1024文字制限 | 極低 | 低 | 3層防御: 再帰的 bar_width 縮小 → ハード切り詰め → `_truncate()` 安全弁 |
| 既存テスト破損 | 低 | 中 | 全パラメータ optional。既存テストへの breaking change なし |
| Config fixture 不整合 | 低 | 低 | Phase 2 (C2) で YAML ローディングテスト追加 |

---

## テスト計画

| Area | Tests | File | Status |
|------|-------|------|--------|
| Stats calculation (empty, single, multi, char_count, segment_count) | 5 | `tests/test_speaker_analytics.py` | Pass |
| Bar graph formatting (empty, basic, single, max_speakers, max_chars, long-name, comma, zero-time) | 8 | `tests/test_speaker_analytics.py` | Pass |
| Embed integration (with/without stats) | 2 | `tests/test_poster.py` | Pass |
| Pipeline flow (enabled/disabled) | 2 | `tests/test_pipeline.py` | Pass |
| Config YAML loading (enabled: false, missing section) | 0 | `tests/test_config.py` | TODO (C2) |
| **Total** | **17 + 2 TODO** | | |

---

## ロールバック計画

| レベル | 手順 | 影響 |
|--------|------|------|
| ソフト無効化 | `config.yaml` で `speaker_analytics.enabled: false` を設定 + Bot再起動 | 統計フィールドが非表示になる。他の議事録機能に影響なし |
| 完全ロールバック | コード変更を revert、`src/speaker_analytics.py` を削除 | 機能完全除去。全パラメータ optional のため既存コードは安全 |
