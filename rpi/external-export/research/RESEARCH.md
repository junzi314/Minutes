# Research Report: Notion/Google Docs 自動エクスポート (External Export)

**Feature Slug**: external-export
**Date**: 2026-03-17
**Recommendation**: CONDITIONAL GO
**Confidence**: 70% (Medium)

---

## Executive Summary

**Recommendation: CONDITIONAL GO** -- Proceed with Google Docs export only; defer Notion until demand is validated.

議事録生成後に Google Docs へ自動エクスポートする機能は、プロジェクトの設計原則（graceful degradation, pipeline-first）と整合し、既存の Google API 認証基盤（`google-api-python-client`, `google-auth`, Service Account credentials）を大幅に再利用できる。ただし、Google Docs API でのリッチな Markdown 変換には固有の制限（見出し・リスト・テーブルの表現力が Google Docs API の `batchUpdate` リクエスト構造に依存）があり、変換品質が期待値に達しないリスクがある。推定工数は Medium（200-350行の新規コード + 50-80行のテスト）であり、パイプラインの 6th stage として post_minutes の後に fire-and-forget で実行する設計が最も適切。条件として、(1) Google Docs API による Markdown 構造変換の PoC 検証（見出し・リスト・テーブルの変換品質確認）、(2) Google Drive API で使用中の Service Account に Google Docs API のスコープを追加可能であることの確認、が merge 前に必要。Notion 対応は Nice-to-Have として Phase 2 に延期する。

---

## 1. Feature Overview

| 項目 | 値 |
|------|-----|
| **Feature Name** | Notion/Google Docs 自動エクスポート (External Export) |
| **Type** | New Feature（パイプライン後処理ステージ追加 + 外部 API 統合） |
| **Target Components** | `src/exporter.py` (new), `src/pipeline.py`, `src/config.py`, `config.yaml`, `bot.py` |
| **Complexity** | Medium (Size M) -- 新規モジュール + 外部 API 統合 |
| **Traceability** | R-82 |
| **Implementation Order** | Ext-5 |
| **Enterprise Phase** | 今後の拡張候補（スコープ外） |

### Goals

1. 議事録生成後に Google Docs にドキュメントを自動作成する
2. Markdown の基本構造（見出し、リスト）を Google Docs 形式に変換する
3. エクスポート失敗時も Discord 投稿は正常に完了する（graceful degradation）
4. config.yaml でエクスポート先を有効/無効化できる
5. Discord Embed にエクスポート先 URL を追加表示する

---

## 2. Requirements Summary

### Must-Have [R-82]

1. **Google Docs 自動エクスポート** -- 議事録生成後に Google Docs にドキュメントを自動作成
2. **config.yaml 制御** -- エクスポート先を有効/無効化
3. **Graceful degradation** -- エクスポート失敗時も Discord 投稿は正常完了

### Nice-to-Have [R-82] (Phase 2 延期推奨)

4. Notion への自動エクスポート
5. エクスポート先フォルダ/データベースのギルドごと設定
6. `/minutes export <message_id>` で既存議事録の手動エクスポート

### Non-Functional

- エクスポート処理は 15 分 SLA に含めない（Discord 投稿後の後処理）
- エクスポート失敗がパイプライン全体を失敗させないこと
- 最大 3 回リトライ
- 認証情報は `.env` で管理

---

## 3. Product Analysis

### User Value: **Medium**

| 観点 | 評価 |
|------|------|
| **課題の深刻度** | 中。Discord 以外のプラットフォームで議事録にアクセスしたいユーザーにとっては明確なペイン。ただし Discord 投稿 + .md ファイル添付で主要なユースケースはカバー済み |
| **影響範囲** | 中。Google Workspace を併用しているチームに限定。Discord のみで完結しているチームには影響なし |
| **ユーザー体験** | 中-高。生成された議事録が自動的に Google Docs に現れることで、非 Discord ユーザーへの共有が容易になる |
| **即効性** | 低。Service Account のスコープ変更、Google Docs API の有効化、共有フォルダの設定が事前に必要 |
| **代替手段の存在** | あり。.md ファイルを手動で Google Docs にコピー/アップロードすることで同等の結果は得られる |

### Market Fit

議事録自動化ツール（Otter.ai、Fireflies.ai、tl;dv 等）の多くは Google Docs/Notion 連携を標準機能として提供しているが、これらは有料プランの付加価値として位置づけられていることが多い。Discord Bot としての差別化ポイントは録音から議事録生成までの自動化にあり、外部エクスポートは付加的な利便性機能。

### Strategic Alignment: **Partial**

| 設計原則 | 適合性 | 根拠 |
|----------|--------|------|
| Pipeline-first | ✅ | 投稿後の独立ステージとして挿入。既存パイプラインの変更は最小限 |
| Async by default | ✅ | Google Docs API 呼び出しを `asyncio.to_thread` で非同期実行 |
| Graceful degradation | ✅ | 要件に明記。エクスポート失敗は非致命的として処理 |
| Multi-guild support | ⚠️ | ギルドごとのエクスポート先設定は Nice-to-Have。初期実装はグローバル設定で可 |
| Minimal state | ⚠️ | エクスポート先 URL の永続化が必要になる可能性あり（Embed への URL 追加のため）。ただし archive テーブルの拡張で対応可能 |

### Product Viability Score: **6/10 -- MODERATE GO**

ユーザー価値はあるが、設定の前提条件が多く（Google Service Account のスコープ拡張、API 有効化、共有フォルダ設定）、「ゼロ設定で恩恵」の speaker-analytics とは対照的。ROI は中程度。

### Concerns

1. **設定の複雑さ** -- Google Docs API の有効化、Service Account へのスコープ追加、対象フォルダへの Service Account 共有権限付与が必要。技術的に可能だが、セルフホスティングユーザーにとってのハードル
2. **Markdown 変換品質** -- Google Docs API は HTML や plain text を挿入する API であり、Markdown をネイティブにサポートしない。見出し・リスト・テーブルの構造化変換には `documents.batchUpdate` の構造化リクエスト構築が必要
3. **元企画書での位置づけ** -- 「今後の拡張候補（スコープ外）」に分類されており、コア機能ではない

---

## 4. Technical Discovery

### Current State: Not Implemented (0%)

ワーキングツリーおよびコミット履歴にエクスポート関連のコードは存在しない。ただし、Google API 統合の基盤は `drive_watcher.py` に確立されている。

### Existing Infrastructure (Reusable)

#### Google API 認証基盤 (`src/drive_watcher.py`)

| Component | Reusability | Detail |
|-----------|-------------|--------|
| `google-api-python-client` | ✅ Full | `requirements.txt` に既存。`build("docs", "v1", ...)` で Google Docs API サービスを構築可能 |
| `google-auth` | ✅ Full | `Credentials.from_service_account_file()` パターンを流用 |
| Service Account JSON | ⚠️ Partial | 既存の `credentials.json` を流用可能だが、スコープ追加が必要（`drive.readonly` → `drive.readonly` + `docs`） |
| `_build_service()` パターン | ✅ Full | キャッシュ付きサービス構築パターンをそのまま適用 |

#### Pipeline 統合ポイント (`src/pipeline.py`, lines 146-161)

```
Stage 5: post_minutes() → message (discord.Message)
   ↓
Archive (fault-tolerant, lines 147-161)
   ↓
[NEW] Stage 6: export (fault-tolerant, same pattern as archive)
```

Archive ブロックのパターン（try/except で非致命的エラーをログに記録）をそのままエクスポートステージに適用できる。

#### Config パターン (`src/config.py`)

`_SECTION_CLASSES` への登録で YAML ローダーが自動処理するパターンが確立済み。`GoogleDriveConfig` と同様のパターンで `ExportConfig` を追加可能。

#### 議事録 Markdown 出力 (`src/generator.py`)

生成される `minutes_md` は以下の構造を持つ標準的な Markdown:

```markdown
# 会議議事録
- 日時: 2026-03-17 14:00
- 参加者: user1, user2

## まとめ
（テキスト、太字トピック）

## 詳細
* **要点**: 説明文 ([MM:SS])

## 推奨される次のステップ
- [ ] 担当者は〇〇を行います。
```

この構造を Google Docs API のリクエストに変換する必要がある。

### Google Docs API の技術的制約

Google Docs API (`documents.batchUpdate`) は構造化リクエストでドキュメントを操作する。Markdown を直接インポートする API は存在しない。変換に必要な主要オペレーション:

| Markdown 要素 | Google Docs API Request | 複雑度 |
|---------------|------------------------|--------|
| `# 見出し1` | `insertText` + `updateParagraphStyle(HEADING_1)` | Low |
| `## 見出し2` | `insertText` + `updateParagraphStyle(HEADING_2)` | Low |
| `**太字**` | `insertText` + `updateTextStyle(bold=True)` | Medium |
| `- リスト項目` | `insertText` + `createParagraphBullets(BULLET_DISC_CIRCLE_SQUARE)` | Medium |
| `- [ ] チェック` | `insertText` + `createParagraphBullets(CHECKBOX)` | Medium |
| テーブル | `insertTable` + `insertText` per cell | High |
| `([MM:SS])` タイムスタンプ | Plain text (変換不要) | None |

**判定**: 見出し・リスト・太字の基本変換は実現可能だが、完全な Markdown パーサーの実装は過剰。正規表現ベースの行指向パーサーで十分。テーブル変換は複雑度が高いため Phase 1 ではスキップし、テキスト表現にフォールバックする。

### 代替アプローチ: Google Drive Upload (Markdown ファイル)

Google Docs API でのリッチ変換の代わりに、`.md` ファイルを Google Drive にアップロードする簡易アプローチも選択肢:

| アプローチ | メリット | デメリット |
|-----------|--------|----------|
| **A: Google Docs API** (batchUpdate) | リッチなフォーマット、ブラウザで即表示 | 変換ロジックが複雑（100-150行）、API レート制限 |
| **B: Drive Upload** (.md file) | 実装が簡単（30行）、既存スコープで動作可能 | Google Docs でのプレビューが plain text、フォーマットなし |
| **C: Drive Upload** (HTML convert) | `mimeType: 'application/vnd.google-apps.document'` で自動変換 | Markdown → HTML 変換が別途必要。`markdown` ライブラリ追加 |

**推奨**: アプローチ C が最も バランスが良い。`markdown` Python ライブラリ（標準的、軽量）で Markdown → HTML に変換し、Google Drive API の `files.create` で `mimeType='application/vnd.google-apps.document'` を指定すれば、Google Drive が HTML → Google Docs に自動変換する。これにより Google Docs API のスコープ追加が不要になり、既存の `drive.file` スコープ（`drive.readonly` からの昇格は必要）で動作する。

### Notion API の技術的評価

| 項目 | 評価 |
|------|------|
| **依存追加** | `notion-client` (公式 Python SDK) の追加が必要。requirements.txt への追加 |
| **認証** | Notion API Key (Integration Token) -- Service Account とは別系統。`.env` での管理が必要 |
| **Markdown 変換** | Notion API は Markdown を直接サポートしない。Notion ブロック形式への変換が必要（`notion-client` が構造化 API を提供） |
| **複雑度** | Google Docs よりも高い。Notion のブロック構造（paragraph, heading, bulleted_list_item, to_do 等）への個別マッピングが必要 |
| **設定の前提条件** | Notion Integration の作成 + ワークスペースへの接続 + データベース/ページの共有設定 |

**判定**: Notion 対応は依存追加 + 認証系統の追加 + 変換ロジックの複雑度から、Phase 2 に延期が適切。

### Integration Points

```
src/pipeline.py (post_minutes 完了後)
  | (minutes_md: str, date: str, speakers: str)
  v
src/exporter.py (export_to_google_docs)
  | (Google Drive API: files.create with HTML conversion)
  v
src/poster.py (Embed にエクスポート URL を追加 -- optional follow-up)

src/config.py (ExportConfig)
  |
  v
src/pipeline.py (if cfg.export.google_docs_enabled)
```

### Code Conflicts: None Expected

- `pipeline.py` への追加は archive ブロックの後に配置するため、他の in-flight 変更との競合は低い
- 新規モジュール `src/exporter.py` で分離するため既存コードへの影響は最小限

---

## 5. Technical Analysis

### Feasibility Score: **7/10**

技術的には実現可能。ただし Markdown → Google Docs 変換の品質保証と、Service Account のスコープ変更に伴う運用上の確認が必要。

### Recommended Approach: Drive Upload with HTML Conversion (Approach C)

1. `markdown` Python ライブラリで `minutes_md` → HTML に変換
2. Google Drive API `files.create` で HTML をアップロード、`mimeType='application/vnd.google-apps.document'` 指定で Google Docs に自動変換
3. レスポンスから `webViewLink` を取得し、ログに記録（Embed への追加は follow-up）

#### メリット

- Google Docs API のスコープ追加が不要（`drive.file` のみ、既存 `drive.readonly` からの昇格は必要）
- Markdown → HTML 変換は確立されたライブラリ（`markdown`）で安定的に実現可能
- Google Drive の HTML → Docs 変換は Google 側で処理されるため、見出し・リスト・太字が適切に変換される
- 実装行数が少ない（推定 80-120 行）

#### デメリット

- `markdown` ライブラリの依存追加（ただし軽量、pure Python）
- Google Drive の HTML → Docs 自動変換の品質が Google 側の実装に依存
- Service Account の権限を `drive.readonly` → `drive.file` に昇格する必要がある（既存の Drive Watcher は read-only で動作しているため、権限変更の影響評価が必要）

### Implementation Plan

| Phase | Component | Lines (est.) | Description |
|-------|-----------|-------------|-------------|
| 1a | `src/config.py` | +15 | `ExportConfig` dataclass (`google_docs_enabled`, `google_docs_folder_id`, `credentials_path`) |
| 1b | `config.yaml` | +8 | `export:` セクション追加 |
| 1c | `src/exporter.py` | +80-120 | `GoogleDocsExporter` クラス（`_build_service`, `_convert_md_to_html`, `export`） |
| 1d | `src/pipeline.py` | +15-20 | エクスポートステージ追加（archive ブロックの後） |
| 1e | `src/errors.py` | +5 | `ExportError` 例外クラス追加 |
| 1f | `tests/test_exporter.py` | +60-80 | Google API モック + 変換テスト |
| **Total** | | **~200-270** | |

### Phase 2 (Future, separate feature)

| Component | Description |
|-----------|-------------|
| Notion export | `notion-client` 統合、ブロック変換ロジック |
| `/minutes export` command | 手動エクスポートの slash command |
| Embed URL 追加 | エクスポート先 URL を Discord Embed に表示 |
| ギルドごと設定 | `guild_settings.json` でギルド別エクスポート先を設定 |
| バッチエクスポート | `minutes_archive` から過去の議事録を一括エクスポート |

### Key Technical Decisions

1. **Drive Upload (HTML) vs Docs API (batchUpdate)**: Drive Upload with HTML conversion を推奨。理由: 実装が簡潔、Markdown → HTML 変換は確立されたライブラリで安定的、Google 側が HTML → Docs 変換を担当するためフォーマット品質が高い

2. **Service Account スコープ**: 既存の `drive.readonly` スコープでは書き込み不可。`drive.file` への昇格が必要。これは Drive Watcher の動作に影響しない（`drive.file` は `drive.readonly` の上位互換ではないが、Service Account レベルで複数スコープを付与可能）

3. **エクスポートタイミング**: Discord 投稿の後、archive の後に実行。タイムアウト SLA（15分）の外側。fire-and-forget パターンで、失敗しても他の処理に影響しない

4. **Notion 延期**: 依存追加（`notion-client`）、認証系統の追加（Notion API Key）、変換ロジックの複雑度から Phase 2 に延期。Google Docs のエクスポート基盤を確立した上で、同じ `Exporter` インターフェースで Notion を追加する設計が適切

### Complexity: **Medium**

| Factor | Assessment |
|--------|-----------|
| **新規コード量** | 200-270 行（新規モジュール + 統合コード + テスト） |
| **外部 API 統合** | Google Drive API（既存パターンの拡張） |
| **依存追加** | `markdown` ライブラリ（pure Python, 軽量） |
| **設定変更** | Service Account スコープの昇格（運用作業） |
| **テスト複雑度** | 中。Google API のモックが必要だが、`drive_watcher.py` のテストパターンを参考にできる |

---

## 6. Risks and Mitigations

| # | Risk | Impact | Probability | Mitigation |
|---|------|--------|-------------|------------|
| R1 | Google Drive HTML → Docs 自動変換の品質が期待に達しない | Medium | Medium | PoC で変換品質を事前検証。見出し・リスト・太字の変換結果を確認。品質が不十分な場合は Google Docs API の `batchUpdate` にフォールバック |
| R2 | Service Account スコープ変更によるセキュリティ影響 | Medium | Low | `drive.file` スコープはアプリが作成したファイルにのみ書き込み可。ユーザーの既存ファイルへのアクセスは不変。運用手順書に明記 |
| R3 | `markdown` ライブラリの追加による依存リスク | Low | Very Low | 成熟した pure Python ライブラリ（14年以上の歴史、4000+ stars）。セキュリティリスクは極めて低い |
| R4 | Google API レート制限 | Low | Low | 議事録生成頻度（日数回程度）は Google API の制限（60 req/min/user）を大幅に下回る。念のため 3 回リトライを実装 |
| R5 | Service Account の credentials.json が Google Docs API 有効化されていない | Medium | Medium | GCP コンソールで Google Docs API を有効化する必要あり。README/設定ガイドに手順を明記 |
| R6 | エクスポートが pipeline のレイテンシに影響 | Low | Low | fire-and-forget パターンで実行。pipeline のタイムアウト SLA の外側で実行 |
| R7 | Notion 対応の需要が不明確 | Low | Medium | Phase 1 で Google Docs のみ実装し、Notion は需要が確認されてから着手。設計時に Exporter インターフェースを抽象化しておくことで、将来の追加を容易にする |

---

## 7. Implementation Estimate

| Phase | Effort | Duration (est.) | Description |
|-------|--------|----------------|-------------|
| **PoC** | 2-3 hours | Day 1 | Google Drive API で HTML → Docs 自動変換の品質検証。Markdown サンプルから HTML 生成 → Drive Upload → Docs 変換結果確認 |
| **Phase 1** | 4-6 hours | Day 2-3 | `ExportConfig`, `GoogleDocsExporter`, pipeline 統合, テスト |
| **Phase 2** (future) | 6-8 hours | TBD | Notion 対応, `/minutes export` command, Embed URL 追加 |

### Dependencies

| Dependency | Type | Status | Action |
|-----------|------|--------|--------|
| `google-api-python-client` | Internal (existing) | ✅ Installed | None |
| `google-auth` | Internal (existing) | ✅ Installed | None |
| `markdown` | External (new) | Not installed | `pip install markdown` + requirements.txt 追加 |
| Google Docs API 有効化 | GCP Console | Unknown | GCP プロジェクトで API を有効化 |
| Service Account スコープ | GCP Console | `drive.readonly` | `drive.file` に昇格（または別スコープ追加） |
| Google Drive 対象フォルダ | Google Drive | N/A | エクスポート先フォルダを作成し、Service Account に書き込み権限を付与 |

---

## 8. Strategic Recommendation

### Decision: **CONDITIONAL GO**

### Confidence: **70% (Medium)**

### Rationale

| Factor | Assessment |
|--------|-----------|
| **Technical feasibility** | 7/10 -- 実現可能だが、Markdown → Google Docs 変換の品質に不確実性あり |
| **Risk of proceeding** | Medium -- 変換品質の PoC 検証で軽減可能 |
| **Risk of NOT proceeding** | Low -- 現状の Discord 投稿 + .md 添付で主要ユースケースはカバー済み |
| **Product value** | Medium -- Google Workspace 併用チームには明確な価値あり |
| **Strategic alignment** | Partial -- コア機能（録音→議事録→Discord 投稿）の外側の付加価値 |
| **Backward compatibility** | Perfect -- `export.google_docs_enabled: false` がデフォルト。既存デプロイメントに影響なし |
| **Breaking changes** | Zero |
| **Setup complexity** | Medium-High -- GCP コンソールでの API 有効化 + スコープ変更 + フォルダ設定が必要 |

### Why CONDITIONAL (Not Full GO)

1. **変換品質の不確実性**: Google Drive の HTML → Docs 自動変換が議事録 Markdown の構造（特に日本語見出し、チェックリスト `- [ ]`、タイムスタンプ参照）を適切に処理するかは PoC で検証が必要
2. **設定のハードル**: Service Account のスコープ変更は GCP コンソールでの手動操作が必要。セルフホスティングユーザーにとっての追加設定負担
3. **元企画書での位置づけ**: 「今後の拡張候補（スコープ外）」であり、コア機能の安定化や他の高優先度機能（template-customization, minutes-archive が実装済み）を優先すべき

### Conditions for Proceeding

| # | Condition | Type | Effort | Verification |
|---|-----------|------|--------|-------------|
| C1 | Google Drive API HTML → Docs 変換の PoC | Required | 2-3 hours | 実際の議事録 Markdown サンプルを HTML に変換し、Google Drive にアップロード。変換後の Google Docs で見出し・リスト・太字・チェックリストが適切に表示されることを確認 |
| C2 | Service Account のスコープ昇格可否確認 | Required | 30 min | 既存の credentials.json の Service Account で `drive.file` スコープを付与可能であること、および Drive Watcher の `drive.readonly` 動作に影響しないことを確認 |
| C3 | `markdown` ライブラリの依存追加評価 | Recommended | 15 min | Docker イメージサイズへの影響、セキュリティ評価（CVE チェック）を確認 |

---

## 9. Next Steps

Based on the CONDITIONAL GO recommendation:

1. **PoC 実施 (C1)**: 議事録 Markdown → HTML → Google Drive Upload → Docs 変換品質の検証スクリプトを作成・実行
2. **スコープ確認 (C2)**: GCP コンソールで Service Account のスコープ設定を確認
3. **GO/NO-GO 判定**: PoC 結果に基づき、変換品質が許容範囲内であれば GO。品質が不十分な場合は Google Docs API `batchUpdate` アプローチへの切り替え、または DEFER を検討
4. **Plan フェーズ**: GO 判定後、`/rpi:plan external-export` で詳細実装計画を作成
5. **実装**: Phase 1 (Google Docs のみ) → テスト → PR → Phase 2 (Notion, 需要に応じて)

---

## Appendix A: Architecture Diagram

```
[Current Pipeline]
  Craig/Drive → Download → Transcribe → Merge → Generate → Post → Archive
                                                              |
                                                              v
[Proposed Addition]                                    Export (fault-tolerant)
                                                              |
                                                        ┌─────┴─────┐
                                                        │ Phase 1   │
                                                        │ Google    │
                                                        │ Docs      │
                                                        └───────────┘
                                                              |
                                                        ┌─────┴─────┐
                                                        │ Phase 2   │
                                                        │ Notion    │
                                                        │ (future)  │
                                                        └───────────┘
```

## Appendix B: Config Schema (Proposed)

```yaml
export:
  # Google Docs export
  google_docs_enabled: false
  # Google Drive folder ID for exported documents
  google_docs_folder_id: ""
  # Path to service account credentials (same as google_drive.credentials_path)
  credentials_path: "credentials.json"
  # Maximum retry attempts for export API calls
  max_retries: 3
```

## Appendix C: Exporter Interface (Proposed)

```python
@dataclass(frozen=True)
class ExportResult:
    """Result of an export operation."""
    success: bool
    url: str | None = None
    error: str | None = None

class BaseExporter(ABC):
    """Abstract base for document exporters."""

    @abstractmethod
    async def export(self, minutes_md: str, title: str, metadata: dict) -> ExportResult:
        ...

class GoogleDocsExporter(BaseExporter):
    """Export minutes to Google Docs via Drive API."""
    ...

# Future:
# class NotionExporter(BaseExporter): ...
```

## Appendix D: Related Code References

| File | Content | Relevance |
|------|---------|-----------|
| `src/drive_watcher.py:101-128` | `_build_service()` -- Google API 認証パターン | Direct reuse |
| `src/drive_watcher.py:29-30` | `_SCOPES = ["drive.readonly"]` -- 現在のスコープ | Scope upgrade needed |
| `src/pipeline.py:146-161` | Archive ブロック（fault-tolerant パターン） | Pattern reuse for export |
| `src/config.py:126-133` | `GoogleDriveConfig` -- 類似 config dataclass | Pattern reuse |
| `src/config.py:166-177` | `_SECTION_CLASSES` -- YAML ローダー登録 | Registration point |
| `src/errors.py` | Custom exception hierarchy | Add `ExportError` |
| `requirements.txt:7-8` | `google-api-python-client`, `google-auth` | Existing dependencies |
| `src/minutes_archive.py:91-128` | `store()` -- アーカイブ書き込み | Export URL 永続化の拡張ポイント |
