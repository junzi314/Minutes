---
description: Create GitHub Issues from RPI plan documentation
argument-hint: "<feature-slug>"
---

## User Input

```text
$ARGUMENTS
```

You **MUST** parse the user input to extract the feature slug (the folder name in `rpi/`).

## Purpose

This command creates GitHub Issues from RPI planning documentation and optionally adds them to a GitHub Project Board. It generates an epic issue for the feature and sub-issues for each implementation phase defined in PLAN.md.

**Prerequisites**:
- Feature folder exists at `rpi/{feature-slug}/`
- Planning completed (`rpi/{feature-slug}/plan/PLAN.md` exists)
- REQUEST.md exists (`rpi/{feature-slug}/REQUEST.md`)
- `gh` CLI is authenticated with `repo` scope

**This is Step 5 (Board) of the RPI Workflow** (after Step 4: Validate, before Step 6: Implement).

---

## Phases

### Phase 0: Load Context

**Process**:

1. **Read DECOMPOSE.md** from `rpi/DECOMPOSE.md`:
   - Find the row for `{feature-slug}` in the feature list tables
   - Extract: **順序**（例: MVP-1, Phase0-3）、**機能名**、**依存先**、**推定規模**（S/M/L/XL）
   - Identify the **フェーズラベル** from the table section header（Phase 0 → `Phase0`, MVP → `MVP`, MVP拡張 → `MVP-Extension`）

2. **Read REQUEST.md** from `rpi/{feature-slug}/REQUEST.md`:
   - Extract: 機能名、要件リスト（Must Have の `[←R-XX]` 番号）、受入基準

3. **Read PLAN.md** from `rpi/{feature-slug}/plan/PLAN.md`:
   - Extract: 各 Phase の名前、タスク一覧、成功基準
   - Count total phases

**Validation**:
- [ ] DECOMPOSE.md exists and feature-slug found in it
- [ ] REQUEST.md exists
- [ ] PLAN.md exists
- [ ] 順序、フェーズラベル、推定規模が特定できている

**Error Handling**:

- **PLAN.md が存在しない**: STOP → "PLAN.md not found. Run `/rpi:plan {feature-slug}` first."
- **REQUEST.md が存在しない**: STOP → "REQUEST.md not found. Create it first."
- **DECOMPOSE.md に feature-slug がない**: WARN → ユーザーに順序・フェーズ・規模を手動入力してもらう

---

### Phase 1: Verify Labels

**Process**:

1. Run `gh label list` to get existing labels
2. Verify required labels exist:

| 必要なラベル | 説明 | 色 |
|-------------|------|-----|
| `Phase0` | Phase 0: 技術検証 | `7057ff` |
| `MVP` | MVP: コア機能 | `0e8a16` |
| `MVP-Extension` | MVP拡張: 余裕次第 | `006b75` |
| `epic` | 親Issue（エピック） | `d93f0b` |
| `size:S` | 半日〜1日 | `c5def5` |
| `size:M` | 1〜3日 | `bfd4f2` |
| `size:L` | 3〜5日 | `0075ca` |
| `size:XL` | 1週間以上 | `b60205` |

3. If any label is missing, create it with `gh label create`

---

### Phase 2: Check for Existing Epic

**Process**:

1. Search for existing issues matching this feature:
   ```bash
   gh issue list --search "{順序}:" --label "epic" --json number,title
   ```

2. **If matching epic found**:
   - Report: "Epic issue #{number} already exists: {title}"
   - Ask user: "Update existing epic, or skip epic creation?"
   - If skip: proceed to Phase 3 using existing epic number

3. **If no matching epic**:
   - Proceed to create new epic

---

### Phase 3: Create Epic Issue

**Process**:

1. **Compose epic issue**:

   - **Title**: `{順序}: {機能名}`
     - Example: `MVP-1: PWAカメラ撮影・境界線検出`

   - **Labels**: `{フェーズラベル}`, `epic`, `size:{推定規模}`
     - Example: `MVP,epic,size:XL`

   - **Body** (markdown):
     ```markdown
     ## feature-slug: `{feature-slug}`

     {機能名の説明（REQUEST.mdの概要セクションから）}

     ### 要件
     {Must Have 要件リスト with R-XX references}

     ### 依存先
     {DECOMPOSE.md から取得した依存先。なければ「なし」}

     ### 推定規模
     {S/M/L/XL}（{規模の説明}）

     ### 実装フェーズ（PLAN.md）
     {PLAN.md の各Phase名をチェックリスト形式で列挙}
     - [ ] Phase 1: {名前}
     - [ ] Phase 2: {名前}
     - ...
     ```

2. **Create issue**:
   ```bash
   gh issue create --title "{title}" --body "{body}" --label "{labels}"
   ```

3. **Record epic issue number** for sub-issue references

**Output**: Epic issue number and URL

---

### Phase 4: Create Sub-Issues (Per PLAN.md Phase)

**Process**:

For each Phase in PLAN.md, create a sub-issue:

1. **Compose sub-issue**:

   - **Title**: `{順序}: Phase{N} {フェーズ名}`
     - Example: `Phase0-3: Phase1 テストフィクスチャ生成`

   - **Labels**: `{フェーズラベル}`, `size:S`
     - Sub-issues default to `size:S` unless the phase is clearly larger

   - **Body** (markdown):
     ```markdown
     Part of #{epic-number}

     ## Phase {N}: {フェーズ名}

     ### タスク
     {PLAN.md からタスクテーブルをコピー}

     ### 成功基準
     {PLAN.md から成功基準をコピー}
     ```

2. **Create issue**:
   ```bash
   gh issue create --title "{title}" --body "{body}" --label "{labels}"
   ```

3. **Collect all created issue numbers**

**Output**: List of sub-issue numbers and URLs

---

### Phase 5: Project Board (Optional)

**Process**:

1. **Attempt to list projects**:
   ```bash
   gh project list --owner {org}
   ```

2. **If successful**: Add epic and sub-issues to the project board
3. **If auth error** (missing `read:project` scope):
   - Skip silently
   - Include manual instruction in completion report:
     ```
     Project Board への追加には追加の認証スコープが必要です:
     gh auth refresh -s read:project,project
     ```

---

## Completion Report

```
## Board 完了

### エピック
- #{epic-number}: {title} ({URL})

### サブ Issue
| # | タイトル | URL |
|---|---------|-----|
| #{N} | {title} | {URL} |
| #{N} | {title} | {URL} |
...

### ラベル
- フェーズ: {フェーズラベル}
- サイズ: size:{推定規模}

### Project Board
- {追加済み / スキップ（スコープ不足）}

### 次のステップ
1. Issue の内容を確認: `gh issue view #{epic-number}`
2. 実装開始: `/rpi:implement {feature-slug}`
```

---

## Sub-Agent Delegation

このコマンドはエージェント委任を行わない。全処理を `gh` CLI で直接実行する。

---

## Error Handling

**gh CLI が未インストール**:
- Action: Stop
- Message: "`gh` CLI が見つかりません。インストールしてください: https://cli.github.com/"

**gh 認証エラー**:
- Action: Stop
- Message: "`gh auth login` を実行してください。必要なスコープ: `repo`"

**Issue 作成失敗**:
- Action: エラーを報告し、残りのIssue作成を続行
- Message: "Issue #{N} の作成に失敗: {error}. 残りを続行します。"

**PLAN.md にフェーズがない**:
- Action: エピックのみ作成（サブIssueなし）
- Message: "PLAN.md にフェーズ構造が見つかりません。エピックのみ作成しました。"

---

## Notes

- **When to Use**: `/rpi:plan` 完了後、`/rpi:implement` の前
- **Part of RPI Workflow**: Step 5 of 7 (Decompose → Describe → Research → Plan → Validate → **Board** → Implement)
- **Idempotent**: 同名エピックが既に存在する場合は重複作成しない
- **Project Board**: `read:project` + `project` スコープが必要。不足時はスキップ

### Best Practices

1. **DECOMPOSE.md を確認**: 順序・ラベルが正しいことを確認
2. **重複チェック**: 既存 Issue と重複しないか確認してから実行
3. **ラベル確認**: 必要なラベルが存在するか事前確認

---

## Command Examples

### 単一機能のIssue作成

```bash
/rpi:board pwa-camera-capture
```

### 検証タスクのIssue作成

```bash
/rpi:board verification-ocr-accuracy
```
