# Implementation Roadmap: minutes-search

**Feature**: 過去議事録検索
**Complexity**: Medium
**Phases**: 3
**Total Tasks**: 10

---

## Phase 1: Core Archive Module

**Goal**: MinutesArchive クラスと Config 拡張を実装し、単体テストで検証する

### Tasks

| # | タスク | ファイル | 複雑度 |
|---|--------|---------|--------|
| 1.1 | `MinutesArchiveConfig` dataclass 追加 | `src/config.py` | Low |
| 1.2 | `Config` に `minutes_archive` フィールド追加 + `_SECTION_CLASSES` 登録 | `src/config.py` | Low |
| 1.3 | `SearchResult` dataclass 定義 | `src/minutes_archive.py` | Low |
| 1.4 | `MinutesArchive.__init__()`: SQLite DB作成、FTS5テーブル+トリガー、WALモード | `src/minutes_archive.py` | Medium |
| 1.5 | `MinutesArchive.store()`: メタデータ+議事録をINSERT | `src/minutes_archive.py` | Medium |
| 1.6 | `MinutesArchive.search()`: FTS5 MATCH + BM25ランキング + snippet() | `src/minutes_archive.py` | Medium |
| 1.7 | `MinutesArchive.count()` + `MinutesArchive.close()` | `src/minutes_archive.py` | Low |
| 1.8 | Unit tests | `tests/test_minutes_archive.py` | Medium |

### 成功基準
- [ ] `store()` がメタデータ付きで議事録を保存できる
- [ ] `search()` が日本語キーワードで正しくマッチする（trigram tokenizer）
- [ ] ギルドID違いで検索結果が混ざらない（guild isolation）
- [ ] 空の検索結果が空リストを返す
- [ ] `MinutesArchiveConfig(enabled=True)` がデフォルトで生成される
- [ ] 全新規テスト + 既存テストがパスする

### 依存関係
- なし（新規モジュール + Config拡張のみ）

---

## Phase 2: Pipeline Integration

**Goal**: パイプラインにアーカイブ書込みを追加し、fault-tolerant に統合する

### Tasks

| # | タスク | ファイル | 複雑度 |
|---|--------|---------|--------|
| 2.1 | `run_pipeline_from_tracks()` に `archive` パラメータ追加 + `post_minutes()` 後にアーカイブ書込み | `src/pipeline.py` | Medium |
| 2.2 | `run_pipeline()` に `archive` パラメータ伝播 | `src/pipeline.py` | Low |
| 2.3 | `MinutesBot` に `archive` 属性追加 + 初期化 | `bot.py` | Low |
| 2.4 | パイプライン呼び出し箇所に `archive` 引数追加 | `bot.py` | Low |
| 2.5 | 統合テスト | `tests/test_pipeline.py` | Medium |

### 成功基準
- [ ] パイプライン成功時に `archive.store()` が正しいメタデータで呼ばれる
- [ ] `archive.store()` 失敗がパイプラインをブロックしない（fault-tolerant）
- [ ] `minutes_archive.enabled=False` 時にアーカイブ書込みが行われない
- [ ] `archive=None`（未初期化）でもパイプラインが正常動作する
- [ ] 既存テスト全パス（breaking changeなし）

### 依存関係
- Phase 1 完了が必須

---

## Phase 3: Search Command + UI

**Goal**: `/minutes search` スラッシュコマンドと検索結果Embedを実装する

### Tasks

| # | タスク | ファイル | 複雑度 |
|---|--------|---------|--------|
| 3.1 | `/minutes search` スラッシュコマンド追加 | `bot.py` | Medium |
| 3.2 | 検索結果Embed構築（スニペット、ランキング、件数表示） | `bot.py` | Medium |
| 3.3 | テスト（コマンドロジック + Embed構築） | `tests/test_bot.py` or inline | Low |

### 成功基準
- [ ] `/minutes search keyword:予算` で検索結果Embedが表示される
- [ ] 検索結果なしの場合に適切なメッセージが表示される
- [ ] アーカイブ未有効時に適切なメッセージが表示される
- [ ] 応答は ephemeral
- [ ] `asyncio.to_thread()` で検索がイベントループをブロックしない
- [ ] 全テストパス

### 依存関係
- Phase 1, 2 完了が必須

---

## Config YAML サンプル

```yaml
minutes_archive:
  enabled: true
  max_search_results: 5
```

---

## 変更影響範囲

| ファイル | 変更種別 | 影響 |
|---------|---------|------|
| `src/minutes_archive.py` | 新規 | なし |
| `src/config.py` | 修正（追加のみ） | Config構造拡張 |
| `src/pipeline.py` | 修正（optional パラメータ追加） | アーカイブ書込み追加 |
| `bot.py` | 修正（コマンド追加 + 初期化追加） | search サブコマンド追加 |
| `tests/test_minutes_archive.py` | 新規 | なし |
| `tests/test_pipeline.py` | 修正（テスト追加） | Config fixture 更新 |
| `config.yaml` | 修正（セクション追加） | なし |
| `state/minutes_archive.db` | 新規（自動生成） | なし |

---

## リスク管理

| リスク | 確率 | 影響 | 対策 |
|--------|------|------|------|
| FTS5 trigram 日本語品質 | 中 | 中 | Phase 1テストで早期検証 |
| Discord 3秒タイムアウト | 低 | 中 | `asyncio.to_thread()` + 必要なら `defer()` |
| SQLite 並行アクセス | 低 | 低 | WALモード |
| アーカイブ書込み失敗 | 低 | 中 | try/except で fault-tolerant |
| 既存テスト破損 | 低 | 中 | 全パラメータ optional、Config fixture 更新 |
