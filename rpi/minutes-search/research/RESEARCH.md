# Research Report: minutes-search

**Feature**: 過去議事録検索
**Date**: 2026-03-17
**Recommendation**: **GO**
**Confidence**: **High**

---

## Executive Summary

minutes-search は、生成済み議事録をキーワードで全文検索する Discord スラッシュコマンド機能。現在の議事録はDiscordに投稿されるのみで、過去の議事録を探すのが困難。この機能は会議ワークフローの「取得」フェーズを完成させ、市場の競合ツール（Otter.ai, Fireflies, Fellow）が標準で提供する機能に追いつく。技術的リスクは低く、sqlite3（stdlib）のFTS5で日本語全文検索を実現でき、外部依存は不要。推定工数2-3日。Ext-5（external-export）のアンブロックにもなる。

---

## Feature Overview

| 項目 | 値 |
|------|-----|
| Feature Name | minutes-search（過去議事録検索） |
| Type | Enhancement — New capability (slash command + data layer) |
| Target Components | `src/state_store.py`, `src/pipeline.py`, `bot.py`, `state/` |
| Complexity | **Medium** (2-3 days) |
| Dependencies (upstream) | なし |
| Dependencies (downstream) | external-export (Ext-5) をアンブロック |

---

## Requirements Summary

### Must Have
- FR-1: 生成した議事録を自動的にローカルアーカイブに永続保存
- FR-2: `/minutes search <keyword>` でキーワード全文検索
- FR-3: 検索結果をEmbed形式で表示（日付、参加者、マッチ箇所スニペット）

### Nice to Have
- FR-4: 日付範囲フィルタリング (`--after`)
- FR-5: 話者名フィルタリング
- FR-6: 検索結果から元の議事録メッセージへのリンク

### Non-Functional
- 100件のアーカイブで検索応答1秒以内
- ギルド間データ漏洩禁止（guild-scoped storage）
- 「minimal state」原則に準拠（SQLite or JSON）

---

## Product Analysis

### User Value: **High**
- 会議ライフサイクル（capture → publish → **retrieve**）の最終段階を完成
- 会議回数の増加に比例して価値が増大する機能
- 代替手段（Discord内検索）はEmbed内容やファイル添付を検索できず不十分

### Market Fit: **Strong**
- 検索可能な議事録アーカイブは全主要競合が標準提供する機能
- この機能がないことは明確なプロダクトギャップ

### Strategic Alignment: **Excellent**
- パイプライン出力の新しい消費者（アーカイブ）を追加するのみ。パイプライン自体は変更なし
- multi-guild対応済みのアーキテクチャに自然に統合可能
- Ext-5（external-export）の前提条件を満たす

### Product Viability Score: **HIGH**

---

## Technical Discovery

### Current State Summary

| コンポーネント | 現状 | Gap |
|--------------|------|-----|
| StateStore (`state_store.py`) | JSON永続化、`{transcript_hash: minutes_md}` でキャッシュ | メタデータなし（guild_id, date, speakers未保存） |
| Pipeline (`pipeline.py`) | 生成時に全メタデータが利用可能（L97-120） | メタデータを永続化していない |
| Bot (`bot.py`) | `/minutes` コマンドグループ（5サブコマンド） | search コマンドなし |
| minutes_cache.json | transcript hash → markdown（1件のみ） | 検索不能、ギルド未紐付け |
| processing.json | rec_id → 処理状態（11件） | minutes_md への逆引きなし |

### Critical Insights
1. **議事録は揮発的**: 11件の処理記録に対しキャッシュは1件のみ。トランスクリプトハッシュが一致しない限り議事録は失われる
2. **ギルドコンテキスト欠落**: guild_id が議事録と共に保存されていない
3. **日付メタデータは脆弱**: 日付はMarkdown本文内にのみ存在（構造化メタデータなし）
4. **セグメントデータ破棄**: Segment[]（話者・タイミング情報）はマージ後に破棄される

### Integration Points
- **書込み**: `pipeline.py` L117（`put_cached_minutes()` 直後）— 全メタデータがスコープ内
- **読取り**: `bot.py` `/minutes` グループに `search` サブコマンド追加
- **既存パターン**: autocomplete（L487-495）、Embed構築（`poster.py`）が再利用可能

---

## Technical Analysis

### Implementation Approaches

| Approach | 概要 | Pros | Cons |
|----------|------|------|------|
| **A: JSON + in-memory search** | JSON に全文保存、Python substring検索 | 既存パターンと整合、依存なし | O(N)フルスキャン、ランキングなし、メモリ増大 |
| **B: SQLite FTS5** (**推奨**) | SQLiteにメタデータ+FTS5全文検索インデックス | BM25ランキング、日本語trigram対応、フィルタリング容易 | 第2の永続化メカニズム追加 |
| C: Hybrid (ファイル+JSON index) | 個別.mdファイル + JSONインデックス | 人間可読 | I/O重い、FTSなし |

### Recommended Approach: **SQLite FTS5 with trigram tokenizer**

**理由**:
1. `sqlite3` は stdlib（外部依存ゼロ）。FTS5は Python 3.10+ の SQLite に同梱確認済み
2. `trigram` トークナイザーは日本語CJKテキストに最適（形態素解析不要）
3. BM25ランキング、フレーズマッチ、プレフィックスマッチが標準機能
4. 日付/話者フィルタリングは標準SQL WHERE句で実現
5. SQLiteファイルは「ファイルベースの永続化」原則に合致

### Architecture Decision

| 決定事項 | 推奨 | 理由 |
|---------|------|------|
| クラス構成 | 独立 `MinutesArchive` クラス（新規 `src/minutes_archive.py`） | StateStore（JSON永続化）とは責務が異なる。単一責任原則 |
| FTS tokenizer | `trigram` | CJKテキストに最適、MeCab不要 |
| ギルド分離 | 必須（WHERE guild_id = ?） | セキュリティ要件 |
| アーカイブ書込み | ブロッキングだが耐障害性あり（try/except） | 失敗しても議事録投稿は継続 |
| 既存キャッシュ | 維持（重複排除目的は別） | minutes_cache.json は dedup 用、archive は retrieval 用 |

### Schema Design
```sql
-- メタデータテーブル
CREATE TABLE minutes_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    source_label TEXT,
    date_str TEXT NOT NULL,
    speakers TEXT,
    channel_name TEXT,
    template_name TEXT DEFAULT 'minutes',
    transcript_hash TEXT,
    minutes_md TEXT NOT NULL,
    message_id INTEGER,  -- Nice to Have: 元メッセージへのリンク
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- FTS5 全文検索インデックス
CREATE VIRTUAL TABLE minutes_fts USING fts5(
    minutes_md, speakers,
    content='minutes_archive',
    content_rowid='id',
    tokenize='trigram'
);
```

### Complexity Breakdown

| タスク | 工数 |
|--------|------|
| SQLite schema + MinutesArchive クラス | 0.5d |
| Pipeline 統合（メタデータ永続化） | 0.25d |
| `/minutes search` スラッシュコマンド | 0.5d |
| 検索結果Embed構築 | 0.25d |
| テスト（unit + integration） | 0.5-1d |
| 既存データ移行 | 0.25d |
| **合計** | **2-3d** |

### Technical Risks

| リスク | 確率 | 影響 | 対策 |
|--------|------|------|------|
| 日本語トークナイゼーション品質 | 中 | 中 | `trigram` トークナイザー使用（CJK最適） |
| Discord 3秒インタラクションタイムアウト | 低 | 中 | `interaction.response.defer()` で安全ネット |
| SQLite並行アクセス | 低 | 低 | WALモード + asyncio.to_thread() |
| アーカイブ書込み失敗で投稿阻害 | 低 | 中 | try/except で fault-tolerant に |
| WSLでのSQLiteファイル破損（DrvFS） | 中 | 中 | stateディレクトリと同じ場所に配置 |

### Performance

| 指標 | 見込み |
|------|--------|
| Archive write latency | <1ms per row |
| FTS5 search (1,000 rows) | <10ms |
| Memory footprint | ディスクバック（起動時メモリ不要） |
| Index size (1,000 entries) | ~9MB |

---

## Strategic Recommendation

### Decision: **GO**
### Confidence: **High**

### Rationale
1. **プロダクト適合**: 会議ライフサイクルの最終段階を完成させる自然な拡張
2. **技術的実現性**: sqlite3 stdlib + FTS5 で外部依存ゼロ。既存パターンに準拠
3. **リスク最小**: 検索は読取り専用。アーカイブ失敗はパイプラインに影響なし
4. **下流効果**: Ext-5（external-export）をアンブロック
5. **データ損失防止**: 現在は議事録が事実上揮発的。早期実装で今後のデータ保全

### Conditions
1. SQLite FTS5 の `trigram` トークナイザーが日本語で正常動作することをテストで確認
2. アーカイブ書込み失敗がパイプラインをブロックしない設計（fault-tolerant）
3. 既存 `minutes_cache.json` は維持（dedup用途は別）

---

## Next Steps

1. **Review this report**
2. **Proceed to planning**: `/rpi:plan minutes-search`
3. **Key decisions to resolve during planning**:
   - Phase分割（Core search → Polish/filtering）
   - 既存キャッシュデータの移行方針（backfill vs start fresh）
   - Embed結果の ephemeral vs public
   - ページネーション方式（ボタン vs 件数制限）

---

## Appendix: Clarifying Questions (from Phase 1)

以下は Planning フェーズで解決すべき項目:

1. **アーカイブ形式**: SQLite FTS5 推奨（本レポートで解決済み）
2. **既存キャッシュ移行**: 1件のみ存在。メタデータをMarkdownから部分抽出して移行推奨
3. **検索スコープ**: コマンド実行ギルドに限定（guild isolation 必須）
4. **Ephemeral応答**: 推奨（議事録内容のプライバシー保護）
5. **インデックス更新**: アーカイブ書込み時に同期更新（FTS5のcontent-sync機能）
6. **元メッセージリンク**: Nice to Have。`post_minutes()` の返り値 `message.id` を保存すれば実現可能
7. **並行書込み**: SQLite WALモードで対応。実運用上は単一ライター
