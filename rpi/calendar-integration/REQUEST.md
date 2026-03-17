# カレンダー連携

## 共通コンテキスト

- **プロジェクト**: Discord Minutes Bot — Discord音声チャンネルの録音から自動で議事録を生成するBot
- **技術スタック**: Python 3.10+, discord.py 2.3+, faster-whisper (large-v3, CUDA), Claude API, Google Drive API
- **設計原則**: Pipeline-first, async by default, graceful degradation, multi-guild support, minimal state
- **用語集**: Craig Bot: Discordマルチトラック録音Bot, パイプライン: 6ステージの非同期議事録生成処理

## この機能について

### フェーズ・優先度
- **企画書フェーズ**: 今後の拡張候補（スコープ外）
- **実装順序**: Ext-6

### 概要
Google Calendar等のカレンダーサービスと連携し、会議の議事録にカレンダーイベント情報（会議名、参加予定者、アジェンダ）を自動付加する。議事録の品質向上と会議コンテキストの自動取得を実現する。

### 元企画書からの該当箇所
> 会議のカレンダー連携（Google Calendar 等）

### 要件

#### Must Have（実装必須）
- Google Calendar APIから録音時間帯のイベントを取得 [←R-80]
- イベント情報（タイトル、参加者、説明）を議事録テンプレートの変数として利用可能にする [←R-80]
- config.yamlでカレンダー連携の有効/無効を設定可能 [←R-80]

#### Nice to Have（余裕があれば）
- 議事録生成後にカレンダーイベントに議事録リンクを追記 [←R-80]
- 定期会議の自動検出と議事録の連続管理 [←R-80]
- 複数カレンダーの監視 [←R-80]

### UI/UX
- カレンダー連携は透過的（ユーザーの追加操作不要）
- 議事録Embedにカレンダーイベント名を表示（取得できた場合）
- カレンダーにイベントがない場合はフォールバック（現行と同じ動作）

### データ
- Google Calendar API: イベントのタイトル、参加者、説明、開始/終了時間
- 認証: Google Service Account（既にDrive Watcherで使用中）+ Calendar APIスコープ追加

### API
- Google Calendar API (Events.list): 録音時間帯のイベント検索
- generator.py テンプレート変数に `{event_title}`, `{event_attendees}`, `{event_description}` 追加

### 非機能要件
- カレンダーAPI呼び出し失敗時もパイプライン処理を継続（graceful degradation）

### 適用される共通制約
- セキュリティ: Service AccountにCalendar API read-onlyスコープのみ付与
- 信頼性: カレンダーAPIコールも最大3回リトライ

## 対象コンポーネント
- `src/calendar_client.py` — 新規: Google Calendar APIクライアント
- `src/pipeline.py` — カレンダー情報取得ステージ追加
- `src/generator.py` — テンプレート変数にカレンダー情報追加
- `src/config.py` — CalendarConfig データクラス追加
- `config.yaml` — calendar セクション追加
- `tests/test_calendar_client.py` — 新規: カレンダー連携テスト

## 依存関係
- **先行（この機能の前に必要）**: なし
- **後続（この機能が完了すると着手可能）**: なし
- **並行可能**: multilingual-support, template-customization, speaker-analytics, minutes-search

## 受入基準
1. 録音時間帯にGoogle Calendarイベントがある場合、議事録にイベント名が表示される
2. カレンダーにイベントがない場合、議事録が従来通り正常に生成される
3. config.yamlでカレンダー連携を無効化できる

## 備考
- Google Service Account は既に drive_watcher.py で認証済み。Calendar APIスコープ追加のみ必要
- 録音の開始/終了時間は Craig Bot のパイプライン情報から取得可能
- 複数の音声チャンネルで同時間帯に録音がある場合のイベントマッチングロジックが必要

## 元企画書セクション
- セクション名: §9 今後の拡張候補
