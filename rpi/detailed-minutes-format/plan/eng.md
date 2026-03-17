# Engineering Plan: 詳細議事録フォーマット + トランスクリプト添付

## Phase 1: プロンプトテンプレート書き換え

**File**: `prompts/minutes.txt`

現在の6セクション構成をGeminiメモ風3セクション構成に全面書き換え:
- `## まとめ` — 概要 + トピック別ハイライト（太字タイトル + 短い説明）
- `## 詳細` — トピック別箇条書き。各項目に `([MM:SS])` タイムスタンプ参照、太字の要点 + 詳細説明
- `## 推奨される次のステップ` — `- [ ]` チェックリスト形式（担当者明記）

出力ルール追加:
- 詳細セクションでは長い会議なら30+項目
- タイムスタンプはトランスクリプトの `[MM:SS]` 形式を参照

## Phase 2: トランスクリプト整形関数

**File**: `src/merger.py` に `format_transcript_markdown()` 追加

```python
def format_transcript_markdown(
    transcript: str,
    date: str,
    speakers: str,
) -> str:
```

処理:
1. 既存の `[MM:SS] speaker: text` 形式のトランスクリプトをパース
2. 一定間隔（configurable、初期値180秒）ごとに `### HH:MM:SS` セクション区切り挿入
3. `**speaker:** text` 形式に変換
4. ヘッダー（タイトル、日時、参加者）を付加

## Phase 3: poster.py 変更

### 3a: `build_transcript_file()` 新関数

```python
def build_transcript_file(transcript_md: str, date: str) -> discord.File:
```

`build_minutes_file()` と同様のパターン。ファイル名: `transcript_YYYY-MM-DD_HHMM.md`

### 3b: `post_minutes()` シグネチャ変更

```python
async def post_minutes(
    channel: OutputChannel,
    minutes_md: str,
    date: str,
    speakers: str,
    cfg: PosterConfig,
    speaker_stats: str | None = None,
    transcript_md: str | None = None,  # NEW
) -> discord.Message:
```

ロジック変更:
- `transcript_md` が `None` でない場合、トランスクリプトファイルも生成
- **ForumChannel**: `thread.send(files=[minutes_file, transcript_file])`
- **TextChannel**: `channel.send(embed=embed, files=[minutes_file, transcript_file])`
- `transcript_md` が `None` の場合は現行動作（後方互換）

### 3c: Embed正規表現更新

```python
_SUMMARY_PATTERN = re.compile(r"## まとめ\s*\n(.*?)(?=\n## |\Z)", re.DOTALL)
_DECISIONS_PATTERN = re.compile(r"## 推奨される次のステップ\s*\n(.*?)(?=\n## |\Z)", re.DOTALL)
```

`build_minutes_embed()` 内のフィールド名も更新:
- `"要約"` → `"まとめ"`
- `"決定事項"` → `"次のステップ"`

## Phase 4: pipeline.py 変更

**File**: `src/pipeline.py` — `run_pipeline_from_tracks()`

Line 93付近（merge後）:
```python
transcript = merge_transcripts(segments, cfg.merger)

# NEW: トランスクリプトMarkdown整形
transcript_md: str | None = None
if cfg.poster.include_transcript:
    from src.merger import format_transcript_markdown
    transcript_md = format_transcript_markdown(transcript, date_str, speakers_str)
```

Line 129-136（post_minutes呼び出し）:
```python
message = await post_minutes(
    channel=output_channel,
    minutes_md=minutes_md,
    date=date_str,
    speakers=speakers_str,
    cfg=cfg.poster,
    speaker_stats=speaker_stats_text,
    transcript_md=transcript_md,  # NEW
)
```

## Phase 5: config.yaml 更新

```yaml
generator:
  max_tokens: 8192  # was 4096

poster:
  include_transcript: true  # was false
```

## Phase 6: テスト更新

### 更新が必要な既存テスト
- `test_poster.py::TestExtractSection` — 新正規表現パターンに合わせて `_SAMPLE_MINUTES` 更新
- `test_poster.py::TestBuildMinutesEmbed` — フィールド名変更（「まとめ」「次のステップ」）
- Forum/TextChannel テスト — `files` パラメータの検証追加

### 新規テスト
1. `test_merger.py::TestFormatTranscriptMarkdown` — 整形関数の単体テスト（3-4件）
2. `test_poster.py::TestBuildTranscriptFile` — ファイル生成テスト（2件）
3. `test_poster.py::TestPostMinutesWithTranscript` — transcript_md ありの投稿テスト（3件）
4. `test_pipeline.py` — transcript がpost_minutesに渡されることの検証（1件）

## Dependency Graph

```
Phase 1 (prompt)     — 独立
Phase 2 (merger)     — 独立
Phase 3a (transcript file) — Phase 2 に依存
Phase 3b (post_minutes)    — Phase 3a に依存
Phase 3c (embed regex)     — Phase 1 に依存（新セクション名を知る必要）
Phase 4 (pipeline)   — Phase 2, 3b に依存
Phase 5 (config)     — 独立
Phase 6 (tests)      — 全フェーズに依存
```

## Risk Mitigation

- `transcript_md=None` デフォルトで後方互換維持
- Embed正規表現はフォールバック空文字列なので、マッチ失敗しても投稿は壊れない
- max_tokens 増加はAPI料金増（~2x）だが、詳細出力に必須
