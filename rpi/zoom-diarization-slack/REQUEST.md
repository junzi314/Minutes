# Feature Request: Zoom録音 → 話者識別付き議事録 → Slack投稿

## Summary

Zoomのクラウドレコーディング（1台のPCをレコーダーとして使用）から、
話者分離・話者識別付きの議事録を自動生成し、Slackに投稿する独立サービス。

Zoomの文字起こし（VTT）をベースに、DiariZenで話者分離し、
事前登録した声紋で話者を実名にマッピングする。

## Motivation

- 会議参加者全員が同じ部屋にいて、1台のPCでZoomを録音代わりに使用
- Zoom参加者は1人のため、Zoom文字起こしの話者ラベルは全て同一人物扱い
- 音声からの話者分離（DiariZen）+ 声紋照合で実名付き議事録を実現したい
- 既存のDiscord Minutes Botとは独立したサービスとして運用
- 出力先はSlack（Discordではない）

## Requirements

### Must Have

1. **Zoom VTT パース**: Zoomクラウドレコーディングの文字起こし（VTT形式）からタイムスタンプ付きテキストセグメントを抽出
2. **話者分離**: DiariZenで音声(m4a)から話者セグメントを取得
3. **VTT × 話者突合**: Zoom VTTのタイムスタンプとDiariZenの話者セグメントをmajority-vote overlapで突合
4. **話者識別**: 事前登録した声紋（WeSpeaker embedding）とcosine similarityで照合し、Speaker_0 → 実名にマッピング
5. **声紋登録**: メンバーの音声サンプル（10-30秒）からembeddingを抽出・保存する仕組み
6. **議事録生成**: 既存のClaude API（generator.py）を再利用
7. **Slack投稿**: Slack Web APIで議事録を投稿（新規モジュール）
8. **Google Drive監視**: Zoomが自動保存するm4a + VTTファイルを検知
9. **独立プロセス**: Discord不要、別エントリーポイント・別設定ファイルで運用

### Nice to Have

- 未登録話者の自動検出（「Unknown Speaker 1」等）
- 話者登録用のSlackコマンド（音声ファイルをSlackにアップロードして登録）
- pyannote 3.1 への差し替えオプション（Diarizer Protocol経由）
- 話者識別の信頼度スコア表示
- 複数会議室（チャンネル）対応

## Technical Approach

### Architecture

```
[Google Drive]
  ├── meeting_2026-04-03.m4a  (Zoom音声録音)
  └── meeting_2026-04-03.vtt  (Zoom文字起こし)
         │
         ▼
[VideoDriveWatcher] ── 検知（m4a + VTT ペア）
         │
         ├──→ VTTパーサー → list[Segment(start, end, text, speaker="")]
         │
         ├──→ DiariZen(m4a) → list[DiarSegment(start, end, speaker)]
         │         │
         │         ▼
         │    話者識別: embedding照合 → Speaker_0 → "田中さん"
         │
         ▼
    segment_aligner: VTTセグメント × 話者 → list[Segment(start, end, text, "田中さん")]
         │
         ▼
    merger → generator (Claude API) → Slack投稿
```

### Whisper不要の理由

- Zoom VTTがタイムスタンプ付きテキストを提供（Whisperの役割を代替）
- DiariZenは話者分離のみ（テキスト変換機能なし）
- GPU VRAM: DiariZen ~340MBのみ（Whisper ~4.5GB が不要）

### Key Components

**新規モジュール:**
- `src/vtt_parser.py` — Zoom VTT → list[Segment] 変換
- `src/speaker_registry.py` — 声紋登録・照合（WeSpeaker embedding + cosine similarity）
- `src/slack_poster.py` — Slack Web API 投稿
- `diarization_bot.py` — 独立エントリーポイント（Discord不要）
- `config_diarization.yaml` — 専用設定ファイル

**既存モジュール再利用:**
- `src/diarizer.py` — DiariZen話者分離（実装済み）
- `src/segment_aligner.py` — 話者突合（実装済み）
- `src/audio_extractor.py` — FFmpeg音声変換（実装済み）
- `src/merger.py` — トランスクリプト統合
- `src/generator.py` — Claude API議事録生成

### Speaker Identification Flow

```
[登録フェーズ]
音声サンプル(10-30秒) → WeSpeaker → embedding(256d) → speakers.json に保存
  { "田中太郎": [0.12, -0.34, ...], "山口花子": [0.56, 0.78, ...] }

[照合フェーズ]
DiariZen出力の各話者クラスター → WeSpeaker → embedding
  → cosine similarity で speakers.json と比較
  → 最も類似度の高い登録者にマッピング（閾値以下は "Unknown"）
```

### Dependencies

- DiariZen（実装済み、話者分離 + WeSpeaker embedding）
- slack_sdk（Slack Web API）
- 既存依存: Claude API, Google Drive API, FFmpeg

### Hardware

- GPU: NVIDIA RTX 3060 12GB
- DiariZen: ~340MB VRAM（Whisper不要のため余裕大）

## Zoom側の設定

1. クラウドレコーディングを有効化（Pro以上）
2. 「音声のみのファイルを録音する」をON
3. 「音声トランスクリプト」をON（VTT生成）
4. Google Drive自動保存を有効化

## Constraints

- Zoom Pro以上のプラン必須（クラウドレコーディング + 文字起こし）
- DiariZenライセンス: CC BY-NC 4.0（非商用、個人プロジェクトなのでOK）
- 同じ部屋での会議が前提（Zoomはレコーダーとして使用）
- Slack Free planの場合、API制限あり（メッセージ履歴90日等）
