# Technical Specification: transcript-glossary

## 1. Architecture Overview

Whisperの文字起こしで頻出する固有名詞・専門用語の誤認識を、ギルド単位のユーザー定義辞書で自動補正する機能。パイプラインのStage 2（Transcribe）とStage 3（Merge）の間に辞書置換ステージを挿入する。

### 変更サマリ

| ファイル | 種別 | 変更量（概算） | 説明 |
|---------|------|------:|-------------|
| `src/glossary.py` | 新規 | ~50行 | `apply_glossary()` 置換ロジック |
| `src/config.py` | 変更 | +15行 | `TranscriptGlossaryConfig` データクラス + `_SECTION_CLASSES` 登録 |
| `src/state_store.py` | 変更 | +30行 | `get_guild_glossary()` / `set_guild_glossary()` 永続化 |
| `src/pipeline.py` | 変更 | +8行 | transcribe後の辞書適用挿入 |
| `bot.py` | 変更 | +60行 | `/minutes glossary add/remove/list` コマンド |
| `config.yaml` | 変更 | +5行 | `transcript_glossary:` セクション追加 |
| `tests/test_glossary.py` | 新規 | ~100行 | 辞書置換ロジックの単体テスト |
| `tests/test_state_store.py` | 変更 | +30行 | 辞書永続化のテスト |
| `tests/test_pipeline.py` | 変更 | +25行 | パイプライン統合テスト（有効/無効） |

### パイプライン配置

```
Stage 2: Transcribe         Stage 2.5: Glossary         Stage 3: Merge
transcriber.transcribe_all  apply_glossary()            merge_transcripts()
  -> list[Segment]            -> list[Segment]            -> str
       |                           |                           |
       |  raw segments             |  corrected segments       |  formatted transcript
       +---------------------------+---------------------------+-->
```

speaker_analytics（既存）と同じ位置（transcribe後・merge前）に挿入する。実行順序は glossary -> speaker_analytics の順とする。理由: speaker_analyticsは `Segment.text` の内容に依存しないため（`len(text)` のみ参照）、順序による影響はない。ただしglossaryを先に適用することで、将来speaker_analyticsがテキスト内容を参照する拡張をしても正しいテキストで計算される。

## 2. Component Design

### 2.1 `src/glossary.py`（新規）

辞書適用の純粋関数モジュール。外部依存なし、ステートレス。

```python
"""Transcript glossary: apply term replacements to transcription segments."""

from __future__ import annotations

import re

from src.transcriber import Segment


def apply_glossary(
    segments: list[Segment],
    glossary: dict[str, str],
    case_sensitive: bool = False,
) -> list[Segment]:
    """Apply glossary replacements to segment text.

    For each segment, all glossary entries are applied sequentially.
    Segment is a frozen dataclass, so new instances are created for
    any modified segments. Unmodified segments are returned as-is
    (identity preservation).

    Args:
        segments: Raw transcription segments from Whisper.
        glossary: Mapping of {wrong_text: correct_text}.
        case_sensitive: If False (default), matching ignores case.

    Returns:
        New list of Segment instances with glossary applied.
    """
    if not glossary:
        return segments

    if case_sensitive:
        return [_apply_case_sensitive(seg, glossary) for seg in segments]

    # Pre-compile regex patterns for case-insensitive mode
    compiled = [
        (re.compile(re.escape(pattern), re.IGNORECASE), replacement)
        for pattern, replacement in glossary.items()
    ]
    return [_apply_regex(seg, compiled) for seg in segments]


def _apply_case_sensitive(
    seg: Segment,
    glossary: dict[str, str],
) -> Segment:
    """Apply glossary using str.replace (case-sensitive)."""
    text = seg.text
    for pattern, replacement in glossary.items():
        text = text.replace(pattern, replacement)
    if text is seg.text:  # identity check: no change
        return seg
    return Segment(start=seg.start, end=seg.end, text=text, speaker=seg.speaker)


def _apply_regex(
    seg: Segment,
    compiled: list[tuple[re.Pattern, str]],
) -> Segment:
    """Apply glossary using pre-compiled regex patterns (case-insensitive)."""
    text = seg.text
    for pattern, replacement in compiled:
        text = pattern.sub(replacement, text)
    if text == seg.text:  # no change
        return seg
    return Segment(start=seg.start, end=seg.end, text=text, speaker=seg.speaker)
```

**設計判断**:

- **`re.escape()`**: 辞書パターンにregex特殊文字（`.` `(` `*` 等）が含まれても安全。ユーザーが `C++` や `node.js` を登録してもクラッシュしない。
- **identity preservation**: テキストが変更されないセグメントは元のオブジェクトをそのまま返す。frozen dataclassの新規インスタンス生成を最小化。
- **case-sensitive mode**: `str.replace()` を使用。regexオーバーヘッドを回避し、日本語テキストでも正確に動作。
- **case-insensitive mode**: `re.sub()` + `re.IGNORECASE` を使用。英語の固有名詞（`figma` -> `Figma`）で有効。
- **パターンのコンパイル**: case-insensitiveモードではregexパターンをループ外で1回だけコンパイルし、全セグメントで再利用。

### 2.2 `src/config.py`（変更）

既存パターン（`SpeakerAnalyticsConfig` 等）に準拠。

**追加するデータクラス**:

```python
@dataclass(frozen=True)
class TranscriptGlossaryConfig:
    enabled: bool = True
    case_sensitive: bool = False
```

**`Config` データクラスへのフィールド追加**:

```python
@dataclass(frozen=True)
class Config:
    # ... 既存フィールド ...
    calendar: CalendarConfig
    transcript_glossary: TranscriptGlossaryConfig  # <-- 追加
```

**`_SECTION_CLASSES` への登録**:

```python
_SECTION_CLASSES: dict[str, type] = {
    # ... 既存エントリ ...
    "calendar": CalendarConfig,
    "transcript_glossary": TranscriptGlossaryConfig,  # <-- 追加
}
```

環境変数オーバーライド: `TRANSCRIPT_GLOSSARY_ENABLED=false`, `TRANSCRIPT_GLOSSARY_CASE_SENSITIVE=true`

### 2.3 `src/state_store.py`（変更）

既存の `get_guild_template()` / `set_guild_template()` パターンに準拠。辞書データは `guild_settings.json` の各ギルドエントリ内に `"glossary"` キーで格納。

**追加するメソッド**:

```python
# ------------------------------------------------------------------
# Glossary methods
# ------------------------------------------------------------------

def get_guild_glossary(self, guild_id: int) -> dict[str, str]:
    """Return the glossary dict for a guild. Empty dict if not set."""
    settings = self._guild_settings.get(str(guild_id))
    if settings is None:
        return {}
    glossary = settings.get("glossary")
    if not isinstance(glossary, dict):
        return {}
    return dict(glossary)  # defensive copy

def set_guild_glossary(self, guild_id: int, glossary: dict[str, str]) -> None:
    """Overwrite the glossary for a guild."""
    key = str(guild_id)
    if key not in self._guild_settings:
        self._guild_settings[key] = {}
    self._guild_settings[key]["glossary"] = glossary
    self._flush_guild_settings()
```

**設計判断**:

- `get_guild_glossary()` は常に新しい `dict` を返す（defensive copy）。呼び出し元がdictを変更してもStateStoreの内部状態に影響しない。
- `set_guild_glossary()` は辞書全体を上書きする。個別エントリのadd/removeは `bot.py` のコマンドハンドラで辞書を取得→変更→書き戻しの3ステップで行う。
- SQLiteではなくJSON永続化を使用する理由: 辞書サイズは小さい（典型的に数十エントリ）、既存のguild_settings.jsonに収まる、新しい依存関係が不要。

### 2.4 `src/pipeline.py`（変更）

`run_pipeline_from_tracks()` 内、transcribe完了後・speaker_analytics前に辞書適用を挿入。

**変更箇所** (現在の行83付近、`segments = await _stage_transcribe(...)` の直後):

```python
# Stage 2: Transcribe (runs in thread to keep event loop free)
segments = await _stage_transcribe(transcriber, tracks)

# Stage 2.5: Glossary replacement (between transcribe and merge)
if cfg.transcript_glossary.enabled:
    guild_id = output_channel.guild.id
    glossary = state_store.get_guild_glossary(guild_id)
    if glossary:
        from src.glossary import apply_glossary
        segments = apply_glossary(
            segments, glossary, cfg.transcript_glossary.case_sensitive,
        )
        logger.info(
            "[glossary] Applied %d glossary entries to %d segments",
            len(glossary), len(segments),
        )

# Speaker analytics (between transcribe and merge)
speaker_stats_text: str | None = None
if cfg.speaker_analytics.enabled:
    # ... 既存コード ...
```

**設計判断**:

- **guild_idの取得**: `output_channel.guild.id` から取得。`run_pipeline_from_tracks()` は既にoutput_channelを受け取っており、関数シグネチャの変更は不要。既存コードも同様のパターンでguild_idを取得している（行185: `archive.store(guild_id=output_channel.guild.id, ...)`）。
- **条件付きインポート**: speaker_analyticsと同じパターン。`cfg.transcript_glossary.enabled` が `False` の場合、`src.glossary` モジュールはロードされない。
- **空辞書チェック**: `if glossary:` で空辞書の場合はスキップ。不要なSegment再生成を回避。

### 2.5 `bot.py`（変更）

既存の `register_commands()` 関数内に glossary サブグループを追加。

```python
# ---------------------------------------------------------------------------
# Glossary subcommands
# ---------------------------------------------------------------------------

glossary_group = discord.app_commands.Group(
    name="glossary",
    description="用語辞書の管理",
    parent=group,
)

@glossary_group.command(name="add", description="用語辞書にエントリを追加")
@discord.app_commands.checks.has_permissions(manage_guild=True)
@discord.app_commands.describe(
    wrong="誤認識テキスト（Whisperが出力する文字列）",
    correct="正しい表記",
)
async def glossary_add(
    interaction: discord.Interaction, wrong: str, correct: str,
) -> None:
    guild_id = interaction.guild_id or 0
    glossary = client.state_store.get_guild_glossary(guild_id)
    glossary[wrong] = correct
    client.state_store.set_guild_glossary(guild_id, glossary)
    await interaction.response.send_message(
        f"辞書に追加しました: `{wrong}` -> `{correct}`\n"
        f"現在の辞書エントリ数: {len(glossary)}",
        ephemeral=True,
    )

@glossary_group.command(name="remove", description="用語辞書からエントリを削除")
@discord.app_commands.checks.has_permissions(manage_guild=True)
@discord.app_commands.describe(wrong="削除する誤認識テキスト")
async def glossary_remove(
    interaction: discord.Interaction, wrong: str,
) -> None:
    guild_id = interaction.guild_id or 0
    glossary = client.state_store.get_guild_glossary(guild_id)
    if wrong not in glossary:
        await interaction.response.send_message(
            f"辞書に `{wrong}` は登録されていません。",
            ephemeral=True,
        )
        return
    del glossary[wrong]
    client.state_store.set_guild_glossary(guild_id, glossary)
    await interaction.response.send_message(
        f"辞書から削除しました: `{wrong}`\n"
        f"現在の辞書エントリ数: {len(glossary)}",
        ephemeral=True,
    )

@glossary_group.command(name="list", description="用語辞書の内容を表示")
async def glossary_list(interaction: discord.Interaction) -> None:
    guild_id = interaction.guild_id or 0
    glossary = client.state_store.get_guild_glossary(guild_id)
    if not glossary:
        await interaction.response.send_message(
            "用語辞書は空です。`/minutes glossary add` で追加してください。",
            ephemeral=True,
        )
        return
    embed = discord.Embed(title="用語辞書", color=0x5865F2)
    lines = [f"`{wrong}` -> `correct`" for wrong, correct in glossary.items()]
    # Discord embed description limit: 4096 chars
    description = "\n".join(lines)
    if len(description) > 4000:
        description = description[:4000] + "\n... (省略)"
    embed.description = description
    embed.set_footer(text=f"{len(glossary)} エントリ")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@glossary_add.error
@glossary_remove.error
async def glossary_permission_error(
    interaction: discord.Interaction,
    error: discord.app_commands.AppCommandError,
) -> None:
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message(
            "辞書の変更には「サーバー管理」権限が必要です。",
            ephemeral=True,
        )
    else:
        raise error

tree.add_command(group)  # 既存（変更なし）
```

**コマンド体系**:

| コマンド | 権限 | 説明 |
|---------|------|------|
| `/minutes glossary add <wrong> <correct>` | manage_guild | エントリ追加（既存キーは上書き） |
| `/minutes glossary remove <wrong>` | manage_guild | エントリ削除 |
| `/minutes glossary list` | なし | 辞書内容を表示 |

**設計判断**:

- `glossary list` は権限不要。辞書内容は秘密情報ではなく、全メンバーが確認できるべき。
- `add` は既存キーの上書きを許可（idempotent）。ユーザーに確認を求める必要はない。
- サブグループ（`glossary_group`）は `parent=group` で `minutes` グループの子として登録。Discord UIでは `/minutes glossary add` の形式で表示される。
- エラーハンドラは `add` と `remove` で共通化。

### 2.6 `config.yaml`（変更）

`speaker_analytics:` セクションの後に追加:

```yaml
transcript_glossary:
  # Enable automatic term replacement in transcription
  enabled: true
  # Case-sensitive matching (false = case-insensitive, recommended for mixed-language)
  case_sensitive: false
```

## 3. Data Flow

### 3.1 辞書登録フロー

```
User: /minutes glossary add "ツーニック" "TOONIQ"
  |
  v
bot.py: glossary_add()
  |  client.state_store.get_guild_glossary(guild_id)  -> {"figma": "Figma"}
  |  glossary["ツーニック"] = "TOONIQ"
  |  client.state_store.set_guild_glossary(guild_id, glossary)
  |
  v
state_store.py: set_guild_glossary()
  |  self._guild_settings["12345"]["glossary"] = {"figma": "Figma", "ツーニック": "TOONIQ"}
  |  self._flush_guild_settings()
  |
  v
state/guild_settings.json (atomic write via .tmp + os.replace)
  {
    "12345": {
      "template": "minutes",
      "glossary": {
        "figma": "Figma",
        "ツーニック": "TOONIQ"
      }
    }
  }
```

### 3.2 パイプライン適用フロー

```
pipeline.py: run_pipeline_from_tracks()
  |
  |  segments = await _stage_transcribe(transcriber, tracks)
  |  # segments[0].text = "ツーニックのfigmaデザインを確認"
  |
  v
  if cfg.transcript_glossary.enabled:
      glossary = state_store.get_guild_glossary(guild_id)
      # glossary = {"figma": "Figma", "ツーニック": "TOONIQ"}
      segments = apply_glossary(segments, glossary, case_sensitive=False)
      # segments[0].text = "TOONIQのFigmaデザインを確認"
  |
  v
  # Speaker analytics (optional, uses corrected segments)
  # Merge (uses corrected segments)
  transcript = merge_transcripts(segments, cfg.merger)
  |
  v
  # Generate minutes (LLM sees corrected text)
  minutes_md = await generator.generate(transcript=transcript, ...)
```

## 4. API Contracts

### 4.1 `apply_glossary()`

```python
def apply_glossary(
    segments: list[Segment],
    glossary: dict[str, str],
    case_sensitive: bool = False,
) -> list[Segment]
```

| 引数 | 型 | 説明 |
|-----|---|------|
| `segments` | `list[Segment]` | Whisperからの生セグメント |
| `glossary` | `dict[str, str]` | `{誤認識パターン: 正しい表記}` |
| `case_sensitive` | `bool` | `True`: `str.replace()`、`False`: `re.sub(re.IGNORECASE)` |

| 戻り値 | 説明 |
|-------|------|
| `list[Segment]` | 辞書適用済みセグメント。変更のないセグメントは元のインスタンスを保持 |

**事前条件**: なし（空リスト・空辞書を許容）

**事後条件**:
- `len(result) == len(segments)`（セグメント数は不変）
- 各セグメントの `start`, `end`, `speaker` は不変
- 変更された `text` を持つセグメントは新しい `Segment` インスタンス

### 4.2 `StateStore.get_guild_glossary()`

```python
def get_guild_glossary(self, guild_id: int) -> dict[str, str]
```

- ギルドの辞書が未設定の場合は `{}` を返す
- 常にdefensive copyを返す（呼び出し元の変更が内部状態に影響しない）

### 4.3 `StateStore.set_guild_glossary()`

```python
def set_guild_glossary(self, guild_id: int, glossary: dict[str, str]) -> None
```

- 辞書全体を上書き保存（部分更新APIは提供しない）
- 内部的に `_flush_guild_settings()` を呼び出し、atomic writeでディスクに永続化

### 4.4 Slash Commands

| エンドポイント | パラメータ | レスポンス |
|---------------|-----------|-----------|
| `/minutes glossary add` | `wrong: str`, `correct: str` | ephemeral: 追加確認 + エントリ数 |
| `/minutes glossary remove` | `wrong: str` | ephemeral: 削除確認 or 「未登録」エラー |
| `/minutes glossary list` | なし | ephemeral: Embed（辞書一覧） |

## 5. Storage Schema

### 5.1 `state/guild_settings.json`

```json
{
  "1027141726340657243": {
    "template": "minutes",
    "glossary": {
      "ツーニック": "TOONIQ",
      "figma": "Figma",
      "リアクト": "React",
      "ジュンジ": "junzi"
    }
  },
  "9876543210": {
    "glossary": {
      "アマゾン": "Amazon"
    }
  }
}
```

**制約**:
- 辞書キー（誤認識パターン）は文字列型、空文字列不可（コマンドハンドラでバリデーション）
- 辞書値（正しい表記）は文字列型、空文字列不可
- 辞書サイズ上限は設けない（v1）。典型的には10-100エントリ。1000エントリでもJSONサイズは数十KB程度

### 5.2 `config.yaml` セクション

```yaml
transcript_glossary:
  enabled: true          # bool, default: true
  case_sensitive: false   # bool, default: false
```

config.yamlは辞書の有効/無効とマッチングモードのみを制御する。辞書データ自体はguild_settings.jsonに格納される。

## 6. Test Strategy

### 6.1 `tests/test_glossary.py`（新規、~100行）

`apply_glossary()` の単体テスト。外部依存なし。

```python
"""Unit tests for src/glossary.py."""

from __future__ import annotations

from src.glossary import apply_glossary
from src.transcriber import Segment


def _seg(text: str, speaker: str = "Alice") -> Segment:
    return Segment(start=0.0, end=1.0, text=text, speaker=speaker)


class TestApplyGlossary:

    def test_empty_glossary_passthrough(self) -> None:
        """Empty glossary returns input list unchanged (identity)."""
        segments = [_seg("hello")]
        result = apply_glossary(segments, {})
        assert result is segments  # same object

    def test_single_replacement(self) -> None:
        """Single glossary entry replaces matching text."""
        segments = [_seg("ツーニックの会議")]
        result = apply_glossary(segments, {"ツーニック": "TOONIQ"})
        assert result[0].text == "TOONIQの会議"

    def test_multiple_replacements(self) -> None:
        """Multiple glossary entries all applied to same segment."""
        segments = [_seg("figmaでリアクトのデザイン")]
        glossary = {"figma": "Figma", "リアクト": "React"}
        result = apply_glossary(segments, glossary)
        assert "Figma" in result[0].text
        assert "React" in result[0].text

    def test_case_insensitive_default(self) -> None:
        """Default case-insensitive mode matches regardless of case."""
        segments = [_seg("FIGMA is great")]
        result = apply_glossary(segments, {"figma": "Figma"})
        assert result[0].text == "Figma is great"

    def test_case_sensitive_mode(self) -> None:
        """Case-sensitive mode only matches exact case."""
        segments = [_seg("FIGMA is great")]
        result = apply_glossary(
            segments, {"figma": "Figma"}, case_sensitive=True,
        )
        assert result[0].text == "FIGMA is great"  # no change

    def test_no_match_returns_original(self) -> None:
        """Segments with no matches are returned as-is (identity)."""
        seg = _seg("unrelated text")
        result = apply_glossary([seg], {"ツーニック": "TOONIQ"})
        assert result[0] is seg  # same object

    def test_segment_immutability(self) -> None:
        """New Segment instances are created for modified text."""
        seg = _seg("ツーニック")
        result = apply_glossary([seg], {"ツーニック": "TOONIQ"})
        assert result[0] is not seg
        assert result[0].text == "TOONIQ"
        assert result[0].start == seg.start
        assert result[0].end == seg.end
        assert result[0].speaker == seg.speaker

    def test_regex_special_chars_escaped(self) -> None:
        """Regex special characters in patterns are safely escaped."""
        segments = [_seg("Use C++ and node.js")]
        glossary = {"C++": "C Plus Plus", "node.js": "Node.js"}
        result = apply_glossary(segments, glossary)
        assert result[0].text == "Use C Plus Plus and Node.js"

    def test_multiple_segments(self) -> None:
        """Glossary is applied to all segments in the list."""
        segments = [
            _seg("ツーニックの話", "Alice"),
            _seg("ツーニックについて", "Bob"),
        ]
        result = apply_glossary(segments, {"ツーニック": "TOONIQ"})
        assert result[0].text == "TOONIQの話"
        assert result[1].text == "TOONIQについて"

    def test_preserves_segment_count(self) -> None:
        """Output segment count equals input segment count."""
        segments = [_seg("a"), _seg("b"), _seg("c")]
        result = apply_glossary(segments, {"x": "y"})
        assert len(result) == 3

    def test_empty_segments_list(self) -> None:
        """Empty segments list with non-empty glossary returns empty list."""
        result = apply_glossary([], {"foo": "bar"})
        assert result == []
```

### 6.2 `tests/test_state_store.py`（追加）

既存テストファイルにglossaryメソッドのテストを追加。

```python
class TestGuildGlossary:

    def test_get_glossary_empty_default(self, tmp_path: Path) -> None:
        """Unknown guild returns empty dict."""
        store = _make_store(tmp_path)
        assert store.get_guild_glossary(12345) == {}

    def test_set_and_get_glossary(self, tmp_path: Path) -> None:
        """Round-trip set/get for glossary."""
        store = _make_store(tmp_path)
        glossary = {"foo": "bar", "baz": "qux"}
        store.set_guild_glossary(12345, glossary)
        assert store.get_guild_glossary(12345) == glossary

    def test_glossary_persists_across_reload(self, tmp_path: Path) -> None:
        """Glossary survives StateStore re-instantiation."""
        store = _make_store(tmp_path)
        store.set_guild_glossary(12345, {"a": "b"})
        store2 = _make_store(tmp_path)
        assert store2.get_guild_glossary(12345) == {"a": "b"}

    def test_glossary_defensive_copy(self, tmp_path: Path) -> None:
        """Returned dict is a copy; mutations do not affect store."""
        store = _make_store(tmp_path)
        store.set_guild_glossary(12345, {"a": "b"})
        result = store.get_guild_glossary(12345)
        result["c"] = "d"
        assert store.get_guild_glossary(12345) == {"a": "b"}

    def test_glossary_independent_per_guild(self, tmp_path: Path) -> None:
        """Different guilds have independent glossaries."""
        store = _make_store(tmp_path)
        store.set_guild_glossary(111, {"x": "y"})
        store.set_guild_glossary(222, {"a": "b"})
        assert store.get_guild_glossary(111) == {"x": "y"}
        assert store.get_guild_glossary(222) == {"a": "b"}

    def test_glossary_coexists_with_template(self, tmp_path: Path) -> None:
        """Glossary and template share guild_settings without interference."""
        store = _make_store(tmp_path)
        store.set_guild_template(12345, "todo-focused")
        store.set_guild_glossary(12345, {"foo": "bar"})
        assert store.get_guild_template(12345) == "todo-focused"
        assert store.get_guild_glossary(12345) == {"foo": "bar"}
```

### 6.3 `tests/test_pipeline.py`（追加）

パイプライン統合テスト。既存の `TestPipelineSpeakerAnalytics` パターンに準拠。

```python
class TestPipelineGlossary:

    @pytest.mark.asyncio
    async def test_glossary_applied_when_enabled(self, ...):
        """Glossary entries are applied to segments before merge."""
        # Setup: cfg with transcript_glossary.enabled=True
        # state_store with glossary {"ツーニック": "TOONIQ"}
        # Verify: generator.generate receives transcript containing "TOONIQ"

    @pytest.mark.asyncio
    async def test_glossary_skipped_when_disabled(self, ...):
        """Glossary is not applied when config disabled."""
        # Setup: cfg with transcript_glossary.enabled=False
        # state_store with glossary {"ツーニック": "TOONIQ"}
        # Verify: generator.generate receives transcript containing "ツーニック"

    @pytest.mark.asyncio
    async def test_glossary_skipped_when_empty(self, ...):
        """Empty glossary does not modify segments."""
        # Setup: cfg with transcript_glossary.enabled=True
        # state_store with empty glossary
        # Verify: no apply_glossary call, segments unchanged
```

### 6.4 テストカバレッジ目標

| テストファイル | テスト数 | カバレッジ対象 |
|-------------|------:|-------------|
| `test_glossary.py` | 11 | `apply_glossary()` の全パス |
| `test_state_store.py` | 6 | `get/set_guild_glossary()` のCRUD + 永続化 |
| `test_pipeline.py` | 3 | パイプライン統合（有効/無効/空辞書） |
| **合計** | **20** | |

## 7. Risks & Mitigations

### R1: 短いパターンの部分一致

**リスク**: 辞書パターン `"AI"` が `"FAIR"` 内の `"AI"` にもマッチし、`"FReplace"` のような破損が発生する。

**緩和策**:
- v1では仕様として許容。ドキュメントで「短いパターンは意図しない一致が発生する可能性がある」旨を記載
- v2で `whole_words_only: bool` オプションを追加予定（`\b` ワードバウンダリを使用）。ただし日本語にはワードバウンダリが存在しないため、日本語テキストでは依然として部分一致が必要

### R2: Regex injection

**リスク**: ユーザーが辞書パターンに `.*` のようなregex構文を含める。

**緩和策**: `re.escape()` で全パターンをエスケープ済み。regex特殊文字はリテラル文字として扱われる。テスト `test_regex_special_chars_escaped` で検証済み。

### R3: パフォーマンス

**リスク**: 大量のセグメント x 大量の辞書エントリで処理時間が増加。

**分析**: O(S * E * L) where S=セグメント数, E=辞書エントリ数, L=平均テキスト長。
- 典型的なケース: S=200, E=50, L=30文字 -> 300,000回の文字列操作。Pythonで数十ミリ秒。
- 最悪ケース: S=1000, E=500 -> 500,000回。それでも数百ミリ秒。
- Whisperの文字起こし（数分）やClaude APIコール（数秒）と比較して無視できる。

**緩和策**: ログで辞書適用のエントリ数を出力。将来的に問題が顕在化した場合、Aho-Corasickアルゴリズムへの切り替えを検討。

### R4: 辞書の適用順序

**リスク**: 辞書エントリの適用順序が置換結果に影響する場合がある。例: `{"AB": "X", "ABC": "Y"}` で "ABC" に対して先に "AB" がマッチすると "XC" になり "Y" にはならない。

**緩和策**:
- v1ではPython dictの挿入順（ユーザーが追加した順）で適用。
- 実用上、辞書エントリは互いに重複しないケースがほとんど（異なる誤認識パターンを修正するため）。
- 問題が報告された場合、パターン長降順でソートするオプションをv2で追加。

### R5: 辞書の同時書き込み

**リスク**: 複数のDiscordコマンドが同時に辞書を変更し、一方の変更が失われる。

**緩和策**:
- discord.pyのイベントループはシングルスレッドで実行されるため、コマンドハンドラは同時実行されない。`await` を挟まない get -> modify -> set のアトミックな操作シーケンスにより、race conditionは発生しない。

## 8. Rollback Plan

### Level 1: ランタイム無効化（即座、デプロイ不要）

```yaml
transcript_glossary:
  enabled: false
```

Bot再起動で辞書適用が無効化。辞書データはguild_settings.jsonに残るが、パイプラインでは参照されない。スラッシュコマンドは引き続き動作する（辞書の編集・閲覧は可能）。

### Level 2: コード完全削除

1. `src/glossary.py` を削除
2. `tests/test_glossary.py` を削除
3. `src/pipeline.py` の辞書適用ブロック（Stage 2.5）を削除
4. `src/config.py` から `TranscriptGlossaryConfig`、`_SECTION_CLASSES` エントリ、`Config.transcript_glossary` フィールドを削除
5. `src/state_store.py` から `get_guild_glossary()` / `set_guild_glossary()` を削除
6. `bot.py` から `glossary_group` とその全コマンドを削除
7. `config.yaml` から `transcript_glossary:` セクションを削除
8. テストファイルから glossary 関連テストを削除
9. `state/guild_settings.json` 内の `"glossary"` キーは残存しても無害（StateStoreは未知キーを無視）

全変更はadditiveであり、既存の関数シグネチャや戻り値型を変更しない。部分的なロールバック（例: コマンドのみ削除してパイプライン適用は維持）も安全。
