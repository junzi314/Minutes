# Notion/Google Docs自動エクスポート

## 共通コンテキスト

- **プロジェクト**: Discord Minutes Bot — Discord音声チャンネルの録音から自動で議事録を生成するBot
- **技術スタック**: Python 3.10+, discord.py 2.3+, faster-whisper (large-v3, CUDA), Claude API, Google Drive API
- **設計原則**: Pipeline-first, async by default, graceful degradation, multi-guild support, minimal state
- **用語集**: XDTS: —（該当なし）

## この機能について

### フェーズ・優先度
- **企画書フェーズ**: 今後の拡張候補（スコープ外）
- **実装順序**: Ext-5

### 概要
生成された議事録をNotionやGoogle Docsに自動エクスポートする。会議終了後にDiscord投稿だけでなく、チーム全体がアクセスしやすいドキュメント管理ツールにも自動保存されることで、議事録の利用性を向上させる。

### 元企画書からの該当箇所
> 議事録の Notion / Google Docs 自動エクスポート

### 要件

#### Must Have（実装必須）
- 議事録生成後にGoogle Docsへ自動エクスポート [←R-82]
- config.yamlでエクスポート先を有効/無効化 [←R-82]
- エクスポート失敗時もDiscord投稿は正常に完了すること [←R-82]

#### Nice to Have（余裕があれば）
- Notionへの自動エクスポート [←R-82]
- エクスポート先のフォルダ/データベースをギルドごとに設定可能 [←R-82]
- `/minutes export <message_id>` で既存の議事録を手動エクスポート [←R-82]

### UI/UX
- 自動エクスポートはパイプラインの最終ステージとして透過的に実行
- Discord Embedにエクスポート先リンクを追加表示
- エクスポート失敗時はEmbed内に「エクスポート失敗」の注記を追加

### データ
- Google Docs: Markdown→Google Docs形式への変換
- Notion: Markdown→Notionブロック形式への変換
- 認証情報: Google Service Account（既にDrive Watcherで使用中）、Notion API Key

### API
- Google Docs API: ドキュメント作成
- Notion API: ページ作成
- pipeline.py にエクスポートステージ追加

### 非機能要件
- エクスポート失敗がパイプライン全体を失敗させないこと（graceful degradation）
- エクスポート処理は15分SLAに含めない（Discord投稿後の後処理）

### 適用される共通制約
- セキュリティ: Notion API Key、Google Service Accountの認証情報は.envで管理
- 信頼性: エクスポートも最大3回リトライ

## 対象コンポーネント
- `src/exporter.py` — 新規: Google Docs/Notion エクスポートロジック
- `src/pipeline.py` — エクスポートステージ追加（投稿後）
- `src/config.py` — ExportConfig データクラス追加
- `config.yaml` — export セクション追加
- `tests/test_exporter.py` — 新規: エクスポートテスト

## 依存関係
- **先行（この機能の前に必要）**: minutes-search（アーカイブ基盤を利用する場合）
- **後続（この機能が完了すると着手可能）**: なし
- **並行可能**: calendar-integration

## 受入基準
1. 議事録生成後にGoogle Docsにドキュメントが自動作成される
2. Markdownの構造（見出し、リスト、テーブル）がGoogle Docsで正しく表示される
3. エクスポート失敗時もDiscordへの投稿は正常完了する
4. config.yamlでエクスポートを無効化できる

## 備考
- Google Drive API は既に drive_watcher.py で使用しているため、Service Account認証を流用可能
- Notion APIは公式Pythonクライアント（notion-client）を使用
- Google Docs APIでのMarkdown→Docs変換は完全ではないため、基本的な構造変換に留める
- minutes-search のアーカイブ機能があれば、過去の議事録のバッチエクスポートも可能

## 元企画書セクション
- セクション名: §9 今後の拡張候補
