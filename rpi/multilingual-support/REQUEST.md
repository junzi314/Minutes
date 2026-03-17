# 日英混在対応

## 共通コンテキスト

- **プロジェクト**: Discord Minutes Bot — Discord音声チャンネルの録音から自動で議事録を生成するBot
- **技術スタック**: Python 3.10+, discord.py 2.3+, faster-whisper (large-v3, CUDA), Claude API, Google Drive API
- **設計原則**: Pipeline-first, async by default, graceful degradation, multi-guild support, minimal state
- **用語集**: faster-whisper: CTranslate2ベースのWhisper推論エンジン, large-v3: Whisperの最大モデル, 統合トランスクリプト: 話者別文字起こしを時系列にマージしたテキスト

## この機能について

### フェーズ・優先度
- **企画書フェーズ**: 今後の拡張候補（スコープ外）
- **実装順序**: Ext-1

### 概要
会議中に日本語と英語が混在する場合に、Whisperの言語パラメータを動的に切り替えて精度を向上させる。現在は `language="ja"` 固定だが、英語の発言や専門用語が含まれる会議で文字起こし品質が低下する課題に対応する。

### 元企画書からの該当箇所
> 日英混在対応（Whisper の `language` パラメータ動的切替）

### 要件

#### Must Have（実装必須）
- Whisperの`language`パラメータを自動検出モード（`language=None`）に切り替え可能にする [←R-84]
- config.yamlで言語設定を`"ja"`, `"en"`, `"auto"`から選択可能にする [←R-84]

#### Nice to Have（余裕があれば）
- ギルドごとに言語設定を変更可能にする [←R-84]
- `/minutes language <lang>` スラッシュコマンドで動的に変更 [←R-84]

### UI/UX
- 既存のスラッシュコマンドに影響なし
- 議事録の言語は入力トランスクリプトの言語に追従

### データ
- config.yaml の `whisper.language` フィールドを拡張（"ja" | "en" | "auto"）

### API
- 新規エンドポイントなし
- faster-whisper APIの`language`パラメータ変更のみ

### 非機能要件
- `language=None`（auto）使用時の処理速度低下を計測・許容範囲内であること

### 適用される共通制約
- パフォーマンス: auto検出時も15分SLA内に収まること
- 技術スタック: faster-whisper APIのlanguageパラメータを使用

## 対象コンポーネント
- `src/transcriber.py` — language パラメータの動的制御
- `src/config.py` — WhisperConfig の language フィールド拡張
- `config.yaml` — whisper.language の選択肢追加
- `tests/test_transcriber.py` — 言語切替テスト追加

## 依存関係
- **先行（この機能の前に必要）**: なし
- **後続（この機能が完了すると着手可能）**: なし
- **並行可能**: template-customization, speaker-analytics

## 受入基準
1. `language: "auto"` 設定時に日英混在音声を正しく文字起こしできる
2. `language: "ja"` 設定時に従来と同じ動作をする（後方互換）
3. auto検出モードの処理時間がja固定比で2倍以内

## 備考
- Whisper large-v3は多言語対応のため、`language=None`で自動検出が可能
- 自動検出モードでは各セグメントの言語も検出されるため、将来的に言語ごとの統計表示にも活用可能
- 処理速度低下がある場合、guildごとの設定で対応可能（マルチギルド設定を活用）

## 元企画書セクション
- セクション名: §9 今後の拡張候補
