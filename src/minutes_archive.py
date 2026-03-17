"""SQLite FTS5-backed archive for full-text search of meeting minutes."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
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
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        logger.info("MinutesArchive opened at %s", db_path)

    def _create_tables(self) -> None:
        cur = self._conn.cursor()

        cur.execute("""
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
            )
        """)

        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_archive_guild ON minutes_archive(guild_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_archive_hash ON minutes_archive(transcript_hash)"
        )

        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS minutes_fts USING fts5(
                minutes_md,
                speakers,
                content='minutes_archive',
                content_rowid='id',
                tokenize='trigram'
            )
        """)

        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS archive_ai AFTER INSERT ON minutes_archive BEGIN
                INSERT INTO minutes_fts(rowid, minutes_md, speakers)
                VALUES (new.id, new.minutes_md, new.speakers);
            END
        """)

        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS archive_ad AFTER DELETE ON minutes_archive BEGIN
                INSERT INTO minutes_fts(minutes_fts, rowid, minutes_md, speakers)
                VALUES ('delete', old.id, old.minutes_md, old.speakers);
            END
        """)

        self._conn.commit()

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
        """Archive a minutes document. Returns the row id."""
        cur = self._conn.execute(
            """
            INSERT INTO minutes_archive
                (guild_id, source_label, date_str, speakers, channel_name,
                 template_name, transcript_hash, minutes_md, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                source_label,
                date_str,
                speakers,
                channel_name,
                template_name,
                transcript_hash,
                minutes_md,
                message_id,
            ),
        )
        self._conn.commit()
        row_id = cur.lastrowid or -1
        logger.info(
            "Archived minutes id=%d guild=%d date=%s", row_id, guild_id, date_str
        )
        return row_id

    def search(
        self,
        guild_id: int,
        query: str,
        limit: int = 5,
    ) -> list[SearchResult]:
        """Full-text search within a guild's archived minutes.

        Uses FTS5 MATCH with trigram tokenizer for queries >= 3 chars.
        Falls back to LIKE for shorter queries (trigram minimum is 3 chars).
        Returns results ranked by BM25 score (FTS5) or id desc (LIKE).
        """
        q = query.strip()
        if not q:
            return []

        # Trigram tokenizer requires at least 3 characters
        if len(q) >= 3:
            cur = self._conn.execute(
                """
                SELECT
                    a.id, a.date_str, a.speakers,
                    snippet(minutes_fts, 0, '', '', '...', 40) AS snippet,
                    rank
                FROM minutes_fts
                JOIN minutes_archive a ON a.id = minutes_fts.rowid
                WHERE minutes_fts MATCH ? AND a.guild_id = ?
                ORDER BY rank
                LIMIT ?
                """,
                (q, guild_id, limit),
            )
            return [
                SearchResult(
                    id=row[0],
                    date_str=row[1],
                    speakers=row[2],
                    snippet=row[3],
                    rank=row[4],
                )
                for row in cur.fetchall()
            ]

        # Fallback for short queries (< 3 chars): LIKE on both columns
        pattern = f"%{q}%"
        cur = self._conn.execute(
            """
            SELECT id, date_str, speakers, minutes_md, 0.0 AS rank
            FROM minutes_archive
            WHERE guild_id = ? AND (minutes_md LIKE ? OR speakers LIKE ?)
            ORDER BY id DESC
            LIMIT ?
            """,
            (guild_id, pattern, pattern, limit),
        )
        results: list[SearchResult] = []
        for row in cur.fetchall():
            # Build a simple snippet around the match
            md: str = row[3]
            idx = md.find(q)
            if idx == -1:
                # Match was in speakers column
                snippet = md[:80]
            else:
                start = max(0, idx - 30)
                end = min(len(md), idx + len(q) + 30)
                snippet = ("..." if start > 0 else "") + md[start:end] + ("..." if end < len(md) else "")
            results.append(SearchResult(
                id=row[0], date_str=row[1], speakers=row[2], snippet=snippet, rank=row[4],
            ))
        return results

    def count(self, guild_id: int) -> int:
        """Return the number of archived minutes for a guild."""
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM minutes_archive WHERE guild_id = ?",
            (guild_id,),
        )
        return cur.fetchone()[0]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
        logger.info("MinutesArchive closed")
