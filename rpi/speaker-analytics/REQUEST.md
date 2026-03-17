# 話者別発言量・発言時間可視化

## 共通コンテキスト

- **プロジェクト**: Discord Minutes Bot — Discord音声チャンネルの録音から自動で議事録を生成するBot
- **技術スタック**: Python 3.10+, discord.py 2.3+, faster-whisper (large-v3, CUDA), Claude API, Google Drive API
- **設計原則**: Pipeline-first, async by default, graceful degradation, multi-guild support, minimal state
- **用語集**: Segment: faster-whisperの文字起こし単位（start, end, text, speaker）, 統合トランスクリプト: 話者別Segmentを時系列にマージしたテキスト

## この機能について

### フェーズ・優先度
- **企画書フェーズ**: 今後の拡張候補（スコープ外）
- **実装順序**: Ext-3

### 概要
会議の話者ごとの発言量（文字数/単語数）と発言時間を集計し、議事録に可視化して付加する。会議のバランスや参加度を把握するのに有用。

### 元企画書からの該当箇所
> 話者ごとの発言量・発言時間の可視化

### 要件

#### Must Have（実装必須）
- 話者ごとの発言時間（秒）を集計する [←R-83]
- 話者ごとの発言文字数を集計する [←R-83]
- 議事録EmbedにDiscord棒グラフ（テキスト）として表示する [←R-83]

#### Nice to Have（余裕があれば）
- 議事録Markdownファイルにも統計テーブルを追加 [←R-83]
- 発言の割合（%）表示 [←R-83]

### UI/UX
- 議事録Embedの末尾に「📊 話者統計」フィールドを追加
- テキストベースの棒グラフ: `田中 ████████░░ 42%`
- 統計情報は任意で無効化可能（config.yaml）

### データ
- Segment の start/end/speaker 情報から集計（既存データを利用）
- 新規永続化データなし

### API
- 新規エンドポイントなし
- poster.py のEmbed生成に統計フィールドを追加

### 非機能要件
- 集計処理は軽量（パイプライン全体のSLAに影響しないこと）

### 適用される共通制約
- パフォーマンス: 集計処理はO(N)で十分高速
- 技術スタック: 外部ライブラリ追加不要（標準ライブラリで集計可能）

## 対象コンポーネント
- `src/merger.py` — 統計集計ロジック追加（またはnew `src/analytics.py`）
- `src/poster.py` — Embedに統計フィールド追加
- `src/pipeline.py` — 集計結果をposter に渡す
- `config.yaml` — 統計表示の有効/無効設定
- `tests/test_merger.py` — 統計集計テスト

## 依存関係
- **先行（この機能の前に必要）**: なし（既存のSegmentデータを使用）
- **後続（この機能が完了すると着手可能）**: なし
- **並行可能**: multilingual-support, template-customization

## 受入基準
1. 議事録Embedに話者ごとの発言時間と発言量が表示される
2. 複数話者の相対的なバランスがテキスト棒グラフで可視化される
3. config.yamlで統計表示を無効化できる
4. 統計集計がパイプライン処理時間に実質的な影響を与えない

## 備考
- Segment dataclass に既に start, end, speaker フィールドがあるため、新規データ取得は不要
- Discord Embedのフィールド数制限（最大25）に注意。統計フィールドは1つにまとめる
- テキスト棒グラフはUnicodeブロック文字（`█`, `░`）で実装

## 元企画書セクション
- セクション名: §9 今後の拡張候補
