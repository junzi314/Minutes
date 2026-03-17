"""Unit tests for src/minutes_archive.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.minutes_archive import MinutesArchive, SearchResult


@pytest.fixture
def archive(tmp_path: Path) -> MinutesArchive:
    db = MinutesArchive(tmp_path / "test.db")
    yield db
    db.close()


class TestStoreAndSearch:
    def test_store_returns_positive_id(self, archive: MinutesArchive) -> None:
        row_id = archive.store(
            guild_id=1,
            date_str="2026-03-17 10:00",
            speakers="Alice, Bob",
            minutes_md="# Meeting\n## Summary\nDiscussed budget.",
        )
        assert row_id > 0

    def test_store_and_search_basic(self, archive: MinutesArchive) -> None:
        archive.store(
            guild_id=1,
            date_str="2026-03-17 10:00",
            speakers="Alice, Bob",
            minutes_md="# Meeting\n## Summary\nDiscussed budget allocation.",
        )
        results = archive.search(1, "budget")
        assert len(results) == 1
        assert results[0].date_str == "2026-03-17 10:00"
        assert results[0].speakers == "Alice, Bob"
        assert isinstance(results[0].rank, float)

    def test_search_returns_search_result_type(self, archive: MinutesArchive) -> None:
        archive.store(
            guild_id=1,
            date_str="2026-03-17",
            speakers="Alice",
            minutes_md="Test content for search result type.",
        )
        results = archive.search(1, "content")
        assert len(results) == 1
        assert isinstance(results[0], SearchResult)


class TestSearchJapanese:
    def test_japanese_keyword(self, archive: MinutesArchive) -> None:
        archive.store(
            guild_id=1,
            date_str="2026-03-17 10:00",
            speakers="Alice",
            minutes_md="# 会議議事録\n## 要約\n来期予算について議論し、各部門の配分を決定した。",
        )
        results = archive.search(1, "予算")
        assert len(results) == 1
        assert "予算" in results[0].snippet

    def test_japanese_speakers_search(self, archive: MinutesArchive) -> None:
        archive.store(
            guild_id=1,
            date_str="2026-03-17",
            speakers="田中, 佐藤",
            minutes_md="# Meeting\nGeneral discussion.",
        )
        results = archive.search(1, "田中")
        assert len(results) == 1


class TestGuildIsolation:
    def test_different_guilds_isolated(self, archive: MinutesArchive) -> None:
        archive.store(
            guild_id=1,
            date_str="2026-03-17",
            speakers="Alice",
            minutes_md="Budget discussion for guild one.",
        )
        archive.store(
            guild_id=2,
            date_str="2026-03-17",
            speakers="Bob",
            minutes_md="Budget discussion for guild two.",
        )
        results_guild1 = archive.search(1, "Budget")
        results_guild2 = archive.search(2, "Budget")
        assert len(results_guild1) == 1
        assert results_guild1[0].speakers == "Alice"
        assert len(results_guild2) == 1
        assert results_guild2[0].speakers == "Bob"


class TestSearchNoResults:
    def test_no_match(self, archive: MinutesArchive) -> None:
        archive.store(
            guild_id=1,
            date_str="2026-03-17",
            speakers="Alice",
            minutes_md="Regular meeting notes.",
        )
        results = archive.search(1, "zzzznonexistent")
        assert results == []

    def test_empty_query(self, archive: MinutesArchive) -> None:
        archive.store(
            guild_id=1,
            date_str="2026-03-17",
            speakers="Alice",
            minutes_md="Some content.",
        )
        results = archive.search(1, "")
        assert results == []

    def test_empty_archive(self, archive: MinutesArchive) -> None:
        results = archive.search(1, "anything")
        assert results == []


class TestSearchRanking:
    def test_bm25_ranking_order(self, archive: MinutesArchive) -> None:
        """Document with more occurrences of the term should rank higher."""
        archive.store(
            guild_id=1,
            date_str="2026-03-10",
            speakers="Alice",
            minutes_md="budget mention once",
        )
        archive.store(
            guild_id=1,
            date_str="2026-03-17",
            speakers="Bob",
            minutes_md="budget budget budget budget budget repeated many times",
        )
        results = archive.search(1, "budget")
        assert len(results) == 2
        # BM25 rank is negative; lower (more negative) = better match
        assert results[0].rank <= results[1].rank


class TestSearchLimit:
    def test_limit_respected(self, archive: MinutesArchive) -> None:
        for i in range(10):
            archive.store(
                guild_id=1,
                date_str=f"2026-03-{i+1:02d}",
                speakers="Alice",
                minutes_md=f"Meeting number {i+1} about testing.",
            )
        results = archive.search(1, "testing", limit=3)
        assert len(results) == 3


class TestStoreDuplicateHash:
    def test_same_hash_stored_separately(self, archive: MinutesArchive) -> None:
        """Duplicate transcript_hash values are allowed (no unique constraint)."""
        id1 = archive.store(
            guild_id=1,
            date_str="2026-03-17",
            speakers="Alice",
            minutes_md="Content A",
            transcript_hash="abc123",
        )
        id2 = archive.store(
            guild_id=1,
            date_str="2026-03-17",
            speakers="Alice",
            minutes_md="Content B",
            transcript_hash="abc123",
        )
        assert id1 != id2


class TestCount:
    def test_count_per_guild(self, archive: MinutesArchive) -> None:
        archive.store(guild_id=1, date_str="d1", speakers="A", minutes_md="m1")
        archive.store(guild_id=1, date_str="d2", speakers="B", minutes_md="m2")
        archive.store(guild_id=2, date_str="d3", speakers="C", minutes_md="m3")
        assert archive.count(1) == 2
        assert archive.count(2) == 1
        assert archive.count(999) == 0


class TestSnippetGeneration:
    def test_snippet_contains_match_context(self, archive: MinutesArchive) -> None:
        long_text = "A" * 100 + " important keyword here " + "B" * 100
        archive.store(
            guild_id=1,
            date_str="2026-03-17",
            speakers="Alice",
            minutes_md=long_text,
        )
        results = archive.search(1, "keyword")
        assert len(results) == 1
        assert "keyword" in results[0].snippet


class TestStoreMetadata:
    def test_all_metadata_stored(self, archive: MinutesArchive) -> None:
        row_id = archive.store(
            guild_id=42,
            date_str="2026-03-17 14:00",
            speakers="Alice, Bob",
            minutes_md="# Minutes",
            source_label="craig:abc123",
            channel_name="general",
            template_name="todo-focused",
            transcript_hash="deadbeef",
            message_id=123456,
        )
        assert row_id > 0
        # Verify via direct query
        cur = archive._conn.execute(
            "SELECT source_label, channel_name, template_name, transcript_hash, message_id "
            "FROM minutes_archive WHERE id = ?",
            (row_id,),
        )
        row = cur.fetchone()
        assert row == ("craig:abc123", "general", "todo-focused", "deadbeef", 123456)
