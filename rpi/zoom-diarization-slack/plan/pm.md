# pm.md -- Product Requirements: Zoom Diarization Slack Service

**Feature Slug**: zoom-diarization-slack
**Date**: 2026-04-03
**Status**: Planning
**Priority**: P1
**Traceability**: REQUEST.md, research/RESEARCH.md (CONDITIONAL GO, 85%)

---

## 1. プロダクト概要

### Context

対面会議において1台のPCでZoomをレコーダーとして使用し、クラウドレコーディング（m4a音声 + VTT文字起こし）をGoogle Driveに自動保存するワークフローが一般的に存在する。しかし、Zoomの文字起こしは参加者が1名（レコーダーPC）のため話者ラベルが全て同一人物扱いとなり、「誰が何を話したか」が不明な議事録しか生成できない。結果として、会議後に30-60分の手動議事録作成が必要になっている。

### Why Now

- 既存Discord Minutes Botのコードベースの~70%（~1,500行）を直接再利用可能であることをResearchフェーズで確認済み
- DiariZen話者分離（~540MB VRAM）のみでWhisper不要（Zoom VTTが代替）のため、GPU負荷が既存パイプラインの約1/9
- `Segment` dataclass が汎用インターフェースとして機能し、aligner/merger/generatorをそのまま利用可能
- 既存の `VideoDriveWatcher` がm4a/mp4のmimeType検知に対応済み
- 既存Discord Botとは完全独立プロセスのため、導入リスクゼロ

### Solution Overview

Google Driveに保存されたZoom録音（m4a + VTT）を自動検知し、VTTパース + DiariZen話者分離 + majority-vote突合 + Claude API議事録生成を経て、Slackチャンネルに構造化された話者付き議事録を投稿する独立サービス。

---

## 2. ユーザーストーリー

### US-1: Zoom録音からの自動議事録生成

**As a** 会議主催者,
**I want** Zoomで会議を録音するだけで話者付き議事録がSlackに自動投稿されること,
**So that** 手動での議事録作成作業（30-60分）を完全に省略できる。

### US-2: 話者識別付きの議事録確認

**As a** 会議参加者,
**I want** 議事録で「誰が何を話したか」が話者ラベル（Speaker A, Speaker B等）で区別されていること,
**So that** 会議の発言内容を正確に振り返り、各自のアクションアイテムを確認できる。

### US-3: Slackワークフローへの統合

**As a** チームメンバー,
**I want** 議事録が普段使っているSlackチャンネルに投稿されること,
**So that** 別ツールに移動せず、チャットの流れの中で議事録を参照・議論できる。

### US-4: 処理失敗時の安全な動作

**As a** サービス管理者,
**I want** 話者分離やVTTパースが失敗しても最低限の議事録が生成されること,
**So that** 処理全体が停止せず、常に何らかの議事録を得られる。

### US-5: ゼロ運用負荷

**As a** サービス管理者,
**I want** 初期設定後はGoogle Drive監視が完全自動で動作し、手動操作が不要であること,
**So that** 日常的な運用コストがゼロになる。

---

## 3. ビジネス価値

| 観点 | 価値 |
|------|------|
| **時間削減** | 会議あたり30-60分の議事録作成作業を自動化。月10回の会議で5-10時間/月の節約 |
| **品質向上** | LLMによる構造化議事録は手動メモより網羅性が高く、抜け漏れを削減 |
| **即時性** | 会議終了後、数分以内に議事録がSlackに投稿される（手動の場合は翌日以降になることも） |
| **技術資産活用** | 既存コードの70%再利用。新規コード~770-1,040行で実現可能 |
| **低リスク** | 既存Discord Botと完全独立。導入による既存機能への影響ゼロ |

---

## 4. 機能要件

### Must Have（v1）

#### FR-1: Zoom VTTパーサー

**説明**: Zoom クラウドレコーディングが生成するWebVTTファイルをパースし、タイムスタンプ付きテキストセグメントのリストに変換する。

**受入基準**:
- AC-1.1: WebVTT形式（`.vtt`）のファイルを入力として受け付け、`list[Segment(start, end, text, speaker="")]` を返すこと
- AC-1.2: Zoomが挿入するヘッダー行（`WEBVTT`, `Kind:`, `Language:`等）を正しくスキップすること
- AC-1.3: タイムスタンプ形式 `HH:MM:SS.mmm --> HH:MM:SS.mmm` を `float` 秒に変換すること
- AC-1.4: 空のキュー、連続する同一テキストのキュー等のエッジケースを適切に処理すること
- AC-1.5: マルチライン字幕（1キュー内に複数行のテキスト）を結合して1セグメントにすること
- AC-1.6: パース失敗時に `VttParseError` を送出し、エラー箇所（行番号）をメッセージに含めること

#### FR-2: DiariZen話者分離

**説明**: Zoomの音声録音（m4a）をDiariZenで処理し、話者ごとの時間セグメントを取得する。

**受入基準**:
- AC-2.1: m4aファイルを入力とし、`list[DiarSegment(start, end, speaker)]` を返すこと
- AC-2.2: 既存の `src/diarizer.py`（`Diarizer` Protocol + `DiariZenDiarizer`）をそのまま再利用すること
- AC-2.3: m4aからWAV 16kHz monoへの変換は既存の `src/audio_extractor.py` を使用すること
- AC-2.4: 推論は `asyncio.to_thread()` で非同期実行すること
- AC-2.5: 推論完了後にモデルを明示的に解放し（`del model` + `torch.cuda.empty_cache()`）、VRAMを回収すること

#### FR-3: VTT x 話者突合（アライメント）

**説明**: VTTパーサーの出力セグメントとDiariZenの話者セグメントをmajority-vote overlapアルゴリズムで突合し、各テキストセグメントに話者ラベルを付与する。

**受入基準**:
- AC-3.1: 既存の `src/segment_aligner.py` をそのまま再利用すること
- AC-3.2: 各VTTセグメントに対し、時間的重複が最大の話者ラベルを割り当てること
- AC-3.3: 重複する話者セグメントが存在しない場合、`speaker="Unknown"` を割り当てること
- AC-3.4: 出力は `list[Segment(start, end, text, speaker)]` であること（既存 `Segment` dataclass）
- AC-3.5: v1では匿名話者ラベル（Speaker A, Speaker B等）を使用すること

#### FR-4: Claude API議事録生成

**説明**: 話者付きセグメントから既存のClaude APIモジュールを使って構造化議事録を生成する。

**受入基準**:
- AC-4.1: 既存の `src/merger.py` で話者付きセグメントを統合テキストに変換すること
- AC-4.2: 既存の `src/generator.py` でClaude APIを呼び出し、議事録Markdownを生成すること
- AC-4.3: プロンプトテンプレートは既存の `prompts/minutes.txt` を使用すること（必要に応じてZoom用テンプレートの追加はNice to Have）
- AC-4.4: APIのリトライ（最大2回）とエラーハンドリングは既存実装を継承すること

#### FR-5: Slack Web API投稿

**説明**: 生成された議事録をSlack Web APIで指定チャンネルに投稿する新規モジュール。

**受入基準**:
- AC-5.1: `slack_sdk` の `WebClient` を使用し、指定チャンネルに議事録を投稿すること
- AC-5.2: 議事録本文は `chat.postMessage` でBlock Kit（`section` + `mrkdwn`）として投稿すること
- AC-5.3: Slackメッセージの文字数制限（3,000文字/block、50 blocks/message）を超える場合、議事録をスニペットファイルとして添付すること
- AC-5.4: 投稿失敗時にリトライ（最大3回、exponential backoff）を行うこと
- AC-5.5: `SlackPostError` 例外クラスを定義し、API応答のエラー詳細を含めること
- AC-5.6: Botトークン（`xoxb-`）による認証を使用すること

#### FR-6: Google Drive監視 + ファイルペアリング

**説明**: Google Driveの指定フォルダを監視し、Zoomが非同期にアップロードするm4aとVTTのファイルペアを検知して処理を開始する。

**受入基準**:
- AC-6.1: 既存の `VideoDriveWatcher` をベースに、m4a + VTTの両ファイルが揃うまで待機するペアリングロジックを実装すること
- AC-6.2: ファイルペアリングはファイル名のプレフィックス一致（Zoomの命名規則: 同一ミーティングのファイルは同名プレフィックス）で行うこと
- AC-6.3: ペアリングタイムアウト（デフォルト5分）を設け、片方のファイルのみの場合はタイムアウト後にVTTなし/m4aなしで処理可能な範囲で継続すること
- AC-6.4: 既存の `StateStore` で重複検知を行い、同じファイルペアが2回処理されないこと
- AC-6.5: ポーリング間隔は設定可能（デフォルト30秒）であること
- AC-6.6: mimeType `audio/mp4`（m4a）と `text/vtt` をフィルタ条件とすること

#### FR-7: 独立エントリーポイント

**説明**: Discord Botとは完全に独立したプロセスとして起動できるエントリーポイント。

**受入基準**:
- AC-7.1: `diarization_slack_bot.py` として独立した起動スクリプトを用意すること
- AC-7.2: `config_diarization.yaml` として専用の設定ファイルを使用すること（既存 `config.yaml` とは独立）
- AC-7.3: Discordライブラリ（`discord.py`）のimportを一切行わないこと
- AC-7.4: `SLACK_BOT_TOKEN`, `ANTHROPIC_API_KEY` を環境変数（`.env`）から読み込むこと
- AC-7.5: `--log-level` オプションでログレベルを指定可能であること
- AC-7.6: SIGINT/SIGTERM で安全にシャットダウンすること（処理中のパイプラインがあれば完了を待機）

#### FR-8: Slackパイプラインアダプター

**説明**: 既存のDiscord依存パイプライン（`pipeline.py` の `run_pipeline_from_segments()`）からDiscord結合を排除し、Slack投稿に接続するアダプターモジュール。

**受入基準**:
- AC-8.1: `src/slack_pipeline.py` として、VTTパース → 話者分離 → 突合 → merger → generator → Slack投稿の全ステージをオーケストレーションすること
- AC-8.2: 各ステージの所要時間をログに記録すること（`elapsed` フィールド）
- AC-8.3: 任意のステージでエラーが発生した場合、エラー詳細をログに記録し、可能な範囲でフォールバック処理を実行すること
- AC-8.4: 処理状態を `StateStore` で管理し、クラッシュ後の再起動時に処理済みファイルをスキップすること

### Nice to Have（v1ストレッチ）

| # | 項目 | 備考 |
|---|------|------|
| NH-1 | Zoom専用プロンプトテンプレート | 会議形式（対面会議）に最適化したプロンプト |
| NH-2 | Slack投稿へのスレッド返信で全文テキスト添付 | メインメッセージは要約、スレッドに全文 |
| NH-3 | VTT品質スコアの自動算出 | 日本語誤変換率の推定。閾値超過でWARNログ |
| NH-4 | 手動トリガーコマンド | Google Driveリンクを引数に手動処理開始 |

---

## 5. 非機能要件

### Performance

| 項目 | 要件 | 根拠 |
|------|------|------|
| エンドツーエンド処理時間 | < 10分（60分音声） | Whisper不要のため既存パイプラインより高速 |
| VTTパース | < 1秒 | テキスト処理のみ |
| DiariZen話者分離 | < 3分（60分音声） | WavLM-basedモデルの推定値 |
| VTT x 話者突合 | < 1秒 | インメモリ計算 |
| Claude API議事録生成 | < 60秒 | 既存ベンチマーク基準 |
| Slack投稿 | < 5秒 | 単一API呼び出し |
| VRAMピーク使用量 | < 1GB | DiariZen ~540MB（Whisper不要） |

### Scale

| 項目 | 要件 |
|------|------|
| 同時処理 | 1件（逐次実行モデル。GPU共有リスク回避） |
| 対応音声長 | 最大4時間（DiariZenのメモリ・処理時間の実用的上限） |
| 対応話者数 | 自動検出（DiariZen依存、通常2-10名） |
| 1日あたり処理件数 | ~5件（会議頻度の実用的上限） |

### Reliability / SLO

| 項目 | SLO |
|------|-----|
| 話者分離付き議事録生成成功率 | >= 85% |
| フォールバック込み議事録生成成功率 | >= 95% |
| Slack投稿成功率 | >= 99%（リトライ込み） |
| 既存Discord Botへの影響 | 0件（独立プロセス） |
| OOM発生率 | 0件/月 |
| 重複処理発生率 | 0件（StateStoreによる排他） |

### Security & Privacy

| 項目 | 要件 |
|------|------|
| 音声データの外部送信 | なし。ローカルGPUで処理。Claude APIにはテキストのみ送信 |
| 認証情報の管理 | Slack Bot Token, Anthropic API Key は `.env` ファイルまたは環境変数。リポジトリに含めない |
| Google Drive認証 | Service Account JSON key。`credentials.json` として管理。`.gitignore` に含めること |
| 一時ファイル | WAV変換ファイルは `tempfile` で自動削除。処理完了後にディスクに残らないこと |
| ログの機密情報 | APIキー、トークン、ファイル内容をログに出力しないこと |

### Observability

| 項目 | 実装 |
|------|------|
| パイプライン開始/完了 | `logger.info` にソースファイル名、話者数、処理結果を含める |
| 各ステージ所要時間 | `logger.info` に `elapsed` フィールドを含める |
| VRAM使用量 | `logger.info` にモデルロード前後の `torch.cuda.memory_allocated()` を記録 |
| 話者分離結果 | `logger.info` に検出話者数とセグメント数を記録 |
| フォールバック発生 | `logger.warning` にエラー詳細とフォールバック理由を記録 |
| Slack投稿結果 | `logger.info` に投稿先チャンネル名と `message.ts` を記録 |
| ファイルペアリング | `logger.info` にペアリング状態（待機中/完了/タイムアウト）を記録 |

---

## 6. 受入基準（Must Have項目別）

各Must Have機能要件の受入基準は「4. 機能要件」セクションに記載済み。以下はシステムレベルの受入基準。

### SA-1: エンドツーエンド処理

- 60分のZoom録音（m4a + VTT）をGoogle Driveにアップロードした場合、10分以内にSlackに話者付き議事録が投稿されること
- 議事録に少なくとも2名以上の話者ラベル（Speaker A, Speaker B）が含まれること（3名以上の会議の場合）
- 議事録の構造（まとめ、詳細、アクションアイテム等）が既存Discord Bot出力と同等であること

### SA-2: フォールバック動作

- DiariZenが失敗した場合、全セグメントが `speaker="Speaker"` として処理され、話者なし議事録がSlackに投稿されること
- VTTパースが失敗した場合、エラーログが出力され、処理がスキップされること（音声のみからの議事録生成はv1スコープ外）
- Slack投稿が3回リトライ後も失敗した場合、`SlackPostError` がログに記録され、処理が完了ステータスになること（無限リトライしない）

### SA-3: 重複排除

- 同じファイルペアが2回目にポーリングで検知されても処理が開始されないこと
- サービス再起動後も、`StateStore` の永続化により処理済みファイルの情報が維持されること

### SA-4: テストカバレッジ

- 新規モジュール（`vtt_parser`, `slack_poster`, `slack_pipeline`）に対するユニットテストが存在すること
- 外部依存（Slack API, Google Drive API, DiariZen）はモックで代替されること
- 全テストが `pytest` で30秒以内に完了すること

---

## 7. スコープ外（v2）

| 項目 | 理由 |
|------|------|
| 話者識別（声紋マッチング） | WeSpeaker embeddingによる実名マッピングは精度検証が必要。v1の話者分離精度を評価してから着手 |
| 声紋登録システム | 音声サンプルからembeddingを抽出・保存する仕組み。v2の話者識別と同時に実装 |
| 未登録話者の自動検出 | 「Unknown Speaker 1」等の表示。話者識別の前提機能 |
| pyannote 3.1への差し替え | 既存 `Diarizer` Protocolで対応可能だが、v1ではDiariZenで十分 |
| Whisperフォールバック | VTT品質が不十分な場合の代替。RTX 3060で実行可能だが、v1ではVTT前提 |
| 話者登録用Slackコマンド | Slackに音声ファイルをアップロードして声紋登録。v2の声紋登録と同時に検討 |
| 複数会議室（チャンネル）対応 | 設定でチャンネルマッピングを定義。v1は単一チャンネル |
| リアルタイム話者分離 | バッチ処理のみ。リアルタイムは別アーキテクチャが必要 |
| 音声前処理（ノイズ除去） | DiariZen内部のVADで十分。追加の前処理は精度評価後 |

---

## 8. 制約条件

| 制約 | 詳細 | 影響 |
|------|------|------|
| **Zoom Pro以上必須** | クラウドレコーディング + 音声トランスクリプト機能はPro以上のプランで利用可能 | ユーザーのZoomプランに依存 |
| **DiariZen CC BY-NC 4.0** | 非商用利用のみ許可。個人プロジェクト/チーム内ツールとしての使用は許容範囲 | 商用展開する場合はpyannote（MIT）への差し替えが必要 |
| **単一室内録音が前提** | Zoomを対面会議のレコーダーとして使用。リモート参加者がいる場合は話者分離精度が低下する可能性 | ユースケースの制約を文書に明記 |
| **Slack Freeプラン制限** | メッセージ履歴90日、API呼び出し制限あり | 議事録の長期保存にはSlack Pro以上またはGoogle Docs連携が必要 |
| **GPU必須** | DiariZenはCUDA対応GPUを要求。CPU実行は大幅な速度低下 | RTX 3060 12GBで動作確認済み |
| **Zoom VTT日本語品質** | 20-40%のエラー率の可能性（Research調査）。Gate 0で要検証 | 品質不足の場合はv2でWhisperフォールバックを検討 |
| **ファイルペアリング** | Zoomはm4aとVTTを非同期にアップロード。アップロード完了までのラグが発生 | タイムアウト付きバッファで対応 |

---

## 9. 成功指標

### Leading Indicators（先行指標）

| 指標 | 目標 | 計測方法 |
|------|------|----------|
| パイプライン処理成功率 | >= 85%（話者分離付き） | ログ集計（`slack_pipeline.success` / `slack_pipeline.total`） |
| フォールバック込み成功率 | >= 95% | ログ集計（フォールバック含む全議事録生成の成功率） |
| エンドツーエンド処理時間 | < 10分（60分音声） | ログ集計（`pipeline.elapsed`） |
| VRAMピーク使用量 | < 1GB | `torch.cuda.memory_allocated()` ログ |
| ファイルペアリング成功率 | >= 90% | ログ集計（タイムアウト内にペア成立した割合） |
| Slack投稿成功率 | >= 99% | ログ集計（リトライ込みの投稿成功率） |

### Lagging Indicators（遅行指標）

| 指標 | 目標 | 計測方法 |
|------|------|----------|
| 月間処理ファイル数 | >= 4件/月 | StateStoreの処理済みファイル数 |
| OOM発生率 | 0件/月 | プロセスクラッシュログ |
| 手動議事録作成時間の削減 | 月あたり2-5時間の削減 | ユーザーヒアリング |
| 議事録の活用率 | Slack上での議事録メッセージへのリアクション/スレッド返信 | Slack Analytics |
| VTT品質起因の問題報告 | < 2件/月 | ユーザーフィードバック |

---

## 10. ロールアウト計画

### Phase 0: 実データ検証ゲート（4h）

**Gate Criteria** -- 全て通過でPhase 1に進行:

1. 実際のZoom会議を録音し、m4a + VTTをGoogle Driveに取得する
2. VTTの日本語精度を目視確認する（エラー率40%超ならWhisperフォールバックをv1スコープに格上げ）
3. DiariZenでm4aを処理し、3-4名の話者分離精度を確認する（DER 30%超なら要件を再検討）
4. Slack App作成とBot Token取得を完了する

### Phase 1: VTTパーサー + Slackポスター（10-14h）

- `src/vtt_parser.py`: WebVTT -> `list[Segment]`
- `src/slack_poster.py`: Slack Web API投稿モジュール
- `tests/test_vtt_parser.py`: パーサーのユニットテスト
- `tests/test_slack_poster.py`: Slackモック付きユニットテスト

### Phase 2: パイプライン + エントリーポイント（6-8h）

- `src/slack_pipeline.py`: Slack用パイプラインアダプター
- `diarization_slack_bot.py`: 独立エントリーポイント
- `config_diarization.yaml`: 専用設定ファイル
- Config dataclass追加（`SlackConfig`等）

### Phase 3: ファイルペアリング（4-6h）

- `VideoDriveWatcher` にm4a + VTTペアリングロジック追加
- タイムアウト付きバッファ実装
- ペアリングのユニットテスト

### Phase 4: 統合テスト + ポリッシュ（6-8h）

- エンドツーエンド統合テスト（モック付き）
- エラーハンドリング・フォールバックの網羅テスト
- ログ出力の確認と調整
- ドキュメント更新

**合計推定工数: 30-40h（4-5日）**

---

## 11. リスクと未解決事項

### リスク

| # | リスク | 影響 | 確率 | 緩和策 |
|---|--------|------|------|--------|
| R1 | Zoom VTTの日本語品質が想定以下（エラー率40%超） | Medium | 60% | Gate 0で検証。不合格ならWhisperフォールバックをv1に含める。RTX 3060 12GBで同時実行可能 |
| R2 | 室内マイクでの話者分離精度が低い（DER > 30%） | Medium | 40% | Gate 0で実データ検証。精度不足なら話者ラベルなし（単一話者）でv1出荷し、v2でモデル改善 |
| R3 | m4a + VTTのファイルペアリング競合・タイミング問題 | Low | 40% | タイムアウト付きバッファ（5分）。片方のみでもフォールバック処理可能に設計 |
| R4 | Slack API仕様変更（`files.upload` v2移行等） | Low | 20% | `slack-sdk` v3使用。`files.getUploadURLExternal` の新APIを採用 |
| R5 | DiariZenのPython 3.12互換性問題 | Low | Medium | 既にspeaker-diarization機能でインストール検証済み。問題発生時はpyannote 3.1へ差し替え |
| R6 | Slack Freeプランの制限によるUX制約 | Low | N/A | 議事録の90日消失を明記。Google Docs連携をNice to Haveとして将来検討 |

### 未解決事項

1. **Zoom VTTの実際の日本語品質は?** -- Gate 0で実データを用いた検証が必要
2. **Slack Appの権限スコープは?** -- `chat:write`, `files:write` が最低限必要。追加スコープの要否を確認
3. **Block Kitのフォーマット詳細は?** -- 議事録のMarkdownからBlock Kitへの変換ルールの詳細設計が必要
4. **Google Driveフォルダの分離は?** -- 既存Craig用フォルダとZoom用フォルダを分けるか、同一フォルダでmimeTypeフィルタのみで区別するか（推奨: 別フォルダ）
5. **VTTなしの場合の処理方針は?** -- m4aのみがアップロードされた場合、Whisperフォールバックなしではテキスト生成不可。v1ではスキップしてログ出力するか
