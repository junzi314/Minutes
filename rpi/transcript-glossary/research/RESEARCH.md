# Research Report: 用語辞書（Transcript Glossary）

## Executive Summary

用語辞書機能は、Whisper文字起こしの誤認識をユーザー定義の辞書で自動補正する機能である。技術的にはパイプラインの自然な拡張であり、既存のStateStore・Segment・Config・スラッシュコマンドパターンをそのまま活用できる。新規依存なし、アーキテクチャ変更なし、実装工数1日以内。transcript-correction-ui（DEFER判定）の代替として、パイプライン再設計なしに80%のニーズをカバーする。**GO** を推奨。

## Feature Overview

- **Name**: 用語辞書（Transcript Glossary）
- **Type**: Enhancement（パイプライン拡張）
- **Components**: glossary.py (新規), pipeline.py, state_store.py, bot.py, config.py
- **Complexity**: Simple
- **Background**: transcript-correction-ui (DEFER) の軽量代替案

## Requirements Summary

### Functional Requirements

1. 辞書データ構造: `{誤認識パターン: 正しい表記}` のマッピング
2. 自動補正: transcribe後・merge前にセグメントテキストを置換
3. スラッシュコマンドCRUD: add / remove / list
4. ギルド単位の辞書: 各サーバーで独立
5. 永続化: StateStoreのguild_settings.jsonに保存

### Non-Functional Requirements

- パフォーマンス: 100-200セグメント × 100エントリ = O(n×m) で十分高速
- 後方互換: enabled=trueがデフォルト、辞書が空なら何もしない
- 権限: glossary操作にmanage_guild権限を要求

## Product Analysis

### User Value: **High**

- Whisperの固有名詞誤認識は最も頻繁なユーザー苦情
- 同じ誤りが毎回繰り返されるため、辞書で一度登録すれば永続的に解決
- 例: 「TOONIQ」→「ツーニック」、人名の漢字誤り

### Market Fit: **High**

- Whisper利用ツールの共通課題
- 競合ツール（Otter.ai, Fireflies等）は有料プランでカスタム語彙を提供
- OSS議事録Botでは差別化要素

### Strategic Alignment: **High**

- transcript-correction-ui (DEFER) の代替として明確な位置づけ
- パイプラインの「正確性」向上に直結
- 既存アーキテクチャの自然な拡張

## Technical Discovery

### Current State

| 領域 | 現状 | 活用可能性 |
|------|------|-----------|
| パイプライン | transcribe → merge の間に挿入ポイントあり (pipeline.py L83-94) | そのまま利用 |
| Segment | frozen dataclass — 新インスタンス生成で対応 | パターン確立済み |
| StateStore | guild_settings.json にテンプレート設定を保存済み | 同パターンで辞書保存 |
| スラッシュコマンド | /minutes グループにサブコマンド追加パターン確立 | そのまま利用 |
| Config | _SECTION_CLASSES に追加するだけ | そのまま利用 |
| 権限 | manage_guild パーミッションチェック既存 | そのまま利用 |

### Integration Points

1. `src/pipeline.py:83-94` — transcribe後、merge前に辞書適用を挿入
2. `src/state_store.py` — `get_guild_glossary()` / `set_guild_glossary()` 追加
3. `bot.py` — `/minutes glossary-add/remove/list` コマンド追加
4. `src/config.py` — `TranscriptGlossaryConfig` 追加

### Implementation Approach

```python
# src/glossary.py (新規, ~40行)
def apply_glossary(
    segments: list[Segment],
    glossary: dict[str, str],
    case_sensitive: bool = False,
) -> list[Segment]:
    """Apply glossary replacements to segment text."""
    if not glossary:
        return segments
    result = []
    for seg in segments:
        text = seg.text
        for pattern, replacement in glossary.items():
            if case_sensitive:
                text = text.replace(pattern, replacement)
            else:
                # Case-insensitive replacement
                import re
                text = re.sub(re.escape(pattern), replacement, text, flags=re.IGNORECASE)
        result.append(Segment(start=seg.start, end=seg.end, text=text, speaker=seg.speaker))
    return result
```

```python
# pipeline.py 挿入 (2行)
if cfg.transcript_glossary.enabled:
    glossary = state_store.get_guild_glossary(guild_id)
    if glossary:
        segments = apply_glossary(segments, glossary, cfg.transcript_glossary.case_sensitive)
```

### Risks

| Risk | Level | Mitigation |
|------|-------|------------|
| 意図しない置換（短い語が長い語に含まれる） | Low | whole_words_onlyオプション（v2で対応可） |
| 辞書が大きくなりすぎる | Very Low | 実用的には数十〜数百エントリで十分 |
| 正規表現インジェクション | Low | re.escape()で安全にエスケープ |
| guild_settings.json肥大化 | Very Low | 100エントリで~5KB、問題なし |

## Technical Analysis

**Feasibility**: **High**

- 全パターンが既存コードベースに存在
- 新規外部依存なし
- Segment immutability は新インスタンス生成で対応（確立済みパターン）

**Effort Estimate**: ~4-6時間

| コンポーネント | 工数 | 新規/変更 |
|---------------|------|----------|
| `src/glossary.py` | 1h | 新規 ~50行 |
| `src/state_store.py` | 0.5h | 変更 +30行 |
| `src/pipeline.py` | 0.5h | 変更 +10行 |
| `src/config.py` | 0.5h | 変更 +15行 |
| `bot.py` | 1h | 変更 +60行 |
| `config.yaml` | 0.5h | 変更 +5行 |
| `tests/test_glossary.py` | 1-2h | 新規 ~100行 |

**Total**: ~150行新規 + ~120行変更 + ~100行テスト

## Strategic Recommendation

**Decision**: **GO**

**Confidence**: **High** (95%)

**Rationale**:
1. 技術的に最も低リスクな機能拡張の一つ。既存パターンの再利用のみ。
2. ユーザー価値が高い（Whisper誤認識は最頻出の不満）。
3. transcript-correction-ui (DEFER) の80%を10%のコストでカバー。
4. 新規外部依存なし、アーキテクチャ変更なし。
5. 後方互換を完全に維持（辞書が空なら何もしない）。

## Next Steps

1. `/rpi:plan "transcript-glossary"` でプラン生成
2. 実装（4-6時間）
3. `pytest` で全テスト通過確認
4. PR作成
