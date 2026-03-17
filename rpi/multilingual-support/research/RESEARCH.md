# Research Report: 日英混在対応（Multilingual Support）

**Date**: 2026-03-17
**Decision**: CONDITIONAL GO (90% confidence)
**Effort**: ~2-3 hours (Nice to Have only; Must-Have is already implemented)

---

## Executive Summary

日英混在会議でのWhisper文字起こし品質を改善するため、`language`パラメータを`"ja"`固定から`"auto"`（自動検出）に切り替え可能にする機能の調査結果。**Must-Have要件はすべて実装済み**であることが判明した。`transcriber.py`の`language=None`変換（line 80）、`config.py`の`VALID_WHISPER_LANGUAGES`定数（line 27-32）と言語バリデーション（line 344-348）、および4件のユニットテストが現在のコードベースに存在し、173件のテストがすべてパスする。残る作業は**デフォルト値を`"ja"`から`"auto"`に変更するかの判断**と、Nice to Haveのギルド別言語オーバーライド（~60-80行、2-3時間）のみ。推奨アプローチは、GPUでの実録音バリデーション後にデフォルトを`"auto"`に変更し、ギルド別オーバーライドはマルチギルド需要が具体化するまでバックログに残すこと（Option B+）。

---

## Feature Overview

| 項目 | 値 |
|------|-----|
| **Feature** | 日英混在対応（Whisper language動的切替） |
| **Type** | 既存パイプラインの機能拡張 |
| **Target Components** | `src/transcriber.py`, `src/config.py`, `config.yaml`, `tests/` |
| **Complexity** | Simple (Size S) |
| **Traceability** | R-84 |
| **Implementation Order** | Ext-1 |

### Goals

1. Whisperの`language`パラメータを自動検出モード（`language=None`）に切り替え可能にする
2. `config.yaml`で`"ja"`, `"en"`, `"auto"`等の言語コードを選択可能にする
3. 日英混在会議での文字起こし品質を向上させる
4. 後方互換性を完全に維持する

---

## Requirements Summary

### Functional Requirements (Must-Have)

1. **Whisperの`language`パラメータを`"auto"`（`language=None`）に切り替え可能** [R-84]
   - **状態: 実装済み** -- `transcriber.py` line 80
2. **config.yamlで`"ja"`, `"en"`, `"auto"`から選択可能** [R-84]
   - **状態: 実装済み** -- `config.py` lines 27-32, 344-348

### Nice to Have

3. ギルドごとに言語設定を変更可能 [R-84]
   - **状態: 未実装** -- パターンは存在（`StateStore.get_guild_template`/`set_guild_template`）
4. `/minutes language <lang>` スラッシュコマンドで動的変更 [R-84]
   - **状態: 未実装**

### Non-Functional Requirements

| 要件 | 詳細 |
|------|------|
| **処理速度** | `language="auto"`使用時の処理速度がja固定比2倍以内 |
| **SLA** | auto検出時も15分SLA内に収まること |
| **後方互換** | デフォルト`"ja"`で既存動作を維持 |
| **テストカバレッジ** | 言語切替に関するユニットテストが存在すること |

### CRITICAL FINDING: Must-Have Requirements Already Implemented

コードベース調査の結果、Must-Have要件2件はすべて実装済みであることが確認された。

| 要件 | 実装箇所 | コード |
|------|----------|--------|
| auto → None変換 | `src/transcriber.py` line 80 | `language = None if self._cfg.language == "auto" else self._cfg.language` |
| 言語コード定数 | `src/config.py` lines 27-32 | `VALID_WHISPER_LANGUAGES` に `"auto"` を含む16言語コード |
| バリデーション | `src/config.py` lines 344-348 | 無効な言語コードを `ConfigError` で拒否 |
| テスト: auto→None | `tests/test_transcriber.py` line 115 | `test_auto_language_passes_none` |
| テスト: 明示的言語 | `tests/test_transcriber.py` line 130 | `test_explicit_language_passes_through` |
| テスト: 無効言語拒否 | `tests/test_config.py` line 216 | `test_invalid_whisper_language_rejected` |
| テスト: auto受理 | `tests/test_config.py` line 234 | `test_auto_whisper_language_accepted` |

---

## Product Analysis

### User Value: **High**

| 観点 | 評価 |
|------|------|
| **課題の深刻度** | 中-高。日英混在会議で英語の専門用語・固有名詞が誤認識される。技術系DiscordサーバーではIT用語が英語のまま使われることが多い |
| **影響範囲** | 全ギルドのユーザー。特に技術系・国際系コミュニティで顕著 |
| **ユーザー体験** | 透過的改善（設定変更のみ、ワークフロー変更なし） |
| **即効性** | `config.yaml`のlanguage値を`"auto"`に変更するだけで即座に効果あり |
| **発見可能性** | 現在のデフォルト`"ja"`ではauto機能の存在にユーザーが気づきにくい |

### Market Fit: **Strong**

多言語対応は、日本の技術コミュニティ向けツールにおいてtable-stakesの機能。英語の技術用語が日常的に混在するJP techコミュニティでは、言語自動検出の有無が文字起こし品質に直結する。

### Strategic Alignment: **Full**

| 設計原則 | 適合性 |
|----------|--------|
| Pipeline-first | 完全適合 -- パイプライン構造変更なし |
| Async by default | 完全適合 -- 非同期処理への影響なし |
| Graceful degradation | 完全適合 -- デフォルト`"ja"`で後方互換 |
| Multi-guild support | 完全適合 -- Nice to Haveでギルド別設定を考慮 |
| Minimal state | 完全適合 -- 設定のみ、状態変更なし |

### Product Viability: **High**

---

## Technical Discovery

### Current State: Must-Have Is Fully Implemented

**`src/transcriber.py`** (154行):

- `Segment` dataclass: `start`, `end`, `text`, `speaker` の4フィールド（言語情報なし）
- **line 80**: `language = None if self._cfg.language == "auto" else self._cfg.language` -- **auto変換ロジック実装済み**
- line 118-125: `info.language`, `info.language_probability` をログ出力（`"Transcribed %s: %d segments in %.1fs (lang=%s, prob=%.2f)"`）
- 全話者の全ファイルが同一言語設定で処理される

**`src/config.py`** (449行):

- **lines 27-32**: `VALID_WHISPER_LANGUAGES` 定数 -- `"auto"`, `"ja"`, `"en"`, `"zh"`, `"ko"` 等16言語を含む
- line 77-83: `WhisperConfig` frozen dataclass -- `language: str = "ja"`
- **lines 344-348**: `_validate()` で `whisper.language` をバリデーション -- 無効な言語コードは `ConfigError` を発生
- 環境変数 `WHISPER_LANGUAGE` でオーバーライド可能（既存の汎用機構）

**`config.yaml`** (line 40-41):

- `# Language: "ja", "en", "auto" (auto-detect), or other ISO 639-1 codes`
- `language: "ja"` -- 固定値だがコメントで`"auto"`を案内済み

**テストカバレッジ** (4テスト):

| テストファイル | テスト名 | 検証内容 |
|---------------|----------|----------|
| `test_transcriber.py` | `test_auto_language_passes_none` | `language="auto"` → Whisperに `None` が渡る |
| `test_transcriber.py` | `test_explicit_language_passes_through` | `language="en"` → Whisperにそのまま渡る |
| `test_config.py` | `test_invalid_whisper_language_rejected` | `language="xyz"` → `ConfigError` |
| `test_config.py` | `test_auto_whisper_language_accepted` | `language="auto"` → 正常にロード |

### Integration Points

```
config.yaml → config.py (WhisperConfig) → transcriber.py (transcribe_file) → WhisperModel.transcribe()
                                                                                     |
                                                                             info.language (logged, discarded)
```

変更が必要な箇所はこのチェーンのみ。他のパイプラインステージ（merger, generator, poster）への影響なし。

### Pipeline Data Flow

```
config.yaml  whisper.language: "ja" | "en" | "auto"
    |
    v
WhisperConfig(language="ja")            # config.py で読み込み
    |
    v
Transcriber(cfg: WhisperConfig)         # transcriber.py でインスタンス化
    |
    v
transcribe_file():
    language = None if "auto" else cfg.language   # line 80
    model.transcribe(path, language=language)      # faster-whisper呼び出し
    |
    v
info.language, info.language_probability  # 検出結果（ログ出力のみ、未活用）
    |
    v
list[Segment]                             # language情報なし
    |
    v
merge_transcripts() → generate() → post_minutes()   # 以降のステージは言語非依存
```

### Per-Guild Override Patterns (Nice to Have用)

既存のギルド別テンプレート設定パターンが参考になる:

- `StateStore.get_guild_template(guild_id)` / `set_guild_template(guild_id, name)` -- `state/guild_settings.json` に永続化
- `GuildConfig.template: str = "minutes"` -- config.yamlでギルドごとにデフォルト指定可能
- `/minutes template-set <name>` スラッシュコマンド -- ランタイム変更

同パターンで `get_guild_language` / `set_guild_language` を実装可能。

### Technical Constraints

- `WhisperConfig` は frozen dataclass -- フィールド追加は互換だが、型変更は要注意
- `language` 設定はグローバル -- 現在は全ギルド共通の設定
- `Segment` dataclass に `language` フィールドなし -- 将来の拡張用としては有益だが現時点では不要
- `Transcriber` はシングルインスタンス -- ギルド別言語は `transcribe_file()` のパラメータオーバーライドで対応可能

---

## Technical Analysis

### Feasibility: **High**

Must-Have要件が実装済みのため、技術的実現可能性の評価対象はNice to Have要件のみ。

### Options Comparison

| Criterion | A: 現状維持 | **B+: デフォルト→auto + ギルド別バックログ** | C: 全部実装 |
|-----------|------------|---------------------------------------------|------------|
| Must-Have対応 | 実装済み | 実装済み | 実装済み |
| デフォルト値 | `"ja"` (変更なし) | **`"auto"` (変更)** | `"auto"` (変更) |
| ギルド別オーバーライド | なし | **バックログ** | 実装 |
| スラッシュコマンド | なし | **バックログ** | 実装 |
| ユーザーインパクト | 低 (手動設定要) | **高 (デフォルトで恩恵)** | 高 |
| 実装工数 | 0h | **~0.5h (config変更のみ)** | ~3h |
| リスク | なし | **低 (GPU検証が条件)** | 低-中 |
| 後方互換 | 完全 | **要確認 (モノリンガル会議)** | 完全 (per-guildで対応) |

### Recommended Approach: Option B+ -- デフォルト変更 + バックログ

**デフォルト値変更の根拠**:
- 現在のデフォルト`"ja"`では、ユーザーがauto機能の存在に気づかない
- 技術系コミュニティでは日英混在が常態 -- `"auto"`がより適切なデフォルト
- モノリンガル日本語会議でも、Whisper large-v3の自動検出精度は高く、品質劣化は軽微
- faster-whisperの自動検出は音声冒頭30秒で言語を判定 -- オーバーヘッドは1-3秒（~30%）で15分SLA内

**ギルド別オーバーライドをバックログに残す根拠**:
- 現在のマルチギルド運用は1ギルドのみ -- 需要が具体化していない
- 実装パターンは確立済み（template設定のパターン踏襲）-- 必要時に2-3時間で実装可能
- YAGNI原則 -- 現時点で不要な抽象化を避ける

**デフォルト変更の実装内容** (config.yaml 1行):
```yaml
# Before
language: "ja"
# After
language: "auto"
```

コード変更は不要。`WhisperConfig`のデフォルト値は`"ja"`のまま（config.yamlが優先されるため問題なし）。

### Nice to Have: Per-Guild Language Override (バックログ)

将来実装する場合の設計:

| 変更箇所 | 変更量 | リスク |
|----------|--------|--------|
| `src/state_store.py` | +10-15行（`get_guild_language` / `set_guild_language`） | 極低 |
| `src/pipeline.py` | +5-10行（ギルド別言語の解決・伝播） | 低 |
| `src/transcriber.py` | +5行（`transcribe_file`にlanguage引数追加） | 低 |
| `bot.py` | +30-40行（`/minutes language`コマンド追加） | 低 |
| `tests/` | +20-30行（3-4テストケース） | なし |
| **合計** | **~60-80行** | **低** |

### Performance Analysis

| 設定 | 処理時間（推定） | オーバーヘッド |
|------|-----------------|---------------|
| `language="ja"` | ベースライン | -- |
| `language="auto"` | +1-3秒/話者トラック | ~30% |
| `language="en"` | ベースラインと同等 | ~0% |

**根拠**: faster-whisperの言語自動検出は音声冒頭30秒のみを分析。large-v3モデルの検出精度は高く（日本語: >95%）、追加のデコードパスは不要。5人参加・各15分の会議で+5-15秒の増加は、全体処理時間（3-5分）の中で許容範囲。

### Technical Risks

| # | リスク | 影響度 | 発生確率 | 対策 |
|---|--------|--------|----------|------|
| 1 | auto検出で処理速度低下 | 中 | 中 | GPU検証で確認。受入基準: ja固定比2倍以内 |
| 2 | モノリンガル日本語会議で品質低下 | 低 | 低 | Whisper large-v3の日本語検出精度は高い。問題時は`"ja"`に戻せる |
| 3 | 短いセグメントで言語誤検出 | 低 | 低 | VADフィルタで短いセグメントは既に除外。言語検出は音声冒頭で一括判定 |
| 4 | 既存テスト破壊 | なし | 極低 | `WhisperConfig`のPythonデフォルト`"ja"`は変更なし。config.yamlの値変更のみ |

---

## Strategic Recommendation

### Decision: **CONDITIONAL GO**

**Confidence**: 90% (HIGH)

### Recommended Action: Option B+

1. **デフォルト値変更**: `config.yaml`の`whisper.language`を`"ja"`から`"auto"`に変更
2. **ギルド別オーバーライド**: バックログに残す（マルチギルド需要が具体化した時点で実装）

### Rationale

| Factor | Assessment |
|--------|------------|
| **Must-Have実装状態** | 完了済み -- コード変更不要 |
| **ユーザーインパクト** | 高 -- デフォルト変更だけで全ユーザーが恩恵を受ける |
| **リスク** | 低 -- config.yaml 1行変更、コード変更なし、即座にロールバック可能 |
| **YAGNI適合** | 高 -- 不要な抽象化（per-guild override）を現時点で追加しない |
| **設計原則適合** | 完全 -- Pipeline-first, Async, Graceful degradation, Multi-guild, Minimal state すべてに適合 |

### Conditions for Proceeding

1. **GPU検証**: 実際の録音データ（日英混在）で`language="auto"`の文字起こし品質と処理速度を検証すること
   - 受入基準: 処理速度が`"ja"`固定比2倍以内
   - 受入基準: 英語の専門用語・固有名詞の認識精度が`"ja"`固定より改善
2. **モノリンガル回帰テスト**: 日本語のみの録音で品質劣化がないことを確認
3. **ロールバック手順の確認**: `config.yaml`を`"ja"`に戻すだけでロールバック可能であることを確認

### Why Not Full Implementation Now?

ギルド別オーバーライド（Nice to Have）を現時点で実装しない理由:

1. **需要が未具体化**: マルチギルド運用は現在1ギルドのみ
2. **パターン確立済み**: `StateStore.get_guild_template`パターンの踏襲で、必要時に2-3時間で実装可能
3. **YAGNI**: 使われない抽象化はメンテナンスコストのみを増やす
4. **デフォルト変更で十分**: 大多数のユースケースは`"auto"`で対応可能

---

## Next Steps

1. **GPU検証**: 実際の日英混在録音で`language="auto"`の品質・速度を検証
   - `config.yaml`の`language`を一時的に`"auto"`に変更して実録音をテスト
   - 処理時間と文字起こし品質を`"ja"`固定と比較
2. **デフォルト変更**: 検証結果が受入基準を満たす場合、`config.yaml`の`language`を`"auto"`に変更
3. **リリース**: 変更はconfig値のみのため、コードレビュー・PRは不要（運用設定変更として適用）
4. **バックログ追加**: ギルド別言語オーバーライドをバックログに記録（将来のマルチギルド需要に備える）
5. **モニタリング**: デフォルト変更後のログで`info.language`と`info.language_probability`の値を監視し、誤検出の頻度を確認

---

## Appendix: Code References

| File | Line(s) | Content | Status |
|------|---------|---------|--------|
| `src/transcriber.py` | 80 | `language = None if self._cfg.language == "auto" else self._cfg.language` | 実装済み |
| `src/transcriber.py` | 118-125 | `info.language`, `info.language_probability` ログ出力 | 実装済み |
| `src/config.py` | 27-32 | `VALID_WHISPER_LANGUAGES` -- `"auto"` を含む16言語コード | 実装済み |
| `src/config.py` | 77-83 | `WhisperConfig` -- `language: str = "ja"` | 実装済み |
| `src/config.py` | 344-348 | `_validate()` -- `whisper.language` バリデーション | 実装済み |
| `config.yaml` | 40-41 | `language: "ja"` -- 変更対象 | 設定変更のみ |
| `tests/test_transcriber.py` | 115-128 | `test_auto_language_passes_none` | テスト済み |
| `tests/test_transcriber.py` | 130-143 | `test_explicit_language_passes_through` | テスト済み |
| `tests/test_config.py` | 216-232 | `test_invalid_whisper_language_rejected` | テスト済み |
| `tests/test_config.py` | 234-250 | `test_auto_whisper_language_accepted` | テスト済み |
| `src/state_store.py` | 216-229 | `get_guild_template` / `set_guild_template` -- 参考パターン | 流用可能 |
| `src/pipeline.py` | 40-43 | `_transcript_hash()` -- テンプレート名をキャッシュキーに含む | 参考パターン |

## Appendix: Supported Language Codes

`VALID_WHISPER_LANGUAGES` で定義されている16言語:

| Code | Language | Code | Language |
|------|----------|------|----------|
| `auto` | 自動検出 | `nl` | Dutch |
| `ja` | Japanese | `ru` | Russian |
| `en` | English | `ar` | Arabic |
| `zh` | Chinese | `hi` | Hindi |
| `ko` | Korean | `th` | Thai |
| `fr` | French | `vi` | Vietnamese |
| `de` | German | `id` | Indonesian |
| `es` | Spanish | `pt` | Portuguese |
| `it` | Italian | | |
