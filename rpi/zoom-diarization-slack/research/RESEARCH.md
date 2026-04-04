# Research Report: Zoom録音 → 話者識別付き議事録 → Slack投稿

**Feature Slug**: zoom-diarization-slack
**Date**: 2026-04-03
**Recommendation**: **CONDITIONAL GO** (85% confidence)

---

## Executive Summary

Zoomクラウドレコーディング（1台のPCをレコーダーとして使用）から話者分離付き議事録を自動生成し、Slackに投稿する独立サービス。既存コードベースの~70%（~1,500行）を直接再利用でき、新規コード~770-1,040行で実現可能。Whisper不要（Zoom VTTが代替）のためGPU負荷が大幅に軽減（~540MB vs ~4.8GB）。ただし、Zoom VTTの日本語精度（20-40%エラー率の可能性）と室内マイクでの話者分離精度は実データでの検証が必須。話者識別（声紋マッチング）はv2として分離し、v1では匿名話者ラベル（Speaker A, B, C）で出荷することを推奨。

---

## 1. Feature Overview

| 項目 | 内容 |
|------|------|
| **名称** | Zoom Diarization Slack Service |
| **種別** | 独立サービス（既存Discord Botとは別プロセス） |
| **入力** | Zoom m4a音声 + VTT文字起こし（Google Drive経由） |
| **処理** | VTTパース → DiariZen話者分離 → 突合 → Claude API議事録生成 |
| **出力** | Slack投稿 |
| **複雑度** | Medium |

---

## 2. Requirements Summary

### Must Have
1. Zoom VTTパーサー（VTT → `list[Segment]`）
2. DiariZen話者分離（m4a → `list[DiarSegment]`）
3. VTT × 話者突合（majority-vote overlap）
4. Claude API議事録生成（既存 `generator.py` 再利用）
5. Slack Web API投稿（新規モジュール）
6. Google Drive監視（既存 `VideoDriveWatcher` 再利用）
7. m4a + VTTファイルペアリング（Drive上で両ファイル揃ってから処理）
8. 独立エントリーポイント（Discord不要）

### v2（初回スコープ外に分離推奨）
- 話者識別（声紋登録 + WeSpeaker embedding照合）
- 未登録話者の自動検出
- pyannote 3.1差し替え
- Whisperフォールバック（VTT品質不足時）

---

## 3. Product Analysis

### User Value: **HIGH**
- 会議後30-60分の手動議事録作成作業を完全自動化
- 「1台のPCでZoomをレコーダー代わり」という一般的なユースケースに直接対応
- Slack投稿により、チームのワークフローに自然に統合

### Strategic Alignment: **MODERATE-GOOD**
- 既存Discord Minutes Botの技術資産を最大限活用（~70%再利用）
- ただし2つのサービスを維持するメンテナンスコストが発生
- 共有モジュールの抽出（`core/` パッケージ化）を推奨

### License
- DiariZen CC BY-NC 4.0 — **非商用限定**
- 個人プロジェクト/チーム内ツールとしての使用は許容範囲
- 商用展開する場合はpyannote 3.1（MIT）またはNeMo MSDD（Apache 2.0）への差し替えが必要
- 既存の `Diarizer` Protocolにより差し替えは容易

---

## 4. Technical Discovery

### 再利用可能モジュール（~1,500行、変更不要）

| モジュール | LOC | 再利用度 | 備考 |
|-----------|-----|---------|------|
| `src/diarizer.py` | 176 | 100% | DiariZen wrapper、Protocol interface |
| `src/segment_aligner.py` | 90 | 100% | Majority-vote overlap |
| `src/audio_extractor.py` | 90 | 100% | FFmpeg async |
| `src/merger.py` | 159 | 100% | Transcript merging |
| `src/generator.py` | 314 | 100% | Claude API（Discord非依存） |
| `src/drive_watcher.py` (VideoDriveWatcher) | ~280 | 100% | m4a/mp4 mime type対応済み |
| `src/state_store.py` | 359 | 100% | Dedup/state tracking |
| `src/config.py` (パターン) | ~50 | 100% | DiarizationConfig存在済み |

### 新規モジュール（~770-1,040行）

| モジュール | 推定LOC | 備考 |
|-----------|---------|------|
| `src/vtt_parser.py` | 60-80 | WebVTT → list[Segment] |
| `src/slack_poster.py` | 100-130 | Slack Web API + Block Kit |
| `src/slack_pipeline.py` | 130-170 | パイプラインアダプター |
| `diarization_slack_bot.py` | 80-100 | 独立エントリーポイント |
| Config追加 | 30-40 | SlackConfig dataclass |
| テスト | 250-350 | 全新規モジュール |

### 重要な技術発見

1. **WeSpeakerは別依存ではない**: DiariZenが内部で`pyannote/wespeaker-voxceleb-resnet34-LM`をバンドル。speaker_identifier実装時にpyannoteのSpeakerEmbedding APIで直接利用可能（追加インストール不要）

2. **Segment dataclassが汎用**: `Segment(start, end, text, speaker)`はWhisper非依存。VTTパーサーが同じdataclassを生成すれば、既存のaligner/mergerがそのまま動作

3. **pipeline.pyはDiscord結合**: `run_pipeline_from_segments()`は`OutputChannel`（Discord型）を要求。Slack用には並行アダプター（`slack_pipeline.py`）が必要

4. **ファイルペアリングが必要**: Zoomはm4aとVTTを非同期にアップロード。VideoDriveWatcherに「ペア待ち」ロジックの追加が必要（タイムアウト付きバッファ）

---

## 5. Technical Analysis

### Feasibility: **HIGH**
### Complexity: **MEDIUM**

### VRAM使用量

| モデル | VRAM | 備考 |
|--------|------|------|
| DiariZen | ~340 MB | 話者分離 |
| WeSpeaker embeddings | ~200 MB | DiariZen内部モデルと共有可能 |
| **合計** | **~540 MB** | RTX 3060 12GBで余裕大 |
| ~~Whisper large-v3~~ | ~~4,500 MB~~ | **不要**（Zoom VTTが代替） |

### リスク評価

| リスク | 深刻度 | 確率 | 対策 |
|--------|--------|------|------|
| Zoom VTT日本語品質不足 | Medium | 60% | Whisperフォールバックパス。RTX 3060でDiariZen+Whisper同時可能 |
| 室内マイク話者分離精度低下 | Medium | 40% | Gate 0で実データ検証。DER>30%なら要件再検討 |
| m4a+VTTペアリング競合 | Low | 40% | タイムアウト付きバッファ（5分）、state_store活用 |
| Slack API変更 | Low | 20% | slack-sdk v3使用、files.getUploadURLExternal採用 |

### 工数見積

| フェーズ | 内容 | 工数 |
|---------|------|------|
| Phase 0 | 実データ検証（Gate 0） | 4h |
| Phase 1 | VTTパーサー + Slackポスター | 10-14h |
| Phase 2 | パイプラインアダプター + エントリーポイント | 6-8h |
| Phase 3 | ファイルペアリングロジック | 4-6h |
| Phase 4 | 統合テスト + ポリッシュ | 6-8h |
| **合計** | | **30-40h (4-5日)** |

---

## 6. Strategic Recommendation

### Decision: **CONDITIONAL GO** (85% confidence)

### 条件

**Gate 0（実装前、必須）:**
1. 実際のZoom会議を録音し、m4a + VTTを取得
2. VTTの日本語精度を目視確認（エラー率40%超ならWhisperフォールバック必須）
3. DiariZenでm4aを処理し、3-4名の話者分離精度を確認（DER 30%超なら要件再検討）

**Gate 1（実装開始時）:**
- 話者識別（声紋マッチング）はv2に分離。v1では匿名ラベル（Speaker A, B, C）で出荷
- 共有モジュール整理（コピペ禁止、同一ソースからimport）

**Gate 2（VTTパーサー + ファイルペアリング完了後）:**
- 実Zoomデータでのend-to-endテスト実施

### Rationale

ライセンス懸念（DiariZen CC BY-NC 4.0）は非商用プロジェクトのため解消済み。既存コードの70%再利用により実装リスクが大幅に低減。Whisper不要によるVRAM節約（~4.5GB→0）はアーキテクチャ上の大きな利点。最大のリスクはZoom VTTの日本語品質だが、Whisperフォールバックパスが存在し、RTX 3060 12GBで同時実行可能。

---

## 7. Next Steps

1. **Gate 0 実施**: Zoom会議を録音してm4a + VTTを取得、品質検証
2. **Gate 0 通過後**: `/rpi:plan zoom-diarization-slack` で計画フェーズへ
3. **v1スコープ**: VTTパース → DiariZen話者分離 → 突合 → Claude → Slack（声紋なし）
4. **v2スコープ**: 声紋登録 + 話者識別 + Whisperフォールバック
