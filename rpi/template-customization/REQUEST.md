# 議事録テンプレートカスタマイズ

## 共通コンテキスト

- **プロジェクト**: Discord Minutes Bot — Discord音声チャンネルの録音から自動で議事録を生成するBot
- **技術スタック**: Python 3.10+, discord.py 2.3+, faster-whisper (large-v3, CUDA), Claude API, Google Drive API
- **設計原則**: Pipeline-first, async by default, graceful degradation, multi-guild support, minimal state
- **用語集**: Claude API: Anthropic社のLLM API, 議事録テンプレート: Claude APIへのシステムプロンプト（prompts/minutes.txt）

## この機能について

### フェーズ・優先度
- **企画書フェーズ**: 今後の拡張候補（スコープ外）
- **実装順序**: Ext-2

### 概要
議事録の出力フォーマットをDiscordコマンドでカスタマイズ可能にする。現在は `prompts/minutes.txt` の固定テンプレートのみだが、ギルドごとに異なるフォーマットや重点項目（例：決定事項重視、TODO重視）を設定できるようにする。

### 元企画書からの該当箇所
> 議事録のテンプレートカスタマイズ（Discordコマンドで変更）

### 要件

#### Must Have（実装必須）
- 複数の議事録テンプレートを `prompts/` ディレクトリに配置可能にする [←R-78]
- config.yaml でギルドごとにデフォルトテンプレートを指定可能にする [←R-78]
- `/minutes template list` でテンプレート一覧表示 [←R-78]
- `/minutes template set <name>` でギルドのテンプレートを変更 [←R-78]

#### Nice to Have（余裕があれば）
- `/minutes template preview <name>` でテンプレートのサンプル出力を表示 [←R-78]
- テンプレートにカスタム変数（会議名、プロジェクト名等）を追加可能にする [←R-78]

### UI/UX
- Discord スラッシュコマンドで操作
- テンプレート変更は即座に反映（次回の議事録生成から適用）
- テンプレート一覧はEmbed形式で表示（名前 + 説明 + サンプル出力の冒頭）

### データ
- `prompts/` ディレクトリに複数の `.txt` テンプレートファイル
- config.yaml の `generator` セクションにギルド別テンプレート設定を追加
- テンプレートメタデータ（名前、説明）はファイル先頭のコメントで管理

### API
- 新規スラッシュコマンド: `/minutes template list`, `/minutes template set <name>`
- generator.py のテンプレートロード機能を拡張

### 非機能要件
- テンプレート切替時にBot再起動不要（ホットリロード）

### 適用される共通制約
- 技術スタック: Claude API のシステムプロンプトとしてテンプレートを使用
- セキュリティ: テンプレートファイルからのパストラバーサル防止

## 対象コンポーネント
- `src/generator.py` — テンプレートロード・選択ロジック
- `src/config.py` — ギルド別テンプレート設定
- `bot.py` — スラッシュコマンド追加
- `prompts/` — テンプレートファイル群
- `tests/test_generator.py` — テンプレート選択テスト追加

## 依存関係
- **先行（この機能の前に必要）**: なし
- **後続（この機能が完了すると着手可能）**: なし
- **並行可能**: multilingual-support, speaker-analytics

## 受入基準
1. `/minutes template list` で利用可能なテンプレート一覧が表示される
2. `/minutes template set` でギルドのテンプレートを変更できる
3. 変更後の議事録生成で新テンプレートが反映される
4. デフォルトテンプレート（現行の minutes.txt）が後方互換で動作する

## 備考
- 現在の `prompts/minutes.txt` はそのままデフォルトテンプレートとして残す
- テンプレートの追加は `.txt` ファイルの配置のみで可能にし、コード変更不要にする
- OPEN-01（OpenAIプロバイダ対応）が決まった場合、プロバイダごとのプロンプト調整も必要になる可能性あり

## 元企画書セクション
- セクション名: §9 今後の拡張候補
