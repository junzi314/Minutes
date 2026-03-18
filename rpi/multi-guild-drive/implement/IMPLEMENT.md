# Implementation Record

**Feature**: multi-guild-drive
**Started**: 2026-03-18
**Status**: COMPLETED

---

## Phase 1: Config拡張

**Date**: 2026-03-18
**Verdict**: PASS

### Deliverables
- [x] `GuildDriveConfig` frozen dataclass (enabled + folder_id)
- [x] `GuildConfig` に `error_mention_role_id` と `google_drive` フィールド追加
- [x] `DiscordConfig.resolve_error_role(guild_id)` メソッド追加
- [x] `_build_discord_section` でギルド別フィールドをパース
- [x] `_validate()` にギルド別Drive設定のバリデーション追加
- [x] テスト: 11件追加（パース、フォールバック、後方互換、バリデーション）

### Files Changed
| File | Lines |
|------|-------|
| src/config.py | +45 |
| tests/test_config.py | +95 |

### Test Results
34/34 passed

---

## Phase 2: Pipeline エラーロール パラメータ化

**Date**: 2026-03-18
**Verdict**: PASS

### Deliverables
- [x] `run_pipeline_from_tracks` に `error_mention_role_id` パラメータ追加
- [x] `run_pipeline` に `error_mention_role_id` パラメータ追加
- [x] 4箇所の `cfg.discord.error_mention_role_id` をパラメータ参照に置換
- [x] `run_pipeline` → `run_pipeline_from_tracks` へのパラメータ転送
- [x] テスト: 2件追加（パラメータ伝播、デフォルトNone）

### Files Changed
| File | Lines |
|------|-------|
| src/pipeline.py | +6 -4 |
| tests/test_pipeline.py | +50 |

### Test Results
16/16 passed

---

## Phase 3: bot.py マルチギルドDrive + エラーロール

**Date**: 2026-03-18
**Verdict**: PASS

### Deliverables
- [x] `self.drive_watcher` → `self.drive_watchers: dict[int, DriveWatcher]`
- [x] `_start_drive_watchers()` メソッド追加（on_readyから呼び出し）
- [x] `close()` で全DriveWatcher停止
- [x] `_launch_pipeline` で `error_mention_role_id` を解決して渡す
- [x] Drive callback closureで `error_mention_role_id` も渡す
- [x] `drive-status` コマンドをギルド別表示に更新

### Files Changed
| File | Lines |
|------|-------|
| bot.py | +75 -30 |

### Test Results
274/276 passed (2 pre-existing GPU failures unrelated)

---

## Phase 4: config.yaml + 全体テスト

**Date**: 2026-03-18
**Verdict**: PASS

### Deliverables
- [x] config.yaml にギルド別 google_drive / error_mention_role_id コメント付きサンプル追記

### Files Changed
| File | Lines |
|------|-------|
| config.yaml | +6 |

### Test Results
274/276 passed (2 pre-existing GPU failures)

---

## Summary

**Phases Completed**: 4 of 4
**Final Status**: COMPLETED
**Total Tests Added**: 13
**Total Tests**: 276 (274 pass, 2 pre-existing GPU failures)
