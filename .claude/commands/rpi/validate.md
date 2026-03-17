---
description: Validate plan output against original proposal - detect nuance drift
argument-hint: "<feature-slug> <original-proposal-path>"
---

## User Input

```text
$ARGUMENTS
```

You **MUST** parse the user input to extract:
1. **feature-slug**: the folder name in `rpi/`
2. **original-proposal-path**: path to the original project proposal file

**Expected Input Format**: `{feature-slug} {path/to/original-proposal.md}`

**Example**: `timesheet-verification docs/product-plan.md`

## Purpose

This command validates that `/rpi:plan` output faithfully preserves the intent, nuances, priorities, and constraints of the original project proposal. It detects semantic drift that accumulates through the decomposition pipeline:

```
元企画書 → DECOMPOSE.md → REQUEST.md → plan/ (pm.md, ux.md, eng.md, PLAN.md)
```

Each transformation can introduce subtle drift. This command catches it before implementation begins.

**Prerequisites**:
- Feature folder exists at `rpi/{feature-slug}/`
- Plan completed: `rpi/{feature-slug}/plan/PLAN.md` exists
- REQUEST.md exists: `rpi/{feature-slug}/REQUEST.md`
- Original proposal file exists at the specified path
- (Optional) `rpi/DECOMPOSE.md` exists for traceability

**Output Location**: `rpi/{feature-slug}/plan/VALIDATE.md`

**This is Step 4 (Validate) of the RPI Workflow** (after Step 3: Plan, before Step 5: Implement).

---

## Phases

### Phase 0: Load All Sources

**Process**:

1. **Read original proposal** from the specified path (required)
2. **Read CLAUDE.md** from project root (required — provides architecture constraints for Phase 3)
3. **Read DECOMPOSE.md** from `rpi/DECOMPOSE.md` (optional — skip if not found)
4. **Read REQUEST.md** from `rpi/{feature-slug}/REQUEST.md` (required)
5. **Read all plan files** (required):
   - `rpi/{feature-slug}/plan/pm.md`
   - `rpi/{feature-slug}/plan/ux.md`
   - `rpi/{feature-slug}/plan/eng.md`
   - `rpi/{feature-slug}/plan/PLAN.md`

**Context Management**: If the original proposal exceeds 500 lines, focus on sections referenced in REQUEST.md's「元企画書からの該当箇所」and the proposal's table of contents to identify relevant sections. Load only the sections relevant to this feature.

**Validation**:
- [ ] Original proposal file exists and is readable
- [ ] CLAUDE.md exists and loaded
- [ ] REQUEST.md exists
- [ ] At least PLAN.md exists in plan/
- [ ] All files loaded into context

**If any required file is missing**: STOP and report which file is missing.

---

### Phase 1: 要件トレーサビリティ検証

**Goal**: 元企画書の全要件が plan/ まで欠落なく到達しているか。

**Agent**: requirement-parser

**Process**:

1. **Launch requirement-parser agent** with:
   - 元企画書の該当セクション
   - REQUEST.md
   - PLAN.md

2. **Agent analyzes**:
   - 元企画書から機能要件、非機能要件、UI/UX要件、データ要件、制約条件を抽出
   - 各要件に識別子を付与（REQUEST.md に `[←R-XX]` があればそれを使う）
   - 元企画書 → REQUEST.md → plan/ の各段階での要件の到達状況

3. **Agent provides**:
   - 元企画書の各要件が REQUEST.md に含まれているか
   - REQUEST.md の「元企画書からの引用」セクションが原文と一致しているか
   - REQUEST.md の各 Must Have 要件が PLAN.md のタスクにマッピングされているか
   - pm.md の受入基準が元の要件をカバーしているか
   - Nice to Have 要件が明示的にスコープ外にされているか、含まれているか
   - DECOMPOSE.md のトレーサビリティマトリクスとの整合（存在する場合）

4. **要件ごとの判定**:
   - **到達**: 元企画書 → REQUEST.md → plan/ で完全に追跡可能
   - **部分到達**: 存在するが一部が欠落・変質
   - **欠落**: plan/ に反映されていない

**出力**: 要件ごとに「到達 / 部分到達 / 欠落」を判定

**Validation**:
- [ ] 元企画書の全要件が識別・番号付けされている
- [ ] 各段階（企画書→REQUEST→plan）の到達状況が判定されている
- [ ] 欠落・部分到達の要件に理由が記載されている

---

### Phase 2: ニュアンス・トーン検証

**Goal**: 元企画書の意図・優先度・ニュアンスが plan/ で変質していないか。

**Agent**: product-manager

**Process**:

1. **Launch product-manager agent** with:
   - 元企画書の該当セクション
   - REQUEST.md
   - pm.md, ux.md, PLAN.md

2. **Agent analyzes**:
   - **優先順位のドリフト**: 元企画書で「最重要」「必須」「核心」と書かれた要件が PLAN.md で後半フェーズに回されていないか。逆に「将来検討」「あれば良い」が初期フェーズに入っていないか
   - **用語の一貫性**: 元企画書の専門用語・概念名が plan/ で別の言葉に置き換わっていないか
   - **制約条件の保存**: 元企画書の「〜してはいけない」「〜は避ける」が eng.md に反映されているか。ビジネス制約がスコープに反映されているか
   - **意図の保存**: 「なぜこの機能が必要か」の背景が pm.md に保存されているか。ユーザーストーリーが元のターゲットユーザーと一致しているか。ux.md のフローが元の想定体験と一致しているか

3. **Agent provides**:
   - 項目ごとに「一致 / 軽微なドリフト / 重大なドリフト」を判定
   - ドリフトが検出された場合、具体的な差分（元企画書の表現 vs plan/ の表現）
   - ドリフトの影響度評価

**出力**: 項目ごとに「一致 / 軽微なドリフト / 重大なドリフト」を判定

**Validation**:
- [ ] 優先順位・用語・制約・意図の4観点すべてが検証されている
- [ ] ドリフトが検出された箇所に具体的な差分が記載されている
- [ ] 重大なドリフトがある場合、影響度が評価されている

---

### Phase 3: 技術的整合性検証

**Goal**: eng.md / PLAN.md の技術的アプローチが元企画書の制約と矛盾していないか。

**Agent**: constitutional-validator

**Process**:

1. **Launch constitutional-validator agent** with:
   - 元企画書の技術関連セクション
   - CLAUDE.md（アーキテクチャ制約）
   - eng.md, PLAN.md

2. **Agent analyzes**:
   - **技術制約の突き合わせ**: 元企画書が指定する技術スタック/ライブラリと eng.md の選択が一致しているか。性能要件（レスポンス時間、処理量）が考慮されているか
   - **アーキテクチャの整合性**: CLAUDE.md のアーキテクチャ（ブラウザ内処理、サーバー限定処理）と eng.md が矛盾していないか。新規依存関係が制約に違反していないか
   - **スコープの膨張/縮小**: eng.md が元企画書にない技術要件を追加していないか（膨張）。元の技術要件が欠落していないか（縮小）

3. **Agent provides**:
   - 項目ごとに「整合 / 要確認 / 矛盾」を判定
   - 矛盾が検出された場合、具体的な箇所と修正案
   - スコープ膨張/縮小があった場合、その内容と意図的かどうかの評価

**出力**: 項目ごとに「整合 / 要確認 / 矛盾」を判定

**Validation**:
- [ ] 技術スタック・アーキテクチャ・スコープの3観点が検証されている
- [ ] CLAUDE.md の制約との整合性が確認されている
- [ ] 矛盾がある場合に具体的な修正案が提示されている

---

### Phase 4: 検証レポート生成

**Agent**: documentation-analyst-writer

**Process**:

1. **Launch documentation-analyst-writer agent** with all Phase 1-3 outputs

2. **Agent generates** the validation report, integrating all phase results into `rpi/{feature-slug}/plan/VALIDATE.md`

3. **総合判定の基準**:
   - **ALIGNED**: 全要件が到達、ドリフトなし、技術的整合
   - **MINOR DRIFT**: 全要件が到達/部分到達、軽微なドリフトのみ、技術的整合
   - **MAJOR DRIFT**: 要件の欠落あり、または重大なドリフトあり、技術的には整合
   - **MISALIGNED**: 要件の欠落あり、かつ重大なドリフトあり、または技術的矛盾あり

**レポートフォーマット**:

```markdown
# Plan Validation Report

**Feature**: {feature-slug}
**Original Proposal**: {path}
**Validated**: {date}

## 総合判定

**ALIGNED** | **MINOR DRIFT** | **MAJOR DRIFT** | **MISALIGNED**

{1-2文で総合判定の理由}

---

## 1. 要件トレーサビリティ

| 要件 | 元企画書 | REQUEST.md | plan/ | 判定 |
|------|---------|------------|-------|------|
| {要件概要} | ✓ | ✓ | ✓ | 到達 |
| {要件概要} | ✓ | ✓ | △ | 部分到達 |
| {要件概要} | ✓ | ✓ | ✗ | 欠落 |

**カバー率**: {N}/{M} 要件が完全到達 ({X}%)

### 欠落した要件
- {要件}: {欠落の説明と影響}

### 部分到達の要件
- {要件}: {何が不足しているか}

---

## 2. ニュアンス・トーン

### 優先順位
| 項目 | 元企画書での位置づけ | plan/での位置づけ | 判定 |
|------|-------------------|-----------------|------|
| {項目} | 最重要 | Phase 1 | 一致 |
| {項目} | 将来検討 | Phase 1 | ドリフト |

### 用語の一貫性
| 元企画書の用語 | plan/での表記 | 判定 |
|--------------|-------------|------|
| {用語A} | {用語A} | 一致 |
| {用語B} | {用語C} | 要確認 |

### 制約条件の保存
| 制約 | eng.md に反映 | 判定 |
|------|-------------|------|
| {制約} | ✓ / ✗ | {判定} |

### 意図の保存
| 元の意図 | pm.md での表現 | 判定 |
|---------|--------------|------|
| {意図} | {表現} | 一致 / ドリフト |

---

## 3. 技術的整合性

| チェック項目 | 判定 | 詳細 |
|------------|------|------|
| 技術スタック | 整合 / 矛盾 | {説明} |
| アーキテクチャ制約 | 整合 / 矛盾 | {説明} |
| スコープ膨張 | なし / あり | {説明} |
| スコープ縮小 | なし / あり | {説明} |

---

## 4. 是正アクション

### 必須（実装前に修正）
1. {何を / どのファイルで / どう修正するか}

### 推奨（可能なら修正）
1. {何を / どのファイルで / どう修正するか}

### 情報提供（修正不要だが認識しておくべき）
1. {ドリフトの内容と、なぜ許容範囲か}

---

## 5. 次のステップ

**ALIGNED の場合**:
→ `/rpi:implement {feature-slug}` で実装に進む

**MINOR DRIFT の場合**:
→ 「推奨」の是正アクションを検討してから実装に進む

**MAJOR DRIFT / MISALIGNED の場合**:
→ 「必須」の是正アクションを実施し、plan/ を修正してから再度 `/rpi:validate` を実行
```

---

## Sub-Agent Delegation

| Phase | Agent | Type | Purpose |
|-------|-------|------|---------|
| Phase 1 | requirement-parser | Custom | 元企画書からの要件抽出・トレーサビリティ検証 |
| Phase 2 | product-manager | Custom | ニュアンス・優先順位の評価 |
| Phase 3 | constitutional-validator | Custom | 技術的整合性の検証 |
| Phase 4 | documentation-analyst-writer | Custom | レポート生成 |

### Agent Invocation

**Custom Agents** (requirement-parser, product-manager, constitutional-validator, documentation-analyst-writer):
- Claude Code automatically detects these from `.claude/agents/`
- Reference them naturally: "Acting as the requirement-parser agent..."
- NO Task tool invocation needed

---

## Completion Report

### Validation Summary

**Feature**: {feature-slug}
**Verdict**: [ALIGNED | MINOR DRIFT | MAJOR DRIFT | MISALIGNED]

**Scores**:
- 要件トレーサビリティ: {N}% カバー
- ニュアンス一致度: [High / Medium / Low]
- 技術的整合性: [整合 / 要確認 / 矛盾]

**是正アクション**: 必須 {N}件 / 推奨 {N}件

### Report Location

**Full Report**: `rpi/{feature-slug}/plan/VALIDATE.md`

### Next Steps

**If ALIGNED or MINOR DRIFT**:
→ `/rpi:implement {feature-slug}`

**If MAJOR DRIFT or MISALIGNED**:
→ 是正アクションを実施後、再度 `/rpi:validate {feature-slug} {proposal-path}`

---

## Error Handling

**If original proposal file doesn't exist**:
- Action: Stop and inform user
- Message: "Original proposal file not found at `{path}`. Please provide the correct path."

**If plan files don't exist**:
- Action: Stop and inform user
- Message: "Plan files not found. Run `/rpi:plan {feature-slug}` first."

**If REQUEST.md doesn't exist**:
- Action: Stop and inform user
- Message: "REQUEST.md not found at `rpi/{feature-slug}/REQUEST.md`."

---

## Notes

- **When to Use**: After `/rpi:plan` completes, before `/rpi:implement` begins
- **Part of RPI Workflow**: Step 4 of 6 (Decompose → Describe → Research → Plan → **Validate** → Implement)
- **Re-run after fixes**: If plan files are modified based on validation feedback, re-run this command to confirm alignment

---

## Post-Completion Action

**IMPORTANT**: After completing validation, ALWAYS prompt the user to compact:

> **Context Management**: This validation consumed significant context. Please run:
>
> ```
> /compact
> ```
