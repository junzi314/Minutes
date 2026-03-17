# Implementation Roadmap: 日英混在対応 (Multilingual Support)

**Feature**: Whisper language パラメータ動的切替
**Date**: 2026-03-17
**Traceability**: R-84
**Complexity**: Simple (Size S)
**Phases**: 2 (Phase 1: immediate, Phase 2: backlogged)
**Approach**: Option B+ (Research RESEARCH.md recommended)

---

## Feature Overview

会議中に日本語と英語が混在する場合の文字起こし品質を改善する。Must-Have要件（`"auto"` -> `None` 変換、言語バリデーション、4件のユニットテスト）はすべて実装済み。残る作業は `config.yaml` のデフォルト値を `"ja"` から `"auto"` に変更する1行のみ（GPU検証が前提条件）。Nice to Have のギルド別言語オーバーライドはマルチギルド需要が具体化するまでバックログに残す。

### Implementation Status

| 要件 | 状態 | 実装箇所 |
|------|------|----------|
| `"auto"` -> `None` 変換 | 実装済み | `src/transcriber.py` line 80 |
| `VALID_WHISPER_LANGUAGES` 定数 | 実装済み | `src/config.py` lines 27-32 |
| 言語バリデーション | 実装済み | `src/config.py` lines 344-348 |
| `config.yaml` コメント更新 | 実装済み | `config.yaml` line 40 |
| テスト: `test_auto_language_passes_none` | 実装済み | `tests/test_transcriber.py` |
| テスト: `test_explicit_language_passes_through` | 実装済み | `tests/test_transcriber.py` |
| テスト: `test_invalid_whisper_language_rejected` | 実装済み | `tests/test_config.py` |
| テスト: `test_auto_whisper_language_accepted` | 実装済み | `tests/test_config.py` |
| デフォルト値 `"ja"` -> `"auto"` 変更 | **未実施** | `config.yaml` line 41 |
| ギルド別言語オーバーライド | **未実装** | バックログ |

---

## Phase 1: Config Default Change + GPU Validation

**Goal**: GPU環境で `language="auto"` の品質・速度を検証し、検証通過後に `config.yaml` のデフォルトを `"auto"` に変更する。

**前提条件**: なし（コード変更不要、設定変更のみ）

### Tasks

| # | Task | File | Complexity | Depends |
|---|------|------|------------|---------|
| 1.1 | GPU検証: 日英混在録音で `language="auto"` テスト | (手動) | Low | -- |
| 1.2 | GPU検証: 日本語のみ録音で回帰テスト | (手動) | Low | -- |
| 1.3 | デフォルト値変更 | `config.yaml` | Low | 1.1, 1.2 |
| 1.4 | 全テスト通過確認 | -- | Low | 1.3 |
| 1.5 | デプロイ後モニタリング | (運用) | Low | 1.4 |

### Implementation Details

#### Task 1.1: GPU検証 -- 日英混在録音

実際の日英混在録音を `language="auto"` で処理し、以下を計測する。

```bash
# config.yaml を一時的に変更してテスト
# whisper.language: "auto"
python3 bot.py --log-level DEBUG
# 録音を処理し、ログから以下を確認:
# - info.language: 検出された言語
# - info.language_probability: 検出確度
# - 処理時間（"Transcribed ... in Xs"）
```

**計測項目**:

| 項目 | 受入基準 |
|------|----------|
| 処理時間 | `"ja"` 固定比 2倍以内 |
| 英語用語認識 | `"ja"` 固定より改善 |
| 言語検出確度 | `language_probability >= 0.8` |

#### Task 1.2: GPU検証 -- 日本語のみ回帰テスト

日本語のみの録音で `language="auto"` と `language="ja"` の出力を比較する。

**計測項目**:

| 項目 | 受入基準 |
|------|----------|
| 文字起こし品質 | `"ja"` 固定と同等（目視確認） |
| 処理時間 | `"ja"` 固定比 2倍以内 |
| 言語検出結果 | `info.language == "ja"` |

#### Task 1.3: デフォルト値変更

GPU検証が受入基準を満たした場合のみ実施。

```yaml
# config.yaml line 41
# Before:
language: "ja"
# After:
language: "auto"
```

`WhisperConfig` の Python デフォルト値は `"ja"` のまま変更しない。`config.yaml` が優先されるため問題ない。

#### Task 1.4: テスト通過確認

```bash
pytest
# 既存173+ テスト全 pass
# WhisperConfig の Python デフォルトは "ja" のままなので、テスト影響なし
```

#### Task 1.5: デプロイ後モニタリング

`config.yaml` 変更デプロイ後、ログで以下を監視する:

- `info.language` の値（想定: 日英混在会議で `"ja"` or `"en"` が適切に検出）
- `info.language_probability` の値（想定: >= 0.8）
- 処理時間の傾向（15分SLA内に収まっていること）
- ユーザーからの品質フィードバック

### Success Criteria

- [ ] GPU検証で `"auto"` の処理時間が `"ja"` 固定比 2倍以内
- [ ] 英語の専門用語・固有名詞の認識が `"ja"` 固定より改善
- [ ] 日本語のみの録音で品質劣化なし
- [ ] `config.yaml` 変更後、全テスト pass
- [ ] ロールバック確認: `config.yaml` を `"ja"` に戻すだけで元に戻る

### Files Modified

| File | Change | Lines Changed |
|------|--------|---------------|
| `config.yaml` | `language: "ja"` -> `language: "auto"` | 1 |

### Estimated Effort

| 作業 | 時間 |
|------|------|
| GPU検証（日英混在 + 日本語のみ） | ~30分 |
| config.yaml 変更 + テスト確認 | ~5分 |
| **合計** | **~35分** |

---

## Phase 2: Per-Guild Language Override (Backlogged)

**Goal**: ギルドごとに言語設定をオーバーライド可能にする。

**前提条件**: Phase 1 完了 + マルチギルド需要の具体化
**トリガー**: 2つ以上のギルドが異なる言語設定を必要とした時点
**パターン**: `StateStore.get_guild_template` / `set_guild_template` を踏襲

### Tasks

| # | Task | File | Complexity | Depends |
|---|------|------|------------|---------|
| 2.1 | `get_guild_language` / `set_guild_language` 追加 | `src/state_store.py` | Low | -- |
| 2.2 | `resolve_language()` ヘルパー追加 | `bot.py` | Low | 2.1 |
| 2.3 | 言語解決 + パイプライン伝播 | `src/pipeline.py` | Low | 2.1, 2.2 |
| 2.4 | `language_override` パラメータ追加 | `src/transcriber.py` | Low | -- |
| 2.5 | `/minutes language <lang>` コマンド追加 | `bot.py` | Medium | 2.1, 2.2 |
| 2.6 | ユニットテスト追加 | `tests/` | Medium | 2.1-2.5 |

### Implementation Details

#### Task 2.1: StateStore に言語設定メソッド追加

`src/state_store.py` の Guild settings methods セクションに追加。既存の `get_guild_template` / `set_guild_template` と同一パターン。

```python
def get_guild_language(self, guild_id: int) -> str | None:
    """Return the language override for a guild, or None."""
    settings = self._guild_settings.get(str(guild_id))
    if settings is None:
        return None
    return settings.get("language")

def set_guild_language(self, guild_id: int, language: str) -> None:
    """Set the language for a guild."""
    key = str(guild_id)
    if key not in self._guild_settings:
        self._guild_settings[key] = {}
    self._guild_settings[key]["language"] = language
    self._flush_guild_settings()
```

#### Task 2.2: resolve_language ヘルパー

`bot.py` に追加。既存の `resolve_template` パターンを踏襲。

```python
def resolve_language(self, guild_id: int) -> str:
    """Resolve the language for a guild.

    Priority: guild override > config.yaml default
    """
    override = self.state_store.get_guild_language(guild_id)
    if override is not None:
        return override
    return self.cfg.whisper.language
```

#### Task 2.3: パイプラインへの言語伝播

`src/pipeline.py` の `run_pipeline_from_tracks` に `language_override` パラメータを追加し、transcriber に渡す。

#### Task 2.4: transcriber.py の language_override

`transcribe_file()` と `transcribe_all()` にオプショナルな `language_override` パラメータを追加。指定された場合、`self._cfg.language` の代わりに使用する。

```python
def transcribe_file(
    self, audio_path: Path, speaker_name: str, language_override: str | None = None,
) -> list[Segment]:
    lang = language_override if language_override is not None else self._cfg.language
    language = None if lang == "auto" else lang
    # ...
```

#### Task 2.5: `/minutes language` コマンド

```
/minutes language <lang>   -- ギルドの言語設定を変更
/minutes language          -- 現在の設定を表示
```

`VALID_WHISPER_LANGUAGES` をオートコンプリート選択肢として使用。

#### Task 2.6: テスト

| テスト | ファイル | 検証内容 |
|--------|---------|----------|
| `test_get_set_guild_language` | `tests/test_state_store.py` | 設定の保存・取得・永続化 |
| `test_guild_language_none_default` | `tests/test_state_store.py` | 未設定時に `None` を返す |
| `test_language_override_in_transcriber` | `tests/test_transcriber.py` | `language_override` が優先される |
| `test_language_override_auto` | `tests/test_transcriber.py` | `language_override="auto"` -> `None` |

### Success Criteria

- [ ] `/minutes language auto` でギルドの言語を自動検出に設定できる
- [ ] `/minutes language ja` でギルドの言語を日本語に設定できる
- [ ] ギルド設定が `config.yaml` のデフォルトより優先される
- [ ] ギルド設定が未設定の場合、`config.yaml` のデフォルトにフォールバック
- [ ] `state/guild_settings.json` に永続化される
- [ ] 全テスト pass

### Files Modified

| File | Change | Lines Added |
|------|--------|-------------|
| `src/state_store.py` | `get_guild_language` / `set_guild_language` | ~10-15 |
| `src/pipeline.py` | `language_override` パラメータ伝播 | ~5-10 |
| `src/transcriber.py` | `language_override` パラメータ追加 | ~5 |
| `bot.py` | `resolve_language` + `/minutes language` コマンド | ~30-40 |
| `tests/test_state_store.py` | ギルド言語テスト | ~10-15 |
| `tests/test_transcriber.py` | 言語オーバーライドテスト | ~10-15 |
| **合計** | | **~70-100** |

### Estimated Effort

| 作業 | 時間 |
|------|------|
| state_store + transcriber 変更 | ~30分 |
| bot.py コマンド追加 | ~45分 |
| pipeline.py 伝播 | ~15分 |
| テスト追加 | ~30分 |
| **合計** | **~2時間** |

---

## Dependency Chart

```
[Phase 1]
  Task 1.1 (GPU検証: 日英混在)  ─┐
  Task 1.2 (GPU検証: 日本語のみ) ─┼─→ Task 1.3 (config変更) ─→ Task 1.4 (テスト) ─→ Task 1.5 (監視)
                                  │
[Phase 2 -- Backlogged]           │
  Task 2.1 (state_store)  ─┬─→ Task 2.2 (resolve_language) ─→ Task 2.5 (/minutes language)
  Task 2.4 (transcriber)  ─┤                                          │
                            └─→ Task 2.3 (pipeline) ──────────────────┘
                                                                       │
                                                              Task 2.6 (テスト)
```

**Phase 1**: Task 1.1 と 1.2 は並列実行可能。1.3 は両方の検証通過が前提。
**Phase 2**: Task 2.1 と 2.4 は並列で着手可能。2.5 は 2.1 + 2.2 に依存。

---

## Risk Register

| # | リスク | 影響度 | 発生確率 | 対策 |
|---|--------|--------|----------|------|
| 1 | `"auto"` で処理速度が 2倍以上に低下 | 中 | 低 | Phase 1 の GPU 検証で事前確認。不合格なら `"ja"` 維持 |
| 2 | モノリンガル日本語会議で品質劣化 | 低 | 低 | Whisper large-v3 の日本語検出精度は高い（>95%）。Task 1.2 で回帰テスト |
| 3 | 短いセグメントで言語誤検出 | 低 | 低 | VAD フィルタで短セグメントは既に除外。言語検出は音声冒頭30秒で一括判定 |
| 4 | Phase 2 のギルド設定がテンプレート設定と競合 | 低 | 極低 | `guild_settings.json` は既にテンプレート設定で使用中。同一パターンで共存可能 |
| 5 | 既存テスト破壊 | なし | 極低 | `WhisperConfig` の Python デフォルト `"ja"` は変更しない。テスト影響なし |

---

## Rollback Procedures

### Phase 1 ロールバック

`config.yaml` 変更のみのため、即座にロールバック可能:

```yaml
# config.yaml line 41 を元に戻す
language: "ja"
```

```bash
# Bot 再起動
sudo systemctl restart minutes-bot
# or
docker compose restart
```

コード変更がないため `git revert` は不要。

### Phase 2 ロールバック

1. `state/guild_settings.json` からギルドの `language` キーを手動削除
2. コード変更を `git revert` で1コミット巻き戻し
3. Bot 再起動

Phase 2 はすべて追加のみ（optionalパラメータ）のため、既存機能への影響なし。

---

## Total Effort Summary

| Phase | 状態 | 工数 | コード変更量 |
|-------|------|------|-------------|
| Phase 1 | Ready (GPU検証待ち) | ~35分 | 1行 (`config.yaml`) |
| Phase 2 | Backlogged | ~2時間 | ~70-100行 |
| **合計** | | **~2.5時間** | **~70-100行** |

Phase 1 は GPU 検証の結果次第で即日完了可能。Phase 2 はマルチギルド需要が具体化した時点で着手する。
