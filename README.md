# discord-minutes-bot

Discord音声会議の議事録を自動生成するBot。[Craig Bot](https://craig.horse)の録音をローカルGPUで文字起こしし、Claude APIで議事録にまとめてDiscordチャンネルに投稿する。

## 仕組み

```
Craig Bot (録音)
    │
    ▼
Google Drive (自動アップロード)
    │  30秒間隔で監視
    ▼
┌─────────────────────────────────┐
│         minutes-bot              │
│                                  │
│  ZIP展開 → FFmpeg変換            │
│      → faster-whisper 文字起こし │
│      → 話者統合                  │
│      → Claude API 議事録生成     │
│      → Discord投稿              │
└─────────────────────────────────┘
    │
    ▼
#議事録チャンネル (Embed + .md添付)
```

2分の録音から約20秒で議事録を生成する（RTX 3060, CUDA 13.0環境）。

## 必要なもの

- **Python** 3.10以上
- **NVIDIA GPU** VRAM 6GB以上 + CUDA
- **FFmpeg**
- **Discord Bot トークン** — [Developer Portal](https://discord.com/developers/applications)で取得
- **Anthropic API キー** — [console.anthropic.com](https://console.anthropic.com)で取得
- **Google Cloud サービスアカウント** — Drive監視に使用（自動モードの場合のみ）
- **Craig Bot** — 対象Discordサーバーに導入済みであること

## インストール

```bash
git clone https://github.com/yourname/discord-minutes-bot.git
cd discord-minutes-bot
pip install -r requirements.txt
```

依存パッケージ:

```
discord.py>=2.3
faster-whisper>=1.0
anthropic>=0.40
pyyaml
python-dotenv
aiohttp
ffmpeg-python
google-api-python-client
google-auth
```

## 設定

### 環境変数

```bash
cp .env.example .env
```

```env
DISCORD_BOT_TOKEN=your_discord_bot_token
ANTHROPIC_API_KEY=sk-ant-your_key
```

### config.yaml

```yaml
discord:
  guild_id:         # サーバーID
  watch_channel_id:  # Craig投稿チャンネルID
  output_channel_id:  # 議事録の投稿先チャンネルID

whisper:
  model: large-v3
  device: cuda
  compute_type: float16
  language: ja

claude:
  model: claude-sonnet-4-5-20250929
  max_tokens: 4096

google_drive:
  enabled: true
  folder_id: YOUR_CRAIG_FOLDER_ID
  poll_interval: 30
  credentials_file: credentials.json
```

Discord IDは、Discordの設定 → 詳細設定 → 開発者モードON にした後、サーバーやチャンネルを右クリック → 「IDをコピー」で取得できる。

### Discord Bot の作成と招待

1. [Developer Portal](https://discord.com/developers/applications)で「New Application」
2. Bot → **MESSAGE CONTENT INTENT** を ON
3. OAuth2 URL Generator で `bot` + `applications.commands` を選択
4. Bot Permissionsで View Channels / Send Messages / Embed Links / Attach Files / Read Message History を選択
5. 生成されたURLでサーバーに招待

### Google Drive 連携（自動モードの場合）

**Craig側:**
1. https://craig.horse にDiscordでログイン
2. Google Drive連携を有効化し、保存形式をAAC マルチトラックに設定

**Google Cloud側:**
1. [Cloud Console](https://console.cloud.google.com)でプロジェクトを作成
2. Google Drive APIを有効化
3. サービスアカウントを作成 → JSONキーをダウンロード
4. プロジェクトルートに `credentials.json` として配置
5. Google DriveのCraigフォルダをサービスアカウントのメールアドレスに共有（閲覧者）

## 使い方

### 起動

```bash
./start.sh
```

`start.sh` はCUDA用のライブラリパスを設定してBotを起動する。手動で起動する場合:

```bash
export LD_LIBRARY_PATH="$(python3 -c 'import nvidia.cublas; print(nvidia.cublas.__path__[0])')/lib:$(python3 -c 'import nvidia.cudnn; print(nvidia.cudnn.__path__[0])')/lib:$LD_LIBRARY_PATH"
python3 bot.py
```

### 自動モード（Google Drive監視）

Craig Botで録音 → 停止すると、CraigがGoogle Driveに自動アップロードする。Botが30秒間隔で新ファイルを検知し、パイプラインを自動実行する。ユーザー操作は不要。

### 手動モード（スラッシュコマンド）

CraigのDMに届くURLを使う:

```
/minutes https://craig.horse/rec/xxxxx?key=yyyyy
```

### 出力

Botは2つのものを投稿する:

- **Embed** — 会議タイトル・参加者・要約（プレビュー用）
- **Markdownファイル** — 議題・詳細・決定事項・アクションアイテム（完全版）

出力例:

```markdown
# 会議議事録

## 基本情報
- 日時: 2026-02-10 18:16
- 参加者: yamaguchi_314, genki0

## 議題
### 1. たこ焼きパーティーの計画
- 開催日の候補について議論
- 材料の買い出し担当を決定

## 決定事項
- 来週土曜日に開催
- 材料はgenki0が担当

## アクションアイテム
- [ ] genki0: 材料リストを作成して共有
- [ ] yamaguchi_314: 会場の予約確認
```

## パイプライン

処理は6段階で、各ステージは独立している。失敗時はDiscordにエラー通知を投稿する。

| ステージ | 処理 | エラー時 |
|----------|------|----------|
| `audio_acquisition` | Drive監視 or Craig APIでZIP取得、話者別ファイル展開 | 3回リトライ → 通知 |
| `preprocessing` | AAC/FLAC → 16kHz mono WAV変換、無音除去 | 通知・中止 |
| `transcription` | faster-whisper large-v3 (CUDA float16) で話者ごとに処理 | 通知・中止 |
| `merging` | タイムスタンプで時系列ソート、`[HH:MM:SS] 話者: テキスト`形式に統合 | 通知・中止 |
| `generation` | Claude Sonnetに統合テキストを送信、構造化Markdown生成 | 3回リトライ → 通知 |
| `posting` | Embed (要約) + .mdファイル (詳細) をチャンネルに送信 | リトライ → 分割 |

一時ファイルは `try/finally` で確実に削除される。

## プロジェクト構成

```
discord-minutes-bot/
├── bot.py                     # エントリポイント
├── start.sh                   # 起動スクリプト
├── src/
│   ├── pipeline.py            # パイプライン制御
│   ├── craig_client.py        # Craig API クライアント
│   ├── drive_watcher.py       # Google Drive ポーリング
│   ├── audio_processor.py     # FFmpeg 変換
│   ├── transcriber.py         # faster-whisper 推論
│   ├── transcript_merger.py   # 話者統合
│   ├── generator.py           # Claude API 呼び出し
│   ├── poster.py              # Discord 投稿
│   └── errors.py              # 例外定義
├── prompts/
│   └── minutes.txt            # 議事録生成プロンプト
├── tests/                     # テスト (110件)
├── config.yaml
├── credentials.json           # Google Drive サービスアカウントキー
├── .env                       # シークレット
└── requirements.txt
```

## Craig API

Craig APIは非公式。ブラウザのDevToolsで調査して特定したエンドポイントを使用している。

```
# ジョブステータス（ポーリング）
GET https://craig.horse/api/v1/recordings/{rec_id}/job?key={key}

→ status: "complete" になったら outputFileName を取得

# ZIPダウンロード
GET https://craig.horse/dl/{outputFileName}
```

CraigはDLリンクをDMにしか送らない（チャンネルには投稿しない）ため、自動化にはGoogle Drive連携が必要。仕様は予告なく変更される可能性がある。

## テスト

```bash
pytest
```

```
========================= 110 passed in 12.3s =========================
```

## パフォーマンス

RTX 3060 12GB / CUDA 13.0 / WSL2 Ubuntu 24 で計測:

| 録音時間 | 話者数 | 処理時間 | 内訳 |
|----------|--------|----------|------|
| 2分 | 1人 | ~20秒 | DL 0.6s, 文字起こし 8.3s, 生成 9.0s, 投稿 1.5s |
| 2分 | 2人 | ~25秒 | DL 0.6s, 文字起こし 12s, 生成 9.0s, 投稿 1.5s |

## 既知の制限

- Craig APIは非公式のため、仕様変更で動作しなくなる可能性がある（Drive経由のみで運用可能）
- GPUが必須（CPUフォールバック未実装）
- 同時に複数の録音が来た場合はキューイングされる
- Discord Embedは4096文字制限があり、長い議事録はMDファイル添付で対応


[MIT](LICENSE)
