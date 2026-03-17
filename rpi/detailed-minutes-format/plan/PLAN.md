# Implementation Plan: 詳細議事録フォーマット + トランスクリプト添付

## Overview

議事録出力をGeminiメモ風の「まとめ + 詳細 + 推奨される次のステップ」形式に変更し、トランスクリプト（文字起こし）をMarkdown整形して2つ目の添付ファイルとして投稿する。

## Implementation Phases

### Phase 1: プロンプトテンプレート書き換え
- **File**: `prompts/minutes.txt`
- **変更**: 6セクション → 3セクション（まとめ / 詳細 / 推奨される次のステップ）
- **リスク**: Low（出力品質はイテレーションで調整）

### Phase 2: トランスクリプトMarkdown整形
- **File**: `src/merger.py`
- **変更**: `format_transcript_markdown()` 関数追加
- **入力**: 既存の `[MM:SS] speaker: text` 形式
- **出力**: `### HH:MM:SS` セクション区切り + `**speaker:** text` 形式

### Phase 3: poster.py 拡張
- **File**: `src/poster.py`
- **変更**:
  - `build_transcript_file()` 新関数
  - `post_minutes()` に `transcript_md` オプショナル引数追加
  - `_SUMMARY_PATTERN` / `_DECISIONS_PATTERN` 正規表現更新
  - Embedフィールド名更新（まとめ / 次のステップ）
  - ForumChannel: `thread.send(files=[...])` に変更
  - TextChannel: `channel.send(files=[...])` に変更

### Phase 4: pipeline.py 統合
- **File**: `src/pipeline.py`
- **変更**: `transcript` → `format_transcript_markdown()` → `post_minutes(transcript_md=...)` の流れを追加
- `cfg.poster.include_transcript` フラグで制御

### Phase 5: config.yaml 更新
- **File**: `config.yaml`
- **変更**: `generator.max_tokens: 8192`, `poster.include_transcript: true`

### Phase 6: テスト更新
- **File**: `tests/test_poster.py`, `tests/test_merger.py`, `tests/test_pipeline.py`
- **変更**: 既存テスト修正（正規表現・フィールド名） + 新規テスト追加（8-10件）

## Execution Order

```
[Phase 1] prompts/minutes.txt     ─┐
[Phase 2] merger.py                ─┤─→ [Phase 3] poster.py ─→ [Phase 4] pipeline.py
[Phase 5] config.yaml             ─┘
                                         ↓
                                   [Phase 6] tests
```

## Validation Gates

1. Phase 1-2 完了後: `pytest tests/test_merger.py` — 新関数のテスト通過
2. Phase 3 完了後: `pytest tests/test_poster.py` — Embed + ファイル投稿テスト通過
3. Phase 4-5 完了後: `pytest` — 全テスト通過
4. 最終: Docker rebuild + 実際の会議録音で動作確認

## Estimated Changes

| File | Lines added | Lines modified | Lines removed |
|------|-------------|----------------|---------------|
| prompts/minutes.txt | ~50 | 0 | ~48 |
| src/merger.py | ~40 | 0 | 0 |
| src/poster.py | ~40 | ~25 | ~5 |
| src/pipeline.py | ~8 | ~2 | 0 |
| config.yaml | 0 | 2 | 0 |
| tests/test_poster.py | ~60 | ~30 | ~5 |
| tests/test_merger.py | ~40 | 0 | 0 |
| tests/test_pipeline.py | ~15 | 0 | 0 |
| **Total** | **~253** | **~59** | **~58** |
