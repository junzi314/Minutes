# Implementation Record

**Feature**: template-customization
**Started**: 2026-03-17
**Status**: COMPLETED

---

## Phase 1: Core Infrastructure — Config + Generator + Pipeline

**Date**: 2026-03-17
**Verdict**: PASS

### Deliverables
- [x] GuildConfig.template フィールド追加
- [x] _build_discord_section() でtemplate読み取り
- [x] TemplateInfo dataclass + _parse_template_metadata()
- [x] MinutesGenerator マルチテンプレート対応
- [x] render_prompt() / generate() に template_name パラメータ追加
- [x] _transcript_hash() にテンプレート名含め + パイプライン伝播
- [x] prompts/minutes.txt にメタデータヘッダー追加
- [x] Unit tests (11 new tests)

### Files Changed
- `src/config.py` — +4 lines (GuildConfig.template, _build_discord_section)
- `src/generator.py` — rewrite (+228 lines: TemplateInfo, multi-template, _load_template, list_templates)
- `src/pipeline.py` — +6 lines (_transcript_hash with template, template_name propagation)
- `prompts/minutes.txt` — +2 lines (metadata header)
- `tests/test_generator.py` — rewrite (+222 lines: 11 new tests)
- `tests/test_config.py` — +19 lines (2 new tests)
- `tests/test_pipeline.py` — +16 lines (2 new tests)

### Test Results
- 196 passed, 2 failed (pre-existing GPU/WSL2 issue)
- 11 new tests all passing

### Notes
- Path traversal prevention implemented via name validation
- Template caching via dict prevents redundant file reads

---

## Phase 2: Bot Commands + State Persistence

**Date**: 2026-03-17
**Verdict**: PASS

### Deliverables
- [x] StateStore に guild_settings 機能追加
- [x] MinutesBot.resolve_template() メソッド追加
- [x] /minutes template-list コマンド追加
- [x] /minutes template-set コマンド追加（autocomplete付き）
- [x] _launch_pipeline + Drive watcher にテンプレート伝播
- [x] /minutes status にテンプレート情報追加
- [x] Unit tests (5 new tests)

### Files Changed
- `src/state_store.py` — +20 lines (guild_settings methods)
- `bot.py` — +55 lines (resolve_template, template-list, template-set, autocomplete, status update, pipeline integration)
- `tests/test_state_store.py` — +42 lines (5 new tests)

### Test Results
- 201 passed, 2 failed (pre-existing GPU/WSL2 issue)
- 5 new tests all passing

---

## Phase 3: Sample Templates + Validation

**Date**: 2026-03-17
**Verdict**: PASS

### Deliverables
- [x] prompts/todo-focused.txt サンプルテンプレート作成
- [x] 全テスト実行 + E2E検証

### Files Changed
- `prompts/todo-focused.txt` — new file (44 lines)

### Test Results
- 201 passed, 2 failed (pre-existing GPU/WSL2 issue)
- list_templates() returns 2 templates: minutes, todo-focused

---

## Summary

**Phases Completed**: 3 of 3
**Final Status**: COMPLETED
**Total New Tests**: 18
**Total Tests Passing**: 201 (2 pre-existing GPU failures)
