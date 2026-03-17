# Research Report: カレンダー連携 (Calendar Integration)

**Feature Slug**: calendar-integration
**Date**: 2026-03-17
**Recommendation**: CONDITIONAL GO
**Confidence**: 70% (Medium)

---

## Executive Summary

**Recommendation: CONDITIONAL GO** -- Proceed only after validating Google Calendar Service Account access and defining event-to-recording matching heuristics.

Google Calendarと連携し、録音時間帯のイベント情報（会議名・参加者・アジェンダ）を議事録に自動付加する機能は、既存アーキテクチャとの親和性が高い。Google API クライアントライブラリ (`google-api-python-client 2.189.0`) は既にインストール済みで、`drive_watcher.py` の Service Account 認証パターンがそのまま再利用できる。パイプラインへの統合は Stage 4 (generation) の前に新しいカレンダー情報取得ステップを挿入し、テンプレート変数として `{event_title}`, `{event_attendees}`, `{event_description}` を追加する形が自然である。しかし、**録音時間帯とカレンダーイベントのマッチング精度**、**Service Account がユーザーの Google Calendar にアクセスするための組織的な権限設定**、**録音開始/終了時刻の取得方法が現在のパイプラインで未実装**という3つの課題がある。これらの条件を検証した上で実装に進むことを推奨する。

---

## Recommendation

- **Decision**: CONDITIONAL GO
- **Confidence**: Medium (70%)
- **Rationale**: 技術的にはフル実装可能であり、既存パターンの再利用により工数は中程度。ただしカレンダー連携の有用性は「Service Account がターゲットカレンダーにアクセスできるか」という運用条件と「録音時間帯の正確な取得」という技術条件に依存する。これらの検証なしに本実装に進むのはリスクが高い。

---

## 1. Feature Overview

| 項目 | 値 |
|------|-----|
| **Feature Name** | カレンダー連携 (Calendar Integration) |
| **Type** | Enhancement (パイプラインへの情報付加レイヤー追加) |
| **Target Components** | `src/calendar_client.py` (new), `src/pipeline.py`, `src/generator.py`, `src/config.py`, `config.yaml` |
| **Complexity** | Medium-Complex (Size L) |
| **Traceability** | R-80 |
| **Implementation Order** | Ext-6 (今後の拡張候補) |
| **Phase** | スコープ外 -- 優先度は低い |

### Goals

1. Google Calendar API から録音時間帯のイベントを自動取得する
2. イベント情報（タイトル、参加者、説明）を議事録テンプレート変数として利用可能にする
3. カレンダーにイベントがない場合は従来通りの議事録を生成する（graceful degradation）
4. config.yaml でカレンダー連携の有効/無効を設定可能にする

---

## 2. Requirements Summary

### Must-Have [R-80]

1. **Google Calendar API 連携**: 録音時間帯のイベントを `Events.list` で取得
2. **テンプレート変数追加**: `{event_title}`, `{event_attendees}`, `{event_description}` を generator に渡す
3. **設定制御**: `config.yaml` の `calendar:` セクションで有効/無効を切り替え

### Nice-to-Have [R-80]

4. 議事録生成後にカレンダーイベントに議事録リンクを追記（write access 必要）
5. 定期会議の自動検出と議事録の連続管理
6. 複数カレンダーの監視

### Non-Functional

- カレンダー API 失敗時もパイプライン処理を継続（graceful degradation）
- Service Account に Calendar API read-only スコープのみ付与
- カレンダー API コールは最大3回リトライ
- 透過的な動作（ユーザーの追加操作不要）

---

## 3. Product Analysis

### User Value: **Medium-Low**

| 観点 | 評価 |
|------|------|
| **課題の深刻度** | 低。議事録の品質は主にトランスクリプトの質と LLM の生成力に依存。カレンダー情報は supplemental |
| **影響範囲** | Google Workspace を使用している組織のみ。個人 Discord サーバーや非 Google ユーザーには恩恵なし |
| **ユーザー体験** | 議事録にイベント名が表示されることで「どの会議か」の特定が容易になるが、手動入力でも代替可能 |
| **設定負担** | Service Account の Calendar API スコープ追加 + カレンダーの共有設定が必要。ゼロコンフィグではない |
| **即効性** | 低。Service Account のカレンダーアクセス権限設定が前提条件 |

### User Personas

- **Primary**: Google Workspace を使用する組織の管理者。定例会議が Google Calendar に登録されている
- **Secondary**: 個人の Discord サーバーオーナー。Google Calendar で会議を管理している
- **Not applicable**: Google Calendar を使用していないユーザー、カジュアルな音声チャット利用者

### Strategic Alignment: **Partial**

| 設計原則 | 適合性 | 根拠 |
|----------|--------|------|
| Pipeline-first | ✅ | パイプラインの generation ステージ前に情報を付加する形で統合可能 |
| Async by default | ✅ | Google Calendar API 呼び出しは `asyncio.to_thread` でラップ可能 |
| Graceful degradation | ✅ | API 失敗時は空の変数で続行。コア機能に影響なし |
| Multi-guild support | ⚠️ | ギルドごとに異なるカレンダーを設定する必要があるが、REQUEST.md では未考慮 |
| Minimal state | ✅ | カレンダー情報はステートレスに取得（キャッシュ不要） |

### Product Viability Score: **5/10 -- MODERATE**

カレンダー連携は「あると便利」だが、コア価値（音声 -> 議事録）の向上には間接的にしか寄与しない。Google Workspace 依存が利用可能ユーザーの幅を狭める。REQUEST.md 自身が「スコープ外、今後の拡張候補」と位置づけている。

### Concerns

1. **Google Workspace 依存**: Service Account が個人の Google Calendar にアクセスするには、ドメイン全体委任 (Domain-Wide Delegation) またはカレンダーの明示的共有が必要。個人 Gmail アカウントのカレンダーには Service Account からアクセスできない場合がある
2. **イベントマッチングの曖昧さ**: 「録音時間帯のイベント」の定義が曖昧。同時間帯に複数イベントがある場合のロジック、録音開始/終了時刻の誤差、タイムゾーン問題がある
3. **限定的な利用シーン**: Discord の音声チャンネルを使う会議が Google Calendar に登録されているケースは限定的

---

## 4. Technical Discovery

### Current State: 0% Implemented

カレンダー連携に関するコードは一切存在しない。ただし、以下の既存インフラが再利用可能。

### Reusable Infrastructure

#### 1. Google API クライアント認証パターン (`src/drive_watcher.py`)

`drive_watcher.py` の `_build_service()` メソッドが Service Account 認証の実装パターンを確立している:

```python
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

credentials = Credentials.from_service_account_file(
    str(creds_path), scopes=_SCOPES
)
service = build("drive", "v3", credentials=credentials)
```

Calendar API でも同一パターンが使える:

```python
service = build("calendar", "v3", credentials=credentials)
```

ただし、**スコープが異なる**: Drive は `drive.readonly` だが、Calendar は `calendar.readonly` (`https://www.googleapis.com/auth/calendar.readonly`)。同じ `credentials.json` を使う場合、**両方のスコープを含む Credentials オブジェクトを構築する必要がある**。

#### 2. Config データクラスパターン (`src/config.py`)

`GoogleDriveConfig` や `SpeakerAnalyticsConfig` と同様のパターンで `CalendarConfig` を追加できる:

- `@dataclass(frozen=True)` で immutable 設定を定義
- `_SECTION_CLASSES` に登録するだけで YAML ローダーが自動処理
- 環境変数オーバーライド (`CALENDAR_ENABLED`, `CALENDAR_CALENDAR_ID` 等) が自動的に利用可能

#### 3. テンプレート変数システム (`src/generator.py`)

`render_prompt()` メソッドが単純な文字列置換でテンプレート変数を展開する:

```python
replacements = {
    "{transcript}": transcript,
    "{date}": date,
    "{speakers}": speakers,
    "{guild_name}": guild_name,
    "{channel_name}": channel_name,
}
```

ここに `{event_title}`, `{event_attendees}`, `{event_description}` を追加するだけで良い。**ただし**、`generate()` と `render_prompt()` の関数シグネチャに新パラメータを追加する必要がある。

#### 4. エラー階層 (`src/errors.py`)

`CalendarError(MinutesBotError)` を追加するパターンが確立済み。

### Integration Points

```
src/pipeline.py (run_pipeline_from_tracks)
  |
  | NEW: Stage 3.5 -- Calendar info fetch (between merge and generate)
  v
src/calendar_client.py (new) -- Google Calendar API query
  |
  v
src/generator.py (render_prompt) -- Template variable injection
  |
  v
src/poster.py (build_minutes_embed) -- Event title in embed (optional)
```

### Critical Technical Gaps

#### Gap 1: 録音開始/終了時刻の取得

**現在のパイプラインには録音の開始/終了時刻が伝播されていない。**

- `DetectedRecording` dataclass は `rec_id`, `access_key`, `rec_url` のみを持ち、タイムスタンプを含まない
- `run_pipeline_from_tracks()` は `datetime.now()` を `date_str` として使用するが、これは **パイプライン実行時の時刻** であり、**録音時の時刻ではない**
- Craig Bot の録音メタデータ（開始/終了時刻）を取得する API は現在実装されていない
- Drive watcher 経由の場合、ZIP ファイルのメタデータからタイムスタンプを取得できる可能性があるが、未検証

**影響**: カレンダーイベントの時間帯マッチングが不正確になる。「現在時刻 - N分」のヒューリスティックを使うか、Craig API / ZIP メタデータからの時刻取得を別途実装する必要がある。

#### Gap 2: Service Account のカレンダーアクセス権限

Google Calendar API で他人のカレンダーを読み取るには:

| シナリオ | 方法 | 複雑度 |
|----------|------|--------|
| Google Workspace 組織内 | ドメイン全体委任 (DWD) | High -- Google Admin Console での設定が必要 |
| 個人カレンダーの共有 | カレンダーの共有設定で Service Account のメールアドレスを追加 | Medium -- ユーザーに手動設定を依頼 |
| 公開カレンダー | `calendarId: "public_calendar_id"` で直接アクセス | Low -- ただし公開カレンダーを使う組織は少ない |
| OAuth2 ユーザー認証 | ユーザーの同意を得てアクセストークンを取得 | Very High -- Discord Bot のコンテキストでは実用的でない |

**影響**: Service Account ベースのアクセスは Google Workspace 組織以外では設定が煩雑。ドキュメントによる明確なセットアップガイドが必須。

#### Gap 3: イベントマッチングロジック

録音時間帯に複数のカレンダーイベントがある場合のマッチング戦略が未定義:

- **方法A**: 時間帯が最も重複するイベントを選択
- **方法B**: 最初にヒットしたイベントを使用
- **方法C**: 全イベントをリストとして渡す
- **方法D**: Discord 音声チャンネル名とイベントタイトルの類似度でマッチング

REQUEST.md ではこの問題を「複数の音声チャンネルで同時間帯に録音がある場合のイベントマッチングロジックが必要」として言及しているが、解決策は提案していない。

#### Gap 4: マルチギルド対応

現在の REQUEST.md は単一カレンダー ID を前提としているが、`Config` はマルチギルド対応。ギルドごとに異なるカレンダー ID を設定する必要がある可能性がある。`GuildConfig` へのフィールド追加、または `guild_settings.json` での管理が必要。

### Dependency Check

| 依存 | ステータス | 備考 |
|------|-----------|------|
| `google-api-python-client` | ✅ Installed (2.189.0) | Calendar API も同じライブラリで利用可能 |
| `google-auth` | ✅ Installed (2.48.0) | Service Account 認証に使用 |
| `credentials.json` | ✅ Existing | Drive watcher 用に既に存在。Calendar スコープ追加が必要 |
| 新規 pip パッケージ | 不要 | |

---

## 5. Technical Analysis

### Feasibility Score: **7/10**

技術的には実現可能だが、Gap 1 (録音時刻) と Gap 2 (Service Account 権限) の解決が前提条件。

### Recommended Implementation Approach

#### Phase 0: Validation (推定 2-4 時間)

1. Service Account で Google Calendar API にアクセスできることを手動検証
2. Craig recording のメタデータから開始/終了時刻を取得する方法を調査
3. `Events.list` の `timeMin`/`timeMax` パラメータで期待通りのイベントが返るか検証

#### Phase 1: Core Implementation (推定 8-12 時間)

新規モジュール構成:

| ファイル | 内容 | 推定行数 |
|----------|------|----------|
| `src/calendar_client.py` | Google Calendar API クライアント。`CalendarClient` クラスで `fetch_event()` メソッドを提供 | ~120 |
| `src/config.py` | `CalendarConfig` dataclass 追加 (`enabled`, `credentials_path`, `calendar_id`, `max_retries`) | ~15 |
| `src/pipeline.py` | `_stage_calendar_fetch()` 追加。generation の前にカレンダー情報を取得 | ~25 |
| `src/generator.py` | `render_prompt()` と `generate()` にカレンダー変数パラメータ追加 | ~15 |
| `src/poster.py` | Embed にイベント名フィールド追加 (optional) | ~10 |
| `src/errors.py` | `CalendarError` 追加 | ~5 |
| `config.yaml` | `calendar:` セクション追加 | ~10 |
| `prompts/minutes.txt` | テンプレートにカレンダー変数追加 | ~5 |
| `tests/test_calendar_client.py` | ユニットテスト (API モック含む) | ~150 |
| `tests/test_generator.py` | テンプレート変数テスト追加 | ~20 |
| `tests/test_pipeline.py` | パイプライン統合テスト追加 | ~30 |
| **合計** | | **~405** |

#### `CalendarClient` 設計概要

```python
@dataclass(frozen=True)
class CalendarEvent:
    title: str
    attendees: list[str]
    description: str
    start: datetime
    end: datetime

class CalendarClient:
    def __init__(self, cfg: CalendarConfig) -> None: ...
    def _build_service(self) -> Any: ...  # drive_watcher pattern
    def fetch_event(
        self, time_min: datetime, time_max: datetime
    ) -> CalendarEvent | None: ...
```

`fetch_event` は `Events.list` を呼び出し、`timeMin`/`timeMax` 範囲のイベントを検索。複数ヒット時は最も時間重複が大きいイベントを返す。ヒットなしは `None`。

#### Pipeline Integration

```python
# In run_pipeline_from_tracks(), between merge and generate:
event: CalendarEvent | None = None
if cfg.calendar.enabled:
    try:
        event = await _stage_calendar_fetch(cfg.calendar, recording_start, recording_end)
    except CalendarError:
        logger.warning("Calendar fetch failed, continuing without event info")
        event = None

# Pass to generator:
minutes_md = await generator.generate(
    transcript=transcript,
    date=date_str,
    speakers=speakers_str,
    guild_name=guild_name,
    channel_name=output_channel.name,
    template_name=template_name,
    event_title=event.title if event else "",
    event_attendees=", ".join(event.attendees) if event else "",
    event_description=event.description if event else "",
)
```

### Complexity: **Medium-Complex (Size L)**

| 要因 | 複雑度 | 根拠 |
|------|--------|------|
| API 統合 | Medium | 既存パターン再利用可能だが、Calendar API 固有のレスポンス構造の処理が必要 |
| 時刻マッチング | High | 録音時刻の取得方法未確定。タイムゾーン処理が必要 |
| 設定 | Low | 既存 Config パターンの踏襲 |
| テンプレート統合 | Low | 文字列置換の追加のみ |
| テスト | Medium | API モック + 時間帯マッチングのエッジケースカバレッジ |
| 運用設定 | High | Service Account 権限設定のドキュメント整備が必要 |

### Technical Risks

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Service Account がカレンダーにアクセスできない | High (機能が利用不能) | Medium | Phase 0 で検証。セットアップガイドを整備 |
| 録音開始/終了時刻が取得できない | High (マッチング不能) | Medium | 代替案: (a) パイプライン開始時刻から逆算、(b) Craig API からメタデータ取得、(c) Whisper の最初/最後のセグメント時刻を利用 |
| タイムゾーン不一致 | Medium (誤マッチ) | Medium | UTC ベースで統一。config でタイムゾーン設定を追加 |
| 複数イベント競合 | Low (minor UX issue) | Medium | 最長重複イベントを選択するヒューリスティック |
| API レート制限 | Low (Google Calendar API quota: 1M queries/day) | Very Low | リトライ + exponential backoff |
| Credentials.json のスコープ変更 | Medium (既存 Drive watcher に影響) | Low | 単一 Credentials に複数スコープを含める。互換性を事前検証 |

### Alternatives Considered

| Approach | Assessment | Recommended |
|----------|-----------|-------------|
| Google Calendar API (Service Account) | 既存認証インフラ再利用。read-only で安全 | ✅ Primary |
| Google Calendar API (OAuth2 user flow) | ユーザー認証が必要。Discord Bot コンテキストでは煩雑 | ❌ |
| Microsoft Graph API (Outlook Calendar) | Google 依存を解消するが、追加の認証インフラが必要 | ❌ Future |
| ICS ファイル読み込み | ローカルファイルベース。API 不要だが手動エクスポートが必要 | ❌ |
| Discord Events 連携 | Discord サーバーイベントから会議情報を取得。Google 依存なし | ⚠️ Alternative (要検討) |

**注目**: Discord サーバーイベント (`ScheduledEvent`) は Google Calendar 依存なしで会議情報を取得できる可能性がある。discord.py 2.3+ でサポートされており、Discord ネイティブな方法として検討価値がある。ただし REQUEST.md は明示的に Google Calendar を指定している。

---

## 6. Implementation Estimate

### Effort Breakdown

| Phase | Effort | Description |
|-------|--------|-------------|
| Phase 0: Validation | 2-4h | Service Account 検証、Craig メタデータ調査、API テスト |
| Phase 1: Core | 8-12h | `calendar_client.py`, config, pipeline integration, template |
| Phase 2: Testing | 4-6h | Unit tests, integration tests, edge cases |
| Phase 3: Documentation | 2-3h | セットアップガイド、config.yaml コメント |
| **Total** | **16-25h** | |

### Dependencies

| 依存 | Type | Status |
|------|------|--------|
| `google-api-python-client` | External library | ✅ Already installed |
| `credentials.json` (Calendar scope) | Configuration | ⚠️ Scope addition needed |
| 録音時刻の取得方法 | Internal prerequisite | ❌ Not implemented |
| テンプレートカスタマイズ機能 | Feature dependency | ✅ Already merged (template system exists) |

---

## 7. Risks and Mitigations

### High-Priority Risks

| # | Risk | Severity | Mitigation Strategy |
|---|------|----------|---------------------|
| R1 | Service Account がターゲットカレンダーにアクセスできない | High | Phase 0 で実際の credentials.json を使って検証。Google Workspace DWD の設定手順をドキュメント化。個人カレンダーの場合はカレンダー共有設定の手順を提供 |
| R2 | 録音の開始/終了時刻が不明 | High | 3段階のフォールバック: (1) Craig API metadata, (2) Whisper segments の min(start)/max(end), (3) pipeline 開始時刻 - 推定会議時間 |
| R3 | credentials.json のスコープ変更が Drive watcher に影響 | Medium | 単一 Credentials オブジェクトに `drive.readonly` + `calendar.readonly` の両スコープを含める。テストで後方互換性を検証 |

### Low-Priority Risks

| # | Risk | Severity | Mitigation Strategy |
|---|------|----------|---------------------|
| R4 | タイムゾーン不一致 | Medium | config.yaml に `timezone` フィールド追加。デフォルト `Asia/Tokyo`。Google Calendar API は RFC3339 タイムスタンプで応答するため、適切にパース |
| R5 | 複数カレンダーイベントの競合 | Low | 最長時間重複イベントを優先。tie-break は作成日時が新しいものを選択 |
| R6 | カレンダー API のレスポンス遅延 | Low | 5秒タイムアウト。失敗時は graceful degradation で空の変数を渡す |

---

## 8. Strategic Assessment

### Decision: **CONDITIONAL GO**

### Confidence: **70% (Medium)**

### Rationale

| Factor | Assessment |
|--------|-----------|
| **Technical feasibility** | 7/10 -- 実装可能だが、録音時刻取得と Service Account 権限の未検証部分あり |
| **Product value** | 5/10 -- Supplemental。コア価値への寄与は間接的。Google Workspace 依存で利用者が限定 |
| **Implementation effort** | Medium-Large (16-25h)。新規モジュール + API 統合 + テスト + ドキュメント |
| **Risk profile** | Medium -- 2つの High-severity リスクが Phase 0 検証で解消可能 |
| **Strategic alignment** | Partial -- Pipeline-first, graceful degradation に適合するが、Minimal state / Multi-guild の考慮が不足 |
| **Priority** | Low -- REQUEST.md 自身が「スコープ外、Ext-6」と位置づけ |
| **Dependency on external setup** | High -- Service Account 権限設定が必須。ゼロコンフィグでは動作しない |

### Why CONDITIONAL (not full GO)

1. **High-severity リスクが未検証**: Service Account のカレンダーアクセスと録音時刻の取得が実際に動作するか検証されていない
2. **Product value が限定的**: 全ユーザーに恩恵があるわけではなく、Google Workspace 利用者に限定される
3. **Priority が低い**: 企画書でスコープ外と位置づけられており、他の機能 (multilingual-support, template-customization 等) の方が優先度が高い可能性

### Why not NO-GO or DEFER

1. **技術的障壁がない**: 既存のインフラ (Google API client, Service Account, Config pattern) が再利用可能
2. **Graceful degradation が自然**: カレンダー情報は additive であり、取得失敗がパイプラインを壊さない
3. **段階的実装が可能**: Phase 0 の検証結果次第で、実装範囲を調整できる

---

## 9. Conditions for Proceeding

### Phase 0 Validation Checklist (CONDITIONAL の解除条件)

| # | Condition | Method | Expected Result |
|---|-----------|--------|-----------------|
| C1 | Service Account で Google Calendar API にアクセスできることを検証 | 既存の `credentials.json` に Calendar scope を追加し、`Events.list` を手動実行 | 200 OK + イベントリスト取得 |
| C2 | 録音の開始/終了時刻を取得する方法を確定 | Craig API metadata / ZIP file metadata / Whisper segment timestamps を調査 | 少なくとも1つの方法で +-5分以内の精度で時刻取得 |
| C3 | credentials.json のスコープ変更が Drive watcher に影響しないことを確認 | 両スコープを含む Credentials で Drive API, Calendar API の両方が動作することをテスト | 既存 Drive watcher が正常動作 |
| C4 | ターゲットユーザーが Service Account にカレンダーアクセスを許可する手順が現実的であることを確認 | Google Workspace DWD 設定 or 個人カレンダー共有の手順をテスト | 15分以内で設定完了可能 |

全4条件がクリアされた場合、Phase 1 (Core Implementation) に進む。
1つでもクリアできない場合は **DEFER** に切り替え、代替アプローチ (Discord Events 連携等) を検討。

---

## 10. Next Steps

Based on the CONDITIONAL GO recommendation:

### Immediate (Phase 0)

1. **Validate C1**: `credentials.json` に `https://www.googleapis.com/auth/calendar.readonly` スコープを追加し、テストスクリプトで Calendar API にアクセス
2. **Validate C2**: Craig recording の ZIP ファイルを調べ、メタデータ (ファイル名、タイムスタンプ、info.json 等) から録音時刻を取得できるか検証。代替として Whisper segment の最初/最後のタイムスタンプを使用
3. **Validate C3**: Drive watcher が新しいスコープ付き credentials で正常動作することをテスト
4. **Validate C4**: Google Calendar の共有設定で Service Account メールアドレスを追加し、イベントが取得できることを確認

### If All Conditions Met (Phase 1)

5. **Plan**: `/rpi:plan calendar-integration` で詳細実装計画を作成
6. **Implement**: Phase 1 (Core), Phase 2 (Testing), Phase 3 (Documentation) を順次実行
7. **PR**: 実装完了後に PR を作成しレビュー

### If Conditions Not Met

8. **DEFER**: 代替アプローチを検討
   - **Option A**: Discord サーバーイベント (`ScheduledEvent`) から会議情報を取得 (Google 依存なし)
   - **Option B**: `/minutes process` コマンドにオプショナルな `--event-name` パラメータを追加 (手動入力)
   - **Option C**: 議事録テンプレートの `{channel_name}` を活用し、チャンネル名に会議名を含める運用で代替

---

## Appendix A: Google Calendar API Reference

### Events.list Parameters

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `calendarId` | config.yaml で設定 | 対象カレンダー |
| `timeMin` | 録音開始時刻 (RFC3339) | 検索範囲の開始 |
| `timeMax` | 録音終了時刻 (RFC3339) | 検索範囲の終了 |
| `singleEvents` | `true` | 定期イベントを個別展開 |
| `orderBy` | `startTime` | 開始時刻順 |
| `maxResults` | `5` | 取得件数上限 |

### API Quota

- Google Calendar API: 1,000,000 queries/day (free tier)
- 1回の議事録生成で 1 API call -> 実質無制限

## Appendix B: Code References

| File | Relevant Code | Purpose |
|------|---------------|---------|
| `src/drive_watcher.py:101-128` | `_build_service()` -- Google API Service Account authentication | Pattern to reuse |
| `src/config.py:127-133` | `GoogleDriveConfig` -- Frozen dataclass with `enabled`, `credentials_path` | Pattern to follow |
| `src/config.py:166-177` | `_SECTION_CLASSES` -- YAML section registration | Where to add `CalendarConfig` |
| `src/generator.py:124-151` | `render_prompt()` -- Template variable replacement | Where to add calendar variables |
| `src/generator.py:153-245` | `generate()` -- Claude API call with template | Signature to extend |
| `src/pipeline.py:99-126` | Stage 4: Generate minutes | Where to insert calendar fetch |
| `src/detector.py:23-31` | `DetectedRecording` -- No timestamp fields | Gap 1 root cause |
| `src/errors.py` | Error hierarchy | Where to add `CalendarError` |
| `prompts/minutes.txt:7-11` | Template header with `{date}`, `{speakers}` | Where to add `{event_title}` etc. |

## Appendix C: Comparison with Other RPI Features

| Feature | Priority | Effort | Value | Status |
|---------|----------|--------|-------|--------|
| speaker-analytics (Ext-3) | High | Small | Medium-High | ✅ Implemented |
| template-customization (Ext-4) | High | Medium | High | ✅ Implemented |
| minutes-search (Ext-5) | Medium | Medium | Medium | ✅ Implemented |
| **calendar-integration (Ext-6)** | **Low** | **Medium-Large** | **Medium-Low** | **CONDITIONAL GO** |
| multilingual-support (Ext-7) | Medium | Large | High | Pending |
| external-export (Ext-8) | Low | Medium | Medium | Pending |

カレンダー連携は Ext-6 の位置づけであり、multilingual-support (Ext-7) の方が ROI が高い可能性がある。実装順序の再評価を推奨。
