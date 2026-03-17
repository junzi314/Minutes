# 企画書分解結果

## メタ情報
- **元企画書**: `docs/requirements.md`
- **分解日**: 2026-03-17
- **総機能数**: 11（完了 3 + 新規 8）
- **総要件数**: 85（R-01 〜 R-85）

## 機能一覧（推奨実装順序）

> **注**: 元企画書に Phase 1-4 のマイルストーン構造があるため、それを第1レベルとして保持。コア機能は全て完了済み。将来拡張（Section 9）を Extension フェーズとして追加。

### Core（Phase 1-4）— ✅ 全完了

元企画書の Phase 1-4 は `discord-minutes-bot` RPI で全て実装済み。

| 順序 | feature-slug | 機能名 | 状態 | 要件数 |
|------|---|---|---|---|
| Core-1 | discord-minutes-bot | 自動議事録生成パイプライン | ✅ 完了 | 68 |

**追加実装**（元企画書スコープ外だが完了済み）:

| 順序 | feature-slug | 機能名 | 状態 | 備考 |
|------|---|---|---|---|
| Bonus-1 | — (Phase 5) | Google Drive監視 | ✅ 完了 | drive_watcher.py |
| Bonus-2 | — (Phase 6) | Docker/CUDA対応 | ✅ 完了 | Dockerfile + compose |
| Bonus-3 | — (Phase 7) | Craig Job API POST | ✅ 完了 | cook APIの改善 |
| Bonus-4 | — (Phase 8) | マルチギルド対応 | ✅ 完了 | R-77を先行実装 |
| Bonus-5 | forum-channel-support | フォーラムチャンネル対応 | ✅ 完了 | — |
| Bonus-6 | dedup-redesign | 状態管理リデザイン | ✅ 完了 | StateStore統合 |

### Extension（将来拡張）— 🆕 新規

| 順序 | feature-slug | 機能名 | 依存先 | 推定規模 | 要件数 |
|------|---|---|---|---|---|
| Ext-1 | multilingual-support | 日英混在対応 | なし | S | 1 |
| Ext-2 | template-customization | 議事録テンプレートカスタマイズ | なし | M | 1 |
| Ext-3 | speaker-analytics | 話者別発言量可視化 | なし | M | 1 |
| Ext-4 | minutes-search | 過去議事録検索 | なし | M | 1 |
| Ext-5 | external-export | Notion/Google Docs連携 | Ext-4 | M | 1 |
| Ext-6 | calendar-integration | カレンダー連携 | なし | M | 1 |
| Ext-7 | transcript-correction-ui | 文字起こし手動修正UI | なし | XL | 1 |
| Ext-8 | cloud-migration | VPS/クラウド移行 | なし | L | 1 |

### 推定規模の定義
- **S**: 半日〜1日（単一コンポーネントの変更）
- **M**: 1〜3日（複数ファイルの変更、新規API追加など）
- **L**: 3〜5日（新機能の追加、複数コンポーネントの連携）
- **XL**: 1週間以上（複雑なロジック統合。精度維持のために分割しない判断）

---

## 共通制約（Cross-cutting Concerns）

特定機能に紐付かない横断的な要件。各 REQUEST.md はこのセクションを参照する。

### パフォーマンス
| 指標 | 目標値 | 根拠（企画書セクション） |
|------|--------|----------------------|
| End-to-end処理時間 | 15分以内（1時間会議・GPU使用時） | §4.1 |
| 文字起こし速度 | 5〜10分/1時間音声（GPU） | §3.4 |
| 同時処理数 | 1件 | §4.1 |

### セキュリティ
- APIキー管理: `.env` ファイル、Git管理外 (§4.3)
- 音声データ: 処理完了後に自動削除 (§4.3)
- Bot権限: 必要最小限のDiscord権限のみ (§4.3)

### 信頼性
- APIリトライ: 最大3回、指数バックオフ (§4.2)
- エラー通知: 指定チャンネルにエラー詳細を投稿 (§4.2)
- ログ: ローテーション付きファイルログ (§4.2)
- 一時ファイル: 処理完了後に自動削除 (§4.2)

### 技術スタック指定
| ライブラリ/技術 | 用途 | 根拠 |
|---------------|------|------|
| Python 3.10+ / discord.py 2.3+ | Bot基盤 | §2.1 |
| faster-whisper (large-v3) | 文字起こし | §3.4 |
| Claude API (Sonnet) | 議事録生成 | §3.6 |
| FFmpeg | 音声変換（任意） | §3.3 |
| config.yaml + .env | 設定管理 | §5 |

### コスト
| 指標 | 目標値 | 根拠 |
|------|--------|------|
| 月額API費用 | ~$0.50（月4回1時間会議） | §4.4 |
| ホスティング | ¥0（ローカルPC） | §7 |

---

## トレーサビリティマトリクス

### Core 要件（R-01 〜 R-76） — 全て `discord-minutes-bot` で実装済み

| 要件番号 | 要件の概要 | 割当先 | カバー | 備考 |
|---|---|---|---|---|
| R-01 | Craig Botマルチトラック録音の利用 | discord-minutes-bot | 完全 | |
| R-02 | /joinコマンドで録音開始 | discord-minutes-bot | 完全 | Craig Bot側の機能 |
| R-03 | /stop or /leaveで録音終了 | discord-minutes-bot | 完全 | Craig Bot側の機能 |
| R-04 | 話者別音声ファイル出力（FLAC/Ogg） | discord-minutes-bot | 完全 | 実際はAAC |
| R-05 | 1サーバー・1チャンネル対象 | discord-minutes-bot | 完全 | Phase 8でマルチギルド拡張済み |
| R-06 | Craig Botメッセージ自動検知 | discord-minutes-bot | 完全 | detector.py |
| R-07 | Bot ID/embed内容で監視 | discord-minutes-bot | 完全 | on_raw_message_update |
| R-08 | 話者別音声ファイル自動DL | discord-minutes-bot | 完全 | craig_client.py |
| R-09 | 一時ディレクトリ保存・自動削除 | discord-minutes-bot | 完全 | tempfile.TemporaryDirectory |
| R-10 | DLリンク有効期限（7日）への対応 | discord-minutes-bot | 完全 | 即時処理で対応 |
| R-11 | FLAC/Ogg→WAV変換（FFmpeg） | discord-minutes-bot | 不要 | faster-whisperがAAC直接対応 |
| R-12 | 無音区間トリミング（任意） | — | 未実装 | 任意項目、影響軽微 |
| R-13 | ファイル分割不要 | discord-minutes-bot | 完全 | ローカル実行 |
| R-14 | faster-whisper使用 | discord-minutes-bot | 完全 | transcriber.py |
| R-15 | large-v3モデル | discord-minutes-bot | 完全 | config.yaml設定可能 |
| R-16 | ローカルGPU（NVIDIA CUDA） | discord-minutes-bot | 完全 | |
| R-17 | 話者別順次処理 | discord-minutes-bot | 完全 | VRAM制約対応 |
| R-18 | タイムスタンプ付きセグメント出力 | discord-minutes-bot | 完全 | Segment dataclass |
| R-19 | 日本語固定（language="ja"） | discord-minutes-bot | 完全 | config.yaml設定可能 |
| R-20 | トラック=話者（diarization不要） | discord-minutes-bot | 完全 | |
| R-21 | ファイル名から話者名解決 | discord-minutes-bot | 完全 | audio_source.py |
| R-22 | 処理速度 5-10分/1時間音声 | discord-minutes-bot | 完全 | |
| R-23 | CUDA Toolkit + cuDNN前提 | discord-minutes-bot | 完全 | |
| R-24 | 話者別結果を時系列マージ | discord-minutes-bot | 完全 | merger.py |
| R-25 | [HH:MM:SS] Speaker: text形式 | discord-minutes-bot | 完全 | |
| R-26 | Claude API (Sonnet) | discord-minutes-bot | 完全 | generator.py |
| R-27 | 統合トランスクリプトをLLM入力 | discord-minutes-bot | 完全 | |
| R-28 | 構造化議事録出力 | discord-minutes-bot | 完全 | prompts/minutes.txt |
| R-29 | 指定チャンネルに投稿 | discord-minutes-bot | 完全 | poster.py |
| R-30 | Embed形式サマリー投稿 | discord-minutes-bot | 完全 | |
| R-31 | Markdown詳細ファイル添付 | discord-minutes-bot | 完全 | |
| R-32 | 処理完了後に自動投稿 | discord-minutes-bot | 完全 | |
| R-33 | エラー時に管理者メンション | discord-minutes-bot | 完全 | |
| R-34 | 処理時間15分以内 | discord-minutes-bot | 完全 | |
| R-35 | 同時処理1件 | discord-minutes-bot | 完全 | |
| R-36 | API リトライ 最大3回 | discord-minutes-bot | 完全 | |
| R-37 | エラー通知 | discord-minutes-bot | 完全 | |
| R-38 | ログ保存 | discord-minutes-bot | 完全 | RotatingFileHandler |
| R-39 | 一時ファイル自動削除 | discord-minutes-bot | 完全 | |
| R-40 | APIキーは.envで管理 | discord-minutes-bot | 完全 | |
| R-41 | 音声データ処理後削除 | discord-minutes-bot | 完全 | |
| R-42 | Bot権限最小限 | discord-minutes-bot | 完全 | |
| R-43 | 月額$0.50目標 | discord-minutes-bot | 完全 | |
| R-44 | config.yaml設定 | discord-minutes-bot | 完全 | |
| R-45 | Discord設定項目 | discord-minutes-bot | 完全 | マルチギルド対応 |
| R-46 | Craig設定項目 | discord-minutes-bot | 完全 | |
| R-47 | Whisper設定項目 | discord-minutes-bot | 完全 | |
| R-48 | LLM設定項目 | discord-minutes-bot | 部分 | openaiプロバイダ未実装 |
| R-49 | 出力設定項目 | discord-minutes-bot | 完全 | |
| R-50 | Python/discord.py | discord-minutes-bot | 完全 | |
| R-51 | FFmpeg | discord-minutes-bot | 不要 | AAC直接対応で不要 |
| R-52 | Craig Bot導入済み前提 | discord-minutes-bot | 完全 | |
| R-53 | Craig仕様変更リスク | discord-minutes-bot | 完全 | detector.pyで対応 |
| R-54 | Whisper APIファイルサイズ制限 | discord-minutes-bot | 不要 | ローカル実行で制限なし |
| R-55 | GPU要件 | discord-minutes-bot | 完全 | |
| R-56 | Discord Embed制限 | discord-minutes-bot | 完全 | 4096文字制限対応 |
| R-57 | Discord添付ファイル制限 | discord-minutes-bot | 完全 | |
| R-58 | ローカルPC運用 | discord-minutes-bot | 完全 | |
| R-59 | 月額ホスティング¥0 | discord-minutes-bot | 完全 | |
| R-60 | OS起動時Bot自動起動 | discord-minutes-bot | 完全 | systemdサービス |
| R-61 | 会議時間帯にPC起動前提 | discord-minutes-bot | 完全 | |
| R-62 | Windows自動起動 | discord-minutes-bot | 部分 | systemdのみ実装 |
| R-63 | Mac自動起動 | discord-minutes-bot | 部分 | systemdのみ実装 |
| R-64 | Linux自動起動 | discord-minutes-bot | 完全 | systemdサービス |
| R-65 | スリープ防止 | — | 未実装 | 任意項目 |
| R-66 | 再起動後復帰 | discord-minutes-bot | 完全 | systemd Restart=on-failure |
| R-67 | 安定ネットワーク前提 | discord-minutes-bot | 完全 | |
| R-68 | Step 1: 指定チャンネルでCraigメッセージ監視 | discord-minutes-bot | 完全 | bot.py |
| R-69 | Step 2: Craig DLリンクメッセージ検出 | discord-minutes-bot | 完全 | detector.py |
| R-70 | Step 3: 音声ファイルDL・話者名抽出 | discord-minutes-bot | 完全 | craig_client.py |
| R-71 | Step 4: FFmpeg変換・最適化 | discord-minutes-bot | 不要 | AAC直接対応で不要 |
| R-72 | Step 5: 話者別faster-whisper文字起こし | discord-minutes-bot | 完全 | transcriber.py |
| R-73 | Step 6: 話者別結果を時系列マージ | discord-minutes-bot | 完全 | merger.py |
| R-74 | Step 7: LLMに送信・議事録取得 | discord-minutes-bot | 完全 | generator.py |
| R-75 | Step 8: Discord投稿（Embed+ファイル） | discord-minutes-bot | 完全 | poster.py |
| R-76 | Step 9: 一時ファイル削除・ログ記録 | discord-minutes-bot | 完全 | pipeline.py |

### Extension 要件（R-77 〜 R-85） — 将来拡張

| 要件番号 | 要件の概要 | 割当先 feature-slug | カバー |
|---|---|---|---|
| R-77 | 複数チャンネル/複数サーバー対応 | ✅ Phase 8で実装済み | 完全 |
| R-78 | 議事録テンプレートカスタマイズ | template-customization | — |
| R-79 | 文字起こし手動修正UI | transcript-correction-ui | — |
| R-80 | カレンダー連携 | calendar-integration | — |
| R-81 | 過去議事録検索 | minutes-search | — |
| R-82 | Notion/Google Docs自動エクスポート | external-export | — |
| R-83 | 話者別発言量・発言時間可視化 | speaker-analytics | — |
| R-84 | 日英混在対応 | multilingual-support | — |
| R-85 | VPS/クラウド移行 | cloud-migration | — |

## 未割当の要件

| 要件番号 | 要件の概要 | 未割当の理由 |
|---|---|---|
| R-11 | FLAC/Ogg→WAV変換 | Researchで不要と判明（faster-whisperがAAC直接対応） |
| R-12 | 無音区間トリミング | 任意項目、効果が限定的。将来必要時にExtensionとして追加可能 |
| R-51 | FFmpeg必須 | R-11と同様、必須ではなくなった |
| R-54 | Whisper APIファイルサイズ制限 | ローカル実行のため該当しない（元企画書の誤記） |
| R-65 | スリープ防止 | OS固有の対応が必要で優先度低。将来必要時に追加可能 |

## 未決事項（Open Items）

| ID | 項目 | 現在のステータス | 影響する機能 | 企画書参照 |
|---|---|---|---|---|
| OPEN-01 | OpenAIプロバイダ対応のスコープ | 未決（config.yaml にキーはあるが未実装） | template-customization | §5 R-48 |
| OPEN-02 | Craig Bot仕様変更への追従方針 | 運用で対応中 | discord-minutes-bot | §8 R-53 |
| OPEN-03 | Windows/Mac自動起動の実装要否 | 未決（現在Linux systemdのみ） | cloud-migration | §7.1 R-62, R-63 |

---

## 推奨ワークフロー

以下の順序で `/rpi:research` を実行してください:

### Extension（将来拡張）— 依存関係順

**Ext-1**（依存なし・最も小さい）:
```
/rpi:research rpi/multilingual-support/REQUEST.md
```

**Ext-2**（依存なし）:
```
/rpi:research rpi/template-customization/REQUEST.md
```

**Ext-3**（依存なし・Ext-1/2と並行可能）:
```
/rpi:research rpi/speaker-analytics/REQUEST.md
```

**Ext-4**（依存なし）:
```
/rpi:research rpi/minutes-search/REQUEST.md
```

**Ext-5**（Ext-4完了後）:
```
/rpi:research rpi/external-export/REQUEST.md
```

**Ext-6**（依存なし・Ext-1〜5と並行可能）:
```
/rpi:research rpi/calendar-integration/REQUEST.md
```

**Ext-7**（依存なし・最も規模大）:
```
/rpi:research rpi/transcript-correction-ui/REQUEST.md
```

**Ext-8**（Docker対応済みなので依存なし）:
```
/rpi:research rpi/cloud-migration/REQUEST.md
```

---

## 整合性チェックリスト
- [x] 全要件がトレーサビリティマトリクスに記載されている
- [x] 未割当の要件が理由付きで明記されている
- [x] 各 REQUEST.md の依存関係が矛盾していない（循環依存なし）
- [x] 推奨ワークフローの順序が依存関係と整合している
- [x] 共通制約セクションに横断・非機能要件が構造化されている
- [x] 未決事項が一覧化され、影響する機能にマッピングされている
- [ ] Q&A・付録由来の確定仕様 — 該当なし（企画書にQ&Aセクションなし）
- [ ] 検証タスク — 該当なし（Phase 0で実施済み）
- [ ] デモシナリオ — 該当なし
