# Template Customization -- Product Requirements Document

**Feature**: 議事録テンプレートカスタマイズ (Per-guild minutes template selection)
**Traceability**: R-78 (REQUEST.md)
**Status**: 95% implemented -- merge readiness document
**Date**: 2026-03-17

---

## 1. Feature Overview

ギルドごとに議事録の出力フォーマットをDiscordスラッシュコマンドで切り替え可能にする。
現在のBot は `prompts/minutes.txt` 固定のClaude APIプロンプトを使用しているが、本機能により:

- `prompts/` 配下に複数テンプレートをファイルとして配置（コード変更不要）
- ギルドごとにデフォルトテンプレートを `config.yaml` で指定
- `/minutes template-set <name>` で実行時にテンプレートを切り替え
- 切り替え結果は `state/guild_settings.json` に永続化

全Must Have要件は実装・テスト済み。残作業は権限ガードの1行追加と任意のEmbed互換性改善のみ。

---

## 2. Problem Statement

単一テンプレートの制約により、以下の課題が発生している:

1. **マルチギルド差別化の欠如**: ギルドごとにチャンネルルーティングは設定可能だが、出力フォーマットは全ギルド共通。技術チームのスタンドアップと企画チームのブレストで同じ形式は不適切。
2. **運用者の負担**: 新しいフォーマットを導入するにはサーバー側の `prompts/minutes.txt` を直接編集する必要がある。ギルドAの変更がギルドBにも影響する。
3. **ユーザー体験の制限**: TODOテーブルが欲しいギルド、要約中心のギルド、決定事項重視のギルドなど、ニーズは多様だが対応手段がない。

---

## 3. User Stories

| # | As a... | I want to... | So that... |
|---|---------|-------------|-----------|
| US-1 | ギルド管理者 | `/minutes template-list` でテンプレート一覧を確認したい | どのフォーマットが使えるか把握できる |
| US-2 | ギルド管理者 | `/minutes template-set todo-focused` でテンプレートを変更したい | 次回以降の議事録がTODO重視フォーマットで生成される |
| US-3 | テンプレート作成者 | `prompts/` に `.txt` ファイルを追加するだけで新テンプレートを提供したい | コード変更なしでフォーマットのバリエーションを増やせる |
| US-4 | 会議参加者 | テンプレート変更後も過去の議事録キャッシュが正しく動作してほしい | テンプレート切替時に古いフォーマットの議事録が再利用されない |

---

## 4. Acceptance Criteria

全項目にテスト結果の検証状況を記載。250テスト全通過確認済み (2026-03-17)。

### FR-1: Multiple templates in `prompts/` directory
- [x] AC-1.1: `prompts/` 配下の `.txt` ファイルが自動的にテンプレートとして検出される
- [x] AC-1.2: ファイル先頭の `# name:`, `# description:` コメントからメタデータが抽出される
- [x] AC-1.3: メタデータヘッダーがないテンプレートはファイル名のstemが表示名になる
- **Implementation**: `MinutesGenerator.list_templates()`, `_parse_template_metadata()`

### FR-2: Per-guild default in `config.yaml`
- [x] AC-2.1: `GuildConfig.template` はデフォルト値 `"minutes"` で後方互換
- [x] AC-2.2: マルチギルド形式で `template:` フィールドを受け付ける
- [x] AC-2.3: 旧シングルギルド形式でも `template:` フィールドが動作する
- **Implementation**: `GuildConfig` dataclass, `_build_discord_section()`

### FR-3: `/minutes template-list` command
- [x] AC-3.1: テンプレート一覧をEmbed形式で表示し、現在のテンプレートに `(現在)` マーカーを付与
- [x] AC-3.2: Embed形式（プレーンテキストではない）
- [x] AC-3.3: フッターに `/minutes template-set <名前> で変更` の案内を表示
- **Implementation**: `bot.py` `template_list` command

### FR-4: `/minutes template-set <name>` command
- [x] AC-4.1: ギルドのテンプレートが `state/guild_settings.json` に永続化される
- [x] AC-4.2: 存在しないテンプレート名の場合、エラーメッセージを返す
- [x] AC-4.3: 変更完了メッセージに「次回の議事録生成から適用」と表示
- [x] AC-4.4: テンプレート名のオートコンプリートが動作する
- [ ] **AC-4.5: `manage_guild` 権限が必要** -- **未実装 (C1)**
- **Implementation**: `bot.py` `template_set` command + autocomplete

### FR-5: Template resolution priority chain
- [x] AC-5.1: 解決順序: state_store override > GuildConfig.template > `"minutes"` default
- [x] AC-5.2: パイプライン全ステージにtemplate_nameが伝搬される
- [x] AC-5.3: キャッシュキーにtemplate_nameが含まれる（SHA-256）
- **Implementation**: `resolve_template()`, `_transcript_hash()`

### FR-6: Path traversal prevention
- [x] AC-6.1: `..`, `/`, `\` を含むテンプレート名は `GenerationError` で拒否
- [x] AC-6.2: `prompts/` ディレクトリ外のファイルはロードされない
- **Implementation**: `_load_template()` validation guard

### FR-7: Bundled templates
- [x] AC-7.1: `prompts/minutes.txt` (標準議事録) と `prompts/todo-focused.txt` (TODO重視) の2テンプレートを同梱
- [x] AC-7.2: 両テンプレートに `# name:` と `# description:` メタデータを記載
- **Implementation**: `prompts/minutes.txt`, `prompts/todo-focused.txt`

---

## 5. Scope

### In Scope (all implemented)
- ファイルベースのテンプレート管理 (`prompts/*.txt`)
- テンプレートメタデータパース (`# name:`, `# description:`)
- ギルド別テンプレート設定 (`config.yaml`, `state/guild_settings.json`)
- Discord スラッシュコマンド (`template-list`, `template-set`)
- テンプレート名によるキャッシュキー分離
- パストラバーサル防止
- オートコンプリート対応
- `/minutes status` にテンプレート名を表示

### Out of Scope (deferred)
- `/minutes template-preview <name>` -- サンプルトランスクリプト + Claude API呼び出しが必要（コスト懸念）
- カスタムテンプレート変数 -- 現行5プレースホルダー (`{transcript}`, `{date}`, `{speakers}`, `{guild_name}`, `{channel_name}`) で十分
- Discordからのテンプレートアップロード -- ユーザー供給プロンプトのセキュリティリスク
- テンプレートバリデーション -- poster.py のEmbed抽出に必要なセクション見出しの検証
- テンプレートホットリロード -- 現在はBot再起動でキャッシュクリア。頻度が低いため許容
- `/minutes process <url> --template=X` による一時テンプレート指定

---

## 6. Business Value

| Value Driver | Impact |
|-------------|--------|
| マルチギルド差別化 | 各ギルドが独立してフォーマットを選択可能。マルチギルドサポートの実質的完成 |
| 運用負荷削減 | テンプレート追加はファイル配置のみ、ギルド切替はDiscordコマンドのみ。サーバーSSH不要 |
| Claude API コスト | 追加コストなし。テンプレート切替は同一APIコール、プロンプト長もほぼ同等 |
| ユーザー満足度 | 「このフォーマットに変えてほしい」という要望に管理者が即座に対応可能 |
| 拡張性 | 新テンプレートは `.txt` ファイル追加で対応完了。開発者不要 |

---

## 7. Success Metrics

### Leading Indicators

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| テンプレート切替採用率 | マージ後30日で50%以上のギルドがデフォルトから変更 | `state/guild_settings.json` のエントリ数 |
| 同梱テンプレート数 | マージ後60日で3種類以上 | `prompts/*.txt` のファイル数 |
| テンプレート起因パイプラインエラー | 0件 | エラーログの `stage=generation` + template関連メッセージ |

### Lagging Indicators

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| フォーマット変更要望の減少 | 定性: 「形式を変えてほしい」リクエストの消滅 | ユーザーフィードバック |
| テンプレート利用分布 | 2種類以上のテンプレートが定常的に使用される | `minutes_archive.db` の `template_name` カラム集計 |

---

## 8. Non-Functional Requirements

### NFR-1: Performance
- テンプレートファイル読み込みは同期I/Oだが <1ms（negligible）。初回読み込み後はメモリキャッシュ (`_templates` dict)。
- パイプラインレイテンシへの影響なし。Claude API呼び出し (数秒) が支配的。

### NFR-2: Scale
- ファイルベースストレージで2-20テンプレートに対応。DBは不要。
- `guild_settings.json` はフラットJSONで数十ギルドに対応可能。

### NFR-3: SLOs
- テンプレート解決は O(1) dict lookup。パイプライン起動に +10ms 以下。
- 設定済みテンプレートがディスクに存在しない場合、`GenerationError` で明示的に失敗（クラッシュしない）。

### NFR-4: Security
- パストラバーサル防止: `..`, `/`, `\` を含むテンプレート名を拒否。
- テンプレートはサーバー側ファイル。ユーザーアップロードなし。インジェクションリスクなし。

### NFR-5: Observability
- アーカイブレコードに `template_name` を記録 (`minutes_archive.db`)。
- `/minutes status` で現在のテンプレート名を表示。
- キャッシュキーにテンプレート名を含むため、cache hit/miss ログでテンプレート特定可能。

### NFR-6: Backward Compatibility
- デフォルトテンプレートは `"minutes"` (既存の `prompts/minutes.txt`)。テンプレート未設定のギルドは動作変更なし。
- `config.yaml` に `template:` キーがなくても動作する。

---

## 9. Risks & Mitigations

### C1: Permission Guard Missing on `template-set` (OPEN -- Required fix)

**Problem**: 現在、ギルドの全メンバーが `/minutes template-set` を実行可能。
**Impact**: 低。テンプレート変更は非破壊的（即座に元に戻せる）、将来の議事録生成にのみ影響。
**Fix**: `@discord.app_commands.checks.has_permissions(manage_guild=True)` デコレータを追加（1行）。
**Status**: マージ前に修正推奨。

### R-1: Embed Section Extraction Fragility (ACCEPTED -- Graceful degradation)

**Problem**: `poster.py` は `## まとめ` と `## 推奨される次のステップ` をハードコード正規表現で抽出。
`todo-focused.txt` テンプレートは `## 要約` と `## アクションアイテム / TODO` を使用しており、Embedのサマリー・次ステップフィールドが空になる。

**Impact**: Embed品質の低下のみ。完全な議事録は常に `.md` 添付ファイルとして添付されるため、データ損失なし。
**Mitigation**: Graceful degradation として許容。Fast-follow で `poster.py` の正規表現に `## 要約` パターンを追加（約4行）。

### R-2: Template Hot-Reload Limitation (ACCEPTED)

**Problem**: テンプレートは初回ロード後にメモリキャッシュされ、ファイル編集はBot再起動まで反映されない。
**Impact**: 低。テンプレートファイル変更は低頻度。運用者がファイルとBotプロセスの両方を管理。

### R-3: No Template Validation (ACCEPTED)

**Problem**: テンプレートが poster.py のEmbed抽出に必要なセクション見出しを含むか検証する仕組みがない。
**Impact**: 低。運用者作成テンプレートのみ。コミュニティ提供テンプレートは現時点でスコープ外。

---

## 10. Dependencies

**External**: なし。本機能は完全に自己完結。
**Internal**: 既存インフラ（`StateStore`, `GuildConfig`, `MinutesGenerator`, pipeline orchestration）を活用。新規モジュールの追加なし。

---

## 11. Rollout Plan

| Phase | Action | Effort | Status |
|-------|--------|--------|--------|
| Phase 0 | テストスイート実行 (250 tests) | 30 min | DONE |
| Phase 1 | C1修正 (permission guard) + マージ | 30 min | PENDING |
| Phase 2 | 自ギルドでドッグフーディング (両テンプレートで議事録生成) | 1 week | PENDING |
| Phase 3 | ドキュメント (テンプレート作成ガイド: メタデータ形式、プレースホルダー一覧) | 2 hours | PENDING |
| Phase 4 | Fast-follow: poster.py Embed パターン拡張 (`## 要約` 対応) | 1 hour | OPTIONAL |
| Phase 5 | 追加テンプレート (e.g., 決定事項重視, ブリーフサマリー) | 2-4h/template | FUTURE |

---

## 12. Implementation Map

| Component | File | Changes |
|-----------|------|---------|
| Template Engine | `src/generator.py` | `TemplateInfo`, `_parse_template_metadata()`, `_load_template()`, `list_templates()`, `render_prompt()` template_name param |
| Per-Guild Config | `src/config.py` | `GuildConfig.template` field, YAML parsing in `_build_discord_section()` |
| Runtime Override | `src/state_store.py` | `get_guild_template()`, `set_guild_template()`, `guild_settings.json` persistence |
| Slash Commands | `bot.py` | `resolve_template()`, `template-list`, `template-set`, autocomplete, status integration |
| Pipeline | `src/pipeline.py` | `_transcript_hash()` includes template_name, `template_name` parameter propagation |
| Templates | `prompts/minutes.txt`, `prompts/todo-focused.txt` | Metadata headers added |
| Tests | `tests/test_generator.py`, `test_config.py`, `test_pipeline.py`, `test_state_store.py`, `test_minutes_archive.py` | 62 template-related assertions across 5 files |
