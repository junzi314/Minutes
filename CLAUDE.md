# Discord Minutes Bot — Claude Code設定

## プロジェクト概要

Discord音声チャンネルの録音（Craig Bot）から自動で議事録を生成するBot。
Whisperで文字起こし → Claude APIで議事録生成 → Discordに投稿。

- **言語**: Python 3.10+ (開発環境 3.12)
- **フレームワーク**: discord.py 2.3+
- **音声認識**: faster-whisper (large-v3, CUDA)
- **LLM**: Anthropic Claude API (claude-sonnet-4-5-20250929)
- **音声処理**: FFmpeg
- **クラウドストレージ**: Google Drive API（自動監視用、任意）
- **テスト**: pytest + pytest-asyncio（139+ テスト）
- **デプロイ**: Docker (NVIDIA CUDA 12.6 + Ubuntu 24.04) / systemd

## コマンド

```bash
python3 bot.py                     # Bot起動
python3 bot.py --log-level DEBUG   # デバッグモード
./start.sh                         # CUDA設定付き起動
pytest                             # テスト実行（~30秒）
docker compose up -d               # Docker起動
```

## ディレクトリ構成

```
├── bot.py                  # エントリーポイント、Discordクライアント、コマンドハンドラ
├── config.yaml             # マルチギルド設定、Whisper/Claude/Craig設定
├── src/
│   ├── config.py           # 設定データクラス & ローダー
│   ├── pipeline.py         # 6ステージパイプライン オーケストレーション
│   ├── craig_client.py     # Craig Bot API クライアント（非公式API）
│   ├── drive_watcher.py    # Google Drive ポーリング監視
│   ├── audio_source.py     # AudioSource抽象 + ZIP展開
│   ├── transcriber.py      # faster-whisper ラッパー
│   ├── merger.py           # 話者別トランスクリプト統合
│   ├── generator.py        # Claude API 議事録生成
│   ├── poster.py           # Discord Embed構築 & 投稿
│   ├── detector.py         # Craig録音終了メッセージ検知
│   ├── state_store.py      # 処理重複排除 & 議事録キャッシュ
│   └── errors.py           # カスタム例外階層
├── prompts/
│   └── minutes.txt         # Claude APIシステムプロンプト（日本語テンプレート）
├── tests/                  # 139+ テストケース
│   ├── conftest.py         # pytestフィクスチャ
│   ├── fixtures/           # テストデータ（Craigペイロード等）
│   └── test_*.py           # 各モジュールのテスト
├── state/                  # 永続化（processing.json, minutes_cache.json）
├── logs/                   # ローテーションログ
├── docs/
│   └── requirements.md     # 詳細要件定義書（日本語）
└── .env                    # DISCORD_BOT_TOKEN, ANTHROPIC_API_KEY
```

## パイプラインアーキテクチャ

6ステージの非同期パイプラインで処理:

| ステージ | モジュール | 処理内容 |
|---------|-----------|---------|
| 1. audio_acquisition | craig_client.py | Craig録音ZIPダウンロード（非公式Job API） |
| 2. preprocessing | — | FFmpeg音声変換（現在コメントアウト） |
| 3. transcription | transcriber.py | faster-whisper large-v3 文字起こし + 話者属性 |
| 4. merging | merger.py | 話者別セグメントを時系列統合 |
| 5. generation | generator.py | Claude APIで構造化議事録生成 |
| 6. posting | poster.py | Discord Embed + .mdファイル投稿 |

### 入力経路
- **自動**: Google Drive監視でCraig録音ZIPを検知
- **手動**: `/minutes process <url>` スラッシュコマンド

## 外部API

- **Craig Bot API**（非公式）: 録音データのcook/ダウンロード
- **Anthropic Claude API**: 議事録生成（temperature 0.3, max_tokens 4096）
- **Google Drive API**: フォルダ監視（Service Account認証）
- **Discord Gateway**: メッセージ・音声イベント

## 環境変数

- `DISCORD_BOT_TOKEN` — Discord Botトークン
- `ANTHROPIC_API_KEY` — Anthropic APIキー
- `credentials.json` — Google Service Accountキー（Drive監視用）

## RPIワークフロー

機能開発は **RPI（Research → Plan → Implement）** フローで進める。

### 使い方

0. **Decompose**（任意）: `/rpi:decompose {企画書パス}` → 個別REQUEST.mdに分解
1. **Describe**: Plan Modeで機能概要を書き、`rpi/{feature-slug}/REQUEST.md` を作成
2. **Research**: `/rpi:research rpi/{feature-slug}/REQUEST.md` → GO/NO-GO判定
3. **Plan**: `/rpi:plan {feature-slug}` → pm.md / ux.md / eng.md / PLAN.md 生成
4. **Validate**（任意）: `/rpi:validate {feature-slug} {企画書パス}` → ドリフト検証
5. **Board**（任意）: `/rpi:board {feature-slug}` → GitHub Issues作成
6. **Implement**: `/rpi:implement {feature-slug}` → フェーズ別実装＋バリデーションゲート

### ベストプラクティス

- CLAUDE.md は150行以内を維持
- 複雑なタスクはPlan Modeから開始
- コンテキスト50%付近で `/compact` を実行
- サブタスクはコンテキスト50%以内で完了できる粒度に分割
