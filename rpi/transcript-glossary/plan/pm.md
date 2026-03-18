# Product Requirements: 用語辞書（Transcript Glossary）

**Feature**: Whisper文字起こし誤認識の辞書ベース自動補正
**Traceability**: REQUEST.md (transcript-glossary)
**Status**: Plan
**Date**: 2026-03-17

---

## 1. Context and Why Now

Discord Minutes Botは Craig Bot録音をWhisper (faster-whisper large-v3) で文字起こしし、Claude APIで議事録を生成する。Whisperは一般的な日本語・英語に対して高い認識精度を持つが、以下のカテゴリの語彙で体系的な誤認識が発生する:

- **固有名詞**: 社名、プロダクト名（例: 「TOONIQ」->「ツーニック」）
- **人名**: 漢字の当て字誤り、カタカナ表記の揺れ
- **専門用語**: 業界固有の略語、技術用語
- **社内用語**: ギルド固有のプロジェクト名、コードネーム

これらの誤認識は毎回同じパターンで繰り返されるため、辞書ベースの置換で高い費用対効果が見込める。transcript-correction-ui（DEFER判定済み）の80%のニーズを、10%のコストでカバーする軽量代替案である。

**Why Now**:
- template-customization, speaker-analyticsが完了し、パイプライン拡張パターンが確立された
- 既存のStateStore guild_settings、Configパターン、スラッシュコマンドパターンをそのまま再利用できる
- 新規依存・アーキテクチャ変更なしで実装可能（推定4-6時間）

---

## 2. Users and Jobs-to-Be-Done

| User | Job | Outcome |
|------|-----|---------|
| ギルド管理者 | 頻出する誤認識を辞書に登録したい | 登録以降の全議事録で同じ誤りが自動補正される |
| 会議参加者 | 自分の名前やプロジェクト名が正しく表記されてほしい | 議事録の可読性・信頼性が向上する |
| Bot運用者 | 辞書機能を有効/無効にしたい | config.yamlのフラグで制御できる |

### User Stories

**US-1: 辞書エントリの追加**
ギルド管理者として、`/minutes glossary add <誤認識> <正しい表記>` で辞書エントリを追加したい。次回の議事録生成からその補正が自動適用されるように。

**US-2: 辞書エントリの削除**
ギルド管理者として、不要になった辞書エントリを `/minutes glossary remove <誤認識>` で削除したい。誤って登録したエントリを修正できるように。

**US-3: 辞書一覧の確認**
ギルド管理者として、`/minutes glossary list` で現在の辞書を確認したい。登録済みの補正ルールを把握できるように。

**US-4: 自動補正の適用**
会議参加者として、辞書に登録された語彙が文字起こし後に自動補正されてほしい。議事録で固有名詞が正しく表記されるように。

**US-5: 機能の無効化**
Bot運用者として、`transcript_glossary.enabled: false` で辞書補正を無効化したい。トラブル時にパイプラインから切り離せるように。

---

## 3. Business Value

| Dimension | Impact |
|-----------|--------|
| **議事録品質** | 固有名詞の正確性向上。議事録の信頼性に直結 |
| **ユーザー満足度** | 「名前が間違っている」は最頻出のフィードバック。辞書で永続的に解決 |
| **運用コスト** | 一度登録すれば以降は自動。手動修正の繰り返しが不要 |
| **追加コスト** | ゼロ。新規APIコール・外部依存・インフラ不要 |
| **差別化** | 競合のOSS議事録Botにカスタム語彙機能は稀。Otter.ai等は有料プランで提供 |
| **戦略的位置づけ** | transcript-correction-ui (DEFER) の80%を10%のコストでカバーする代替案 |

---

## 4. Functional Requirements

### FR-1: 辞書データ構造

ギルドごとに `{誤認識パターン: 正しい表記}` のマッピングを保持する。

**Acceptance Criteria:**
- [ ] AC-1.1: 辞書は `dict[str, str]` 形式で、キーが誤認識パターン、値が正しい表記
- [ ] AC-1.2: ギルドごとに独立した辞書を持つ（guild_id をキーとして分離）
- [ ] AC-1.3: 辞書が空の場合、補正処理はスキップされる（パフォーマンスへの影響なし）
- [ ] AC-1.4: エントリの重複登録（同一キー）は上書きとして扱う

### FR-2: 自動補正（パイプライン統合）

transcribe完了後、merge前にセグメントテキストに対して辞書置換を適用する。

**Acceptance Criteria:**
- [ ] AC-2.1: `apply_glossary(segments, glossary)` が Segment リストの各 text フィールドを辞書で置換する
- [ ] AC-2.2: 置換はデフォルトで大文字小文字を区別しない（case_sensitive オプションで制御可能）
- [ ] AC-2.3: Segment は frozen dataclass のため、置換時は新しい Segment インスタンスを生成する
- [ ] AC-2.4: `pipeline.py` の `_stage_transcribe()` 後、`merge_transcripts()` 前に挿入される
- [ ] AC-2.5: `cfg.transcript_glossary.enabled` が `false` の場合、辞書適用をスキップする
- [ ] AC-2.6: guild_id はパイプラインの output_channel から取得する

### FR-3: `/minutes glossary add` コマンド

辞書にエントリを追加するスラッシュコマンド。

**Acceptance Criteria:**
- [ ] AC-3.1: `/minutes glossary-add <wrong> <correct>` で辞書エントリを追加する
- [ ] AC-3.2: 追加成功時、ephemeral メッセージで `"<wrong>" -> "<correct>" を追加しました` と表示
- [ ] AC-3.3: 既存キーへの上書き時、`"<wrong>" を更新しました（旧: "<old>" -> 新: "<correct>"）` と表示
- [ ] AC-3.4: `manage_guild` 権限が必要。権限不足時はエラーメッセージを返す

### FR-4: `/minutes glossary remove` コマンド

辞書からエントリを削除するスラッシュコマンド。

**Acceptance Criteria:**
- [ ] AC-4.1: `/minutes glossary-remove <wrong>` で辞書エントリを削除する
- [ ] AC-4.2: 削除成功時、ephemeral メッセージで `"<wrong>" を削除しました` と表示
- [ ] AC-4.3: 存在しないキーの場合、エラーメッセージ `"<wrong>" は辞書に登録されていません` を返す
- [ ] AC-4.4: `manage_guild` 権限が必要

### FR-5: `/minutes glossary list` コマンド

現在の辞書を一覧表示するスラッシュコマンド。

**Acceptance Criteria:**
- [ ] AC-5.1: Embed 形式で辞書の全エントリを表示する
- [ ] AC-5.2: 辞書が空の場合、`辞書にエントリがありません` と表示する
- [ ] AC-5.3: エントリが多い場合、Discord Embed の文字数制限 (4096文字) に収まるよう切り詰める
- [ ] AC-5.4: ephemeral メッセージとして送信する
- [ ] AC-5.5: フッターに `/minutes glossary-add で追加` の案内を表示する

### FR-6: 永続化

辞書データを StateStore 経由で永続保存する。

**Acceptance Criteria:**
- [ ] AC-6.1: `state/guild_settings.json` にギルドごとの辞書を保存する
- [ ] AC-6.2: `state_store.get_guild_glossary(guild_id)` で辞書を取得する
- [ ] AC-6.3: `state_store.set_guild_glossary(guild_id, glossary)` で辞書を保存する
- [ ] AC-6.4: Bot再起動後も辞書が保持される
- [ ] AC-6.5: 辞書の読み書きはアトミック書き込み（既存の `_flush()` パターン）で行う

### FR-7: Config セクション

`config.yaml` に glossary セクションを追加する。

**Acceptance Criteria:**
- [ ] AC-7.1: `TranscriptGlossaryConfig(enabled: bool = True, case_sensitive: bool = False)` frozen dataclass
- [ ] AC-7.2: `_SECTION_CLASSES` に登録し、YAML から自動ロードされる
- [ ] AC-7.3: `cfg.transcript_glossary` でアクセス可能
- [ ] AC-7.4: `config.yaml` に `transcript_glossary:` セクションを追加（`enabled: true`, `case_sensitive: false`）

### FR-8: テストカバレッジ

全コンポーネントに対してユニットテスト・統合テストを追加する。

**Acceptance Criteria:**
- [ ] AC-8.1: `tests/test_glossary.py` -- `apply_glossary` のユニットテスト（空辞書、単一置換、複数置換、大文字小文字、元テキスト未変更時のパススルー）
- [ ] AC-8.2: `tests/test_state_store.py` -- `get_guild_glossary` / `set_guild_glossary` のテスト
- [ ] AC-8.3: `tests/test_pipeline.py` -- パイプライン統合テスト（glossary有効/無効）
- [ ] AC-8.4: `tests/test_config.py` -- `TranscriptGlossaryConfig` YAML ロードテスト
- [ ] AC-8.5: 既存テストスイートが全パスすること（リグレッションなし）

---

## 5. Non-Functional Requirements

### NFR-1: Performance

- **置換計算量**: O(N x M)（N = セグメント数、M = 辞書エントリ数）。典型的な会議（200セグメント x 100エントリ）で < 10ms。パイプライン全体（Whisper数分 + Claude API数秒）に対して無視可能。
- **メモリ**: 辞書100エントリで約5KB。guild_settings.json のロードは O(1) dict lookup。

### NFR-2: Scale

- 辞書エントリ数の実用的上限は数百。1000エントリでも置換は < 100ms で問題ない。
- guild_settings.json はフラットJSON。数十ギルド x 数百エントリでもファイルサイズは数十KB。

### NFR-3: Reliability

- 辞書補正は非クリティカル。`apply_glossary` が例外を発生させた場合、パイプラインは補正なしで続行すべき（fault-tolerant 統合）。
- guild_settings.json の破損時は空辞書にフォールバック（StateStore の既存パターン）。

### NFR-4: Security

- `re.escape()` でパターン文字列をエスケープし、正規表現インジェクションを防止。
- スラッシュコマンドは `manage_guild` 権限ガード付き。一般メンバーは辞書を変更できない。
- 辞書の値（置換先テキスト）はDiscord Embedに表示されるため、Markdownインジェクションのリスクは低い（Embedはレンダリング制限あり）。

### NFR-5: Privacy

- 辞書データはサーバーローカルの `state/guild_settings.json` に保存。外部送信なし。
- 辞書エントリ自体は機密情報を含まない想定（社名・人名等の表記補正）。

### NFR-6: Observability

- `apply_glossary` 実行時にログ出力: 適用エントリ数と置換回数。
- スラッシュコマンドの実行は既存の discord.py ログで追跡可能。
- パイプラインの既存ステージログ（`[transcribe]`, `[merge]`）の間に `[glossary]` ログを追加。

### NFR-7: Backward Compatibility

- `enabled: true` がデフォルトだが、辞書が空なら何もしない。既存ギルドの動作は一切変更されない。
- `config.yaml` に `transcript_glossary:` セクションがなくてもデフォルト値で動作する。
- StateStore の guild_settings.json に `glossary` キーがないギルドは空辞書として扱う。

---

## 6. Scope

### In Scope (v1)

- `src/glossary.py` 新規モジュール: `apply_glossary()` 関数
- `src/state_store.py` 拡張: `get_guild_glossary()` / `set_guild_glossary()`
- `src/pipeline.py` 拡張: transcribe後・merge前にglossary適用を挿入
- `src/config.py` 拡張: `TranscriptGlossaryConfig` dataclass
- `config.yaml` 拡張: `transcript_glossary:` セクション追加
- `bot.py` 拡張: `/minutes glossary-add`, `glossary-remove`, `glossary-list` コマンド
- テスト: `test_glossary.py` (新規) + 既存テストファイルへの追加
- 大文字小文字を区別しない置換（デフォルト）
- 大文字小文字区別オプション (`case_sensitive` config)

### Out of Scope (v2以降)

- **正規表現パターンマッチ**: v1は単純文字列置換のみ。正規表現対応はユーザーニーズ確認後
- **辞書のインポート/エクスポート (JSON)**: 管理UIの拡張として将来対応
- **補正ログ**: 何が何に補正されたかの記録・表示。observability拡張として将来対応
- **辞書のサジェスト**: 頻出する未登録語の自動検出。ML/統計的アプローチが必要で別機能
- **whole_words_only オプション**: 短い語が長い語に含まれる問題の対策。v1はユーザーが辞書エントリで回避
- **辞書エントリの一括操作**: 複数エントリの同時追加/削除
- **Webダッシュボード**: スラッシュコマンドで十分。UIは将来検討

---

## 7. Success Metrics

### Leading Indicators

| Metric | Target | Measurement |
|--------|--------|-------------|
| テスト全パス | 既存 + 新規テスト 100% green | `pytest` CI |
| 辞書機能が本番で有効 | config.yaml で `enabled: true` | 設定ファイル確認 |
| 辞書エントリ登録率 | マージ後14日で1ギルド以上がエントリ登録 | `state/guild_settings.json` の glossary エントリ数 |

### Lagging Indicators

| Metric | Target | Measurement |
|--------|--------|-------------|
| 固有名詞誤認識の繰り返し報告 | 定性: 同一語彙の誤認識報告ゼロ | ユーザーフィードバック |
| 辞書エントリの活用率 | マージ後30日で辞書登録ギルドの90%以上が継続利用 | guild_settings.json のエントリ推移 |
| パイプラインエラー (glossary起因) | 0件 | ログ監視: `[glossary]` ステージのエラー |

---

## 8. Rollout Plan

| Phase | Action | Effort | Prerequisites |
|-------|--------|--------|---------------|
| Phase 1 | `src/glossary.py` + `tests/test_glossary.py` 実装 | 2h | なし |
| Phase 2 | `state_store.py`, `config.py`, `config.yaml` 拡張 | 1h | Phase 1 |
| Phase 3 | `pipeline.py` 統合 + パイプラインテスト | 1h | Phase 2 |
| Phase 4 | `bot.py` スラッシュコマンド追加 | 1h | Phase 2 |
| Phase 5 | 全テスト実行 + リグレッション確認 | 0.5h | Phase 3, 4 |
| Phase 6 | PR作成 + レビュー + マージ | 0.5h | Phase 5 |
| Phase 7 | 本番デプロイ + ドッグフーディング | 1 week | Phase 6 |

**Total estimated effort**: 4-6時間 (Phase 1-6)

---

## 9. Risks and Open Questions

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **意図しない部分一致置換** -- 短い辞書エントリが長い語の一部にマッチする（例: 辞書「AI」が「MAIL」の中の「AI」を置換） | Medium | Medium | v1はユーザーが辞書エントリで回避（より具体的なパターンを登録）。v2で `whole_words_only` オプションを追加 |
| **辞書の肥大化** -- エントリが際限なく増える | Very Low | Low | 実用的には数十〜数百エントリ。必要に応じてエントリ数上限を追加（v2） |
| **正規表現インジェクション** -- ユーザー入力がre.subに渡される | Low | Low | `re.escape()` で全パターンをエスケープ。正規表現メタ文字は無効化される |
| **guild_settings.json の同時書き込み** -- 複数コマンドが同時実行される | Very Low | Low | StateStoreはシングルスレッド。asyncioイベントループ内で逐次実行されるため競合なし |
| **apply_glossaryの例外がパイプラインを停止** | Low | Medium | fault-tolerant統合: try/except で補正失敗時はログ出力して続行 |

### Open Questions

1. **置換順序の保証**: 辞書エントリの適用順序は辞書のキー順（挿入順）とするか、長いパターン優先とするか? -> v1は挿入順。問題が発生したらv2で長いパターン優先に変更。
2. **辞書エントリ数の上限**: 上限を設けるべきか? -> v1は上限なし。実用的に問題になるのは数千エントリ以上で、現実的にそこまで増えることは稀。
3. **大文字小文字のデフォルト**: case_insensitive をデフォルトとしたが、日本語（Unicode）の case folding で予期しない動作はないか? -> 日本語にはcase概念がないため影響なし。英語の固有名詞補正で有用。

---

## 10. Implementation Map

| Component | File | Changes | Type |
|-----------|------|---------|------|
| Glossary engine | `src/glossary.py` | `apply_glossary()` 関数 ~50行 | 新規 |
| State persistence | `src/state_store.py` | `get_guild_glossary()`, `set_guild_glossary()` ~30行 | 変更 |
| Pipeline integration | `src/pipeline.py` | glossary適用の挿入 ~10行 | 変更 |
| Configuration | `src/config.py` | `TranscriptGlossaryConfig` ~15行 | 変更 |
| YAML config | `config.yaml` | `transcript_glossary:` セクション ~3行 | 変更 |
| Slash commands | `bot.py` | `glossary-add`, `glossary-remove`, `glossary-list` ~60行 | 変更 |
| Unit tests | `tests/test_glossary.py` | apply_glossary テスト ~100行 | 新規 |
| Integration tests | `tests/test_state_store.py`, `test_pipeline.py`, `test_config.py` | 各ファイルに数テスト追加 | 変更 |

**Total**: ~150行新規 + ~120行変更 + ~100行テスト

---

## 11. Dependencies

- **External**: なし。新規パッケージ不要（`re` は標準ライブラリ）。
- **Internal**: `StateStore` (guild_settings), `Segment` dataclass, `Config` loader, `/minutes` コマンドグループ -- すべて既存。
- **Upstream blockers**: なし。即時着手可能。
