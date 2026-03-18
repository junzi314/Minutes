# Research Report: multi-guild-drive

**Feature**: マルチギルド対応（Google Drive監視 + エラーロール）
**Date**: 2026-03-18
**Recommendation**: **GO** (Confidence: High)

---

## Executive Summary

本機能は新機能ではなく、既存のマルチギルドアーキテクチャの**修正完了**である。Botは既に `discord.guilds[]` で複数ギルドをサポートしているが、Google Drive監視が `guilds[0]` にハードコードされ、エラーロールがグローバルであるため、2つ目以降のギルドではDrive経由の議事録投稿とエラー通知が正しく機能しない。変更範囲は限定的（config拡張 + bot.pyオーケストレーション + pipeline.pyの4箇所）で、既存パターンに沿った実装が可能。

---

## Feature Overview

| 項目 | 値 |
|------|-----|
| Feature Name | マルチギルド対応（Drive + Error Role） |
| Type | Enhancement（アーキテクチャ修正） |
| Target Components | config.py, bot.py, pipeline.py |
| Complexity | Medium |
| Estimated Effort | 1-2日 |

---

## Requirements Summary

### Must Have
1. **ギルド別Google Drive監視**: 各ギルドに固有の `folder_id` を設定、検出ファイルをそのギルドの出力チャンネルに投稿
2. **ギルド別エラーロール**: `error_mention_role_id` をギルドごとに設定可能
3. **後方互換**: 既存の単一ギルド設定が変更なしで動作

### Nice to Have
4. ギルドごとのDrive監視有効/無効切り替え
5. グローバル `error_mention_role_id` をフォールバックとして維持

### Non-Functional
- 複数 `DriveWatcher` インスタンスの並行動作（asyncio task）
- Google credentials は全ギルド共有（変更不要）
- 既存テスト全パス維持

---

## Product Analysis

**Product Viability: HIGH**

- **正当性**: 新機能ではなく、既にコミットされたマルチギルドアーキテクチャの完成
- **影響**: Drive監視が `guilds[0]` にハードコードされており、2つ目以降のギルドでは**サイレントに失敗**（ログもエラーもなし）
- **優先度**: P1 — 正確性の修正は機能追加に優先する
- **ユーザー影響**: 単一ギルド運用者には変更なし。マルチギルド運用者のブロッカーを解消

---

## Technical Discovery

### 現在のマルチギルド対応状況

| サブシステム | 対応状況 |
|-------------|---------|
| Craig検知 (`on_raw_message_update`) | ✅ ギルド別 |
| スラッシュコマンド | ✅ `interaction.guild_id` |
| テンプレート選択 | ✅ StateStore per-guild |
| 用語集 (glossary) | ✅ StateStore per-guild |
| MinutesArchive | ✅ `guild_id` インデックス |
| **Google Drive監視** | **❌ `guilds[0]` ハードコード** |
| **エラーロール** | **❌ グローバル設定** |

### 主要なコード箇所

| 箇所 | ファイル | 行 |
|------|---------|-----|
| GuildConfig定義 | `src/config.py` | 40-47 |
| DiscordConfig.error_mention_role_id | `src/config.py` | 50-63 |
| GoogleDriveConfig | `src/config.py` | 127-133 |
| `_build_discord_section` (後方互換) | `src/config.py` | 250-306 |
| on_ready Drive初期化 (`guilds[0]`) | `bot.py` | 183 |
| _on_drive_tracks コールバック | `bot.py` | 193-209 |
| DriveWatcher生成 | `bot.py` | 211-216 |
| error_role_id 参照（4箇所） | `src/pipeline.py` | 213, 233, 303, 317 |

### 変更不要なファイル
- `src/drive_watcher.py` — 既に `GoogleDriveConfig` + callback で疎結合
- `src/poster.py` — `post_error()` は既にパラメータで `error_mention_role_id` を受け取る
- `src/state_store.py` — ギルド非依存（rec_id ベースの重複排除）
- `src/detector.py` — Craig検知は既にマルチギルド対応

---

## Technical Analysis

**Technical Feasibility: HIGH**

### 推奨アプローチ

**Phase 1: Config拡張**
- `GuildConfig` に `error_mention_role_id: int | None = None` と `google_drive` サブ設定を追加
- `_build_discord_section` でパース（既存の後方互換パターンに従う）
- グローバル設定をフォールバックとして維持

**Phase 2: bot.py Drive監視のマルチギルド化**
- `self.drive_watcher` → `self.drive_watchers: dict[int, DriveWatcher]`
- ギルドごとにコールバッククロージャ生成、出力チャンネルを正しくバインド
- `close()` と `drive-status` コマンドを更新

**Phase 3: pipeline.py エラーロール解決**
- 4箇所の `cfg.discord.error_mention_role_id` をギルド別に解決
- 呼び出し元（bot.py）で解決済みの値をパラメータとして渡す

**Phase 4: テスト + config.yaml**
- 後方互換テスト、フォールバックテスト、マルチギルドテスト追加
- config.yaml にコメント付きサンプル追記

### 変更見積もり

| ファイル | 変更種別 | 推定行数 |
|---------|---------|---------|
| `src/config.py` | 修正 | ~50行 |
| `bot.py` | 修正 | ~40行 |
| `src/pipeline.py` | 修正 | ~10行 |
| `config.yaml` | 修正 | ~10行 |
| `tests/test_config.py` | 修正 | ~70行 |
| `tests/test_pipeline.py` | 修正 | ~30行 |
| **合計** | | **~210行** |

### リスク評価

| リスク | 確率 | 影響 | 対策 |
|--------|------|------|------|
| Config後方互換の破損 | 低 | 高 | `_build_discord_section` の既存パターンに従う。新フィールドは全て optional |
| 複数DriveWatcherのAPI quota消費 | 中 | 低 | 2-3ギルドでは無視可能。大規模運用時はドキュメントで注意喚起 |
| 同一folder_idの重複監視 | 低 | 低 | `StateStore.is_known()` で重複排除済み。先着ギルドが処理 |
| Frozen dataclass のネスト複雑化 | 低 | 低 | per-guild Drive設定はフラットに保つ（folder_id + enabled のみ） |

---

## Strategic Recommendation

### Decision: **GO**

**Confidence: HIGH (90%)**

### Rationale

1. **正確性の修正**: 新機能ではなく、既にコミットされたマルチギルドアーキテクチャの完成。現状はサイレント障害。
2. **低リスク・高確実性**: 既存パターン（`_build_discord_section`、`resolve_template`、`get_guild`）に沿った変更。外部依存の追加なし。
3. **限定的な変更範囲**: 6ファイル・約210行。DriveWatcher自体の変更不要（疎結合設計の恩恵）。

### Conditions
- テストスイート全パス維持（現在261テスト）
- 後方互換性は必須（既存config.yamlが変更なしで動作）
- フェーズごとに独立してコミット・テスト可能であること

---

## Next Steps

1. Review this report
2. Proceed to planning: `/rpi:plan "multi-guild-drive"`
