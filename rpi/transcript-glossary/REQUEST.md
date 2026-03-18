# 用語辞書（Transcript Glossary）

## 概要

Whisper文字起こしで頻出する誤認識を、ユーザー定義の用語辞書で自動補正する機能。
transcript-correction-ui（DEFER判定）の代替案として、パイプラインの再設計なしに80%のニーズをカバーする。

## 背景

- Whisperは固有名詞・専門用語・社内用語を高確率で誤認識する
- 例: 「TOONIQ」→「ツーニック」、「Figma」→「フィグマ」、人名の漢字誤り
- 同じ誤りが毎回繰り返されるため、辞書ベースの補正が有効

## 要件

### Must Have

1. **辞書データ構造**: 誤認識パターン → 正しい表記のマッピング（JSON/YAML）
2. **自動補正**: パイプラインのtranscribe後・merge前にセグメントテキストを辞書で置換
3. **Discordスラッシュコマンド**: 辞書のCRUD操作
   - `/minutes glossary add <wrong> <correct>` — エントリ追加
   - `/minutes glossary remove <wrong>` — エントリ削除
   - `/minutes glossary list` — 現在の辞書を表示
4. **ギルド単位の辞書**: 各サーバーで独立した辞書を持つ
5. **永続化**: StateStore または専用ファイルで辞書を永続保存

### Nice to Have

- 正規表現対応（パターンマッチ）
- 辞書のインポート/エクスポート（JSON）
- 補正ログ（何が何に補正されたかの記録）
- 辞書のサジェスト（頻出する未登録語の検出）

## UI/UX

- スラッシュコマンドで操作（Web UIは不要）
- ephemeralメッセージで操作結果を表示
- 辞書リストはEmbed形式で見やすく表示

## 技術的制約

- パイプラインの `_stage_transcribe()` 後、`merge_transcripts()` 前に挿入
- 既存のSegmentデータクラスは変更不要（text フィールドを置換するだけ）
- StateStoreの拡張またはSQLiteで辞書を永続化

## 変更対象ファイル（想定）

1. `src/glossary.py` — 新規: 辞書管理モジュール
2. `src/pipeline.py` — 辞書適用の挿入
3. `bot.py` — スラッシュコマンド追加
4. `src/config.py` — GlossaryConfig追加
5. `config.yaml` — glossary セクション追加
6. テスト追加
