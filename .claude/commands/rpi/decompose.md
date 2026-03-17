---
description: Decompose a project proposal into individual feature REQUEST.md files
argument-hint: "<path/to/proposal.md>"
---

## User Input

```text
$ARGUMENTS
```

You **MUST** parse the user input to extract the path to the project proposal file.

**Expected Input Format**: `path/to/proposal.md`

**Example**: `docs/product-plan.md`

## Purpose

This command decomposes a full project proposal into individual feature-level `REQUEST.md` files, each sized for independent RPI workflow execution. It ensures no requirements are lost, maintains traceability via `[←R-XX]` numbering, and produces a dependency-aware execution order.

**Key Objectives**:
- Extract ALL requirements from the original proposal without loss — **網羅性が最優先**
- Split into independently implementable feature units（個数制限なし。精度のために必要な数だけ生成する）
- Preserve terminology, nuance, and constraints from the original
- Map dependencies and recommend implementation order
- Generate traceability matrix linking every requirement to a feature
- **検証タスク（Phase 0 等）を独立した REQUEST.md として切り出す**
- **横断・非機能要件を共通制約として構造化する**
- **Q&A・付録に埋め込まれた確定仕様を漏れなく抽出する**

**Prerequisites**:
- Proposal file exists at the specified path
- `rpi/` directory exists

**Output Location**:
- `rpi/DECOMPOSE.md` — Traceability matrix and execution order
- `rpi/{feature-slug}/REQUEST.md` — Individual feature specifications (one per feature)

**This is Step 0 (Decompose) of the RPI Workflow** (before Step 1: Describe).

---

## Phases

### Phase 0: Load Proposal and Project Context

**Process**:

1. **Read proposal file** from the specified path (required)
2. **Read CLAUDE.md** from project root (required — provides tech stack and architecture context)
3. **Check for existing `rpi/` contents** — warn if feature folders already exist
4. **Load project constitution** (if exists):
   - Look for a constitution or principles document in the repository
   - Extract constraints and design principles

**Validation**:
- [ ] Proposal file exists and is readable
- [ ] CLAUDE.md exists and loaded
- [ ] Project context established
- [ ] Existing rpi/ state checked

**If proposal file is missing**: STOP and report: "Proposal file not found at `{path}`. Please provide the correct path."

---

### Phase 1: 要件の構造化抽出

**Goal**: 企画書から全要件を漏れなく抽出し、番号付きリストにする。

**Agent**: requirement-parser

**Process**:

1. **Launch requirement-parser agent** with proposal content and project context

2. **Agent analyzes** — 以下の**全カテゴリ**から要件を抽出する:
   - 機能要件（ユーザーができること）
   - 非機能要件（性能、セキュリティ、アクセシビリティ）
   - UI/UX要件（画面、フロー、インタラクション）
   - データ要件（スキーマ変更、マイグレーション）
   - 外部連携要件（API、サービス）
   - ビジネス要件（制約、ルール、ブランディング）
   - 技術的制約（使用技術、禁止事項）
   - **検証・実測タスク**（「Phase 0で決定」「要検証」「実測で決定」等の文言をトリガーに抽出）
   - **デモ・運用要件**（デモシナリオ、運用体制、リセット手順、感情設計等）
   - **Q&A・付録の確定仕様**（本文だけでなく、Q&A・確認事項・付録に埋め込まれた決定済み仕様も抽出対象）

   **矛盾解決ルール**: 本文とQ&A/付録で内容が矛盾する場合は、**日付やバージョンが新しい方（通常はQ&A側）を確定仕様として優先する**。矛盾があった事実も記録する。

3. **Agent provides**:
   - 全要件の番号付きリスト（R-01, R-02, ... 形式）
   - 各要件のカテゴリ分類
   - 優先度の推定（企画書内の表現に基づく：「必須」「最重要」→ P0、「あれば良い」「将来」→ P2）
   - **企画書の既存フェーズ/ロードマップ構造**の検出（例: 「MVP」「Phase 2」「Phase 3」等のフェーズ区分があればそのまま記録）
   - **検証タスクリスト**（「Phase 0で決定」等から抽出した技術検証項目）
   - **未決事項リスト**（🔴マーク・「未決」「未定」等のステータスの項目）
   - 曖昧な要件や矛盾の指摘

4. **Review parsing results**:
   - If clarifying questions exist, **STOP and ask user** before proceeding
   - If proposal sections are ambiguous, list them and request clarification

**出力**: 番号付き要件リスト（全要件を網羅）、検証タスクリスト、未決事項リスト

**Validation**:
- [ ] 全カテゴリの要件が抽出されている（機能要件だけでなく検証・デモ・Q&A由来も含む）
- [ ] 各要件に R-XX 番号が付与されている
- [ ] 優先度が推定されている
- [ ] Q&A・付録セクションも抽出対象として処理されている
- [ ] 本文とQ&Aの矛盾が検出・解決されている（該当する場合）
- [ ] 検証タスクが識別されている（該当する場合）
- [ ] 未決事項が識別されている（該当する場合）
- [ ] 曖昧な要件がユーザーに確認済み（該当する場合）

---

### Phase 2: 機能単位への分割

**Goal**: 要件リストを独立して実装可能な機能単位に分割する。

**Agent**: product-manager

**Process**:

1. **Launch product-manager agent** with:
   - Phase 1 の番号付き要件リスト
   - プロジェクトコンテキスト（CLAUDE.md）
   - 憲法的制約（存在する場合）

2. **Agent analyzes**:
   - **既存フェーズ構造の検出**（最優先）:
     - 企画書にフェーズ/ロードマップ/優先度の階層構造があるか確認
     - ある場合 → そのフェーズ区分を**そのまま保持**し、フェーズ内で細分化する
     - ない場合 → P0/P1/P2 のフラット優先度を新たに割り当てる
   - **分割の基準**:
     - 1つの画面またはユーザーフローにまとまるもの
     - 1つのデータモデル/APIエンドポイントに集約されるもの
     - 他の機能と独立してテスト・デプロイできるもの
     - **個数・粒度に数値的な制限は設けない**。精度（情報の漏れなさ）を最優先する
     - 1〜3日は目安であり強制ではない。OCRエンジン統合のような複雑なロジックは1週間規模でも1つのREQUESTとして認める
   - **分割してはいけないもの**:
     - 分割すると意味が壊れる一連のフロー
     - 共有状態に強く依存する機能群
   - **特殊な切り出しルール**:
     - **検証タスク**: 企画書内の「Phase 0で決定」「要検証」「実測で決定」をトリガーに、`verification-{slug}` として独立したREQUEST.mdを生成する（例: `verification-ocr-accuracy`, `verification-scanic-boundary`）
     - **デモシナリオ**: デモフロー全体（ステップ構成・感情設計・運用制約・リセット手順）を `demo-scenario` として独立したREQUEST.mdにまとめる。個別機能に分散させない
     - **横断・非機能要件**: 特定機能に紐付かない要件（性能目標、オフライン動作、KPI等）は個別REQUESTに入れず、Phase 3で共通制約として構造化する

3. **Agent provides**:
   - 機能分割リスト（各機能に `feature-slug`（英語ケバブケース）を付与）
   - 各機能への要件マッピング（R-XX → feature-slug）
   - 依存関係マップ（先行・後続・並行可能）
   - 循環依存がないことの確認
   - **優先度の決定**（2パターン）:

     **A. 企画書に既存フェーズがある場合（階層型）**:
     - 企画書のフェーズ名を第1レベルとして保持（例: MVP, Phase 2, Phase 3...）
     - 各フェーズ内で実装順のサブ番号を付与（例: MVP-1, MVP-2, MVP-3...）
     - サブ番号は依存関係・技術的難易度・ユーザー価値を考慮して決定
     - 企画書で「余裕があれば」「将来検討」とされた要件は元のフェーズ区分を尊重

     **B. 企画書にフェーズ構造がない場合（フラット型）**:
     - **P0**: プロダクトの核。これがないと成立しない
     - **P1**: ユーザー体験を大きく向上。P0の後に実装
     - **P2**: あると良い。P0/P1完了後に検討

4. **Review split results**:
   - If any R-XX requirement is unassigned, **flag it explicitly**
   - 検証タスクが `verification-{slug}` として切り出されているか確認
   - デモシナリオが `demo-scenario` として独立しているか確認
   - 横断要件が個別REQUESTではなく共通制約に回されているか確認

**出力**: 機能分割リスト（slug、機能名、要件マッピング、依存関係、優先度）、検証タスクリスト、横断要件リスト

**Validation**:
- [ ] 全要件がいずれかの機能に割り当てられている（横断要件は共通制約へ）
- [ ] 循環依存がない
- [ ] 優先度が設定されている
- [ ] 企画書に既存フェーズ構造がある場合、それが保持されている
- [ ] フェーズ内のサブ順序が依存関係と整合している
- [ ] 検証タスクが `verification-*` として独立している（該当する場合）
- [ ] デモシナリオが `demo-scenario` として独立している（該当する場合）

---

### Phase 2.5: 技術的分割妥当性の検証

**Goal**: コードベースの実態を踏まえて分割の妥当性を確認する。

**Agent**: Explore (via Task tool with subagent_type="Explore")

**Process**:

1. Launch Explore agent via Task tool with `subagent_type="Explore"`

**Explore Agent Prompt**:
```
Analyze the codebase to validate the proposed feature decomposition.

Proposed features and their target components:
[List feature-slugs with estimated target components from Phase 2]

Investigate and document:

1. **Component Existence**
   - Do the target components/files actually exist?
   - What is the current state of each component?
   - What functions/classes are relevant?

2. **Integration Points**
   - What imports/dependencies exist between components?
   - Where would features share code or state?
   - What APIs or interfaces connect components?

3. **Code Conflicts**
   - Would any two features modify the same file sections?
   - Are there shared utilities that multiple features would change?
   - What merge conflicts could arise from parallel implementation?

4. **Reusability**
   - What existing patterns, utilities, or components can be reused?
   - What shared infrastructure is already available?
   - What would need to be built from scratch?

5. **Split Validation**
   - Are any proposed features too tightly coupled in code to separate?
   - Are any features that should be split further based on code structure?
   - What is the recommended implementation order based on code dependencies?

Provide a discovery summary with specific file paths and code references.
```

2. **Review Explore results and adjust splits**:
   - コード構造上、統合すべき機能があれば統合
   - 技術的に分離すべき機能があれば分割
   - 各機能の対象コンポーネントを実ファイルパスで確定

**出力**: 検証済み機能分割リスト、対象コンポーネントの確定

**Validation**:
- [ ] 全機能の対象コンポーネントが実ファイルパスで特定されている
- [ ] 機能間のコード競合が検出・解消されている
- [ ] 分割調整が必要な場合は Phase 2 の結果を更新済み

---

### Phase 3: 共通コンテキストと共通制約の定義

**Goal**: 全 REQUEST.md に含める共通情報と、横断的な非機能要件（共通制約）を定義する。

**Process**:

1. **共通コンテキストの構成**:
   - プロジェクト名と概要（CLAUDE.md から）
   - 技術スタック（CLAUDE.md から）
   - 設計原則（企画書から抽出）
   - 用語集（企画書内の専門用語の定義）
   - 既存コードベース概要（CLAUDE.md のディレクトリ構成から）

2. **用語の統一**:
   - 企画書内で使われる全専門用語をリスト化
   - 各用語の定義を確定
   - 全 REQUEST.md で統一して使用する表現を決定

3. **共通制約（Cross-cutting Concerns）の構造化**:

   特定の機能に紐付かない横断的な要件を以下のカテゴリで整理する。各 REQUEST.md はこのセクションを参照する形を取る。

   - **パフォーマンス**: 処理速度目標、レスポンスタイム（例: 「OCR 3秒以内」）
   - **UX・感情設計**: 全体を通じたUX方針、デモで狙う感情（例: 「安堵感最優先」）
   - **技術スタック指定**: 特定ライブラリの使用指定（例: vaul, sonner, natural-compare-lite）
   - **オフライン・PWA**: Service Worker、キャッシュ戦略
   - **アクセシビリティ**: 画面回転ロック、フォント指定等
   - **デザインシステム**: カラー体系、QCアラート色、フォント規則
   - **セキュリティ**: APIキー管理、Edge Functions制約
   - **KPI・成功指標**: デモ完遂率、名刺獲得数等（受入基準に影響）

4. **未決事項の収集**:
   - 企画書内の 🔴マーク、「未決」「未定」「要確認」ステータスの項目を一覧化
   - 各未決事項がどの機能に影響するかをマッピング

**出力**: 共通コンテキストテンプレート、統一用語集、共通制約リスト、未決事項リスト

**Validation**:
- [ ] 共通コンテキストにプロジェクト概要・技術スタック・設計原則が含まれている
- [ ] 企画書の全専門用語が用語集に定義されている
- [ ] 用語の表記ゆれがない（同一概念に同一用語）
- [ ] 横断的な非機能要件が共通制約として構造化されている
- [ ] 未決事項が収集・マッピングされている

---

### Phase 4: REQUEST.md の生成

**Goal**: 各機能の REQUEST.md を生成し、ファイルとして保存する。

**Agent**: documentation-analyst-writer

**Process**:

各機能について、以下のフォーマットで `rpi/{feature-slug}/REQUEST.md` を生成する:

```markdown
# {機能名}

## 共通コンテキスト

- **プロジェクト**: {プロジェクト名} — {1文の概要}
- **技術スタック**: {CLAUDE.md の技術スタック}
- **設計原則**: {企画書から抽出した方針をカンマ区切りで}
- **用語集**: {この機能に関連する専門用語: 定義}

## この機能について

### フェーズ・優先度
- **企画書フェーズ**: {企画書での元フェーズ名（例: MVP, Phase 2）。フェーズ構造がない場合は「—」}
- **実装順序**: {フェーズ内サブ番号（例: MVP-2）。フラット構造の場合は P0/P1/P2}

### 概要
{2-3文で機能の目的と価値を説明}

### 元企画書からの該当箇所
> {元企画書の該当セクションをそのまま引用。複数セクションにまたがる場合は全て引用する}

### 要件

#### Must Have（実装必須）
- {要件} [←R-XX]
- {要件} [←R-XX]

#### Nice to Have（余裕があれば）
- {要件} [←R-XX]

### UI/UX
- {画面・フロー・インタラクションの要件}

### データ
- {スキーマ変更、新規モデル、マイグレーション}

### API
- {新規/変更エンドポイント}

### 非機能要件
- {性能、セキュリティ、アクセシビリティ}

### 適用される共通制約
- {DECOMPOSE.md の共通制約セクションから、この機能に関連するものを列挙}
- （例: 「パフォーマンス: OCR 3秒以内」「技術スタック: sonner でトースト通知」）

## 対象コンポーネント
- {Phase 2.5 のコード探索結果に基づく、影響を受けるファイル/ディレクトリ}

## 依存関係
- **先行（この機能の前に必要）**: {feature-slug} — {理由} / なし
- **後続（この機能が完了すると着手可能）**: {feature-slug} — {理由} / なし
- **並行可能**: {同時に実装できる feature-slug} / なし

## 受入基準
1. {ユーザー視点で検証可能な基準}
2. {ユーザー視点で検証可能な基準}
3. {ユーザー視点で検証可能な基準}

## 備考
- {実装上の注意点、リスク、前提条件}

## 元企画書セクション
- セクション名: {元企画書内の参照箇所}
```

**重要ルール**:
- 各要件の末尾に `[←R-XX]` を必ず付記（トレーサビリティの根拠）
- 「元企画書からの該当箇所」は原文をそのまま引用（要約しない）
- 共通コンテキストは全ファイルで同一の内容を使用
- 対象コンポーネントは Phase 2.5 の実コード探索に基づく

**Validation**:
- [ ] 全機能の REQUEST.md が生成されている
- [ ] 全 REQUEST.md に共通コンテキストが含まれている
- [ ] 全要件に `[←R-XX]` が付記されている
- [ ] 元企画書の引用が原文のまま
- [ ] 各 REQUEST.md が単独で理解可能

---

### Phase 5: DECOMPOSE.md の生成

**Goal**: 分解結果の全体マップとトレーサビリティマトリクスを生成する。

**Agent**: documentation-analyst-writer

**Process**:

`rpi/DECOMPOSE.md` に以下のフォーマットで保存する:

```markdown
# 企画書分解結果

## メタ情報
- **元企画書**: {ファイルパス}
- **分解日**: {日付}
- **総機能数**: {N}
- **総要件数**: {R-XX の総数}

## 機能一覧（推奨実装順序）

> **注**: 企画書に既存のフェーズ構造がある場合はそれを第1レベルとして保持し、フェーズ内でサブ番号を付与する（例: MVP-1, MVP-2...）。企画書にフェーズ構造がない場合は P0/P1/P2 のフラット構造を使用する。

**【パターンA: 企画書に既存フェーズがある場合】**

### {企画書のフェーズ名}（例: MVP / Phase 0）

| 順序 | feature-slug | 機能名 | 依存先 | 推定規模 | 要件数 |
|------|---|---|---|---|---|
| {フェーズ名}-1 | {slug} | {名前} | なし | S/M/L | {N} |
| {フェーズ名}-2 | {slug} | {名前} | {フェーズ名}-1 | S/M/L | {N} |
| {フェーズ名}-3 | {slug} | {名前} | なし | S/M/L | {N} |

### {次のフェーズ名}（例: Phase 2）

| 順序 | feature-slug | 機能名 | 依存先 | 推定規模 | 要件数 |
|------|---|---|---|---|---|
| {フェーズ名}-1 | {slug} | {名前} | {前フェーズ} 完了 | S/M/L | {N} |

**【パターンB: フラット構造の場合】**

| 順序 | feature-slug | 機能名 | 優先度 | 依存先 | 推定規模 | 要件数 |
|------|---|---|---|---|---|---|
| 1 | {slug} | {名前} | P0 | なし | S/M/L | {N} |
| 2 | {slug} | {名前} | P1 | #1 | S/M/L | {N} |

### 優先度・フェーズの定義

**パターンA（階層型）**: 企画書のフェーズ名をそのまま使用。フェーズ内のサブ番号は依存関係・技術的難易度・ユーザー価値で決定。

**パターンB（フラット型）**:
- **P0**: プロダクトの核となる機能。これがないと成立しない
- **P1**: ユーザー体験を大きく向上させる機能。P0の後に実装
- **P2**: あると良い機能。P0/P1完了後に検討

### 推定規模の定義
- **S**: 半日〜1日（単一コンポーネントの変更）
- **M**: 1〜3日（複数ファイルの変更、新規API追加など）
- **L**: 3〜5日（新機能の追加、複数コンポーネントの連携）
- **XL**: 1週間以上（複雑なロジック統合。精度維持のために分割しない判断）

## 共通制約（Cross-cutting Concerns）

特定機能に紐付かない横断的な要件。各 REQUEST.md はこのセクションを参照する。

### パフォーマンス
| 指標 | 目標値 | 根拠（企画書セクション） |
|------|--------|----------------------|
| {指標} | {目標値} | §{セクション番号} |

### UX・感情設計
- {全体を通じたUX方針、デモで狙う感情}

### 技術スタック指定
| ライブラリ/技術 | 用途 | 根拠 |
|---------------|------|------|
| {名前} | {用途} | §{セクション} |

### オフライン・PWA
- {Service Worker、キャッシュ戦略}

### デザインシステム
- {カラー体系、QCアラート色、フォント規則}

### セキュリティ
- {APIキー管理、Edge Functions制約}

### KPI・成功指標
| 指標 | 目標値 | 測定方法 |
|------|--------|---------|
| {指標} | {目標値} | {方法} |

## トレーサビリティマトリクス

| 要件番号 | 要件の概要 | 割当先 feature-slug | カバー |
|---|---|---|---|
| R-01 | {要件概要} | {feature-slug} | 完全/部分 |
| R-02 | {要件概要} | {feature-slug} | 完全/部分 |

## 未割当の要件

{すべて割り当て済みの場合}:
なし（全要件が割り当て済み）

{未割当がある場合}:
| 要件番号 | 要件の概要 | 未割当の理由 |
|---|---|---|
| R-XX | {要件概要} | {理由: スコープ外/情報不足/要確認} |

## 未決事項（Open Items）

企画書内で未確定（🔴・未決・未定）とされている項目。実装前に確定が必要。

| ID | 項目 | 現在のステータス | 影響する機能 | 企画書参照 |
|---|---|---|---|---|
| OPEN-01 | {未決事項} | 🔴 未決 | {feature-slug} | Q{番号} / §{セクション} |

## 推奨ワークフロー

以下の順序で `/rpi:research` を実行してください:

> **注**: 企画書に既存フェーズがある場合、ワークフローもそのフェーズ構造に従う。各フェーズ内のサブ順序は依存関係に基づく。

### {企画書のフェーズ名}（例: MVP）

**{フェーズ名}-1**（依存なし・最初に着手）:
```
/rpi:research rpi/{slug-1}/REQUEST.md
```

**{フェーズ名}-2**（{フェーズ名}-1 完了後）:
```
/rpi:research rpi/{slug-2}/REQUEST.md
```

**{フェーズ名}-3**（並行可能）:
```
/rpi:research rpi/{slug-3}/REQUEST.md
```

### {次のフェーズ名}（例: Phase 2 — 前フェーズ完了後に着手）

**{フェーズ名}-1**:
```
/rpi:research rpi/{slug-4}/REQUEST.md
```

## 整合性チェックリスト
- [ ] 全要件がトレーサビリティマトリクスに記載されている
- [ ] 未割当の要件が0件、または理由が明記されている
- [ ] 各 REQUEST.md の依存関係が矛盾していない（循環依存なし）
- [ ] 推奨ワークフローの順序が依存関係と整合している
- [ ] 共通コンテキストが全 REQUEST.md で統一されている
- [ ] 共通制約セクションに横断・非機能要件が構造化されている
- [ ] 検証タスクが `verification-*` として独立している（該当する場合）
- [ ] デモシナリオが `demo-scenario` として独立している（該当する場合）
- [ ] 未決事項が一覧化され、影響する機能にマッピングされている
- [ ] Q&A・付録由来の確定仕様が漏れなく抽出されている
```

**Validation**:
- [ ] DECOMPOSE.md が生成されている
- [ ] トレーサビリティマトリクスに全 R-XX 番号が含まれている
- [ ] 未割当要件が明示されている（0件でも記載）
- [ ] 共通制約セクションが構造化されている
- [ ] 未決事項セクションが作成されている
- [ ] 推奨ワークフローが依存関係と整合している
- [ ] 整合性チェックリストが全て PASS

---

### Phase 6: 最終検証

**Goal**: 分解結果の品質を自己検証する。

**Process**:

1. **漏れチェック**: Phase 1 の全 R-XX 番号がいずれかの REQUEST.md に出現するか（横断要件は共通制約に含まれていればOK）
2. **重複チェック**: 同一要件が複数の REQUEST.md に重複していないか（共有要件は最も関連の深い1つに割り当て、他は依存関係で参照）
3. **循環依存チェック**: 依存関係グラフに循環がないか
4. **用語統一チェック**: 全 REQUEST.md で同じ概念に同じ用語を使っているか
5. **検証タスクチェック**: 「Phase 0で決定」等の検証項目が `verification-*` REQUEST.md に含まれているか
6. **共通制約チェック**: 横断的な非機能要件が DECOMPOSE.md の共通制約セクションに構造化されているか
7. **未決事項チェック**: 企画書内の未決・未定項目が DECOMPOSE.md の未決事項セクションに収集されているか
8. **Q&A仕様チェック**: Q&A・付録で確定した仕様が該当する REQUEST.md に反映されているか

**問題が見つかった場合**: Phase 4-5 に戻って修正してから完了する。

---

## Sub-Agent Delegation

| Phase | Agent | Type | Purpose |
|-------|-------|------|---------|
| Phase 1 | requirement-parser | Custom | 企画書からの要件抽出 |
| Phase 2 | product-manager | Custom | 機能分割と優先度決定 |
| Phase 2.5 | Explore | Built-in (Task tool) | コードベース探索 |
| Phase 3 | (orchestrator) | — | 共通コンテキスト定義 |
| Phase 4 | documentation-analyst-writer | Custom | REQUEST.md 生成 |
| Phase 5 | documentation-analyst-writer | Custom | DECOMPOSE.md 生成 |

### Agent Invocation

**Custom Agents** (requirement-parser, product-manager, documentation-analyst-writer):
- Claude Code automatically detects these from `.claude/agents/`
- Reference them naturally: "Acting as the requirement-parser agent..."
- NO Task tool invocation needed

**Built-in Agents** (Explore):
- Launch via Task tool with `subagent_type="Explore"`
- Provide detailed prompt with specific investigation targets

### Context Management for Large Proposals

**If the proposal exceeds 500 lines**:
1. Phase 1 (要件抽出) should process the proposal in logical sections (by chapter/heading)
2. Maintain a running requirement counter (R-XX) across sections
3. After all sections are processed, consolidate and deduplicate
4. This prevents context overflow while ensuring completeness

---

## Completion Report

### Decomposition Summary

**Proposal**: {file path}
**Features Generated**: {N} features
**Total Requirements**: {M} requirements (R-01 〜 R-{M})

**Traceability**:
- 割当済み: {X}/{M} 要件 ({Y}%)
- 未割当: {Z} 要件
- 共通制約: {N} カテゴリ
- 未決事項: {N} 件
- 検証タスク: {N} 件

### Generated Files

```
rpi/
├── DECOMPOSE.md                              ← トレーサビリティ + 共通制約 + 未決事項
├── {feature-slug-1}/REQUEST.md               ← {機能名1}
├── {feature-slug-2}/REQUEST.md               ← {機能名2}
├── verification-{slug-1}/REQUEST.md          ← {検証タスク1}
├── demo-scenario/REQUEST.md                  ← デモシナリオ（該当する場合）
└── ...
```

### Feature Overview

| # | feature-slug | 種別 | 機能名 | 優先度 | 規模 |
|---|---|---|---|---|---|
| 1 | {slug} | 機能 | {名前} | {フェーズ}-1 | M |
| 2 | {slug} | 検証 | {名前} | {フェーズ}-2 | S |
| 3 | demo-scenario | デモ | デモシナリオ | {フェーズ}-N | L |

### Next Steps

1. **分解結果のレビュー**:
   - `rpi/DECOMPOSE.md` でトレーサビリティを確認
   - 各 `REQUEST.md` の内容を確認

2. **RPI ワークフローの実行**:
   - DECOMPOSE.md の推奨ワークフロー順に実行
   - 最初の機能: `/rpi:research rpi/{first-slug}/REQUEST.md`

---

## Error Handling

**If proposal file doesn't exist**:
- Action: Stop and inform user
- Message: "Proposal file not found at `{path}`. Please provide the correct path."

**If proposal is too short or vague**:
- Action: Stop and inform user
- Message: "Proposal contains insufficient detail for decomposition. Please provide a more detailed proposal."

**If CLAUDE.md doesn't exist**:
- Action: Warn but proceed
- Message: "CLAUDE.md not found. Proceeding without project context — target components will be estimated."

**If rpi/ already contains feature folders**:
- Action: Warn and ask user
- Message: "Existing feature folders found in rpi/. Overwrite? (y/n)"

---

## Notes

- **When to Use**: When you have a full project proposal covering multiple features
- **When NOT to Use**: When you already have a single, focused feature description — go directly to Step 1 (Describe)
- **Part of RPI Workflow**: Step 0 of 6 (**Decompose** → Describe → Research → Plan → Validate → Implement)
- **Information Preservation**: The `[←R-XX]` traceability system ensures no requirements are lost through the pipeline. `/rpi:validate` can later verify this.

---

## Post-Completion Action

**IMPORTANT**: After completing decomposition, ALWAYS prompt the user to compact:

> **Context Management**: This decomposition consumed significant context. Please run:
>
> ```
> /compact
> ```
>
> Then proceed with the recommended workflow in `rpi/DECOMPOSE.md`.
