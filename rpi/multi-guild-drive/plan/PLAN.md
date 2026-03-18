# Implementation Roadmap: マルチギルド対応（Google Drive監視 + エラーロール）

**Feature**: multi-guild-drive
**Complexity**: Medium
**Total Phases**: 4
**Total Tasks**: 16
**Estimated Lines**: ~210

---

## Phase 1: Config拡張（GuildDriveConfig + GuildConfigフィールド追加）

**目的**: ギルド別のDrive設定とエラーロールIDをconfigレイヤーで表現可能にする

### タスク

| # | タスク | ファイル | 複雑度 |
|---|--------|---------|--------|
| 1.1 | `GuildDriveConfig` frozen dataclass 追加 | src/config.py | Low |
| 1.2 | `GuildConfig` に `error_mention_role_id` と `google_drive` フィールド追加 | src/config.py | Low |
| 1.3 | `DiscordConfig.resolve_error_role(guild_id)` メソッド追加 | src/config.py | Low |
| 1.4 | `_build_discord_section` でギルド別フィールドをパース | src/config.py | Medium |
| 1.5 | `_validate()` にギルド別Drive設定のバリデーション追加 | src/config.py | Low |
| 1.6 | テスト: パース、フォールバック、後方互換、バリデーション | tests/test_config.py | Medium |

### 成功基準

- [ ] `GuildDriveConfig(enabled=True, folder_id="xxx")` が frozen dataclass として動作
- [ ] `GuildConfig` に新フィールドが追加され、デフォルト値で既存テストが全パス
- [ ] `resolve_error_role()` がギルド別 → グローバルフォールバック動作
- [ ] `_build_discord_section` がギルド別 `error_mention_role_id` と `google_drive` をパース
- [ ] 既存config（新フィールドなし）が変更なしで動作（後方互換）
- [ ] `pytest tests/test_config.py` 全パス

### バリデーション方法

```bash
pytest tests/test_config.py -v
```

---

## Phase 2: pipeline.py エラーロールのパラメータ化

**目的**: `cfg.discord.error_mention_role_id` のハードコード参照をパラメータに置換

### タスク

| # | タスク | ファイル | 複雑度 |
|---|--------|---------|--------|
| 2.1 | `run_pipeline_from_tracks` に `error_mention_role_id` パラメータ追加 | src/pipeline.py | Low |
| 2.2 | `run_pipeline` に `error_mention_role_id` パラメータ追加 | src/pipeline.py | Low |
| 2.3 | 4箇所の `cfg.discord.error_mention_role_id` をパラメータ参照に置換 | src/pipeline.py | Low |
| 2.4 | テスト: パラメータ伝播、既存テスト維持 | tests/test_pipeline.py | Low |

### 成功基準

- [ ] `run_pipeline_from_tracks(error_mention_role_id=...)` で値を受け取り可能
- [ ] `run_pipeline(error_mention_role_id=...)` で値を受け取り可能
- [ ] 4箇所全てでパラメータ値が `post_error` に渡される
- [ ] `error_mention_role_id` 未指定時は `None` （デフォルト）
- [ ] 既存テストが変更なしでパス（デフォルト値の恩恵）
- [ ] `pytest tests/test_pipeline.py` 全パス

### バリデーション方法

```bash
pytest tests/test_pipeline.py -v
```

---

## Phase 3: bot.py マルチギルドDrive + エラーロール解決

**目的**: Drive監視を全ギルドに拡張し、エラーロールをギルド別に解決してパイプラインに渡す

### タスク

| # | タスク | ファイル | 複雑度 |
|---|--------|---------|--------|
| 3.1 | `self.drive_watcher` → `self.drive_watchers: dict[int, DriveWatcher]` 変更 | bot.py | Low |
| 3.2 | `on_ready` Drive初期化をギルドイテレーションに置換 | bot.py | Medium |
| 3.3 | `close()` で全DriveWatcher停止 | bot.py | Low |
| 3.4 | `_launch_pipeline` と Craig検知フローで `error_mention_role_id` を解決して渡す | bot.py | Medium |
| 3.5 | Drive callback closureで `error_mention_role_id` も渡す | bot.py | Low |
| 3.6 | `drive-status` コマンドをギルド別表示に更新 | bot.py | Low |

### 成功基準

- [ ] 複数ギルドそれぞれに DriveWatcher が起動される
- [ ] 各 DriveWatcher のコールバックが正しいギルドの output_channel にバインド
- [ ] ギルド別 error_mention_role_id が解決され pipeline に渡される
- [ ] `close()` が全 DriveWatcher を停止
- [ ] `drive-status` が実行ギルドの状態を表示
- [ ] `pytest` 全テストパス

### バリデーション方法

```bash
pytest -v
```

---

## Phase 4: config.yaml更新 + 全体テスト

**目的**: 設定ファイルにマルチギルドDrive設定のサンプルを追記し、全体テストを実行

### タスク

| # | タスク | ファイル | 複雑度 |
|---|--------|---------|--------|
| 4.1 | config.yaml にギルド別 google_drive と error_mention_role_id のコメント付きサンプル追記 | config.yaml | Low |
| 4.2 | 全テスト実行・確認 | — | Low |

### 成功基準

- [ ] config.yaml にマルチギルドDrive設定のコメント付き例がある
- [ ] `pytest` 全テストパス
- [ ] 既存テスト数が減少していないこと

### バリデーション方法

```bash
pytest -v --tb=short
```

---

## Phase Status

- [x] Phase 1: Config拡張
- [x] Phase 2: Pipeline エラーロール パラメータ化
- [x] Phase 3: bot.py マルチギルドDrive + エラーロール
- [x] Phase 4: config.yaml + 全体テスト

---

## 依存関係

```
Phase 1 (config) ─┬─→ Phase 2 (pipeline)
                   │
                   └─→ Phase 3 (bot.py) ←── Phase 2
                                │
                                └─→ Phase 4 (integration)
```

Phase 1 は Phase 2, 3 の前提。Phase 2 と Phase 3 は Phase 1 完了後に着手可能（Phase 3 は Phase 2 に軽く依存）。Phase 4 は全フェーズ完了後。

---

## 変更ファイル一覧

| ファイル | Phase | 変更種別 | 推定行数 |
|---------|-------|---------|---------|
| src/config.py | 1 | 修正 | ~50 |
| tests/test_config.py | 1 | 修正 | ~70 |
| src/pipeline.py | 2 | 修正 | ~10 |
| tests/test_pipeline.py | 2 | 修正 | ~30 |
| bot.py | 3 | 修正 | ~50 |
| config.yaml | 4 | 修正 | ~10 |
| **合計** | | | **~220** |

## 変更不要ファイル

- `src/drive_watcher.py` — 疎結合設計により変更不要
- `src/poster.py` — `post_error()` は既にパラメータで受け取る
- `src/state_store.py` — ギルド非依存
- `src/detector.py` — 既にマルチギルド対応
- `src/minutes_archive.py` — 既に guild_id インデックス
