# PLAN.md -- Implementation Roadmap: Zoom Diarization Slack Service

**Feature Slug**: zoom-diarization-slack
**Date**: 2026-04-03
**Estimated Total Effort**: 28-32 hours (4 phases)
**Breaking Changes**: None
**Feature Flag**: 独立サービスのため不要（`config_slack.yaml` の有無で制御）
**Research**: CONDITIONAL GO at 85% Confidence (`rpi/zoom-diarization-slack/research/RESEARCH.md`)

---

## Overview

Google Driveに保存されたZoomクラウドレコーディング（m4a音声 + VTT文字起こし）を自動検知し、
DiariZenによる話者分離 → VTTテキストとの突合 → Claude APIで議事録生成 → Slackに投稿する独立サービス。

既存のDiscord Minutes Botとは別プロセスとして運用し、Discord依存を完全に排除する。
Whisper不要（Zoom VTTが代替）のためGPU負荷が大幅に軽減（~540MB vs ~4.8GB）。

### Architecture

```
[Google Drive]
  ├── meeting_2026-04-03.m4a  (Zoom音声録音)
  └── meeting_2026-04-03.vtt  (Zoom文字起こし)
         │
         ▼
[ZoomDriveWatcher] ── m4a + VTT ペア検知（タイムアウト付きバッファ）
         │
         ├──→ VTTパーサー → list[Segment(start, end, text, speaker="")]
         │
         ├──→ audio_extractor(m4a → WAV) → DiariZen → list[DiarSegment]
         │
         ▼
    segment_aligner: VTTセグメント × 話者 → list[Segment(start, end, text, "Speaker_0")]
         │
         ▼
    merger → generator (Claude API) → Slack投稿
```

### 推定規模

| カテゴリ | LOC |
|---------|-----|
| 新規モジュール（src/） | ~470-620 |
| テスト（tests/） | ~530-690 |
| 設定ファイル | ~40-50 |
| エントリーポイント | ~80-100 |
| **合計** | **~1,120-1,460** |

### 複雑度

- **全体**: Medium
- **最大リスク**: Zoom VTTの日本語精度、室内マイクでの話者分離精度

---

## Phase Overview

- [x] Phase 1: VTTパーサー + Slack設定 (~8h)
- [x] Phase 2: Slackポスター + ファイルペアリング (~10h)
- [x] Phase 3: パイプライン + エントリーポイント (~8h)
- [!] Phase 4: 統合テスト + ポリッシュ (~6h) — コード完了、Gate 0 E2E pending

| Phase | Name | Effort | Dependencies | Validation Gate |
|-------|------|--------|-------------|-----------------|
| 1 | VTTパーサー + Slack設定 | ~8h | None | VTTパース正常、Config読込正常、全テスト pass |
| 2 | Slackポスター + ファイルペアリング | ~10h | Phase 1 | Slack投稿フォーマット正常、ファイルペアリング正常、全テスト pass |
| 3 | パイプライン + エントリーポイント | ~8h | Phase 1, 2 | E2Eフロー正常、シグナルハンドリング正常、全テスト pass |
| 4 | 統合テスト + ポリッシュ | ~6h | Phase 1-3 | 実Zoomデータで E2E 成功、議事録品質確認 |

---

## Phase 1: VTTパーサー + Slack設定（Foundation）

- [ ] **Status**: Not started

**目標**: Zoom VTTファイルのパースと独立した設定システムを構築し、後続フェーズの基盤を整備する。

**工数**: ~8 hours
**Dependencies**: None（即時開始可能）

### タスクリスト

- [ ] **Task 1.1**: `src/vtt_parser.py` 新規作成
  - `parse_vtt(vtt_text: str) -> list[Segment]`
    - WebVTT形式のテキストを解析し、`Segment(start, end, text, speaker="")` のリストを返す
    - Zoom VTTフォーマット: タイムスタンプ行 (`HH:MM:SS.mmm --> HH:MM:SS.mmm`) + テキスト行（話者ラベルなし）
  - `parse_vtt_file(vtt_path: Path) -> list[Segment]`
    - ファイルパスからVTTを読み込み、`parse_vtt()` に委譲
    - BOM付きUTF-8、UTF-8、UTF-16の自動検出
  - **エッジケース処理**:
    - 空VTTファイル → `[]` を返す
    - 不正タイムスタンプ（欠落、逆順） → 警告ログ + 該当キューをスキップ
    - BOM（`\ufeff`）→ 自動除去
    - マルチラインキュー → 改行を半角スペースに結合
    - `WEBVTT` ヘッダーの有無を両方サポート
    - 空テキストキュー → スキップ
  - 既存 `src/transcriber.py` の `Segment` dataclass を再利用
  - **複雑度**: Medium
  - **推定LOC**: 60-80

- [ ] **Task 1.2**: `src/slack_config.py` 新規作成
  - `SlackConfig` frozen dataclass
    - `bot_token: str` — Slack Bot Token (`xoxb-...`)
    - `channel_id: str` — 投稿先チャンネルID
    - `include_transcript: bool = True` — 文字起こし全文を添付するか
    - `thread_replies: bool = True` — スレッドでステータス更新するか
  - `ZoomConfig` frozen dataclass
    - `vtt_file_pattern: str = "*.vtt"` — VTTファイル名パターン
    - `audio_file_pattern: str = "*.m4a"` — 音声ファイル名パターン
    - `pair_timeout_sec: int = 300` — ペアリングタイムアウト（秒）
  - `SlackServiceConfig` frozen dataclass（トップレベル）
    - `slack: SlackConfig`
    - `zoom: ZoomConfig`
    - `diarization: DiarizationConfig`（既存 `src/config.py` から import）
    - `generator: GeneratorConfig`（既存 `src/config.py` から import）
    - `merger: MergerConfig`（既存 `src/config.py` から import）
    - `google_drive: GoogleDriveConfig`（既存 `src/config.py` から import）
    - `pipeline: PipelineConfig`（既存 `src/config.py` から import）
  - `load_slack_config(path: str = "config_slack.yaml") -> SlackServiceConfig`
    - YAML読み込み + 環境変数オーバーライド（`SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`）
    - 既存 `_build_section()` パターンを参考にしつつ、独立した設定ローダーとして実装
  - `_validate_slack_config(cfg: SlackServiceConfig) -> None`
    - `slack.bot_token` が空でないこと
    - `slack.channel_id` が空でないこと
    - `zoom.pair_timeout_sec >= 30`
  - **複雑度**: Medium
  - **推定LOC**: 80-100

- [ ] **Task 1.3**: `config_slack.yaml` テンプレート作成
  - 全フィールドのコメント付きテンプレート
  - `slack:`, `zoom:`, `diarization:`, `generator:`, `merger:`, `google_drive:`, `pipeline:` セクション
  - デフォルト値は全て明示
  - 環境変数オーバーライドの説明コメント
  - **複雑度**: Low
  - **推定LOC**: 40-50

- [ ] **Task 1.4**: `tests/test_vtt_parser.py` 新規作成
  - `test_parse_vtt_basic` — 標準的なZoom VTTフォーマットのパース
  - `test_parse_vtt_multiline_cue` — マルチラインキューの結合
  - `test_parse_vtt_empty` — 空VTT → `[]`
  - `test_parse_vtt_malformed_timestamp` — 不正タイムスタンプのスキップ
  - `test_parse_vtt_bom` — BOM付きUTF-8の処理
  - `test_parse_vtt_no_header` — `WEBVTT` ヘッダーなしでもパース可能
  - `test_parse_vtt_empty_cue` — 空テキストキューのスキップ
  - `test_parse_vtt_returns_segment_type` — 戻り値が `Segment` dataclass であること
  - `test_parse_vtt_file_not_found` — ファイル不在で `FileNotFoundError`
  - `test_parse_vtt_file_encoding_detection` — BOMエンコーディング検出
  - **Mock戦略**: モック不要（純粋データ変換テスト）
  - **複雑度**: Medium
  - **推定LOC**: 100-130

- [ ] **Task 1.5**: `tests/test_slack_config.py` 新規作成
  - `test_load_slack_config_defaults` — デフォルト値の検証
  - `test_load_slack_config_from_yaml` — YAML ファイルからの読み込み
  - `test_load_slack_config_env_override` — 環境変数によるオーバーライド
  - `test_load_slack_config_validation_error` — `bot_token` 空でバリデーションエラー
  - `test_load_slack_config_missing_channel` — `channel_id` 空でバリデーションエラー
  - `test_load_slack_config_reuses_existing_dataclasses` — 既存 Config dataclass の型確認
  - **Mock戦略**: 一時ファイルにYAMLを書き出してテスト
  - **複雑度**: Low
  - **推定LOC**: 50-70

### Deliverables

| File | Type | Est. LOC |
|------|------|----------|
| `src/vtt_parser.py` | New | 60-80 |
| `src/slack_config.py` | New | 80-100 |
| `config_slack.yaml` | New | 40-50 |
| `tests/test_vtt_parser.py` | New | 100-130 |
| `tests/test_slack_config.py` | New | 50-70 |

### Validation Gate 1

```bash
pytest tests/test_vtt_parser.py tests/test_slack_config.py -v
```

**合格条件**:
- 全テスト pass
- VTTパーサーが Zoom VTT フォーマットを正しくパース
- パーサーの戻り値が既存 `Segment` dataclass と互換
- Config が YAML + 環境変数オーバーライドで正しく読み込まれる
- バリデーションが不正値を検出する
- import cycle なし

---

## Phase 2: Slackポスター + ファイルペアリング（Core Integration）

- [ ] **Status**: Not started

**目標**: Slack Web APIによる議事録投稿モジュールと、Google Driveでのm4a+VTTファイルペアリング監視を実装する。

**工数**: ~10 hours
**Dependencies**: Phase 1 complete（SlackConfig が必要）

### タスクリスト

- [ ] **Task 2.1**: `src/slack_poster.py` 新規作成
  - `SlackPoster.__init__(cfg: SlackConfig)` — `slack_sdk.WebClient` 初期化
  - `post_minutes_to_slack(minutes_md: str, title: str, metadata: dict) -> str`
    - Block Kit形式でフォーマットされた議事録を投稿
    - ヘッダー: 会議タイトル + 日時
    - セクション: 議事録本文（Markdown）
    - コンテキスト: メタデータ（話者数、処理時間等）
    - 戻り値: 投稿メッセージの `ts`（タイムスタンプID）
  - `post_transcript_file(channel: str, thread_ts: str, transcript_md: str, filename: str) -> None`
    - `files.getUploadURLExternal` + `files.completeUploadExternal` で文字起こし全文を `.md` ファイルとしてスレッドにアップロード
    - `include_transcript: false` の場合はスキップ
  - `send_slack_status(thread_ts: str, message: str) -> None`
    - スレッドにステータス更新を投稿（処理中、完了、エラー等）
    - `thread_replies: false` の場合はスキップ
  - `post_error_to_slack(error_message: str, source_label: str) -> None`
    - エラー通知を投稿（赤色 Block Kit アタッチメント）
  - **レート制限対応**: HTTP 429 レスポンス時に `Retry-After` ヘッダーに従って待機
  - **エラーハンドリング**: `SlackApiError` を catch し、警告ログ + 再送可否を判断
  - **複雑度**: High
  - **推定LOC**: 100-130

- [ ] **Task 2.2**: `src/zoom_drive_watcher.py` 新規作成
  - `ZoomDriveWatcher.__init__(cfg: GoogleDriveConfig, zoom_cfg: ZoomConfig, state_store: StateStore, on_pair_ready: OnPairReadyCallback)`
    - `OnPairReadyCallback = Callable[[Path, Path, str], Awaitable[None]]`
    - コールバック引数: `(audio_path, vtt_path, source_label)`
  - `start()` / `stop()` — 既存 `DriveWatcher` / `VideoDriveWatcher` と同パターン
  - `is_running` プロパティ
  - `_watch_loop()` — ポーリングループ
    - Drive API で新規ファイルを検索
    - ファイル名パターンに基づき m4a / VTT を分類
    - ペアリングバッファに追加
  - `_pair_buffer: dict[str, PendingPair]` — ファイルペアリング用バッファ
    - `PendingPair` dataclass: `audio_path: Path | None`, `vtt_path: Path | None`, `first_seen: float`
    - ファイル名のプレフィックス（拡張子除去）をキーにペアリング
  - `_check_pairs()` — バッファを巡回し、揃ったペアのコールバックを発火
    - 両ファイル揃い → コールバック発火 + バッファから削除
    - `pair_timeout_sec` 超過 → 警告ログ + バッファから削除（片方のみで処理しない）
  - `_download_file_sync()` — 共有 Drive ユーティリティ（`drive_watcher.py` の関数）を呼び出し
  - **StateStore 統合**: ペアキー（プレフィックス）で重複排除
  - **複雑度**: High
  - **推定LOC**: 100-140

- [ ] **Task 2.3**: `tests/test_slack_poster.py` 新規作成
  - `test_post_minutes_success` — Mock WebClient、Block Kit構造の検証
  - `test_post_minutes_block_kit_format` — ヘッダー、セクション、コンテキスト各ブロックの存在確認
  - `test_post_transcript_file_upload` — ファイルアップロードAPI呼び出しの検証
  - `test_post_transcript_file_skipped` — `include_transcript=False` 時にスキップ
  - `test_send_status_in_thread` — スレッド返信の `thread_ts` 指定確認
  - `test_send_status_skipped` — `thread_replies=False` 時にスキップ
  - `test_post_error_format` — エラー通知のフォーマット確認
  - `test_rate_limit_retry` — HTTP 429 でリトライ
  - `test_api_error_handling` — `SlackApiError` のハンドリング
  - `test_post_minutes_returns_ts` — 戻り値が `ts` であること
  - **Mock戦略**: `slack_sdk.WebClient` をパッチ。各APIメソッド（`chat_postMessage`, `files_getUploadURLExternal`, `files_completeUploadExternal`）のレスポンスをモック
  - **複雑度**: High
  - **推定LOC**: 120-150

- [ ] **Task 2.4**: `tests/test_zoom_drive_watcher.py` 新規作成
  - `test_watcher_detects_m4a` — m4a ファイル検知
  - `test_watcher_detects_vtt` — VTT ファイル検知
  - `test_watcher_pairs_files` — m4a + VTT が揃ったらコールバック発火
  - `test_watcher_pair_timeout` — タイムアウト時に警告ログ + バッファクリア
  - `test_watcher_pair_order_independent` — VTT先着 → m4a後着でもペアリング成功
  - `test_watcher_dedup` — StateStore で処理済みファイルをスキップ
  - `test_watcher_start_stop` — start/stop ライフサイクル
  - `test_watcher_filename_prefix_matching` — 拡張子除去によるペアリングキー生成
  - `test_watcher_ignores_unmatched_pattern` — パターン不一致ファイルの無視
  - `test_watcher_multiple_pairs` — 複数ペアの同時処理
  - **Mock戦略**: 既存 `test_drive_watcher.py` のパターンに準拠。Google Drive API をモック
  - **複雑度**: High
  - **推定LOC**: 120-150

### Deliverables

| File | Type | Est. LOC |
|------|------|----------|
| `src/slack_poster.py` | New | 100-130 |
| `src/zoom_drive_watcher.py` | New | 100-140 |
| `tests/test_slack_poster.py` | New | 120-150 |
| `tests/test_zoom_drive_watcher.py` | New | 120-150 |

### Validation Gate 2

```bash
pytest tests/test_slack_poster.py tests/test_zoom_drive_watcher.py -v
```

**合格条件**:
- 全テスト pass
- Slack ポスターが Block Kit フォーマットで議事録を投稿
- Slack ポスターが HTTP 429 レート制限をハンドリング
- ファイルペアリングが m4a + VTT の両方揃った時点でコールバック発火
- ファイルペアリングがタイムアウト時に正しくクリーンアップ
- StateStore による重複排除が機能
- Phase 1 テスト引き続き pass

---

## Phase 3: パイプライン + エントリーポイント（End-to-End）

- [ ] **Status**: Not started

**目標**: Drive検知からSlack投稿までのEnd-to-Endパイプラインを構築し、独立したエントリーポイントでサービスとして起動可能にする。

**工数**: ~8 hours
**Dependencies**: Phase 1, 2 complete

### タスクリスト

- [ ] **Task 3.1**: `src/slack_pipeline.py` 新規作成
  - `run_slack_pipeline(audio_path: Path, vtt_path: Path, cfg: SlackServiceConfig, generator: MinutesGenerator, slack_poster: SlackPoster, state_store: StateStore, source_label: str = "zoom") -> None`
  - **フロー**:
    1. VTTパース: `parse_vtt_file(vtt_path)` → `list[Segment]`
    2. 音声抽出: `extract_audio(audio_path, wav_path)` → WAV
    3. 話者分離: `DiariZenDiarizer.load_model()` → `diarize(wav_path)` → `list[DiarSegment]`
    4. 突合: `align_segments(vtt_segments, diar_segments)` → `list[Segment]`（話者付き）
    5. 統合: `merge_segments(segments)` → `transcript_md`
    6. 議事録生成: `generator.generate(transcript_md)` → `minutes_md`
    7. Slack投稿: `slack_poster.post_minutes_to_slack(minutes_md, title, metadata)`
    8. 文字起こし添付: `slack_poster.post_transcript_file(...)` （設定有効時）
  - **エラーハンドリング**:
    - 各ステージで try/except + ステージ名ログ
    - 致命的エラー: `slack_poster.post_error_to_slack()` でエラー通知
    - 話者分離失敗: `diar_segments = []` でフォールバック（全セグメント "Speaker" ラベル）
  - **VRAM管理**: `finally` ブロックで `diarizer.unload_model()` を保証
  - **StateStore**: パイプライン開始時に処理中フラグ、完了時にキャッシュ保存
  - **ステータス更新**: 各ステージ完了時に `send_slack_status()` でスレッド通知（設定有効時）
  - **複雑度**: High
  - **推定LOC**: 130-170

- [ ] **Task 3.2**: `zoom_slack_bot.py` 新規作成（エントリーポイント）
  - `main()` — メインエントリーポイント
    1. `argparse` でコマンドライン引数パース（`--config`, `--log-level`）
    2. ロギング設定（既存 `bot.py` と同パターン: ローテーションファイル + コンソール）
    3. `load_slack_config()` で設定読み込み
    4. コンポーネント初期化:
       - `MinutesGenerator(cfg.generator)` — Claude API クライアント
       - `StateStore("state/slack_processing.json", "state/slack_minutes_cache.json")` — 独立したステートファイル
       - `SlackPoster(cfg.slack)` — Slack投稿クライアント
       - `DiariZenDiarizer(cfg.diarization)` — 話者分離（lazy load）
       - `ZoomDriveWatcher(cfg.google_drive, cfg.zoom, state_store, on_pair_ready=callback)` — Drive監視
    5. `on_pair_ready` コールバック: `run_slack_pipeline()` を呼び出し
    6. `asyncio.run(run())` — イベントループ起動
  - **シグナルハンドリング**:
    - `SIGINT` / `SIGTERM` → `ZoomDriveWatcher.stop()` + graceful shutdown
    - `asyncio.Event` による停止待機
  - **ヘルスログ**: 起動時に設定サマリーをログ出力（Drive folder, Slack channel, diarization model 等）
  - **複雑度**: Medium
  - **推定LOC**: 80-100

- [ ] **Task 3.3**: `tests/test_slack_pipeline.py` 新規作成
  - `test_pipeline_success` — 全ステージ成功で Slack 投稿まで到達
  - `test_pipeline_vtt_parse_error` — VTTパース失敗 → エラー通知
  - `test_pipeline_diarization_fallback` — 話者分離失敗 → 単一話者でフォールバック
  - `test_pipeline_generation_error` — Claude API 失敗 → エラー通知
  - `test_pipeline_slack_post_error` — Slack 投稿失敗 → 警告ログ
  - `test_pipeline_vram_cleanup` — 例外発生時でも `unload_model()` が呼ばれる
  - `test_pipeline_state_store_integration` — 処理中フラグとキャッシュ保存
  - `test_pipeline_status_updates` — 各ステージのステータス通知
  - **Mock戦略**: generator, slack_poster, diarizer, state_store を全てモック。既存 `test_pipeline.py` のパターンを参考
  - **複雑度**: High
  - **推定LOC**: 100-130

- [ ] **Task 3.4**: `tests/test_zoom_slack_bot.py` 新規作成
  - `test_main_loads_config` — 設定ファイル読み込みの検証
  - `test_main_initializes_components` — 各コンポーネントの初期化確認
  - `test_signal_handler_stops_watcher` — SIGTERM で watcher.stop() が呼ばれる
  - `test_main_missing_config_exits` — 設定ファイル不在で sys.exit(1)
  - **Mock戦略**: `load_slack_config`, `ZoomDriveWatcher`, `SlackPoster` 等をパッチ
  - **複雑度**: Medium
  - **推定LOC**: 40-60

### Deliverables

| File | Type | Est. LOC |
|------|------|----------|
| `src/slack_pipeline.py` | New | 130-170 |
| `zoom_slack_bot.py` | New | 80-100 |
| `tests/test_slack_pipeline.py` | New | 100-130 |
| `tests/test_zoom_slack_bot.py` | New | 40-60 |

### Validation Gate 3

```bash
pytest tests/test_slack_pipeline.py tests/test_zoom_slack_bot.py -v
```

**合格条件**:
- 全テスト pass
- パイプラインが VTT + 音声の入力から Slack 投稿まで End-to-End で処理
- 話者分離失敗時にフォールバック（単一話者）で継続
- エラー発生時に Slack エラー通知が送信される
- エントリーポイントが設定読み込み → 初期化 → 起動の流れで動作
- シグナルハンドリングで graceful shutdown が機能
- Phase 1, 2 テスト引き続き pass

---

## Phase 4: 統合テスト + ポリッシュ（Validation）

- [ ] **Status**: Not started

**目標**: 実際のZoomデータでのEnd-to-End検証、エラーハンドリングの強化、運用ドキュメント整備。

**工数**: ~6 hours
**Dependencies**: Phase 1-3 complete

### タスクリスト

- [ ] **Task 4.1**: Gate 0/2 E2E検証
  - 実際のZoom会議を録音し、m4a + VTT を取得
  - Google Drive にアップロードし、`ZoomDriveWatcher` が検知することを確認
  - パイプライン全体が正常に処理され、Slack に議事録が投稿されることを確認
  - **検証ポイント**:
    - Zoom VTT の日本語テキストが正しくパースされる
    - DiariZen の話者分離が 3-4 名を識別できる（DER < 30% が目標）
    - 突合後の話者ラベルが議事録に反映される
    - 議事録の品質が実用レベルである
    - Slack の Block Kit フォーマットが正しく表示される
  - **複雑度**: High

- [ ] **Task 4.2**: エラーハンドリング検証
  - 意図的なエラー注入テスト:
    - 不正なVTTファイル → エラー通知確認
    - Drive接続失敗 → リトライ + エラー通知確認
    - Claude API タイムアウト → エラー通知確認
    - Slack API 認証失敗 → ログ確認
    - DiariZen OOM → フォールバック確認
  - 長時間稼働テスト（1時間以上）でメモリリークやVRAM累積がないことを確認
  - **複雑度**: Medium

- [ ] **Task 4.3**: ロギング整備
  - 全ステージで統一されたログフォーマット（`[source_label] Stage N: ...`）
  - 処理時間の計測と出力（パイプライン全体 + 各ステージ）
  - VRAM使用量ログ（DiariZen load/unload 前後）
  - **複雑度**: Low

- [ ] **Task 4.4**: 運用ドキュメント
  - `config_slack.yaml` の設定項目説明
  - 起動方法（`python zoom_slack_bot.py`）
  - Slack App 作成手順（Bot Token Scopes: `chat:write`, `files:write`）
  - Google Drive Service Account 設定手順（既存ドキュメントへの参照）
  - トラブルシューティングガイド
  - **複雑度**: Low

- [ ] **Task 4.5**: Docker Compose サービス定義（optional）
  - `docker-compose.yml` に `zoom-slack` サービスを追加
  - 既存の `minutes-bot` サービスと同一ネットワーク、異なるエントリーポイント
  - GPU リソース共有設定
  - **複雑度**: Low

### Deliverables

| File | Type | Notes |
|------|------|-------|
| Various | Fix | E2Eテストで発見されたバグ修正 |
| `docker-compose.yml` | Modify | zoom-slack サービス追加（optional） |

### Validation Gate 4

```bash
pytest -v
```

**合格条件**:
- 全テスト pass（既存テスト + 新規テスト）
- 実 Zoom m4a + VTT で End-to-End パイプラインが成功
- 議事録の品質が実用レベル
- 話者分離ラベル（Speaker_0, Speaker_1 等）が議事録に正しく反映
- エラーハンドリングが全エッジケースをカバー
- サービスが長時間安定して稼働
- VRAM が DiariZen unload 後に確実に解放される

---

## Dependency Graph

```
[Phase 1: VTTパーサー + Slack設定]
  Task 1.1 (vtt_parser.py)
  Task 1.2 (slack_config.py)
  Task 1.3 (config_slack.yaml)    ── depends on ── Task 1.2
  Task 1.4 (test_vtt_parser)      ── depends on ── Task 1.1
  Task 1.5 (test_slack_config)    ── depends on ── Task 1.2, 1.3
        |
        v
[Phase 2: Slackポスター + ファイルペアリング]
  Task 2.1 (slack_poster.py)      ── depends on ── Task 1.2 (SlackConfig)
  Task 2.2 (zoom_drive_watcher.py)── depends on ── Task 1.2 (ZoomConfig)
  Task 2.3 (test_slack_poster)    ── depends on ── Task 2.1
  Task 2.4 (test_zoom_drive_watcher) ── depends on ── Task 2.2
        |
        v
[Phase 3: パイプライン + エントリーポイント]
  Task 3.1 (slack_pipeline.py)    ── depends on ── Task 1.1, 2.1, 2.2
  Task 3.2 (zoom_slack_bot.py)    ── depends on ── Task 1.2, 2.1, 2.2, 3.1
  Task 3.3 (test_slack_pipeline)  ── depends on ── Task 3.1
  Task 3.4 (test_zoom_slack_bot)  ── depends on ── Task 3.2
        |
        v
[Phase 4: 統合テスト + ポリッシュ]
  Task 4.1-4.5                    ── depends on ── Phase 3 complete
```

### Phase 内並行実行可能タスク

| Phase | 並行可能 | 直列必須 |
|-------|---------|---------|
| Phase 1 | Task 1.1 と Task 1.2 は独立可能 | Task 1.3 → Task 1.2, Task 1.4 → Task 1.1, Task 1.5 → Task 1.2+1.3 |
| Phase 2 | Task 2.1 と Task 2.2 は独立可能 | Task 2.3 → Task 2.1, Task 2.4 → Task 2.2 |
| Phase 3 | なし | Task 3.1 → 3.2, Task 3.3 → Task 3.1, Task 3.4 → Task 3.2 |
| Phase 4 | Task 4.3, 4.4, 4.5 は並行可能 | Task 4.1 → Phase 3, Task 4.2 → Task 4.1 |

---

## 再利用モジュール一覧（変更不要）

| Module | LOC | Purpose | 再利用度 |
|--------|-----|---------|---------|
| `src/diarizer.py` | 176 | DiariZen wrapper + Diarizer Protocol | 100% |
| `src/segment_aligner.py` | 90 | Majority-vote overlap alignment | 100% |
| `src/audio_extractor.py` | 90 | FFmpeg async subprocess wrapper | 100% |
| `src/merger.py` | 159 | 話者別トランスクリプト統合 | 100% |
| `src/generator.py` | 314 | Claude API 議事録生成（Discord非依存） | 100% |
| `src/state_store.py` | 359 | 処理重複排除 + 議事録キャッシュ | 100% |
| `src/transcriber.py` | - | `Segment` dataclass（VTTパーサーが同一型を生成） | 100% |
| `src/errors.py` | - | カスタム例外階層 | 100% |
| `src/drive_watcher.py` | - | 共有 Drive ユーティリティ関数 | 部分的 |

---

## 新規依存関係

| パッケージ | バージョン | 用途 | ライセンス |
|-----------|-----------|------|----------|
| `slack-sdk` | >=3.27 | Slack Web API クライアント | MIT |

### インストール

```bash
pip install slack-sdk>=3.27
```

`requirements.txt` への追記:

```
slack-sdk>=3.27
```

### Slack App 必要スコープ

| スコープ | 用途 |
|---------|------|
| `chat:write` | メッセージ投稿 |
| `files:write` | 文字起こしファイルアップロード |

---

## Testing Requirements

### 新規テストファイル

| ファイル | テスト数 | Mock 戦略 |
|---------|---------|----------|
| `tests/test_vtt_parser.py` | 10 | モック不要（純粋データ変換） |
| `tests/test_slack_config.py` | 6 | 一時YAMLファイル |
| `tests/test_slack_poster.py` | 10 | `slack_sdk.WebClient` パッチ |
| `tests/test_zoom_drive_watcher.py` | 10 | Google Drive API モック |
| `tests/test_slack_pipeline.py` | 8 | generator, poster, diarizer, state_store モック |
| `tests/test_zoom_slack_bot.py` | 4 | コンポーネント初期化モック |

### テスト合計: ~48 新規テスト

### Mock 方針

1. **Slack API**: `slack_sdk.WebClient` をパッチ。`chat_postMessage`, `files_getUploadURLExternal`, `files_completeUploadExternal` のレスポンスをモック
2. **Google Drive API**: 既存 `test_drive_watcher.py` のパターンに準拠。`googleapiclient` をモック
3. **ML モデル**: 既存 `test_diarizer.py` のパターンに準拠。`DiariZenPipeline.from_pretrained` をパッチ
4. **パイプラインステージ**: 既存 `test_pipeline.py` のモックパターンを再利用
5. **データ変換**: モック不要 — VTTテキストから `Segment` dataclass への直接変換テスト

---

## Risk Mitigation

### R1: Zoom VTT 日本語精度不足

- **リスク**: Zoom の日本語文字起こし精度が低く（20-40% エラー率の可能性）、議事録品質に影響
- **深刻度**: Medium
- **確率**: 60%
- **対策**: Gate 0 で実データ検証。精度不足の場合は Whisper フォールバックパスを Phase 5（v2）として追加
- **回避策**: RTX 3060 12GB で DiariZen + Whisper 同時実行可能（~4.8GB）

### R2: 室内マイク話者分離精度

- **リスク**: 1台のPC内蔵マイクでの録音は、話者間の音量差・距離差が小さく、DER が高くなる可能性
- **深刻度**: Medium
- **確率**: 40%
- **対策**: Gate 0 で 3-4 名での分離精度を検証（DER 30% 超なら要件再検討）
- **回避策**: 外部マイクの使用を推奨、または Zoom の個別音声トラック機能の調査

### R3: m4a + VTT ファイルペアリング競合

- **リスク**: Zoom が m4a と VTT を非同期にアップロードし、タイミングによりペアリングに失敗
- **深刻度**: Low
- **確率**: 40%
- **対策**: タイムアウト付きバッファ（デフォルト 5 分）で両ファイルの到着を待機。StateStore による重複排除
- **回避策**: `pair_timeout_sec` を増加（最大 30 分）

### R4: Slack API レート制限

- **リスク**: 大量の議事録投稿時に Slack API のレート制限に抵触
- **深刻度**: Low
- **確率**: 20%
- **対策**: HTTP 429 レスポンス時に `Retry-After` ヘッダーに従って待機。1会議あたりの API コール数は 2-3 回程度
- **回避策**: 投稿間隔の設定を追加

### R5: 独立サービスのメンテナンスコスト

- **リスク**: Discord Bot と Slack サービスの 2 つのコードベースを維持するコスト
- **深刻度**: Low
- **確率**: 30%
- **対策**: 共有モジュール（diarizer, aligner, merger, generator 等）は同一ソースから import。新規コードは Slack 固有部分のみ
- **回避策**: 将来的に `core/` パッケージ化を検討

### R6: DiariZen モデルダウンロード（初回起動）

- **リスク**: HuggingFace からの初回モデルダウンロードが遅い/失敗
- **深刻度**: Low
- **確率**: 20%
- **対策**: on-demand ロードなのでサービス起動は遅延しない。`DiarizationError` catch でフォールバック
- **回避策**: Docker ビルド時にモデルを事前ダウンロード

---

## Validation Gates（フェーズ別チェックリスト）

### Phase 1 完了時

- [ ] `pytest tests/test_vtt_parser.py tests/test_slack_config.py -v` 全 pass
- [ ] VTTパーサーが Zoom VTTフォーマットの全エッジケースを処理
- [ ] パーサーの戻り値が `Segment` dataclass 互換
- [ ] `SlackServiceConfig` が YAML + 環境変数オーバーライドで読み込み可能
- [ ] バリデーションが不正値を検出
- [ ] import cycle なし

### Phase 2 完了時

- [ ] `pytest tests/test_slack_poster.py tests/test_zoom_drive_watcher.py -v` 全 pass
- [ ] Slack ポスターが Block Kit フォーマットで投稿
- [ ] HTTP 429 レート制限のリトライが機能
- [ ] ファイルペアリングが m4a + VTT 揃い時にコールバック発火
- [ ] ペアリングタイムアウトが正しくクリーンアップ
- [ ] StateStore 重複排除が機能
- [ ] Phase 1 テスト引き続き pass

### Phase 3 完了時

- [ ] `pytest tests/test_slack_pipeline.py tests/test_zoom_slack_bot.py -v` 全 pass
- [ ] パイプラインが VTT + 音声 → Slack 投稿まで End-to-End で処理
- [ ] 話者分離失敗時のフォールバック（単一話者）が機能
- [ ] エラー発生時に Slack エラー通知が送信
- [ ] エントリーポイントの起動 → 停止が正常動作
- [ ] シグナルハンドリング（SIGINT/SIGTERM）が機能
- [ ] Phase 1, 2 テスト引き続き pass

### Phase 4 完了時

- [ ] `pytest -v` 全テスト pass（既存 + ~48 新規）
- [ ] 実 Zoom m4a + VTT で End-to-End パイプラインが成功
- [ ] 議事録品質が実用レベル
- [ ] 話者分離ラベルが議事録に正しく反映
- [ ] エラーハンドリングが全エッジケースをカバー
- [ ] 長時間稼働で安定動作

---

## Definition of Done

### 機能要件

- [ ] Google Drive に Zoom m4a + VTT をアップロードすると、話者分離付き議事録が Slack に投稿される
- [ ] 話者ラベル（Speaker_0, Speaker_1 等）が議事録に正しく反映される
- [ ] 話者分離失敗時、単一話者（"Speaker"）でパイプラインが継続する
- [ ] m4a と VTT のペアリングがタイムアウト付きで正しく動作する
- [ ] 既存の Discord Minutes Bot に影響がない

### 品質要件

- [ ] `pytest -v` 全テスト pass（既存 + 新規、合計 ~48 新規テスト）
- [ ] 新規モジュール全てにユニットテストが存在する
- [ ] import cycle なし
- [ ] 型ヒントが全公開 API に付与されている
- [ ] docstring が全公開関数/クラスに存在する

### 運用要件

- [ ] `config_slack.yaml` の全フィールドにコメントが記載されている
- [ ] 起動方法と Slack App 設定手順がドキュメント化されている
- [ ] DiariZen ライセンス（CC BY-NC 4.0）がドキュメントに記載されている
- [ ] VRAM 使用量ログが DiariZen load/unload 前後で出力される

### コード品質

- [ ] 新規モジュールが既存コードベースのパターンに準拠（frozen dataclass, logging, エラーハンドリング）
- [ ] 共有モジュール（diarizer, aligner, merger, generator）は同一ソースから import（コピペ禁止）
- [ ] `Diarizer` Protocol により将来のバックエンド差し替えが容易

---

## File Change Summary

### New Files

| File | Lines (est.) | Description |
|------|-------------|-------------|
| `src/vtt_parser.py` | 60-80 | WebVTT → list[Segment] パーサー |
| `src/slack_config.py` | 80-100 | Slack サービス専用設定ローダー |
| `src/slack_poster.py` | 100-130 | Slack Web API 投稿（Block Kit） |
| `src/zoom_drive_watcher.py` | 100-140 | m4a + VTT ファイルペアリング監視 |
| `src/slack_pipeline.py` | 130-170 | Slack サービス用パイプラインオーケストレーター |
| `zoom_slack_bot.py` | 80-100 | 独立エントリーポイント |
| `config_slack.yaml` | 40-50 | Slack サービス設定テンプレート |
| `tests/test_vtt_parser.py` | 100-130 | VTT パーサーテスト |
| `tests/test_slack_config.py` | 50-70 | Slack 設定テスト |
| `tests/test_slack_poster.py` | 120-150 | Slack ポスターテスト |
| `tests/test_zoom_drive_watcher.py` | 120-150 | ファイルペアリング監視テスト |
| `tests/test_slack_pipeline.py` | 100-130 | パイプラインテスト |
| `tests/test_zoom_slack_bot.py` | 40-60 | エントリーポイントテスト |

### Modified Files

| File | Lines Changed (est.) | Description |
|------|---------------------|-------------|
| `requirements.txt` | +1 | `slack-sdk>=3.27` 追加 |
| `docker-compose.yml` | +15 | zoom-slack サービス追加（optional） |

### Total: 13 new files, 1-2 modified files, ~1,120-1,460 lines

---

## Rollback Plan

### サービス停止

独立サービスのため、停止は単純:

```bash
# プロセス停止
kill -SIGTERM <pid>

# Docker の場合
docker compose stop zoom-slack
```

### Rollback Scenarios

| Scenario | Action |
|----------|--------|
| Zoom VTT 品質不足 | Whisper フォールバックを v2 で実装。暫定的にサービス停止 |
| DiariZen が壊れる | サービス停止。既存 Discord Bot に影響なし |
| Slack API 認証失敗 | `config_slack.yaml` の `bot_token` を再設定、restart |
| VRAM 不足 | サービス停止、または `diarization.device: cpu` に設定 |
| 全コード削除が必要 | 新規ファイル 13 個を削除。既存コードへの変更は `requirements.txt` の 1 行のみ |

### Backward Compatibility

- 既存の Discord Minutes Bot への変更は一切なし
- 共有モジュールの API は不変（import するのみ）
- 新規ファイルの削除で完全にロールバック可能

---

## Rollout Plan

1. **開発環境**: `pip install slack-sdk` + `config_slack.yaml` 作成
2. **Gate 0 検証**: 実 Zoom データで VTT 品質 + DiariZen 精度を確認
3. **Phase 1-3 実装**: ユニットテスト駆動で段階的に実装
4. **Gate 2 検証**: 実データで End-to-End テスト
5. **本番デプロイ**: `python zoom_slack_bot.py` または Docker Compose で起動
6. **モニタリング**: ログ監視、Slack 投稿品質の目視確認

---

## Open Decisions

| # | Decision | Options | Recommendation | Status |
|---|----------|---------|----------------|--------|
| D1 | Slack Block Kit のメッセージフォーマット | 全文テキスト vs セクション分割 vs ファイル添付のみ | セクション分割 — 要約をメッセージ本文、全文を添付ファイル | Pending Phase 2 |
| D2 | ファイルペアリングのキー生成方式 | ファイル名プレフィックス vs 親フォルダ + タイムスタンプ | ファイル名プレフィックス — Zoom の命名規則に合致 | Pending Phase 2 |
| D3 | StateStore のファイルパス | 既存 `state/` ディレクトリ共有 vs 専用ディレクトリ | 既存 `state/` ディレクトリ — ファイル名で分離（`slack_processing.json`） | Decided |
| D4 | 話者ラベルの形式 | `Speaker_0` vs `Speaker A` vs `話者 1` | `Speaker A` — 可読性重視、v2 で実名に差し替え予定 | Pending Phase 3 |
| D5 | Docker Compose での GPU 共有 | 同一 GPU 共有 vs 排他制御 | 同一 GPU 共有 — DiariZen は on-demand load/unload で VRAM を解放 | Decided |

---

## Phase Summary

| Phase | 内容 | 工数 | 新規テスト | 累積テスト |
|-------|------|------|----------|----------|
| Phase 1 | VTTパーサー + Slack設定 | ~8h | +16 | +16 |
| Phase 2 | Slackポスター + ファイルペアリング | ~10h | +20 | +36 |
| Phase 3 | パイプライン + エントリーポイント | ~8h | +12 | +48 |
| Phase 4 | 統合テスト + ポリッシュ | ~6h | 0 | +48 |
| **Total** | | **~32h** | **+48** | **+48** |
