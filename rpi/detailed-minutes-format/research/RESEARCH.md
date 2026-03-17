# Research Report: 詳細議事録フォーマット + トランスクリプト添付

## Executive Summary

Geminiメモ風の詳細議事録フォーマットへの変更とトランスクリプト添付の追加は、技術的に容易かつ低リスクで実現可能。既存の未使用フィールド（`include_transcript`）の活用、オプショナル引数の追加により後方互換を維持しながら実装できる。**GO** を推奨。

## Feature Overview

- **Name**: 詳細議事録フォーマット + トランスクリプト添付
- **Type**: Enhancement（既存機能の拡張）
- **Components**: poster.py, pipeline.py, prompts/minutes.txt, merger.py, config.yaml
- **Complexity**: Medium

## Requirements Summary

1. 添付ファイルを2つに変更（議事録 + 文字起こし）
2. プロンプトテンプレートをGeminiメモ風に更新
3. max_tokens を 4096 → 8192 に引き上げ
4. Discord Embed は概ね現状維持

## Technical Discovery

### Current State

| Module | 現状 | 備考 |
|--------|------|------|
| poster.py | 1ファイルのみ添付 | Discord APIは最大10ファイル対応 |
| pipeline.py | transcript を post_minutes に渡していない | オプショナル引数追加で対応 |
| PosterConfig | `include_transcript: bool = False` 定義済み・未使用 | そのまま活用可能 |
| merger.py | `[MM:SS] speaker: text` 形式 | Markdown整形関数追加で対応 |
| prompts/minutes.txt | 6セクション構成 | 全面書き換え |

### Integration Points

1. `pipeline.py:93` — transcript 生成後、post_minutes への受け渡し追加
2. `poster.py:post_minutes()` — transcript 引数追加 + 2ファイル添付
3. `poster.py:build_minutes_embed()` — 正規表現を新フォーマットに調整
4. `poster.py` — `build_transcript_file()` 新関数追加

### Test Impact

- `post_minutes()` にオプショナル引数追加 → 既存11テスト影響なし
- Embed正規表現変更 → 3テスト更新必要
- 新機能用テスト追加: 5-8件程度

## Technical Analysis

**Feasibility**: High
**Effort**: 1-2日

### Implementation Approach

1. `prompts/minutes.txt` 書き換え（Geminiメモ風）
2. `poster.py` に `build_transcript_file()` 追加
3. `post_minutes()` に `transcript: str | None = None` 引数追加
4. ForumChannel: `thread.send(file=transcript_file)` 追加
5. TextChannel: `files=[minutes_file, transcript_file]` に変更
6. `pipeline.py` で transcript を post_minutes に渡す
7. `config.yaml` の `max_tokens` 引き上げ + `include_transcript: true`
8. Embed正規表現 + テスト更新

### Risks

1. **Low**: 新プロンプトの出力品質チューニング（イテレーション必要）
2. **Low**: 長時間会議でのmax_tokens不足（8192で大幅改善）
3. **Very Low**: Discord APIレート制限（2ファイルなので問題なし）

## Strategic Recommendation

**Decision**: **GO**
**Confidence**: **High**

**Rationale**: 既存アーキテクチャの自然な拡張。未使用フィールドの活用でコード変更最小。ユーザー価値が高く、リスクが低い。

## Next Steps

1. `/rpi:plan detailed-minutes-format` でプラン生成
2. 実装（1-2日）
3. テスト実行
4. Docker rebuild & deploy
