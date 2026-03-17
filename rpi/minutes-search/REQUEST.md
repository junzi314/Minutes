# 過去議事録検索

## 共通コンテキスト

- **プロジェクト**: Discord Minutes Bot — Discord音声チャンネルの録音から自動で議事録を生成するBot
- **技術スタック**: Python 3.10+, discord.py 2.3+, faster-whisper (large-v3, CUDA), Claude API, Google Drive API
- **設計原則**: Pipeline-first, async by default, graceful degradation, multi-guild support, minimal state
- **用語集**: StateStore: 処理状態と議事録キャッシュを管理するJSONファイルストア, minutes_cache.json: 生成済み議事録のハッシュ→内容マッピング

## この機能について

### フェーズ・優先度
- **企画書フェーズ**: 今後の拡張候補（スコープ外）
- **実装順序**: Ext-4

### 概要
過去に生成された議事録をキーワードで検索できる機能。現在は議事録がDiscordチャンネルに投稿されるのみだが、古い議事録を探すのが困難。Discordスラッシュコマンドで全文検索を提供する。

### 元企画書からの該当箇所
> 過去の議事録検索機能

### 要件

#### Must Have（実装必須）
- 生成した議事録をローカルに永続保存する [←R-81]
- `/minutes search <keyword>` で過去議事録をキーワード検索 [←R-81]
- 検索結果をEmbed形式で表示（日付、参加者、マッチ箇所のスニペット） [←R-81]

#### Nice to Have（余裕があれば）
- 日付範囲での絞り込み `/minutes search <keyword> --after 2026-01-01` [←R-81]
- 話者名での絞り込み [←R-81]
- 検索結果から元の議事録メッセージへのリンク [←R-81]

### UI/UX
- `/minutes search <keyword>` スラッシュコマンド
- 結果はEmbed形式（最大5件表示、ページネーション付き）
- 各結果にマッチした箇所のスニペット（前後50文字程度）を表示

### データ
- 議事録の永続保存: `state/minutes_archive/` に日付別MarkdownファイルまたはJSONインデックス
- 検索インデックス: シンプルな全文検索（SQLite FTS5 or 純Python）
- 既存の `state/minutes_cache.json` から移行検討

### API
- 新規スラッシュコマンド: `/minutes search <keyword>`
- state_store.py にアーカイブ・検索メソッド追加

### 非機能要件
- 検索応答時間: 100件のアーカイブで1秒以内
- ストレージ: 議事録1件あたり数KB、年間で数MB程度

### 適用される共通制約
- セキュリティ: 議事録はギルドごとに分離して保存
- 技術スタック: 「minimal state」原則に従い、SQLiteまたはJSONファイルで実装（RDBMSなし）

## 対象コンポーネント
- `src/state_store.py` — アーカイブ保存・検索メソッド追加
- `src/pipeline.py` — 議事録生成後のアーカイブ保存を追加
- `bot.py` — `/minutes search` スラッシュコマンド追加
- `state/` — `minutes_archive/` ディレクトリ追加
- `tests/test_state_store.py` — アーカイブ・検索テスト追加

## 依存関係
- **先行（この機能の前に必要）**: なし
- **後続（この機能が完了すると着手可能）**: external-export（エクスポート対象として利用）
- **並行可能**: multilingual-support, template-customization, speaker-analytics

## 受入基準
1. 生成された議事録が自動的にアーカイブに保存される
2. `/minutes search <keyword>` でキーワードにマッチする議事録が表示される
3. 検索結果にマッチ箇所のスニペットが表示される
4. ギルドをまたいだ検索結果の漏洩がないこと

## 備考
- 「minimal state」原則に従い、最初はファイルベースの全文検索で実装
- 件数が増えた場合にSQLite FTS5への移行パスを確保しておく
- 既存の `minutes_cache.json` はトランスクリプトハッシュ→議事録のマッピングだが、検索には不向き

## 元企画書セクション
- セクション名: §9 今後の拡張候補
