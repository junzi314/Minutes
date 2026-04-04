# eng.md -- Technical Specification: Zoom Diarization Slack Service

**Feature Slug**: zoom-diarization-slack
**Date**: 2026-04-03
**Size**: L (Complex)
**Estimated LOC**: ~500 (new) + ~0 (modified) + ~400 (tests)
**Prerequisite Gate**: Gate 0 PENDING (Zoom VTT日本語品質 + DiariZen室内マイク精度の実データ検証)

---

## 1. 技術概要

Zoomクラウドレコーディング（1台のPCをレコーダーとして使用）から、話者分離付き議事録を自動生成し、Slackに投稿する独立サービス。

既存Discord Minutes Botのコアモジュール（~1,500行）を直接再利用し、新規コード~500行で実現する。Whisperは不要（Zoom VTTが代替）のため、GPU VRAM負荷はDiariZen ~340MBのみ。

### 設計原則

1. **既存モジュール無変更**: `diarizer.py`, `segment_aligner.py`, `audio_extractor.py`, `merger.py`, `generator.py`, `state_store.py`, `errors.py` は一切変更しない
2. **Discord非依存**: 新規モジュールは `discord` パッケージを一切importしない
3. **独立プロセス**: `zoom_slack_bot.py` で単独起動、既存 `bot.py` とは別プロセス
4. **同一リポジトリ**: 共有モジュールを `src/` からimportし、コード重複を回避

---

## 2. アーキテクチャ設計

### 2.1 システム構成図

```
[Google Drive]
  ├── GMT20260403-050000_Recording.m4a   (Zoom音声)
  └── GMT20260403-050000_Recording.transcript.vtt  (Zoom文字起こし)
         │
         ▼
[ZoomDriveWatcher]  ── ポーリング検知 + m4a/VTTペアリング
         │
         │ ペア完成コールバック: (m4a_path, vtt_path, source_label)
         ▼
[SlackPipeline]
  ├── vtt_parser.py: VTT → list[Segment(start, end, text, speaker="")]
  ├── audio_extractor.py: m4a → WAV 16kHz mono  ← 既存再利用
  ├── diarizer.py: WAV → list[DiarSegment]       ← 既存再利用
  ├── segment_aligner.py: VTTセグ × 話者 → list[Segment]  ← 既存再利用
  ├── merger.py: Segment[] → transcript文字列    ← 既存再利用
  ├── generator.py: transcript → 議事録MD        ← 既存再利用
  └── slack_poster.py: 議事録 → Slack投稿        ← 新規
```

### 2.2 データフロー

```
m4a (Google Drive)
  │
  ├──→ [audio_extractor] ──→ WAV 16kHz mono ──→ [diarizer] ──→ list[DiarSegment]
  │                                                                    │
  │                                                                    ▼
VTT (Google Drive)                                          [segment_aligner]
  │                                                            ↑         │
  └──→ [vtt_parser] ──→ list[Segment(speaker="")]  ───────────┘         │
                                                                        ▼
                                                              list[Segment(speaker="Speaker_0")]
                                                                        │
                                                                        ▼
                                                              [merge_transcripts]
                                                                        │
                                                                        ▼
                                                              transcript: str
                                                                        │
                                                                 ┌──────┤
                                                                 │      ▼
                                                                 │  [state_store] キャッシュ確認
                                                                 │      │
                                                                 │      ▼ (cache miss)
                                                                 │  [generator.generate()]
                                                                 │      │
                                                                 ▼      ▼
                                                              minutes_md: str
                                                                        │
                                                                        ▼
                                                              [slack_poster] ──→ Slack
```

### 2.3 コンポーネント一覧

| 種別 | ファイル | LOC(推定) | 説明 |
|------|----------|-----------|------|
| **再利用** | `src/diarizer.py` | 176 | DiariZen話者分離 |
| **再利用** | `src/segment_aligner.py` | 90 | Majority-vote overlap突合 |
| **再利用** | `src/audio_extractor.py` | 90 | FFmpeg音声変換 |
| **再利用** | `src/merger.py` | 159 | トランスクリプト統合 |
| **再利用** | `src/generator.py` | 314 | Claude API議事録生成 |
| **再利用** | `src/state_store.py` | 359 | 処理重複排除 + キャッシュ |
| **再利用** | `src/errors.py` | 69 | 例外階層 |
| **再利用** | `src/config.py` (部分) | ~50 | DiarizationConfig, GeneratorConfig, MergerConfig |
| **新規** | `src/vtt_parser.py` | 60-80 | Zoom VTTパーサー |
| **新規** | `src/slack_poster.py` | 100-130 | Slack投稿 |
| **新規** | `src/slack_pipeline.py` | 130-170 | パイプラインオーケストレーター |
| **新規** | `src/zoom_drive_watcher.py` | 100-140 | m4a+VTTペアリング付きDrive監視 |
| **新規** | `zoom_slack_bot.py` | 80-100 | 独立エントリーポイント |
| **新規** | `src/config.py` (追加分) | 30-40 | SlackConfig + ZoomDriveConfig |

---

## 3. 再利用モジュール詳細

以下のモジュールは変更なし（AS-IS）で再利用する。

### 3.1 `src/diarizer.py` (176 LOC)

DiariZen話者分離。`Diarizer` Protocolと `DiariZenDiarizer` 実装。

**入力**: WAV 16kHz mono ファイルパス
**出力**: `list[DiarSegment(start, end, speaker)]`

VTTセグメントとの突合前に音声からの話者分離を行う。Slackパイプラインでは `load_model()` → `diarize()` → `unload_model()` のライフサイクルをファイル単位で管理する。

### 3.2 `src/segment_aligner.py` (90 LOC)

VTTパーサーが生成する `list[Segment]`（speaker=""）と、DiariZenが生成する `list[DiarSegment]` を突合する。

**重要**: パラメータ名は `whisper_segments` だが、VTTパーサーが生成する `Segment` も同じdataclassのため、そのまま渡せる。Whisperに依存する実装はない。

### 3.3 `src/audio_extractor.py` (90 LOC)

m4aファイルをWAV 16kHz monoに変換。DiariZenの入力として必要。

### 3.4 `src/merger.py` (159 LOC)

`merge_transcripts()`: Segment配列を時系列ソート → 同一話者の隣接セグメント結合 → `[MM:SS] Speaker: text` 形式の文字列に変換。

`format_transcript_markdown()`: トランスクリプトを整形Markdown化。Slack投稿時の添付ファイル用。

### 3.5 `src/generator.py` (314 LOC)

`MinutesGenerator`: Claude API呼び出しで議事録MD生成。Discord非依存。`guild_name` / `channel_name` パラメータは空文字でも動作する。テンプレート変数の未使用プレースホルダーは空文字に置換される。

### 3.6 `src/state_store.py` (359 LOC)

処理重複排除（`mark_processing` / `mark_success` / `mark_failed` / `is_known`）と議事録キャッシュ（`get_cached_minutes` / `put_cached_minutes`）。

`rec_id` はCraig固有のパターンだが、任意の文字列キーで動作する。ZoomDriveWatcherでは `zoom:{drive_file_id}` 形式のキーを使用する。

### 3.7 `src/errors.py` (69 LOC)

`DiarizationError`, `GenerationError`, `DriveWatchError` 等を使用。新規例外 `SlackPostingError`, `VttParseError` を追加する。

### 3.8 `src/config.py` (既存dataclass)

以下のdataclassを再利用:
- `DiarizationConfig` — DiariZenモデル設定
- `GeneratorConfig` — Claude API設定
- `MergerConfig` — トランスクリプト統合設定
- `GoogleDriveConfig` — Drive監視設定

---

## 4. 新規モジュール設計

### 4.1 `src/vtt_parser.py` (~60-80 LOC)

Zoom WebVTTファイルをパースし、`list[Segment]` に変換する。

#### Zoom VTTフォーマット

```
WEBVTT

1
00:00:01.000 --> 00:00:05.500
こんにちは、会議を始めましょう。

2
00:00:06.200 --> 00:00:10.800
はい、まずは前回の振り返りからお願いします。
```

**特徴**:
- `WEBVTT` ヘッダー行で始まる
- キュー番号（数字のみの行）がある場合とない場合がある
- タイムスタンプ形式: `HH:MM:SS.mmm --> HH:MM:SS.mmm`
- 話者ラベルなし（Zoom参加者が1名のため全テキストが同一話者扱い）
- テキストが複数行にまたがる場合がある

#### Public API

```python
"""Parse Zoom WebVTT transcript files into Segment list."""

from __future__ import annotations

from pathlib import Path

from src.transcriber import Segment


class VttParseError(Exception):
    """Raised when VTT file parsing fails."""
    pass


def parse_vtt(vtt_path: Path) -> list[Segment]:
    """Parse a Zoom WebVTT file into a list of Segment objects.

    Each VTT cue becomes one Segment with:
      - start/end: timestamps in seconds (float)
      - text: cue text (whitespace-normalized, multi-line joined)
      - speaker: "" (empty — populated later by segment_aligner)

    Args:
        vtt_path: Path to the .vtt file.

    Returns:
        list[Segment] sorted by start time.

    Raises:
        VttParseError: If the file is not valid WebVTT.
        FileNotFoundError: If vtt_path does not exist.
    """


def _parse_timestamp(ts: str) -> float:
    """Convert 'HH:MM:SS.mmm' or 'MM:SS.mmm' to seconds.

    Args:
        ts: Timestamp string from VTT file.

    Returns:
        Time in seconds as float.

    Raises:
        VttParseError: If timestamp format is invalid.
    """
```

#### 内部実装ロジック

```python
def parse_vtt(vtt_path: Path) -> list[Segment]:
    if not vtt_path.exists():
        raise FileNotFoundError(f"VTT file not found: {vtt_path}")

    text = vtt_path.read_text(encoding="utf-8")

    # ヘッダー検証
    if not text.strip().startswith("WEBVTT"):
        raise VttParseError(f"Not a valid WebVTT file: {vtt_path.name}")

    segments: list[Segment] = []
    # 空行で分割してキュー単位に処理
    blocks = text.strip().split("\n\n")

    for block in blocks[1:]:  # 最初のブロック（ヘッダー）をスキップ
        lines = block.strip().splitlines()
        if not lines:
            continue

        # キュー番号行をスキップ（数字のみの行）
        start_idx = 0
        if lines[0].strip().isdigit():
            start_idx = 1

        if start_idx >= len(lines):
            continue

        # タイムスタンプ行を検索
        timestamp_line = lines[start_idx]
        if "-->" not in timestamp_line:
            continue

        parts = timestamp_line.split("-->")
        start = _parse_timestamp(parts[0].strip())
        end = _parse_timestamp(parts[1].strip())

        # テキスト行（タイムスタンプの次の行から）
        cue_text = " ".join(
            line.strip() for line in lines[start_idx + 1:] if line.strip()
        )

        if cue_text:
            segments.append(Segment(
                start=start,
                end=end,
                text=cue_text,
                speaker="",
            ))

    if not segments:
        raise VttParseError(f"No cues found in VTT file: {vtt_path.name}")

    segments.sort(key=lambda s: s.start)
    logger.info("Parsed %d segments from %s", len(segments), vtt_path.name)
    return segments
```

#### エッジケース

| ケース | 動作 |
|--------|------|
| ファイル不存在 | `FileNotFoundError` |
| WEBVTTヘッダーなし | `VttParseError` |
| キューが0件 | `VttParseError` |
| キュー番号あり/なし混在 | 両方対応（数字のみ行をスキップ） |
| 複数行テキスト | スペースで結合 |
| 空のテキストキュー | スキップ |
| BOM付きUTF-8 | `read_text(encoding="utf-8")` で自動処理 |

---

### 4.2 `src/slack_poster.py` (~100-130 LOC)

Slack Web APIを使用して議事録を投稿する。

#### Public API

```python
"""Slack Web API posting for meeting minutes."""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class SlackPostingError(Exception):
    """Raised when Slack API posting fails."""
    pass


@dataclass(frozen=True)
class SlackConfig:
    """Slack service configuration."""
    bot_token: str = ""
    channel_id: str = ""
    max_text_length: int = 3000


async def post_minutes_to_slack(
    cfg: SlackConfig,
    minutes_md: str,
    date: str,
    speakers: str,
    *,
    source_label: str = "",
) -> str:
    """Post meeting minutes to Slack using Block Kit.

    Sends a structured message with:
      - Header block: 会議議事録 - {date}
      - Section block: 参加者
      - Section block: まとめ (extracted from minutes_md)
      - Section block: 次のステップ (extracted from minutes_md)
      - File upload: 全文議事録 (.md attachment)

    Args:
        cfg: Slack configuration.
        minutes_md: Full minutes markdown text.
        date: Meeting date string (e.g. "2026-04-03 14:00").
        speakers: Comma-separated speaker names.
        source_label: Source identifier for logging.

    Returns:
        Slack message timestamp (ts) of the posted message.

    Raises:
        SlackPostingError: If Slack API call fails.
    """


async def post_error_to_slack(
    cfg: SlackConfig,
    error_message: str,
    stage: str,
) -> str:
    """Post an error notification to Slack.

    Args:
        cfg: Slack configuration.
        error_message: Error description.
        stage: Pipeline stage where error occurred.

    Returns:
        Slack message timestamp (ts).

    Raises:
        SlackPostingError: If Slack API call fails.
    """


async def send_slack_status(
    cfg: SlackConfig,
    thread_ts: str | None,
    status_text: str,
) -> str | None:
    """Send or update a status message in Slack.

    If thread_ts is provided, sends as a thread reply.
    Status messages are non-critical; failures are logged but not raised.

    Args:
        cfg: Slack configuration.
        thread_ts: Parent message timestamp for threading (None = new message).
        status_text: Status text to display.

    Returns:
        Message timestamp (ts) or None on failure.
    """
```

#### Block Kit メッセージ構造

```python
def _build_minutes_blocks(
    minutes_md: str,
    date: str,
    speakers: str,
    max_text_length: int,
) -> list[dict]:
    """Build Slack Block Kit blocks for minutes posting.

    Structure:
      1. Header: "会議議事録 - 2026-04-03 14:00"
      2. Divider
      3. Section: 参加者
      4. Section: まとめ (extracted, truncated)
      5. Section: 次のステップ (extracted, truncated)
      6. Context: "詳細は添付ファイルを参照"
    """
    # 議事録MDからセクション抽出（poster.pyのパターンと同じ正規表現）
    summary = _extract_section(minutes_md, _SUMMARY_PATTERN)
    next_steps = _extract_section(minutes_md, _DECISIONS_PATTERN)

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"会議議事録 - {date}"}},
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*参加者:* {speakers}"},
        },
    ]

    if summary:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*まとめ*\n{_truncate(summary, max_text_length)}"},
        })

    if next_steps:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*次のステップ*\n{_truncate(next_steps, max_text_length)}"},
        })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "詳細は添付ファイルを参照"}],
    })

    return blocks
```

#### ファイルアップロード

```python
async def _upload_minutes_file(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    minutes_md: str,
    date: str,
) -> None:
    """Upload the full minutes as a .md file attachment in the thread.

    Uses files_upload_v2 (recommended by Slack API docs).
    """
    safe_date = date.replace("/", "-").replace(" ", "_")
    filename = f"minutes_{safe_date}.md"

    await asyncio.to_thread(
        client.files_upload_v2,
        channel=channel_id,
        thread_ts=thread_ts,
        content=minutes_md,
        filename=filename,
        title=f"議事録全文 - {date}",
    )
```

#### エラーハンドリング

| エラー | 動作 |
|--------|------|
| `slack_sdk.errors.SlackApiError` (rate_limited) | exponential backoff + retry (max 3回) |
| `slack_sdk.errors.SlackApiError` (channel_not_found) | `SlackPostingError` |
| `slack_sdk.errors.SlackApiError` (not_authed) | `SlackPostingError` |
| ステータスメッセージ送信失敗 | ログ警告、例外発生なし |
| ファイルアップロード失敗 | ログ警告、例外発生なし（メッセージ投稿は成功済み） |

---

### 4.3 `src/slack_pipeline.py` (~130-170 LOC)

Slackフロー専用のパイプラインオーケストレーター。既存 `pipeline.py` のDiscord依存を排除し、同じ再利用コンポーネントを直接呼び出す。

#### 既存pipeline.pyを再利用できない理由

| 結合ポイント | `pipeline.py` の該当コード | 問題 |
|-------------|--------------------------|------|
| Guild ID取得 | `output_channel.guild.id` | Discord固有 |
| Guild名取得 | `output_channel.guild.name` | Discord固有 |
| Channel名取得 | `output_channel.name` | Discord固有 |
| ステータス送信 | `send_status_update()` → `discord.Message` | Discord固有 |
| エラー投稿 | `post_error()` → `discord.Embed` | Discord固有 |
| 議事録投稿 | `post_minutes()` → `discord.Embed` + `discord.File` | Discord固有 |
| ステータス削除 | `status_msg.delete()` | Discord固有 |

#### Public API

```python
"""Pipeline orchestrator for Zoom VTT → diarization → Slack flow."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime
from pathlib import Path

from src.audio_extractor import extract_audio
from src.config import DiarizationConfig, GeneratorConfig, MergerConfig
from src.diarizer import Diarizer
from src.errors import DiarizationError, GenerationError
from src.generator import MinutesGenerator
from src.merger import merge_transcripts
from src.segment_aligner import align_segments
from src.slack_poster import SlackConfig, post_minutes_to_slack, post_error_to_slack, send_slack_status
from src.state_store import StateStore
from src.transcriber import Segment
from src.vtt_parser import parse_vtt

logger = logging.getLogger(__name__)


def _transcript_hash(transcript: str, template_name: str = "minutes") -> str:
    """Compute a deterministic cache key from the transcript text and template."""
    key = f"{template_name}:{transcript}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


async def run_slack_pipeline(
    m4a_path: Path,
    vtt_path: Path,
    *,
    diarizer: Diarizer,
    generator: MinutesGenerator,
    state_store: StateStore,
    slack_cfg: SlackConfig,
    diar_cfg: DiarizationConfig,
    merger_cfg: MergerConfig,
    source_label: str = "unknown",
    template_name: str = "minutes",
    timeout_sec: int = 3600,
) -> None:
    """Execute the full Zoom→Slack pipeline.

    Stages:
      1. VTTパース: vtt_path → list[Segment(speaker="")]
      2. 音声変換: m4a_path → WAV 16kHz mono
      3. 話者分離: WAV → list[DiarSegment]
      4. 話者突合: VTTセグメント × DiarSegment → list[Segment(speaker="Speaker_N")]
      5. トランスクリプト統合: merge_transcripts()
      6. 議事録生成: generator.generate() (with cache)
      7. Slack投稿: post_minutes_to_slack()

    Args:
        m4a_path: Path to Zoom audio file.
        vtt_path: Path to Zoom VTT transcript file.
        diarizer: Diarizer instance (must be loaded).
        generator: MinutesGenerator instance (must be loaded).
        state_store: StateStore for dedup and caching.
        slack_cfg: Slack posting configuration.
        diar_cfg: Diarization configuration.
        merger_cfg: Merger configuration.
        source_label: Identifier for logging.
        template_name: Prompt template name.
        timeout_sec: Pipeline timeout in seconds.

    Raises:
        VttParseError: If VTT parsing fails.
        DiarizationError: If diarization fails.
        GenerationError: If Claude API fails.
        SlackPostingError: If Slack posting fails.
        TimeoutError: If pipeline exceeds timeout_sec.
    """
```

#### 内部実装

```python
async def run_slack_pipeline(...) -> None:
    pipeline_start = time.monotonic()
    thread_ts: str | None = None

    try:
        async with asyncio.timeout(timeout_sec):
            # Stage 1: VTTパース
            logger.info("[vtt_parse] Parsing %s", vtt_path.name)
            vtt_segments = parse_vtt(vtt_path)
            logger.info("[vtt_parse] Got %d segments", len(vtt_segments))

            # Status: 話者分離中
            thread_ts = await send_slack_status(
                slack_cfg, thread_ts,
                f"処理中: 話者分離 ({m4a_path.name})...",
            )

            # Stage 2: 音声変換 (m4a → WAV)
            wav_path = m4a_path.with_suffix(".wav")
            await extract_audio(
                m4a_path, wav_path,
                timeout_sec=diar_cfg.ffmpeg_timeout_sec,
            )

            # Stage 3: 話者分離
            logger.info("[diarize] Starting diarization")
            diar_segments = await asyncio.to_thread(
                diarizer.diarize, wav_path,
            )
            logger.info("[diarize] Got %d segments", len(diar_segments))

            # Stage 4: 話者突合
            aligned_segments = align_segments(
                vtt_segments, diar_segments,
                fallback_speaker="Speaker",
            )

            # Stage 5: トランスクリプト統合
            transcript = merge_transcripts(aligned_segments, merger_cfg)
            if not transcript:
                raise DiarizationError(
                    f"Merged transcript is empty for {source_label}"
                )

            # Stage 6: 議事録生成 (with cache)
            speakers_str = ", ".join(sorted({s.speaker for s in aligned_segments}))
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            th = _transcript_hash(transcript, template_name)
            minutes_md = state_store.get_cached_minutes(th)

            if minutes_md is None:
                thread_ts = await send_slack_status(
                    slack_cfg, thread_ts, "議事録を生成中..."
                )
                minutes_md = await generator.generate(
                    transcript=transcript,
                    date=date_str,
                    speakers=speakers_str,
                    template_name=template_name,
                )
                state_store.put_cached_minutes(th, minutes_md)
            else:
                logger.info("Using cached minutes for %s", source_label)

            # Stage 7: Slack投稿
            thread_ts = await send_slack_status(
                slack_cfg, thread_ts, "議事録を投稿中..."
            )
            await post_minutes_to_slack(
                cfg=slack_cfg,
                minutes_md=minutes_md,
                date=date_str,
                speakers=speakers_str,
                source_label=source_label,
            )

            elapsed = time.monotonic() - pipeline_start
            logger.info(
                "Slack pipeline complete for %s in %.1fs (%d segments)",
                source_label, elapsed, len(aligned_segments),
            )

    except TimeoutError:
        elapsed = time.monotonic() - pipeline_start
        logger.error(
            "Slack pipeline timed out for %s after %.1fs (limit=%ds)",
            source_label, elapsed, timeout_sec,
        )
        await post_error_to_slack(
            slack_cfg,
            f"パイプライン処理がタイムアウトしました ({timeout_sec}秒)",
            stage="timeout",
        )
        raise

    except Exception as exc:
        stage = getattr(exc, "stage", "unknown")
        logger.error(
            "Slack pipeline failed for %s at stage '%s': %s",
            source_label, stage, exc,
        )
        await post_error_to_slack(
            slack_cfg, str(exc), stage=stage,
        )
        raise
```

#### pipeline.py との対比

| 機能 | `pipeline.py` | `slack_pipeline.py` |
|------|--------------|---------------------|
| 入力 | SpeakerAudio[] / Craig | m4a + VTT ファイルパス |
| 文字起こし | Whisper | VTTパーサー |
| 話者分離 | なし (Craig ZIP = 話者別) | DiariZen |
| 出力先 | Discord Embed + File | Slack Block Kit + File |
| ステータス | `discord.Message.edit()` | Slack thread reply |
| 設定型 | `Config` (全体) | 個別Config引数 |

---

### 4.4 `src/zoom_drive_watcher.py` (~100-140 LOC)

Google Drive上のZoom録音ファイル（m4a + VTT）を監視し、ペアが揃ったらコールバックを呼び出す。

#### ファイルペアリングロジック

Zoomは m4a と VTT を**非同期にアップロード**するため、片方だけ先に検知される可能性がある。

**ファイル名規則（Zoom標準）**:
```
GMT20260403-050000_Recording.m4a
GMT20260403-050000_Recording.transcript.vtt
```

**ペアリングキー**: ファイル名からタイムスタンプ部分（`GMT20260403-050000`）を抽出し、ペアの識別子とする。

**待機ロジック**: 片方のファイルのみ検知した場合、バッファに保持し、設定されたタイムアウト（デフォルト300秒）まで待機。タイムアウト後はVTTなしとして処理（Whisperフォールバック用の将来拡張ポイント）。

#### Public API

```python
"""Google Drive watcher for Zoom recording file pairs (m4a + VTT)."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.config import GoogleDriveConfig
from src.errors import DriveWatchError
from src.state_store import StateStore

logger = logging.getLogger(__name__)

# Callback type: (m4a_path, vtt_path, source_label) -> None
OnZoomPairCallback = Callable[
    [Path, Path, str],
    Awaitable[None],
]

# Zoom recording filename pattern: GMT{date}-{time}_Recording
_ZOOM_SESSION_PATTERN = re.compile(
    r"^(GMT\d{8}-\d{6}_Recording)"
)


@dataclass(frozen=True)
class ZoomDriveConfig:
    """Configuration for Zoom Drive watcher."""
    enabled: bool = False
    credentials_path: str = "credentials.json"
    folder_id: str = ""
    poll_interval_sec: int = 30
    pair_timeout_sec: int = 300
    m4a_pattern: str = "*.m4a"
    vtt_pattern: str = "*.vtt"
    mime_types: tuple[str, ...] = (
        "audio/mp4",
        "audio/x-m4a",
        "text/vtt",
        "text/plain",
    )


@dataclass
class _PendingPair:
    """Buffer for an incomplete m4a+VTT pair."""
    session_key: str
    m4a_file: dict[str, str] | None = None    # Drive file info {id, name}
    vtt_file: dict[str, str] | None = None     # Drive file info {id, name}
    first_seen: float = 0.0                     # monotonic timestamp

    @property
    def is_complete(self) -> bool:
        return self.m4a_file is not None and self.vtt_file is not None


class ZoomDriveWatcher:
    """Monitors Google Drive for Zoom m4a + VTT file pairs.

    Polls a Drive folder for audio (m4a) and transcript (VTT) files.
    When a matching pair is found, downloads both files and invokes
    the callback. Incomplete pairs are buffered with a configurable
    timeout.

    Usage::

        watcher = ZoomDriveWatcher(cfg, state_store, on_pair=my_callback)
        watcher.start()
        ...
        watcher.stop()
    """

    def __init__(
        self,
        cfg: ZoomDriveConfig,
        state_store: StateStore,
        on_pair: OnZoomPairCallback,
    ) -> None: ...

    @property
    def is_running(self) -> bool: ...

    def start(self) -> None:
        """Create the background polling task."""
        ...

    def stop(self) -> None:
        """Cancel the background polling task."""
        ...
```

#### 内部実装: ペアリングアルゴリズム

```python
class ZoomDriveWatcher:
    def __init__(self, cfg, state_store, on_pair):
        self._cfg = cfg
        self._state_store = state_store
        self._on_pair = on_pair
        self._task: asyncio.Task[None] | None = None
        self._service: Any = None
        self._pending: dict[str, _PendingPair] = {}  # session_key -> pair

    async def _watch_loop(self) -> None:
        loop = asyncio.get_running_loop()

        while True:
            try:
                files = await loop.run_in_executor(None, self._list_files_sync)

                for f in files:
                    file_key = f"zoom:{f['id']}"
                    if self._state_store.is_known(file_key):
                        continue
                    self._buffer_file(f)

                # 完成ペアの処理
                completed = [
                    k for k, p in self._pending.items() if p.is_complete
                ]
                for session_key in completed:
                    pair = self._pending.pop(session_key)
                    await self._process_pair(loop, pair)

                # タイムアウトしたペアの処理
                self._expire_pending()

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Watch loop error: %s", exc)

            await asyncio.sleep(self._cfg.poll_interval_sec)

    def _buffer_file(self, file_info: dict[str, str]) -> None:
        """Add a file to the pending buffer, grouped by session key."""
        name = file_info["name"]
        match = _ZOOM_SESSION_PATTERN.match(name)
        if not match:
            logger.debug("Skipping non-Zoom file: %s", name)
            return

        session_key = match.group(1)

        if session_key not in self._pending:
            self._pending[session_key] = _PendingPair(
                session_key=session_key,
                first_seen=time.monotonic(),
            )

        pair = self._pending[session_key]
        if name.endswith(".m4a"):
            pair.m4a_file = file_info
        elif name.endswith(".vtt"):
            pair.vtt_file = file_info

    def _expire_pending(self) -> None:
        """Remove pairs that have timed out waiting for the partner file."""
        now = time.monotonic()
        expired = [
            k for k, p in self._pending.items()
            if now - p.first_seen > self._cfg.pair_timeout_sec and not p.is_complete
        ]
        for key in expired:
            pair = self._pending.pop(key)
            logger.warning(
                "Pair timeout for session %s (m4a=%s, vtt=%s)",
                key,
                pair.m4a_file["name"] if pair.m4a_file else "MISSING",
                pair.vtt_file["name"] if pair.vtt_file else "MISSING",
            )
            # Mark the existing file(s) as known to avoid re-processing
            for f in [pair.m4a_file, pair.vtt_file]:
                if f:
                    self._state_store.mark_failed(
                        f"zoom:{f['id']}", "pair_timeout"
                    )

    async def _process_pair(
        self,
        loop: asyncio.AbstractEventLoop,
        pair: _PendingPair,
    ) -> None:
        """Download both files and invoke the callback."""
        m4a_info = pair.m4a_file
        vtt_info = pair.vtt_file
        assert m4a_info is not None and vtt_info is not None

        m4a_key = f"zoom:{m4a_info['id']}"
        vtt_key = f"zoom:{vtt_info['id']}"

        # Mark both as processing
        if not self._state_store.mark_processing(
            m4a_key, source="zoom_drive", source_id=m4a_info["id"],
            file_name=m4a_info["name"],
        ):
            return
        self._state_store.mark_processing(
            vtt_key, source="zoom_drive", source_id=vtt_info["id"],
            file_name=vtt_info["name"],
        )

        tmp_dir_obj = tempfile.TemporaryDirectory(prefix="zoom-")
        tmp_path = Path(tmp_dir_obj.name)

        try:
            # Download both files
            service = self._get_service()
            m4a_bytes = await loop.run_in_executor(
                None, _download_drive_file, service, m4a_info["id"], m4a_info["name"],
            )
            vtt_bytes = await loop.run_in_executor(
                None, _download_drive_file, service, vtt_info["id"], vtt_info["name"],
            )

            m4a_path = tmp_path / m4a_info["name"]
            vtt_path = tmp_path / vtt_info["name"]
            m4a_path.write_bytes(m4a_bytes)
            vtt_path.write_bytes(vtt_bytes)

            source_label = f"zoom:{pair.session_key}"
            await self._on_pair(m4a_path, vtt_path, source_label)

            self._state_store.mark_success(m4a_key)
            self._state_store.mark_success(vtt_key)

        except Exception as exc:
            self._state_store.mark_failed(m4a_key, str(exc))
            self._state_store.mark_failed(vtt_key, str(exc))
            raise
        finally:
            try:
                tmp_dir_obj.cleanup()
            except OSError:
                pass
```

#### Drive API クエリ

`_list_files_sync()` は既存の `_build_drive_service()` と `_download_drive_file()` (drive_watcher.py のモジュールレベルユーティリティ) を再利用する。

```python
from src.drive_watcher import _build_drive_service, _download_drive_file
```

mimeTypeクエリ:
```
'{folder_id}' in parents
AND trashed = false
AND (mimeType = 'audio/mp4' OR mimeType = 'audio/x-m4a' OR mimeType = 'text/vtt' OR mimeType = 'text/plain')
```

ローカルフィルタ: `fnmatch` でm4a/vttパターンマッチ後、ファイル名末尾で分類。

---

### 4.5 `zoom_slack_bot.py` (~80-100 LOC)

独立エントリーポイント。Discord Botとは完全に別プロセスとして起動する。

#### Public API

```python
"""Zoom Diarization → Slack Bot — independent entry point.

Usage:
    python3 zoom_slack_bot.py
    python3 zoom_slack_bot.py --config config_slack.yaml
    python3 zoom_slack_bot.py --log-level DEBUG
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def main() -> None:
    """Parse CLI args, load config, and start the async event loop."""
    ...


async def _run(config_path: str) -> None:
    """Initialize components and run the watcher loop.

    1. Load config from config_slack.yaml
    2. Initialize StateStore
    3. Initialize MinutesGenerator (load Claude API client)
    4. Initialize DiariZenDiarizer (load model)
    5. Define on_pair callback (calls run_slack_pipeline)
    6. Start ZoomDriveWatcher
    7. Run forever (handle SIGINT/SIGTERM for graceful shutdown)
    """
    ...


if __name__ == "__main__":
    main()
```

#### 初期化シーケンス

```python
async def _run(config_path: str) -> None:
    cfg = load_slack_config(config_path)

    # State store
    state_store = StateStore(Path(cfg.state_dir))

    # Generator
    generator = MinutesGenerator(cfg.generator)
    generator.load()

    # Diarizer
    diarizer = DiariZenDiarizer(cfg.diarization)
    diarizer.load_model()

    # Callback
    async def on_pair(m4a_path: Path, vtt_path: Path, source_label: str) -> None:
        await run_slack_pipeline(
            m4a_path=m4a_path,
            vtt_path=vtt_path,
            diarizer=diarizer,
            generator=generator,
            state_store=state_store,
            slack_cfg=cfg.slack,
            diar_cfg=cfg.diarization,
            merger_cfg=cfg.merger,
            source_label=source_label,
            template_name=cfg.template,
            timeout_sec=cfg.pipeline_timeout_sec,
        )

    # Watcher
    watcher = ZoomDriveWatcher(cfg.zoom_drive, state_store, on_pair=on_pair)
    watcher.start()

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal():
        logger.info("Shutdown signal received")
        watcher.stop()
        diarizer.unload_model()
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    logger.info("Zoom Slack Bot started. Monitoring Drive folder...")
    await stop_event.wait()
    logger.info("Zoom Slack Bot stopped.")
```

#### CLI引数

| 引数 | デフォルト | 説明 |
|------|-----------|------|
| `--config` | `config_slack.yaml` | 設定ファイルパス |
| `--log-level` | `INFO` | ログレベル |

---

## 5. 設定ファイル設計

### 5.1 `config_slack.yaml` 構成

```yaml
# Zoom Diarization Slack Service - Configuration
# ===============================================
# Secrets (API keys, tokens) are loaded from .env.

# Slack posting configuration
slack:
  # Slack channel ID for minutes posting
  channel_id: "C0123456789"
  # Maximum text length per Block Kit section
  max_text_length: 3000

# Zoom recording file watcher
zoom_drive:
  enabled: true
  # Path to Google service account JSON key
  credentials_path: "credentials.json"
  # Google Drive folder ID where Zoom saves recordings
  folder_id: "1AbCdEfGhIjKlMnOpQrStUvWxYz"
  # Polling interval in seconds
  poll_interval_sec: 30
  # Timeout for m4a + VTT pair completion (seconds)
  pair_timeout_sec: 300
  # File patterns
  m4a_pattern: "*.m4a"
  vtt_pattern: "*.vtt"

# Speaker diarization (DiariZen)
diarization:
  enabled: true
  model: "BUT-FIT/diarizen-wavlm-large-s80-md"
  device: "cuda"
  num_speakers: 0
  ffmpeg_timeout_sec: 300

# Transcript merger
merger:
  timestamp_format: "[{mm}:{ss}]"
  min_segment_chars: 1
  gap_merge_threshold_sec: 1.0

# Minutes generation (Claude API)
generator:
  model: "claude-sonnet-4-5-20250929"
  max_tokens: 8192
  temperature: 0.3
  prompt_template_path: "prompts/minutes.txt"
  max_retries: 2

# Pipeline
pipeline:
  processing_timeout_sec: 3600
  state_dir: "state"
  template: "minutes"

# Logging
logging:
  level: "INFO"
  file: "logs/zoom_slack.log"
  max_bytes: 10485760
  backup_count: 5
```

### 5.2 環境変数（`.env`）

```
SLACK_BOT_TOKEN=xoxb-...
ANTHROPIC_API_KEY=sk-ant-...
```

### 5.3 Config Dataclass追加

`src/config.py` に以下を追加（または新規 `src/slack_config.py` として分離）。

```python
@dataclass(frozen=True)
class SlackConfig:
    """Slack Web API posting configuration."""
    bot_token: str = ""
    channel_id: str = ""
    max_text_length: int = 3000


@dataclass(frozen=True)
class ZoomDriveConfig:
    """Zoom Google Drive watcher configuration."""
    enabled: bool = False
    credentials_path: str = "credentials.json"
    folder_id: str = ""
    poll_interval_sec: int = 30
    pair_timeout_sec: int = 300
    m4a_pattern: str = "*.m4a"
    vtt_pattern: str = "*.vtt"
    mime_types: tuple[str, ...] = (
        "audio/mp4",
        "audio/x-m4a",
        "text/vtt",
        "text/plain",
    )


@dataclass(frozen=True)
class SlackServiceConfig:
    """Top-level config for the Zoom Slack service."""
    slack: SlackConfig
    zoom_drive: ZoomDriveConfig
    diarization: DiarizationConfig
    merger: MergerConfig
    generator: GeneratorConfig
    state_dir: str = "state"
    pipeline_timeout_sec: int = 3600
    template: str = "minutes"


def load_slack_config(
    config_path: str = "config_slack.yaml",
    env_path: str = ".env",
) -> SlackServiceConfig:
    """Load Slack service configuration.

    Same precedence as load(): env vars > YAML > defaults.
    """
```

#### 設計判断: 分離vs統合

| 選択肢 | 長所 | 短所 |
|--------|------|------|
| **`src/config.py` に追加** | import path統一 | config.py肥大化、Discord依存のload()と混在 |
| **`src/slack_config.py` に分離** | 独立性が高い、Discordコード不要 | 共有dataclassのimport |

**推奨**: `src/slack_config.py` として分離。理由:
1. `load()` 関数がDiscord固有のバリデーション（guild_id等）を含む
2. Slack用の `load_slack_config()` は独自のバリデーションが必要
3. 共有dataclass（`DiarizationConfig`, `GeneratorConfig`, `MergerConfig`）は `src/config.py` からimport

---

## 6. エラーハンドリング戦略

### 6.1 例外階層追加

```
MinutesBotError
├── DiarizationError     (stage: "diarization")     ← 既存
├── GenerationError      (stage: "generation")      ← 既存
├── DriveWatchError      (stage: "drive_watch")     ← 既存
├── VttParseError        (stage: "vtt_parse")       ← 新規
└── SlackPostingError    (stage: "slack_posting")   ← 新規
```

`VttParseError` と `SlackPostingError` を `src/errors.py` に追加:

```python
class VttParseError(MinutesBotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, stage="vtt_parse")


class SlackPostingError(MinutesBotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, stage="slack_posting")
```

### 6.2 ステージ別エラー対応

| ステージ | エラー | リトライ | Slack通知 | 動作 |
|---------|--------|---------|-----------|------|
| VTTパース | `VttParseError` | No | Yes | ファイルをfailedとしてマーク |
| 音声変換 | `AudioExtractionError` | No | Yes | ファイルをfailedとしてマーク |
| 話者分離 | `DiarizationError` (CUDA OOM) | No | Yes | VRAM不足 → CPU fallback検討 |
| 話者分離 | `DiarizationError` (その他) | No | Yes | ファイルをfailedとしてマーク |
| 議事録生成 | `GenerationError` (rate limit) | Yes (3回) | 最終失敗時Yes | exponential backoff |
| 議事録生成 | `GenerationError` (client error) | No | Yes | 即座に失敗 |
| Slack投稿 | `SlackPostingError` (rate_limited) | Yes (3回) | N/A | exponential backoff |
| Slack投稿 | `SlackPostingError` (channel_not_found) | No | Log only | 設定エラー |
| ペアタイムアウト | N/A | No | No | state_storeにfailedマーク |

### 6.3 graceful degradation

- **ステータスメッセージ失敗**: ログ警告のみ、パイプライン継続
- **ファイルアップロード失敗**: ログ警告のみ、メッセージ投稿は成功済み
- **キャッシュ書き込み失敗**: ログ警告のみ、次回再生成

---

## 7. テスト戦略

### 7.1 新規テストファイル

| テストファイル | 対象 | テスト数(推定) |
|--------------|------|-------------|
| `tests/test_vtt_parser.py` | `vtt_parser.py` | 12-15 |
| `tests/test_slack_poster.py` | `slack_poster.py` | 8-10 |
| `tests/test_slack_pipeline.py` | `slack_pipeline.py` | 8-12 |
| `tests/test_zoom_drive_watcher.py` | `zoom_drive_watcher.py` | 10-15 |
| `tests/test_slack_config.py` | `slack_config.py` | 5-8 |

### 7.2 テストケース詳細

#### `test_vtt_parser.py`

```python
# 正常系
def test_parse_vtt_basic():
    """Basic VTT with 3 cues → 3 Segments."""

def test_parse_vtt_with_cue_numbers():
    """VTT with numeric cue IDs → correctly skipped."""

def test_parse_vtt_multiline_text():
    """Cue with multi-line text → joined with space."""

def test_parse_vtt_japanese():
    """VTT with Japanese text → preserved correctly."""

def test_parse_vtt_millisecond_timestamps():
    """HH:MM:SS.mmm → correct float seconds."""

def test_parse_vtt_mm_ss_format():
    """MM:SS.mmm (no hours) → correct float seconds."""

def test_parse_vtt_speaker_field_empty():
    """All Segments have speaker="" (populated by aligner later)."""

def test_parse_vtt_sorted_by_start():
    """Output is sorted by start time even if input is unsorted."""

# 異常系
def test_parse_vtt_file_not_found():
    """Non-existent file → FileNotFoundError."""

def test_parse_vtt_invalid_header():
    """File without WEBVTT header → VttParseError."""

def test_parse_vtt_no_cues():
    """File with header but no cues → VttParseError."""

def test_parse_vtt_empty_cue_text():
    """Cue with empty text line → skipped."""

def test_parse_timestamp_invalid():
    """Malformed timestamp → VttParseError."""
```

#### `test_slack_poster.py`

```python
# 正常系
@pytest.mark.asyncio
async def test_post_minutes_to_slack():
    """Post minutes → returns message ts."""

@pytest.mark.asyncio
async def test_post_minutes_blocks_structure():
    """Verify Block Kit structure: header, divider, sections, context."""

@pytest.mark.asyncio
async def test_post_minutes_file_upload():
    """Minutes file uploaded in thread."""

@pytest.mark.asyncio
async def test_send_slack_status():
    """Status message sent → returns ts."""

# 異常系
@pytest.mark.asyncio
async def test_post_minutes_rate_limited_retry():
    """Rate limit → retries with backoff."""

@pytest.mark.asyncio
async def test_post_minutes_channel_not_found():
    """Invalid channel → SlackPostingError."""

@pytest.mark.asyncio
async def test_send_slack_status_failure_non_critical():
    """Status failure → returns None, no exception."""

@pytest.mark.asyncio
async def test_post_error_to_slack():
    """Error notification posted correctly."""
```

#### `test_slack_pipeline.py`

```python
@pytest.mark.asyncio
async def test_pipeline_full_flow(mock_diarizer, mock_generator, tmp_path):
    """End-to-end: VTT + m4a → segments → minutes → Slack post."""

@pytest.mark.asyncio
async def test_pipeline_cached_minutes(mock_diarizer, mock_generator, state_store):
    """Second run with same transcript → uses cache, skips generate."""

@pytest.mark.asyncio
async def test_pipeline_diarization_error():
    """Diarizer failure → error posted to Slack."""

@pytest.mark.asyncio
async def test_pipeline_generation_error():
    """Generator failure → error posted to Slack."""

@pytest.mark.asyncio
async def test_pipeline_empty_transcript():
    """Empty merged transcript → DiarizationError."""

@pytest.mark.asyncio
async def test_pipeline_timeout():
    """Pipeline exceeds timeout → error posted to Slack."""

@pytest.mark.asyncio
async def test_pipeline_vtt_parse_error():
    """Invalid VTT → error posted to Slack."""

@pytest.mark.asyncio
async def test_pipeline_transcript_hash_includes_template():
    """Different template → different cache key."""
```

#### `test_zoom_drive_watcher.py`

```python
def test_session_key_extraction():
    """GMT20260403-050000_Recording.m4a → GMT20260403-050000_Recording."""

def test_buffer_m4a_only():
    """Single m4a → pending, not complete."""

def test_buffer_vtt_only():
    """Single VTT → pending, not complete."""

def test_buffer_pair_complete():
    """m4a + VTT with same session key → complete."""

def test_buffer_different_sessions():
    """Files from different sessions → separate pending entries."""

def test_expire_timeout():
    """Pending pair past timeout → removed, marked as failed."""

def test_expire_complete_pair_not_affected():
    """Complete pairs are not expired."""

def test_known_files_skipped():
    """Files already in state_store → not buffered."""

@pytest.mark.asyncio
async def test_process_pair_callback():
    """Complete pair → both files downloaded, callback invoked."""

@pytest.mark.asyncio
async def test_process_pair_marks_success():
    """Successful processing → both files marked as success."""

@pytest.mark.asyncio
async def test_process_pair_marks_failed_on_error():
    """Callback error → both files marked as failed."""

def test_non_zoom_filename_ignored():
    """File not matching Zoom pattern → skipped."""
```

### 7.3 テスト方針

- **Slackクライアント**: `slack_sdk.WebClient` を `unittest.mock.AsyncMock` でモック
- **Drive API**: 既存テスト（`test_drive_watcher.py`）と同じパターンでモック
- **DiariZen**: `Diarizer` Protocolに対するモックを使用（GPUテスト不要）
- **Generator**: `MinutesGenerator` をモック
- **VTTファイル**: `tests/fixtures/` にテスト用VTTファイルを配置
- **tmpディレクトリ**: pytest の `tmp_path` フィクスチャを使用

### 7.4 テストフィクスチャ

```
tests/fixtures/
  ├── sample.vtt                      # 基本的なZoom VTT（3キュー）
  ├── sample_with_cue_numbers.vtt     # キュー番号付き
  ├── sample_multiline.vtt            # 複数行テキスト
  ├── sample_japanese.vtt             # 日本語テキスト
  └── sample_invalid.vtt              # 不正なフォーマット
```

---

## 8. 依存関係

### 8.1 既存依存（変更なし）

| パッケージ | 用途 | バージョン |
|-----------|------|-----------|
| `anthropic` | Claude API | >=0.25 |
| `torch` / `torchaudio` | DiariZen推論 | >=2.5 |
| `diarizen` | 話者分離 | git+https://... |
| `google-api-python-client` | Drive API | >=2.0 |
| `google-auth` | Service Account認証 | >=2.0 |
| `pyyaml` | 設定ファイル読み込み | >=6.0 |
| `python-dotenv` | .env読み込み | >=1.0 |

### 8.2 新規依存

| パッケージ | 用途 | バージョン | インストール |
|-----------|------|-----------|------------|
| `slack_sdk` | Slack Web API | >=3.27 | `pip install slack_sdk` |

### 8.3 システム依存

| ツール | 用途 | 備考 |
|--------|------|------|
| FFmpeg | m4a → WAV変換 | 既存要件、追加インストール不要 |

---

## 9. 技術リスクと対策

### 9.1 Zoom VTT 日本語精度

| 項目 | 詳細 |
|------|------|
| **リスク** | Zoom自動文字起こしの日本語エラー率が20-40%と推測。議事録の品質に直結 |
| **深刻度** | Medium |
| **確率** | 60% |
| **検証方法** | Gate 0で実際のZoom会議を録音し、VTTの精度を目視確認 |
| **対策 (v1)** | Claude APIが部分的にエラーを補正可能（コンテキストから推測） |
| **対策 (v2)** | Whisperフォールバックパス。VTT品質スコアが閾値以下の場合、WhisperでVTTを置換。パイプライン設計上は `vtt_segments` を `whisper_segments` に差し替えるだけで実現可能 |

### 9.2 室内マイクでの話者分離精度

| 項目 | 詳細 |
|------|------|
| **リスク** | 1台のPCマイクで複数話者を収音するため、話者分離精度（DER）が劣化する可能性 |
| **深刻度** | Medium |
| **確率** | 40% |
| **検証方法** | Gate 0で実データ検証。DER > 30%なら要件再検討 |
| **対策** | DiariZen `num_speakers` パラメータで既知の参加者数を事前指定。config_slack.yamlで設定可能 |

### 9.3 ファイルペアリング競合

| 項目 | 詳細 |
|------|------|
| **リスク** | Zoomがm4aとVTTを非同期にアップロードするため、片方だけ検知される |
| **深刻度** | Low |
| **確率** | 40% |
| **対策** | `_PendingPair` バッファ + 設定可能タイムアウト（デフォルト300秒）。タイムアウト後は `state_store.mark_failed()` で記録。再ポーリング時に再検知しない |

### 9.4 Slack API レートリミット

| 項目 | 詳細 |
|------|------|
| **リスク** | Slack Web API Tier 3のレートリミット（50+ requests/min） |
| **深刻度** | Low |
| **確率** | 10% |
| **対策** | `SlackApiError` (rate_limited) → `retry_after` ヘッダーに従いexponential backoff。通常の議事録投稿は1件あたり2-3 API呼び出しのため、リミットに達する可能性は極めて低い |

### 9.5 Zoomファイル名パターン変更

| 項目 | 詳細 |
|------|------|
| **リスク** | Zoomのアップデートによりファイル名パターンが変更される可能性 |
| **深刻度** | Low |
| **確率** | 20% |
| **対策** | `_ZOOM_SESSION_PATTERN` を正規表現として分離。config_slack.yamlで上書き可能にすることも将来的に検討 |

---

## 10. VRAM / パフォーマンス見積

### 10.1 VRAM使用量

| モデル | VRAM | 備考 |
|--------|------|------|
| DiariZen (WavLM-large) | ~340 MB | 話者分離推論 |
| **合計** | **~340 MB** | RTX 3060 12GBの2.8% |
| ~~Whisper large-v3~~ | ~~4,500 MB~~ | **不要**（Zoom VTTが代替） |
| ~~合計（Discord Bot同時）~~ | ~~4,840 MB~~ | **本サービスでは不使用** |

### 10.2 処理時間見積（60分会議）

| ステージ | 推定時間 | 備考 |
|---------|---------|------|
| Drive ダウンロード (m4a ~50MB + VTT ~20KB) | 10-30s | ネットワーク依存 |
| FFmpeg m4a→WAV | 5-10s | CPU処理 |
| VTTパース | <0.1s | テキスト処理のみ |
| DiariZen話者分離 | 30-60s | GPU推論、音声長に比例 |
| Segment突合 | <0.1s | O(N*M) だがN,M < 500 |
| トランスクリプト統合 | <0.1s | テキスト処理のみ |
| Claude API議事録生成 | 10-30s | API応答時間 |
| Slack投稿 | 1-3s | API呼び出し2-3回 |
| **合計** | **~60-135s** | |

### 10.3 同時実行

- Discord Botとの同時実行: 別プロセスのため干渉なし
- DiariZenのみ（~340MB）のため、Discord Bot側のWhisper（~4.5GB）と同時にGPU使用可能
- ただし、同一GPUでWhisper + DiariZen同時推論する場合は合計 ~4,840MB（RTX 3060 12GBで余裕あり）

### 10.4 ディスク使用量

| ファイル | サイズ目安 | ライフタイム |
|---------|-----------|------------|
| Zoom m4a (60分) | ~50 MB | 処理中のみ（tmpdir、処理後削除） |
| WAV 16kHz mono (60分) | ~115 MB | 処理中のみ（tmpdir、処理後削除） |
| VTT | ~20 KB | 処理中のみ |
| state/processing.json | <100 KB | 永続 |
| state/minutes_cache.json | <1 MB | 永続 |

---

## 付録A: 実装フェーズ

| フェーズ | 内容 | 推定工数 | 完了条件 |
|---------|------|---------|---------|
| **Phase 0** | Gate 0: 実データ検証 | 4h | VTT精度確認、DiariZen DER確認 |
| **Phase 1** | `vtt_parser.py` + テスト | 4-6h | 12+テスト通過 |
| **Phase 2** | `slack_poster.py` + テスト | 4-6h | 8+テスト通過 |
| **Phase 3** | `slack_pipeline.py` + テスト | 4-6h | 8+テスト通過 |
| **Phase 4** | `zoom_drive_watcher.py` + テスト | 6-8h | 10+テスト通過 |
| **Phase 5** | `slack_config.py` + `zoom_slack_bot.py` + 統合テスト | 4-6h | E2Eテスト通過 |
| **合計** | | **26-36h (3.5-5日)** | |

## 付録B: v2ロードマップ（スコープ外）

以下はv1スコープに含めない。v1の安定稼働後に検討する。

1. **話者識別（声紋マッチング）**: `src/speaker_registry.py` — WeSpeaker embeddingでSpeaker_0 → 実名マッピング
2. **Whisperフォールバック**: VTT品質スコア自動計測 → 閾値以下でWhisper文字起こしに切り替え
3. **pyannote 3.1差し替え**: `Diarizer` Protocol経由で差し替え可能
4. **Slack Bot Commands**: `/register-speaker` コマンドで音声サンプルアップロード → 声紋登録
5. **複数チャンネル対応**: config_slack.yamlでチャンネル別設定
