# 改善調査レポート — discord-minutes-bot

- **実施日**: 2026-06-25
- **対象ブランチ**: feature/zoom-diarization-slack
- **手法**: 8観点（モジュール別5＋横断3：セキュリティ／並行性／テスト・衛生）で読み取り専用の並行監査を実施。複数エージェントが独立に同一の重大問題へ収束したものは確度が高い。最重要・セキュリティ関連の指摘は本体コードで再確認済み。

## 総評

土台は堅実（ログのシークレットマスキング、状態の原子的書き込み、OAuthスコープ最小化、Claude/Drive 呼び出しのスレッド退避、約394テスト）。ただし **並行処理・失敗時リカバリ周りに潜在的な運用リスクが集中** し、いくつかの機能が壊れている／壊れやすい状態にある。

---

## 🔴 最優先（即対応推奨）

### 1. GPU文字起こしが直列化されていない → VRAM OOM
`bot.py` / `src/pipeline.py:456` — コード全体に `Lock`/`Semaphore` が一つも無い（grep確認済み）。Drive複数フォルダ監視・Craig検知・`/minutes process` が同時発火すると、共有 `WhisperModel` に対し `model.transcribe()` が並列実行され、RTX 3060(12GB)でOOM。最も発生確率の高い実害。
**修正**: 文字起こし段に `asyncio.Semaphore(1)` を導入（DL・Claude・投稿は並列のまま、GPUだけ直列化）。

### 2. 文字起こしが生成前にディスク保存されない → ステージ5失敗で消失
`src/pipeline.py:105-190` — `transcript`/`transcript_md` はメモリのみ。永続化（`archive.store`, `pipeline.py:243`）は `generate()` 成功後。Claude課金切れ等で生成が落ちると高コストなGPU出力が消え、temp ZIPも `finally` で削除。MEMORYの「recover-failed-generation」に一致。
**修正**: マージ直後に transcript を `state/recovery/{rec_id}/` へ保存し、全成功後に削除。`resume_from_transcript()` で再生成可能に。

### 3. `/minutes search` が常時クラッシュ（SQLiteスレッド違反）
`src/minutes_archive.py:34`（`check_same_thread` 既定True・メイン生成）＋ `bot.py:638,642,667`（`to_thread`＝別スレッド実行）→ 毎回 `sqlite3.ProgrammingError`。テストは同一スレッド直呼びのため検知漏れ。
**修正**: `sqlite3.connect(..., check_same_thread=False)` ＋ 全DBアクセスを `threading.Lock` で直列化。

---

## 🔐 セキュリティ（高優先）

### S1. 資格情報が world-readable ＋ `.gitignore` 欠落
`.gitignore` に `token.json`/`oauth_client.json`/`output/`/`recovered/`/`state/`/`*.bak` が無い。権限は `.env`(644)、`credentials.json`(755)、`oauth_client.json`(755)、`token.json`(644)。`output/`・`recovered/` は会議PII、`token.json`/`oauth_client.json` は有効なGoogle OAuth資格情報。`git add .` 一発でPII＋シークレットがコミットされ得る（現状コミット済みは無し）。
**修正**: `chmod 600` ＋ `.gitignore` 追加。`token.json` 書込時に明示的 `os.chmod(0o600)`。

### S2. `/minutes process` が無認可＋コスト濫用＋ホスト検証が緩い
`bot.py:489-541` — `has_permissions` 無し（他コマンドは付与済み）。任意メンバーがGPU＋有償Claudeを起動でき、課金枯渇/GPU占有のDoSが可能。ホスト正規表現 `craig\.\w+`（`detector.py:19`）はピン留め無し、`outputFileName`（`craig_client.py:87`）も無検証でURL連結。
**修正**: `@has_permissions(manage_guild=True)` ＋レート制限、ホストallowlist、`outputFileName` を `^[A-Za-z0-9._-]+$` 検証、`interaction.response.defer()` 追加。

---

## 🟠 High

| # | 場所 | 問題 | 修正 |
|---|------|------|------|
| H1 | `state_store.py:145-147` ＋ `bot.py:440` | `is_known` が `error` も既知扱い → 失敗録音は永久に再試行不可（#2と複合で致命化）。`cleanup_stale` も `processing` のみ回収 | 失敗時はエントリ削除 or dedupから `error` 除外（試行回数で上限） |
| H2 | `drive_watcher.py:406` | アップロード途中のZIP→`BadZipFile`→`mark_failed`→H1でその会議が永久放棄 | 再試行可能/恒久失敗を区別。サイズ安定をポーリング2回で確認 |
| H3 | `generator.py` | 長尺で `stop_reason=="max_tokens"` 未チェック → 切れた議事録を完成扱いでキャッシュ/出力 | stop_reason確認＋警告/キャッシュ抑止、max_tokens引上（>16Kはstream化） |
| H4 | `generator.py` | 入力コンテキスト超過チェック無し → 高コストの文字起こし後に400で遅延失敗 | 送信前 `count_tokens`、分割 or 早期失敗 |
| H5 | `poster.py:116-134` | Embed超過トリムが位置固定で誤フィールドを削る（event_title有時破綻）・負indexスライス・反復無し | フィールド名指定、`max(0,...)`、超過まで反復 |
| H6 | `craig_client.py:92` / `drive_watcher.py:375` | ZIP展開がイベントループ上で実行 → heartbeat停止 | `asyncio.to_thread(self._extract_zip, ...)` |
| H7 | `generator.py:86` | クライアントに `timeout` 未設定＋SDK再試行と自前再試行が二重 | 明示timeout、再試行を一層に |
| H8 | `craig_client.py:110-216` | 401/403/404 を fatal 扱いせず polling継続 → 認証エラーが10分タイムアウト化 | 4xxは即 `AudioAcquisitionError` |
| H9 | `calendar_client.py:91,174` | `api_timeout_sec` 未使用＋ `fromisoformat` が `Z` でPython3.10失敗→無言で常時不一致 | `Z`→`+00:00` 正規化、timeout結線 |
| H10 | `exporter.py:95-97` | `token.json` を複数スレッドからロック無し書込 → torn-write | 起動時に一度だけ構築 or Lock＋原子的書込 |
| H11 | `bot.py:160-185,340` | `on_ready` が再接続毎に発火 → watcher多重・`tree.sync` 毎回 | 起動一回ガード |

---

## 🟡 Medium

- **M1** 設定の未知キーが無言で無視 `config.py:299`（全タイポが既定値にサイレント化）→ `_build_section` で未知キー検出。
- **M2** 死んだ設定 `config.py:93-96`（`craig.domain`/`bot_id` 未使用、既定 `craig.chat` も誤り＝正は `craig.horse`）。
- **M3** 起動時 `ANTHROPIC_API_KEY` 未検証 `config.py:586`（既定claudeでステージ5まで遅延失敗）。
- **M4** discordセクションのみ env override 無効 `config.py:317`（docstringと矛盾）。
- **M5** 状態ストア無制限肥大 `state_store.py`（`_processing`/`minutes_cache` 未剪定、毎回全文再シリアライズ）。
- **M6** `asyncio.timeout` は `to_thread` を中断不可 `pipeline.py:96`（タイムアウト後もGPU占有）。#1のsemaphoreと整合を。
- **M7** `exporter.py`(822行)の index 演算が脆い（チェックボックスbatch順序 `:690`、insert/style chunk分断 `:455`）。
- **M8** FTS5検索が特殊文字でクラッシュ `minutes_archive.py:164`（`"`,`*`,`(` 等）。
- **M9** マージのオーバーラップ／タイムスタンプ既定不整合 `merger.py`/`config.py:120`（`[{mm}:{ss}]` は1h超で時を落とす）。
- **M10** 二つの transcriber で `transcribe_all` 重複 `transcriber.py:136`/`transcriber_api.py:173`。
- **M11** API文字起こしの25MB上限 `transcriber_api.py:123`（通常長で失敗）。
- **M12** ZIP展開: 同名basenameで話者トラック無言上書き、拡張子大文字無視、0トラックでも成功扱い `audio_source.py:65-77`（※トラバーサル自体はbasename化で防御済み）。
- **M13** 用語集が連鎖置換・順序依存 `glossary.py:63`。
- **M14** 投稿に `allowed_mentions` 未指定 `poster.py:228`。
- **M15** Driveクエリのクォート未エスケープ＋例外/PIIをDiscordへ素出力 `drive_watcher.py:155-166`/`poster.py:148`。

---

## 🟢 Low / 衛生

- **CLAUDE.md ドリフト**（※`.gitignore`済の個人ドキュメント）: 「139テスト」→実394、ディレクトリ図に6モジュール欠落（exporter/calendar/archive/transcriber_api/glossary/speaker_analytics）。`docs/requirements.md` のCraig API記述も旧仕様。
- ルート直下 `test_docs_button.py`/`test_forum_live.py` はアサート無しのライブ手動スクリプト＝`pytest` が誤収集（`testpaths` 設定皆無）→ `scripts/` 移動 or 削除。
- 運用スクリプト（`auth_setup.py`/`recover_transcript.py`/`deliver_recovered.py`）は `scripts/` で追跡推奨。`reexport.py`/`demo_export.py` はハードコードパスの使い捨て。
- 依存欠落 `google-auth-oauthlib` が `requirements.txt` 未記載（実使用）。
- 実テスト欠落 `test_audio_source.py`。
- 他: `/minutes status` 内 `import ctranslate2`、`pipeline.py:270` の `segments` 未束縛リスク、`_truncate` の `max_length<3` 下限、`SpeakerInfo.user_id` 常時0 など。

---

## ✅ 良い点（壊さない方がよい設計）

ログのシークレットマスキング（`bot.py:41-107`）／状態の原子的書込（tmp+`os.replace`）＋DrvFs警告／export・archiveの非致命扱い／Claude・Drive呼び出しの `to_thread` 退避／OAuthスコープ最小化（drive.readonly・calendar.readonly・drive.file）／`render_prompt` の `.replace` によるブレース注入回避／約394テストの良質なカバレッジ。

---

## 着手順（推奨）

1. **S1**（.gitignore/権限＝即） → 2. **#1 GPU semaphore** → 3. **#3 SQLite修正** → 4. **#2 transcript永続化＋H1再試行** → 5. **S2 権限** → 6. **H3/H5**（長尺の議事録切れ・投稿超過）。

## 対応状況（2026-06-25 監査セッション）

すべて TDD（失敗テスト→実装→緑）で対応。テストは overlay venv（`pytest<9` + `pytest-asyncio` + `aioresponses`）で実行。

- [x] **#1 GPU semaphore** — `pipeline._stage_transcribe` をループ単位の `Semaphore(1)` で直列化（local backend のみ。api はゲートしない）。テスト: `TestTranscriptionSerialization`。
- [x] **#2 transcript 永続化 + H1 再試行ガード** —
  - 生成前に `state/recovery/{source_label}.json` へ transcript を保存し、議事録生成成功で削除。失敗時はログに保全先を出力（best-effort、パイプラインを止めない）。テスト: `TestTranscriptPersistence`。
  - `state_store.mark_processing` を「error 状態は `max_attempts`(=3) 回まで再請求可」に変更。新規 `is_claimed()` を `drive_watcher` フィルタと `/minutes process` 事前チェックに配線。テスト: `TestRetryAfterFailure`。
- [x] **#3 SQLite check_same_thread + Lock** — `MinutesArchive` を `check_same_thread=False` + `threading.Lock` 化。`/minutes search` のクロススレッドクラッシュを解消。テスト: `TestCrossThreadAccess`。
- [x] **S1 .gitignore + chmod 600** — `token.json`/`oauth_client.json`/`.oauth_state.json`/`output/`/`recovered/`/`state/`/`*.bak` を ignore、資格情報を `chmod 600`。
- [x] **S2 /minutes process 権限 + allowlist + defer** — `@has_permissions(manage_guild=True)` + エラーハンドラ、`defer()`+`followup`、`is_allowed_craig_domain`（craig.horse/craig.chat）、`outputFileName` を `_is_safe_output_filename` で検証。テスト: `test_extract_recording_info_rejects_disallowed_domain`, `TestSafeOutputFilename`。

### 既知の残課題（フォローアップ候補）
- テスト環境: conda `junzi` は `pytest 9`（pin は `<9`）で `pytest-asyncio`/`aioresponses` 未導入 → async テストが流れない。`requirements-dev.txt` 準拠の環境整備が必要（L2）。
- #2 の自動レジューム（保存済み transcript から再生成して再文字起こしを省く）は未実装。現状は再試行で再文字起こしが走る（max 3 回で打ち切り）。
- H3/H5（長尺の議事録切れ・Embed超過）、H6（ZIP展開のループブロック）等は未対応。
