# Implementation Record

**Feature**: transcript-glossary
**Started**: 2026-03-18
**Status**: COMPLETED

---

## Phase 1: Core Module + Config

**Date**: 2026-03-18
**Verdict**: PASS

### Deliverables
- [x] `src/glossary.py` — `apply_glossary()` pure function with case-sensitive/insensitive modes
- [x] `TranscriptGlossaryConfig` frozen dataclass (enabled + case_sensitive)
- [x] `Config` に `transcript_glossary` フィールド追加
- [x] `_SECTION_CLASSES` に登録
- [x] `config.yaml` に `transcript_glossary:` セクション追加
- [x] テスト: 11件 (test_glossary.py) + 2件 (test_config.py) + _make_config更新 (test_pipeline.py)

### Files Changed
| File | Lines |
|------|-------|
| src/glossary.py | +68 (new) |
| src/config.py | +7 |
| config.yaml | +5 |
| tests/test_glossary.py | +81 (new) |
| tests/test_config.py | +25 |
| tests/test_pipeline.py | +2 |

### Test Results
50/50 passed

---

## Phase 2: Storage + Pipeline Integration

**Date**: 2026-03-18
**Verdict**: PASS

### Deliverables
- [x] `StateStore.get_guild_glossary()` / `set_guild_glossary()` メソッド追加
- [x] パイプライン transcribe → glossary → speaker_analytics → merge 順序挿入
- [x] テスト: 4件 (test_state_store.py) + 2件 (test_pipeline.py)

### Files Changed
| File | Lines |
|------|-------|
| src/state_store.py | +16 |
| src/pipeline.py | +9 |
| tests/test_state_store.py | +30 |
| tests/test_pipeline.py | +55 |

### Test Results
73/73 passed

---

## Phase 3: Bot Commands

**Date**: 2026-03-18
**Verdict**: PASS

### Deliverables
- [x] `/minutes glossary-add <wrong> <correct>` — 辞書エントリ追加
- [x] `/minutes glossary-remove <wrong>` — 辞書エントリ削除
- [x] `/minutes glossary-list` — 辞書一覧表示 (Embed)
- [x] 全コマンド `manage_guild` 権限チェック + エラーハンドラ
- [x] 全応答 ephemeral

### Files Changed
| File | Lines |
|------|-------|
| bot.py | +65 |

### Test Results
269/271 passed (2 pre-existing GPU failures)

---

## Summary

**Phases Completed**: 3 of 3
**Final Status**: COMPLETED
**Total Tests Added**: 19
**Total Tests**: 271 (269 pass, 2 pre-existing GPU failures)
