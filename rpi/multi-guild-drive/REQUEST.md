# Feature Request: マルチギルド対応（Google Drive監視 + エラーロール）

## 概要

複数のDiscordサーバー（ギルド）でBotを運用する際、Google Drive監視とエラー通知ロールがギルドごとに機能するようにする。

## 背景

現在のBot設計では `config.yaml` の `discord.guilds[]` で複数ギルドの定義は可能だが、以下の制約がある:

1. **Google Drive監視**: `bot.py` の `on_ready()` で `guilds[0]`（最初のギルド）の出力チャンネルにのみ紐づけられている。2つ目以降のギルドではDrive経由の議事録が投稿されない。
2. **エラー通知ロール**: `discord.error_mention_role_id` がグローバル設定のため、全ギルドで同じロールIDが使われる。ロールIDはギルド固有なので、別サーバーではメンションが機能しない。

## 要件

### 必須（Must）

1. **ギルド別Google Drive監視**: 各ギルドに固有のDriveフォルダIDを設定でき、検出されたファイルがそのギルドの出力チャンネルに投稿される
2. **ギルド別エラーロール**: `error_mention_role_id` をギルドごとに設定可能にする
3. **後方互換**: 既存の単一ギルド設定（`google_drive` グローバル設定）が引き続き動作する

### 任意（Nice-to-have）

4. ギルドごとにDrive監視の有効/無効を切り替え可能
5. グローバル `error_mention_role_id` をフォールバックとして維持

## 設定イメージ

```yaml
discord:
  error_mention_role_id: 0  # グローバルフォールバック（任意）
  guilds:
    - guild_id: 111111111111
      watch_channel_id: 222222222222
      output_channel_id: 333333333333
      error_mention_role_id: 444444444444  # ギルド固有
      google_drive:
        enabled: true
        folder_id: "drive_folder_id_for_guild_A"
    - guild_id: 555555555555
      watch_channel_id: 666666666666
      output_channel_id: 777777777777
      error_mention_role_id: 888888888888
      google_drive:
        enabled: false  # このギルドではDrive監視しない
```

## 影響範囲

- `src/config.py`: `GuildConfig` にフィールド追加
- `bot.py`: Drive監視の初期化ロジック変更、エラーロール参照変更
- `src/pipeline.py`: エラーロールIDの取得元変更
- `config.yaml`: スキーマ拡張
- テスト: Config / Pipeline テスト更新

## 制約

- Craig検知フローは既にマルチギルド対応済み（変更不要）
- スラッシュコマンドは既にギルドスコープ済み（変更不要）
- StateStore / MinutesArchive は既にギルド対応済み（変更不要）
