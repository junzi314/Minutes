# Feature Request: Discord会議 自動議事録生成システム

## 概要
Discord音声チャンネルでの会議を自動的に録音・文字起こし・議事録生成し、指定チャンネルへ投稿するシステムを構築する。

## ゴール
Craig Botによる録音終了をトリガーに、人手を介さず議事録が生成・投稿される完全自動パイプラインを実現する。

## Phase 1 スコープ（今回の調査対象）
- Discord Bot基盤の構築（Python / discord.py）
- Craig Botのメッセージ検知機能
- Craig Botのダウンロードリンク解析
- 音声ファイルの自動ダウンロード
- config.yaml + .env による設定管理

## 技術スタック
| レイヤー | 技術 |
|---------|------|
| パイプライン制御 | Discord Bot (Python / discord.py) |
| 録音 | Craig Bot (既存、マルチトラック録音) |
| 音声前処理 | FFmpeg |
| 文字起こし | faster-whisper (large-v3) ローカル実行 |
| 議事録生成 | Claude API (Sonnet) |
| 設定管理 | config.yaml + .env |
| ホスティング | ローカルPC |

## パイプライン
```
Craig Bot録音終了 → DLリンク検知 → 音声取得 → FFmpeg前処理 → faster-whisper文字起こし → トランスクリプト統合 → Claude API議事録生成 → Discord投稿
```

## Craig Bot連携の詳細
- Craig BotはDiscord録音終了後、テキストチャンネルにダウンロードリンクを投稿
- 自作BotがCraig BotのメッセージをBot ID / embed内容で監視
- ダウンロードURLから話者別音声ファイル（FLAC/Ogg）を自動取得
- ファイル名にDiscordユーザー名が含まれ、話者識別に利用可能
- ダウンロードリンクには有効期限あり（通常7日間）

## サンプルデータ
- `samples/` ディレクトリにCraigの録音サンプル（AACファイル）あり
- ファイル名形式: `{track-number}-{username}.{format}` (例: `1-shake344.aac`)

## 要件詳細
詳細な要件定義は `docs/requirements.md` を参照。

## 制約
- 1サーバー・1音声チャンネル対象
- NVIDIA GPU (VRAM 6GB以上) 必要
- ローカルPC運用（会議時間帯にPC起動必須）
- Craig Botの仕様変更リスクあり
