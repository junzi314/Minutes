# UX Design: マルチギルド対応（Google Drive監視 + エラーロール）

## 概要

本機能はバックエンド/設定の修正であり、新しいUIフローは発生しない。変更はconfig.yamlスキーマの拡張とスラッシュコマンド出力の更新のみ。

## ユーザーフロー

### フロー1: config.yaml設定（管理者）

```
管理者がconfig.yamlを編集
  │
  ├─ 単一ギルド（既存）: 変更なし、そのまま動作
  │
  └─ マルチギルド: guildsエントリにgoogle_driveとerror_mention_role_idを追加
       │
       ├─ Guild A: google_drive.folder_id = "xxx", error_mention_role_id = 111
       ├─ Guild B: google_drive.enabled = false, error_mention_role_id = 222
       └─ Guild C: (省略 = グローバル設定にフォールバック)
```

### フロー2: Drive監視の動作（自動）

```
Bot起動 (on_ready)
  │
  ├─ Guild A (Drive有効): DriveWatcher起動 → folder_id_A監視
  │     └─ 新ファイル検出 → Guild AのoutputチャンネルAに投稿
  │
  ├─ Guild B (Drive無効): DriveWatcher起動しない
  │
  └─ Guild C (guild設定なし): グローバル設定参照
        └─ グローバルDrive有効ならDriveWatcher起動 → folder_id_global監視
```

### フロー3: エラー発生時

```
Pipeline失敗
  │
  ├─ ギルド別error_mention_role_id設定あり
  │     └─ そのギルドのロールをメンション: <@&222>
  │
  └─ ギルド別設定なし
        └─ グローバルerror_mention_role_idをフォールバック
              └─ グローバルもnull → メンションなし
```

### フロー4: `/minutes drive-status`（更新）

**現在の出力**:
```
Drive watcher: running
Folder ID: xxx
File pattern: craig[_-]*.aac.zip
Poll interval: 30s
Processed files: 5
```

**更新後の出力（マルチギルド時）**:
```
このギルドのDrive監視:
  状態: running
  Folder ID: xxx

全体:
  稼働中: 2/3 ギルド
  Processed files: 12
```

**更新後の出力（単一ギルド・Drive無効時）**:
```
このギルドのDrive監視: 無効
```

## 状態一覧

| 状態 | 表示 |
|------|------|
| Drive有効・稼働中 | "running" |
| Drive有効・停止 | "stopped" |
| Drive無効（ギルド設定） | "無効" |
| Drive未設定（グローバルフォールバック） | グローバル設定に依存 |

## エラー状態

| エラー | ユーザーへの影響 |
|--------|----------------|
| folder_id未設定でDrive有効 | バリデーションエラー（起動時） |
| credentials.json不在 | ログ警告、DriveWatcher起動せず |
| 出力チャンネル見つからない | ログ警告、そのギルドのDriveWatcher起動せず |

## アクセシビリティ

該当なし（CLI設定ファイル + Discord Embed出力のみ）。
