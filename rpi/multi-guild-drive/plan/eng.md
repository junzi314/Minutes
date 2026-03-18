# Technical Specification: マルチギルド対応（Google Drive監視 + エラーロール）

## アーキテクチャ概要

既存のマルチギルドアーキテクチャ（`GuildConfig` + `get_guild()` O(1)ルックアップ）を拡張し、Drive監視とエラーロールをギルド別に解決する。DriveWatcher自体は変更不要（疎結合設計の恩恵）。

## データモデル変更

### 新規: `GuildDriveConfig` dataclass

```python
@dataclass(frozen=True)
class GuildDriveConfig:
    """Per-guild Google Drive overrides (folder_id + enabled)."""
    enabled: bool = True
    folder_id: str = ""
```

### 変更: `GuildConfig` dataclass（config.py:40-47）

```python
@dataclass(frozen=True)
class GuildConfig:
    guild_id: int
    watch_channel_id: int
    output_channel_id: int
    template: str = "minutes"
    error_mention_role_id: int | None = None       # NEW
    google_drive: GuildDriveConfig | None = None    # NEW
```

### 変更なし
- `GoogleDriveConfig` — グローバル設定（credentials_path, file_pattern, poll_interval_sec）として維持
- `DiscordConfig` — `error_mention_role_id` をグローバルフォールバックとして維持

## 解決ロジック

### error_mention_role_id の解決

```python
def resolve_error_role(self, guild_id: int) -> int | None:
    """Guild-level → global fallback."""
    guild_cfg = self.get_guild(guild_id)
    if guild_cfg and guild_cfg.error_mention_role_id is not None:
        return guild_cfg.error_mention_role_id
    return self.error_mention_role_id  # global fallback
```

このメソッドを `DiscordConfig` に追加する。

### Drive設定の解決

bot.py の on_ready で実行:

```python
for gcfg in self.cfg.discord.guilds:
    # Per-guild Drive config → global fallback
    guild_drive = gcfg.google_drive
    if guild_drive is not None:
        enabled = guild_drive.enabled
        folder_id = guild_drive.folder_id or self.cfg.google_drive.folder_id
    else:
        enabled = self.cfg.google_drive.enabled
        folder_id = self.cfg.google_drive.folder_id

    if not enabled or not folder_id:
        continue

    # Create DriveWatcher with guild-specific callback
    ...
```

## bot.py 変更

### 属性変更

```python
# Before
self.drive_watcher: DriveWatcher | None = None

# After
self.drive_watchers: dict[int, DriveWatcher] = {}
```

### on_ready: マルチギルドDrive初期化

現在の `guilds[0]` ハードコードを、全ギルドをイテレートするループに置換:

```python
if self.cfg.google_drive.enabled or any(
    g.google_drive and g.google_drive.enabled
    for g in self.cfg.discord.guilds
):
    for gcfg in self.cfg.discord.guilds:
        # Resolve per-guild Drive config
        # Create callback closure binding gcfg
        # Create DriveWatcher, add to self.drive_watchers
        ...
```

### close() 更新

```python
for watcher in self.drive_watchers.values():
    watcher.stop()
```

### drive-status コマンド更新

実行ギルドの DriveWatcher 状態を表示。マルチギルド時は全体概要も追加。

### _launch_pipeline / on_raw_message_update 更新

`error_mention_role_id` を解決してパイプラインに渡す:

```python
error_role = self.cfg.discord.resolve_error_role(recording.guild_id)
```

## pipeline.py 変更

### パラメータ追加

`run_pipeline_from_tracks` と `run_pipeline` に `error_mention_role_id: int | None = None` パラメータを追加。

### 4箇所の置換

| 行 | Before | After |
|----|--------|-------|
| 213 | `cfg.discord.error_mention_role_id` | `error_mention_role_id` |
| 233 | `cfg.discord.error_mention_role_id` | `error_mention_role_id` |
| 303 | `cfg.discord.error_mention_role_id` | `error_mention_role_id` |
| 317 | `cfg.discord.error_mention_role_id` | `error_mention_role_id` |

## config.yaml スキーマ拡張

```yaml
discord:
  error_mention_role_id: null  # グローバルフォールバック（既存）
  guilds:
    - guild_id: 111111111111
      watch_channel_id: 222222222222
      output_channel_id: 333333333333
      error_mention_role_id: 444444444444  # ギルド固有（NEW）
      google_drive:                         # ギルド固有（NEW）
        enabled: true
        folder_id: "drive_folder_id_for_guild_A"
    - guild_id: 555555555555
      watch_channel_id: 666666666666
      output_channel_id: 777777777777
      # error_mention_role_id 省略 → グローバルフォールバック
      # google_drive 省略 → グローバル google_drive 設定を使用
```

## _build_discord_section パース拡張

```python
# 既存フィールドに加えて:
error_mention_role_id=entry.get("error_mention_role_id"),

# google_drive サブセクション:
gd_raw = entry.get("google_drive")
guild_drive = None
if isinstance(gd_raw, dict):
    guild_drive = GuildDriveConfig(
        enabled=gd_raw.get("enabled", True),
        folder_id=gd_raw.get("folder_id", ""),
    )
```

## バリデーション追加

`_validate()` で:
- ギルド別 `google_drive.enabled=True` かつ `folder_id` 空で、グローバル `folder_id` も空 → エラー

## テスト戦略

### test_config.py 追加テスト

1. **ギルド別error_mention_role_id パース**: YAML → GuildConfig.error_mention_role_id
2. **ギルド別google_drive パース**: YAML → GuildConfig.google_drive (GuildDriveConfig)
3. **resolve_error_role フォールバック**: guild設定あり → guild値、なし → global値、両方なし → None
4. **後方互換**: 新フィールドなしの既存config → 既存動作維持
5. **バリデーション**: guild Drive enabled + 空folder_id → エラー

### test_pipeline.py 追加テスト

1. **error_mention_role_id パラメータ伝播**: パラメータが post_error に正しく渡される
2. **error_mention_role_id デフォルト（None）**: 既存テストが壊れないこと

## リスク

| リスク | 確率 | 影響 | 対策 |
|--------|------|------|------|
| Config後方互換の破損 | 低 | 高 | 新フィールドは全てオプショナル。既存テスト全パス維持 |
| 複数DriveWatcherのAPI quota | 中 | 低 | 2-3ギルドでは無視可能 |
| Frozen dataclassのネスト | 低 | 低 | GuildDriveConfigはフラット（2フィールドのみ） |
| load()のtoken再構築でフィールド漏れ | 低 | 高 | DiscordConfig再構築時に新フィールドを維持するテスト追加 |

## 変更不要ファイル

- `src/drive_watcher.py` — 既に `GoogleDriveConfig` + callback で疎結合
- `src/poster.py` — `post_error()` は既にパラメータで `error_mention_role_id` を受け取る
- `src/state_store.py` — ギルド非依存（rec_id ベースの重複排除）
- `src/detector.py` — Craig検知は既にマルチギルド対応
- `src/minutes_archive.py` — 既に `guild_id` インデックス
