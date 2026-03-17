# Technical Specification: minutes-search

**Feature**: 過去議事録検索
**Date**: 2026-03-17

---

## Architecture Overview

```
                  ┌──────────────┐
                  │  pipeline.py │
                  │  (Stage 5後)  │
                  └──────┬───────┘
                         │ store()
                         ▼
                 ┌───────────────┐     ┌─────────────────────┐
                 │MinutesArchive │────▶│ minutes_archive.db  │
                 │(.py)          │     │  - minutes_archive   │
                 └───────┬───────┘     │  - minutes_fts (FTS5)│
                         │             └─────────────────────┘
                         │ search()
                         ▼
                  ┌──────────────┐
                  │   bot.py     │
                  │ /minutes     │
                  │   search     │
                  └──────────────┘
```

### 設計原則
- **単一責任**: `MinutesArchive` はアーカイブ専用。既存 `StateStore` とは独立
- **Fault-tolerant**: アーカイブ書込み失敗はパイプラインに影響しない（try/except）
- **Guild isolation**: 全クエリに `WHERE guild_id = ?` を強制
- **Minimal dependencies**: sqlite3 stdlib のみ（外部パッケージなし）

---

## New Module: `src/minutes_archive.py`

### Class: `MinutesArchive`

```python
@dataclass
class SearchResult:
    """Single search result from the minutes archive."""
    id: int
    date_str: str
    speakers: str
    snippet: str
    rank: float

class MinutesArchive:
    """SQLite FTS5-backed archive for full-text search of meeting minutes."""

    def __init__(self, db_path: Path) -> None:
        """Open/create the SQLite database with FTS5 index.

        - WAL mode for concurrent read safety
        - Creates tables on first run
        """

    def store(
        self,
        guild_id: int,
        date_str: str,
        speakers: str,
        minutes_md: str,
        source_label: str = "",
        channel_name: str = "",
        template_name: str = "minutes",
        transcript_hash: str = "",
        message_id: int | None = None,
    ) -> int:
        """Archive a minutes document. Returns the row id.

        Also updates the FTS5 index via trigger or manual INSERT.
        Raises no exceptions externally (logs and returns -1 on failure).
        """

    def search(
        self,
        guild_id: int,
        query: str,
        limit: int = 5,
    ) -> list[SearchResult]:
        """Full-text search within a guild's archived minutes.

        Uses FTS5 MATCH with trigram tokenizer.
        Returns results ranked by BM25 score.
        """

    def count(self, guild_id: int) -> int:
        """Return the number of archived minutes for a guild."""

    def close(self) -> None:
        """Close the database connection."""
```

---

## Database Schema

```sql
-- Metadata table
CREATE TABLE IF NOT EXISTS minutes_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    source_label TEXT DEFAULT '',
    date_str TEXT NOT NULL,
    speakers TEXT DEFAULT '',
    channel_name TEXT DEFAULT '',
    template_name TEXT DEFAULT 'minutes',
    transcript_hash TEXT DEFAULT '',
    minutes_md TEXT NOT NULL,
    message_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_archive_guild ON minutes_archive(guild_id);
CREATE INDEX IF NOT EXISTS idx_archive_hash ON minutes_archive(transcript_hash);

-- FTS5 full-text search index (content-sync with minutes_archive)
CREATE VIRTUAL TABLE IF NOT EXISTS minutes_fts USING fts5(
    minutes_md,
    speakers,
    content='minutes_archive',
    content_rowid='id',
    tokenize='trigram'
);

-- Triggers to keep FTS index in sync
CREATE TRIGGER IF NOT EXISTS archive_ai AFTER INSERT ON minutes_archive BEGIN
    INSERT INTO minutes_fts(rowid, minutes_md, speakers)
    VALUES (new.id, new.minutes_md, new.speakers);
END;

CREATE TRIGGER IF NOT EXISTS archive_ad AFTER DELETE ON minutes_archive BEGIN
    INSERT INTO minutes_fts(minutes_fts, rowid, minutes_md, speakers)
    VALUES ('delete', old.id, old.minutes_md, old.speakers);
END;
```

### FTS5 Search Query

```sql
SELECT
    a.id, a.date_str, a.speakers,
    snippet(minutes_fts, 0, '', '', '...', 40) AS snippet,
    rank
FROM minutes_fts
JOIN minutes_archive a ON a.id = minutes_fts.rowid
WHERE minutes_fts MATCH ? AND a.guild_id = ?
ORDER BY rank
LIMIT ?
```

---

## Config Extension

### `MinutesArchiveConfig`

```python
@dataclass(frozen=True)
class MinutesArchiveConfig:
    enabled: bool = True
    max_search_results: int = 5
```

- `Config` に `minutes_archive: MinutesArchiveConfig` フィールド追加
- `_SECTION_CLASSES` に `"minutes_archive": MinutesArchiveConfig` 登録

### config.yaml サンプル

```yaml
minutes_archive:
  enabled: true
  max_search_results: 5
```

---

## Pipeline Integration

**場所**: `pipeline.py` `run_pipeline_from_tracks()` L127（`post_minutes()` の直後）

```python
# Stage 5: Post to Discord
message = await post_minutes(
    channel=output_channel,
    minutes_md=minutes_md,
    date=date_str,
    speakers=speakers_str,
    cfg=cfg.poster,
    speaker_stats=speaker_stats_text,
)

# Archive (fault-tolerant)
if archive is not None and cfg.minutes_archive.enabled:
    try:
        archive.store(
            guild_id=output_channel.guild.id,
            date_str=date_str,
            speakers=speakers_str,
            minutes_md=minutes_md,
            source_label=source_label,
            channel_name=output_channel.name,
            template_name=template_name,
            transcript_hash=th,
            message_id=message.id,
        )
    except Exception:
        logger.warning("Archive write failed (non-critical)", exc_info=True)
```

### パラメータ追加

`run_pipeline_from_tracks()` と `run_pipeline()` に `archive: MinutesArchive | None = None` パラメータ追加。

---

## Bot Integration

### MinutesBot 初期化

```python
# bot.py main()
archive = None
if cfg.minutes_archive.enabled:
    archive_path = Path(cfg.pipeline.state_dir) / "minutes_archive.db"
    archive = MinutesArchive(archive_path)
```

### `/minutes search` コマンド

```python
@group.command(name="search", description="過去の議事録をキーワードで検索")
@discord.app_commands.describe(keyword="検索キーワード")
async def minutes_search(interaction: discord.Interaction, keyword: str) -> None:
    if client.archive is None:
        await interaction.response.send_message(
            "議事録アーカイブが有効になっていません。", ephemeral=True
        )
        return

    guild_id = interaction.guild_id or 0
    results = await asyncio.to_thread(
        client.archive.search, guild_id, keyword
    )

    if not results:
        await interaction.response.send_message(
            f"「{keyword}」に一致する議事録は見つかりませんでした。",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title=f"🔍 議事録検索結果 — 「{keyword}」",
        color=0x5865F2,
    )
    for r in results:
        speakers_text = f" — 参加者: {r.speakers}" if r.speakers else ""
        embed.add_field(
            name=f"📅 {r.date_str}{speakers_text}",
            value=r.snippet[:200] or "(スニペットなし)",
            inline=False,
        )
    total = client.archive.count(guild_id)
    embed.set_footer(text=f"{len(results)}件中 {total}件のアーカイブから検索")
    await interaction.response.send_message(embed=embed, ephemeral=True)
```

---

## Testing Strategy

### Unit Tests (`tests/test_minutes_archive.py`)

| テスト | 検証内容 |
|--------|---------|
| `test_store_and_search` | 保存→検索の基本フロー |
| `test_search_japanese` | 日本語キーワード検索 |
| `test_guild_isolation` | 異なるguild_idの議事録が混ざらない |
| `test_search_no_results` | マッチなし時の空リスト返却 |
| `test_search_ranking` | BM25ランキング順序 |
| `test_store_duplicate_hash` | 同じtranscript_hashの重複保存 |
| `test_snippet_generation` | snippet()の出力確認 |
| `test_count` | ギルド別件数カウント |

### Integration Tests (`tests/test_pipeline.py` 追加)

| テスト | 検証内容 |
|--------|---------|
| `test_pipeline_archives_minutes` | パイプライン成功時にarchive.store()が呼ばれる |
| `test_pipeline_archive_failure_non_blocking` | archive.store()失敗がパイプラインをブロックしない |
| `test_pipeline_no_archive_when_disabled` | enabled=False時にarchiveが呼ばれない |

---

## Technical Risks & Mitigations

| リスク | 確率 | 影響 | 対策 |
|--------|------|------|------|
| FTS5 trigram日本語品質 | 中 | 中 | テストで検証、substring matchのフォールバック不要（trigramで十分） |
| Discord 3秒タイムアウト | 低 | 中 | `asyncio.to_thread()` + 必要なら `interaction.response.defer()` |
| SQLite並行アクセス | 低 | 低 | WALモード、単一ライター |
| WSL DrvFS破損 | 中 | 中 | state/ディレクトリ（Linux FS）と同じ場所に配置 |
| アーカイブ書込み失敗 | 低 | 中 | try/except で fault-tolerant |

---

## Performance Estimates

| 指標 | 見込み |
|------|--------|
| store() latency | <1ms per row |
| search() latency (1,000 rows) | <10ms |
| メモリ使用量 | ディスクバック（起動時追加メモリなし） |
| DB size (1,000 entries) | ~9MB |
