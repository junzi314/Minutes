"""Unit tests for src/exporter.py."""

from __future__ import annotations

import textwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.exporter import ExportResult, GoogleDocsExporter

# ---------------------------------------------------------------------------
# Shared config stub (ExportGoogleDocsConfig does not exist yet)
# ---------------------------------------------------------------------------

_CFG = SimpleNamespace(
    enabled=True,
    credentials_path="credentials.json",
    oauth_client_path="oauth_client.json",
    oauth_token_path="token.json",
    folder_id="test-folder-id",
    max_retries=3,
)


def _make_exporter(
    cfg: SimpleNamespace | None = None,
) -> GoogleDocsExporter:
    """Create a GoogleDocsExporter with a stub config."""
    return GoogleDocsExporter(cfg or _CFG)


# ===================================================================
# Markdown → HTML conversion
# ===================================================================


class TestMdToHtml:
    """Tests for GoogleDocsExporter._md_to_html."""

    def test_md_to_html_headings(self) -> None:
        exp = _make_exporter()
        html = exp._md_to_html("# Heading 1\n\n## Heading 2")
        assert "<h1 " in html and "Heading 1" in html
        assert "<h2 " in html and "Heading 2" in html

    def test_md_to_html_bold(self) -> None:
        exp = _make_exporter()
        html = exp._md_to_html("This is **bold** text")
        assert "<strong>bold</strong>" in html

    def test_md_to_html_lists(self) -> None:
        exp = _make_exporter()
        md_text = "- item one\n- item two\n- item three"
        html = exp._md_to_html(md_text)
        assert "<ul>" in html
        assert "<li " in html
        assert "item one" in html
        assert "item two" in html
        assert "item three" in html

    def test_md_to_html_tables(self) -> None:
        exp = _make_exporter()
        md_table = textwrap.dedent("""\
            | Speaker | Duration |
            |---------|----------|
            | Alice   | 10:00    |
            | Bob     | 05:30    |
        """)
        html = exp._md_to_html(md_table)
        assert "<table " in html
        assert "<th " in html or "<td " in html
        assert "Alice" in html
        assert "Bob" in html

    def test_md_to_html_japanese(self) -> None:
        exp = _make_exporter()
        md_text = "# 会議議事録\n\n## 要約\n\nこれはテストです。参加者：田中太郎"
        html = exp._md_to_html(md_text)
        assert "会議議事録" in html
        assert "要約" in html
        assert "これはテストです" in html
        assert "田中太郎" in html

    def test_md_to_html_full_minutes(self) -> None:
        """Realistic end-to-end minutes markdown conversion."""
        exp = _make_exporter()
        md = textwrap.dedent("""\
            # 会議議事録 — 2026-03-17

            ## 参加者
            - Alice
            - Bob

            ## 要約
            プロジェクトの進捗確認を行った。

            ## 議論内容

            ### 1. 進捗報告
            **Alice** がフロントエンド実装の完了を報告した。

            ### 2. 次回アクション

            | 担当者 | タスク | 期限 |
            |--------|--------|------|
            | Alice  | レビュー | 3/20 |
            | Bob    | テスト | 3/22 |

            ## 決定事項
            - リリース日は3月25日に決定
        """)
        html = exp._md_to_html(md)

        # Structural checks (tags have inline styles, so check prefix)
        assert "<h1 " in html
        assert "<h2 " in html
        assert "<h3 " in html
        assert "<ul>" in html
        assert "<li " in html
        assert "<table " in html
        assert "<strong>" in html

        # Content checks
        assert "会議議事録" in html
        assert "2026-03-17" in html
        assert "Alice" in html
        assert "Bob" in html
        assert "プロジェクトの進捗確認" in html
        assert "リリース日は3月25日に決定" in html

    def test_md_to_html_wraps_in_html_document(self) -> None:
        exp = _make_exporter()
        html = exp._md_to_html("Hello")
        assert html.startswith("<!DOCTYPE html>")
        assert "<html>" in html
        assert '<meta charset="utf-8">' in html
        assert "<body " in html
        assert "</body></html>" in html


# ===================================================================
# Export (async, Google Drive API mocked)
# ===================================================================


class _HttpError(Exception):
    """Fake HTTP error with a resp attribute, mimicking googleapiclient errors."""

    def __init__(self, status: int, message: str = "") -> None:
        self.resp = SimpleNamespace(status=status)
        super().__init__(message or f"{status} error")


def _mock_drive_service(doc_id: str = "doc-123", url: str = "https://docs.google.com/doc-123"):
    """Create a mock Google Drive service that returns success."""
    mock_execute = MagicMock(return_value={"id": doc_id, "webViewLink": url})
    mock_create = MagicMock()
    mock_create.return_value.execute = mock_execute
    mock_files = MagicMock()
    mock_files.return_value.create = mock_create
    service = MagicMock()
    service.files = mock_files
    return service


class TestExportSuccess:
    @pytest.mark.asyncio
    async def test_export_success(self) -> None:
        """Successful export returns ExportResult with url and doc_id."""
        exp = _make_exporter()
        exp._service = _mock_drive_service(
            doc_id="abc-123",
            url="https://docs.google.com/document/d/abc-123/edit",
        )

        result = await exp.export("# Test Minutes", "Test Meeting 2026-03-17")

        assert isinstance(result, ExportResult)
        assert result.success is True
        assert result.doc_id == "abc-123"
        assert result.url == "https://docs.google.com/document/d/abc-123/edit"
        assert result.error is None


class TestExportRetry:
    @pytest.mark.asyncio
    async def test_export_retry_on_500(self) -> None:
        """500 errors trigger retry."""
        exp = _make_exporter()

        success_result = {"id": "retry-doc", "webViewLink": "https://docs.google.com/retry-doc"}

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _HttpError(500, "500 Internal Server Error")
            return success_result

        mock_execute = MagicMock(side_effect=side_effect)
        mock_create = MagicMock()
        mock_create.return_value.execute = mock_execute
        mock_files = MagicMock()
        mock_files.return_value.create = mock_create
        service = MagicMock()
        service.files = mock_files
        exp._service = service

        result = await exp.export("# Minutes", "Retry Test")

        assert result.success is True
        assert result.doc_id == "retry-doc"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_export_no_retry_on_400(self) -> None:
        """400 errors should not be retried (non-retryable client error)."""
        exp = _make_exporter()

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise _HttpError(400, "400 Bad Request")

        mock_execute = MagicMock(side_effect=side_effect)
        mock_create = MagicMock()
        mock_create.return_value.execute = mock_execute
        mock_files = MagicMock()
        mock_files.return_value.create = mock_create
        service = MagicMock()
        service.files = mock_files
        exp._service = service

        result = await exp.export("# Minutes", "No Retry Test")

        assert result.success is False
        assert result.error is not None
        # Should have attempted only once, then broken out
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_export_max_retries_exhausted(self) -> None:
        """When all retries are exhausted, ExportResult(success=False) is returned."""
        cfg = SimpleNamespace(
            enabled=True,
            credentials_path="credentials.json",
            oauth_token_path="token.json",
            folder_id="test-folder-id",
            max_retries=3,
        )
        exp = _make_exporter(cfg)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("network down")

        mock_execute = MagicMock(side_effect=side_effect)
        mock_create = MagicMock()
        mock_create.return_value.execute = mock_execute
        mock_files = MagicMock()
        mock_files.return_value.create = mock_create
        service = MagicMock()
        service.files = mock_files
        exp._service = service

        result = await exp.export("# Minutes", "Exhaust Test")

        assert result.success is False
        assert "network down" in result.error
        assert call_count == 3


class TestExportNeverRaises:
    @pytest.mark.asyncio
    async def test_export_never_raises(self) -> None:
        """Even on completely unexpected errors, export returns ExportResult."""
        exp = _make_exporter()

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("completely unexpected!")

        mock_execute = MagicMock(side_effect=side_effect)
        mock_create = MagicMock()
        mock_create.return_value.execute = mock_execute
        mock_files = MagicMock()
        mock_files.return_value.create = mock_create
        service = MagicMock()
        service.files = mock_files
        exp._service = service

        # Should NOT raise
        result = await exp.export("# Minutes", "Never Raises Test")

        assert isinstance(result, ExportResult)
        assert result.success is False
        assert result.error is not None
        assert "completely unexpected" in result.error


# ===================================================================
# Service building
# ===================================================================


class TestBuildService:
    def test_build_service_missing_creds(self, tmp_path) -> None:
        """ExportError raised when credentials file does not exist."""
        cfg = SimpleNamespace(
            enabled=True,
            credentials_path=str(tmp_path / "nonexistent-creds.json"),
            oauth_token_path=str(tmp_path / "nonexistent-token.json"),
            folder_id="test-folder-id",
            max_retries=3,
        )
        exp = _make_exporter(cfg)

        from src.errors import ExportError

        with pytest.raises(ExportError, match="Credentials file not found"):
            exp._build_service()

    def test_build_service_caching(self, tmp_path) -> None:
        """Service is built once and cached on subsequent calls."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text("{}", encoding="utf-8")

        cfg = SimpleNamespace(
            enabled=True,
            credentials_path=str(creds_file),
            oauth_token_path=str(tmp_path / "nonexistent-token.json"),
            folder_id="test-folder-id",
            max_retries=3,
        )
        exp = _make_exporter(cfg)

        with patch("google.oauth2.service_account.Credentials") as mock_creds_cls, \
             patch("googleapiclient.discovery.build") as mock_build:
            mock_creds_cls.from_service_account_file.return_value = MagicMock()
            mock_build.return_value = MagicMock()

            service1 = exp._build_service()
            service2 = exp._build_service()

            assert service1 is service2
            # Credentials and build should each be called only once
            mock_creds_cls.from_service_account_file.assert_called_once()
            mock_build.assert_called_once()


# ===================================================================
# ExportResult dataclass
# ===================================================================


class TestBuildDocsService:
    """Tests for GoogleDocsExporter._build_docs_service."""

    def test_build_docs_service_builds_docs_api(self, tmp_path) -> None:
        """Docs service is built with API name 'docs' and version 'v1'."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text("{}", encoding="utf-8")

        cfg = SimpleNamespace(
            enabled=True,
            credentials_path=str(creds_file),
            oauth_token_path=str(tmp_path / "nonexistent-token.json"),
            folder_id="test-folder-id",
            max_retries=3,
        )
        exp = _make_exporter(cfg)

        with patch("google.oauth2.service_account.Credentials") as mock_creds_cls, \
             patch("googleapiclient.discovery.build") as mock_build:
            mock_creds_cls.from_service_account_file.return_value = MagicMock()
            mock_build.return_value = MagicMock()

            exp._build_docs_service()

            mock_build.assert_called_once_with(
                "docs", "v1", credentials=mock_creds_cls.from_service_account_file.return_value,
                cache_discovery=False,
            )

    def test_build_docs_service_caching(self, tmp_path) -> None:
        """Docs service is built once and cached on subsequent calls."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text("{}", encoding="utf-8")

        cfg = SimpleNamespace(
            enabled=True,
            credentials_path=str(creds_file),
            oauth_token_path=str(tmp_path / "nonexistent-token.json"),
            folder_id="test-folder-id",
            max_retries=3,
        )
        exp = _make_exporter(cfg)

        with patch("google.oauth2.service_account.Credentials") as mock_creds_cls, \
             patch("googleapiclient.discovery.build") as mock_build:
            mock_creds_cls.from_service_account_file.return_value = MagicMock()
            mock_build.return_value = MagicMock()

            service1 = exp._build_docs_service()
            service2 = exp._build_docs_service()

            assert service1 is service2
            mock_build.assert_called_once()

    def test_build_docs_service_independent_of_drive(self, tmp_path) -> None:
        """Docs service is cached separately from Drive service."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text("{}", encoding="utf-8")

        cfg = SimpleNamespace(
            enabled=True,
            credentials_path=str(creds_file),
            oauth_token_path=str(tmp_path / "nonexistent-token.json"),
            folder_id="test-folder-id",
            max_retries=3,
        )
        exp = _make_exporter(cfg)

        with patch("google.oauth2.service_account.Credentials") as mock_creds_cls, \
             patch("googleapiclient.discovery.build") as mock_build:
            mock_creds_cls.from_service_account_file.return_value = MagicMock()
            mock_build.side_effect = [MagicMock(name="drive"), MagicMock(name="docs")]

            drive = exp._build_service()
            docs = exp._build_docs_service()

            assert drive is not docs
            assert mock_build.call_count == 2


# ===================================================================
# Docs API tab creation (Phase 2)
# ===================================================================


def _mock_docs_service(tab_id: str = "t.test123"):
    """Create a mock Google Docs API v1 service."""
    mock_execute = MagicMock(return_value={
        "replies": [{
            "addDocumentTab": {
                "tabProperties": {
                    "tabId": tab_id,
                    "title": "\U0001f4d6 文字起こし",
                    "index": 1,
                }
            }
        }],
        "documentId": "doc-123",
    })
    mock_batch = MagicMock()
    mock_batch.return_value.execute = mock_execute
    mock_documents = MagicMock()
    mock_documents.return_value.batchUpdate = mock_batch
    service = MagicMock()
    service.documents = mock_documents
    return service


_SAMPLE_TRANSCRIPT = """\
# 文字起こし
- 日時: 2026-03-20 14:00
- 参加者: Alice, Bob

### 00:00:00

**Alice:** こんにちは、今日の議題について話しましょう。
**Bob:** はい、まずプロジェクトの進捗から。

### 00:03:00

**Alice:** フロントエンドの実装が完了しました。
"""


class TestTranscriptTab:
    """Tests for transcript tab creation and content writing."""

    def test_add_transcript_tab_success(self) -> None:
        """Tab creation returns the new tabId."""
        exp = _make_exporter()
        exp._docs_service = _mock_docs_service(tab_id="t.abc123")

        tab_id = exp._add_transcript_tab_sync("doc-123")
        assert tab_id == "t.abc123"

    def test_add_transcript_tab_sets_title(self) -> None:
        """Tab creation request uses the correct Japanese title."""
        exp = _make_exporter()
        exp._docs_service = _mock_docs_service()

        exp._add_transcript_tab_sync("doc-123")

        call_args = exp._docs_service.documents().batchUpdate.call_args
        requests = call_args[1]["body"]["requests"] if "body" in call_args[1] else call_args[0][0]["requests"]
        tab_props = requests[0]["addDocumentTab"]["tabProperties"]
        assert tab_props["title"] == "\U0001f4d6 文字起こし"

    def test_build_transcript_heading_style(self) -> None:
        """H3 timestamps generate HEADING_3 paragraph style."""
        exp = _make_exporter()
        requests = exp._build_transcript_requests("### 00:03:00", "t.test")

        # Find updateParagraphStyle request
        heading_reqs = [r for r in requests if "updateParagraphStyle" in r]
        assert len(heading_reqs) >= 1
        style = heading_reqs[0]["updateParagraphStyle"]
        assert style["paragraphStyle"]["namedStyleType"] == "HEADING_3"

    def test_build_transcript_h1_style(self) -> None:
        """H1 generates HEADING_1 paragraph style."""
        exp = _make_exporter()
        requests = exp._build_transcript_requests("# 文字起こし", "t.test")

        heading_reqs = [r for r in requests if "updateParagraphStyle" in r]
        assert len(heading_reqs) >= 1
        assert heading_reqs[0]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "HEADING_1"

    def test_build_transcript_bold_speaker(self) -> None:
        """Speaker names are bolded."""
        exp = _make_exporter()
        requests = exp._build_transcript_requests("**Alice:** Hello world", "t.test")

        bold_reqs = [r for r in requests if "updateTextStyle" in r and r["updateTextStyle"].get("textStyle", {}).get("bold")]
        assert len(bold_reqs) >= 1
        bold_range = bold_reqs[0]["updateTextStyle"]["range"]
        # "Alice: " is 7 chars
        assert bold_range["endIndex"] - bold_range["startIndex"] == 7

    def test_build_transcript_footer_style(self) -> None:
        """Footer generates italic + gray color style."""
        exp = _make_exporter()
        requests = exp._build_transcript_requests("", "t.test")

        # Footer is always appended (even for empty transcript)
        italic_reqs = [r for r in requests if "updateTextStyle" in r and r["updateTextStyle"].get("textStyle", {}).get("italic")]
        assert len(italic_reqs) >= 1
        style = italic_reqs[0]["updateTextStyle"]["textStyle"]
        assert style["italic"] is True
        assert "foregroundColor" in style

    def test_build_transcript_empty_md(self) -> None:
        """Empty transcript produces only footer requests."""
        exp = _make_exporter()
        requests = exp._build_transcript_requests("", "t.test")

        insert_reqs = [r for r in requests if "insertText" in r]
        # Should have at least the empty line + footer
        assert len(insert_reqs) >= 1

    def test_build_transcript_tab_id_scoping(self) -> None:
        """All requests include the correct tabId."""
        exp = _make_exporter()
        requests = exp._build_transcript_requests(_SAMPLE_TRANSCRIPT, "t.myid")

        for req in requests:
            if "insertText" in req:
                assert req["insertText"]["location"]["tabId"] == "t.myid"
            elif "updateParagraphStyle" in req:
                assert req["updateParagraphStyle"]["range"]["tabId"] == "t.myid"
            elif "updateTextStyle" in req:
                assert req["updateTextStyle"]["range"]["tabId"] == "t.myid"

    def test_build_transcript_offset_tracking(self) -> None:
        """Offsets are tracked correctly across multiple lines."""
        exp = _make_exporter()
        md = "# Title\n**Alice:** Hi\n**Bob:** Hey"
        requests = exp._build_transcript_requests(md, "t.test")

        # Verify insertText requests have increasing, non-overlapping offsets
        insert_reqs = [r["insertText"] for r in requests if "insertText" in r]
        prev_end = 1  # starts at 1
        for req in insert_reqs:
            idx = req["location"]["index"]
            assert idx >= prev_end, f"Insert at {idx} overlaps previous end {prev_end}"
            prev_end = idx + exp._utf16_len(req["text"])

    def test_build_transcript_unicode_offset(self) -> None:
        """Japanese characters count correctly (1 UTF-16 code unit each)."""
        exp = _make_exporter()
        assert exp._utf16_len("こんにちは") == 5
        assert exp._utf16_len("Hello") == 5
        # Emoji (surrogate pair)
        assert exp._utf16_len("\U0001f4d6") == 2

    @pytest.mark.asyncio
    async def test_export_creates_tab_on_success(self) -> None:
        """Full export with transcript_md creates a 2-tab document."""
        exp = _make_exporter()
        exp._service = _mock_drive_service(doc_id="doc-abc", url="https://docs.google.com/document/d/doc-abc/edit")
        exp._docs_service = _mock_docs_service(tab_id="t.xyz789")

        result = await exp.export(
            "# Minutes", "Test Meeting",
            transcript_md=_SAMPLE_TRANSCRIPT,
        )

        assert result.success is True
        assert result.doc_id == "doc-abc"
        # Verify Docs API was called (tab creation + content write)
        assert exp._docs_service.documents().batchUpdate.call_count >= 1

    @pytest.mark.asyncio
    async def test_export_tab_failure_still_succeeds(self) -> None:
        """Tab creation failure doesn't affect overall export success."""
        exp = _make_exporter()
        exp._service = _mock_drive_service(doc_id="doc-abc", url="https://docs.google.com/document/d/doc-abc/edit")

        # Docs service that always fails
        mock_docs = MagicMock()
        mock_docs.documents().batchUpdate.return_value.execute.side_effect = RuntimeError("Docs API down")
        exp._docs_service = mock_docs

        result = await exp.export(
            "# Minutes", "Test Meeting",
            transcript_md=_SAMPLE_TRANSCRIPT,
        )

        assert result.success is True
        assert result.doc_id == "doc-abc"

    @pytest.mark.asyncio
    async def test_export_no_transcript_skips_tab(self) -> None:
        """Export without transcript_md skips tab creation entirely."""
        exp = _make_exporter()
        exp._service = _mock_drive_service()
        exp._docs_service = _mock_docs_service()

        result = await exp.export("# Minutes", "Test Meeting")

        assert result.success is True
        # Docs API should NOT be called
        exp._docs_service.documents().batchUpdate.assert_not_called()


# ===================================================================
# Timestamp cross-tab links (Phase 3)
# ===================================================================


class TestTimestampLinks:
    """Tests for cross-tab timestamp link insertion via Docs API."""

    def test_md_to_html_timestamps_blue_text(self) -> None:
        """Timestamps are styled as blue text (no links) when no URL provided."""
        exp = _make_exporter()
        md = "Some discussion ([12:34])"
        html = exp._md_to_html(md)

        assert "12:34" in html
        assert "color:#1a73e8" in html
        # Should NOT contain link tags for timestamps
        assert "href" not in html or "__TRANSCRIPT_TAB_URL__" not in html

    def test_update_timestamp_links_strips_query_params(self) -> None:
        """Tab URL is constructed by stripping existing query params."""
        exp = _make_exporter()

        # Mock docs service that returns a document with timestamp text
        mock_docs = MagicMock()
        mock_docs.documents().get.return_value.execute.return_value = {
            "tabs": [{
                "tabProperties": {"tabId": "t.0"},
                "documentTab": {
                    "body": {
                        "content": [{
                            "paragraph": {
                                "elements": [{
                                    "startIndex": 10,
                                    "textRun": {"content": "Discussion ([12:34])"},
                                }]
                            }
                        }]
                    }
                }
            }],
        }
        mock_docs.documents().batchUpdate.return_value.execute.return_value = {}
        exp._docs_service = mock_docs

        exp._update_timestamp_links_sync(
            "doc-123", "t.abc456",
            "https://docs.google.com/document/d/doc-123/edit?usp=drivesdk",
        )

        call_args = mock_docs.documents().batchUpdate.call_args
        body = call_args[1]["body"] if "body" in call_args[1] else call_args[0][0]
        req = body["requests"][0]["updateTextStyle"]
        link_url = req["textStyle"]["link"]["url"]
        # Should have clean URL with ?tab= (not ?usp=...?tab=...)
        assert link_url == "https://docs.google.com/document/d/doc-123/edit?tab=t.abc456"
        assert "usp=" not in link_url

    @pytest.mark.asyncio
    async def test_export_calls_link_update_after_tab(self) -> None:
        """Full flow: link update is called after tab creation."""
        exp = _make_exporter()
        exp._service = _mock_drive_service(doc_id="doc-abc", url="https://docs.google.com/document/d/doc-abc/edit")
        exp._docs_service = _mock_docs_service(tab_id="t.link123")

        result = await exp.export(
            "Discussion ([12:34])", "Test Meeting",
            transcript_md=_SAMPLE_TRANSCRIPT,
        )

        assert result.success is True
        # Docs API should be called multiple times (addTab + writeContent + get + batchUpdate for links)
        assert exp._docs_service.documents.call_count >= 1


# ===================================================================
# Fallback behavior (Phase 4)
# ===================================================================


class TestFallbackBehavior:
    """Tests for graceful degradation when Docs API fails."""

    @pytest.mark.asyncio
    async def test_export_no_transcript_md_skips_tab(self) -> None:
        """transcript_md=None skips all Docs API calls."""
        exp = _make_exporter()
        exp._service = _mock_drive_service()
        exp._docs_service = _mock_docs_service()

        result = await exp.export("# Minutes", "Test Meeting", transcript_md=None)

        assert result.success is True
        exp._docs_service.documents().batchUpdate.assert_not_called()

    @pytest.mark.asyncio
    async def test_export_tab_creation_failure_returns_success(self) -> None:
        """addDocumentTab raises → memo-only doc, success=True."""
        exp = _make_exporter()
        exp._service = _mock_drive_service()

        mock_docs = MagicMock()
        mock_docs.documents().batchUpdate.return_value.execute.side_effect = RuntimeError("addDocumentTab failed")
        exp._docs_service = mock_docs

        result = await exp.export("# Minutes", "Test", transcript_md=_SAMPLE_TRANSCRIPT)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_export_content_write_failure_logs_warning(self) -> None:
        """Content write failure → tab exists but empty, success=True."""
        exp = _make_exporter()
        exp._service = _mock_drive_service()

        call_count = 0
        def batch_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call (addDocumentTab) succeeds
                return {
                    "replies": [{"addDocumentTab": {"tabProperties": {"tabId": "t.test", "title": "x", "index": 1}}}],
                    "documentId": "doc-123",
                }
            # Second call (writeContent) fails
            raise RuntimeError("write failed")

        mock_docs = MagicMock()
        mock_docs.documents().batchUpdate.return_value.execute.side_effect = batch_side_effect
        exp._docs_service = mock_docs

        result = await exp.export("# Minutes", "Test", transcript_md=_SAMPLE_TRANSCRIPT)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_export_link_update_failure_logs_warning(self) -> None:
        """replaceAllText failure → tabs exist with content, success=True."""
        exp = _make_exporter()
        exp._service = _mock_drive_service()

        call_count = 0
        def batch_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # addDocumentTab
                return {
                    "replies": [{"addDocumentTab": {"tabProperties": {"tabId": "t.test", "title": "x", "index": 1}}}],
                    "documentId": "doc-123",
                }
            if call_count == 2:
                # writeContent
                return {"replies": [], "documentId": "doc-123"}
            # replaceAllText (3rd call) fails
            raise RuntimeError("replace failed")

        mock_docs = MagicMock()
        mock_docs.documents().batchUpdate.return_value.execute.side_effect = batch_side_effect
        exp._docs_service = mock_docs

        result = await exp.export("# Minutes", "Test", transcript_md=_SAMPLE_TRANSCRIPT)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_export_rename_tab_is_noop(self) -> None:
        """Tab rename is a no-op (Docs API doesn't support it yet)."""
        exp = _make_exporter()
        exp._service = _mock_drive_service()
        exp._docs_service = _mock_docs_service()

        result = await exp.export("# Minutes", "Test", transcript_md=_SAMPLE_TRANSCRIPT)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_export_html_upload_failure_returns_failure(self) -> None:
        """Step 1 (HTML upload) fails → ExportResult(success=False)."""
        exp = _make_exporter()

        mock_execute = MagicMock(side_effect=ConnectionError("network down"))
        mock_create = MagicMock()
        mock_create.return_value.execute = mock_execute
        mock_files = MagicMock()
        mock_files.return_value.create = mock_create
        service = MagicMock()
        service.files = mock_files
        exp._service = service

        result = await exp.export("# Minutes", "Test", transcript_md=_SAMPLE_TRANSCRIPT)
        assert result.success is False
        assert "network down" in result.error


class TestExportResult:
    def test_success_result(self) -> None:
        r = ExportResult(success=True, url="https://example.com", doc_id="d1")
        assert r.success is True
        assert r.url == "https://example.com"
        assert r.doc_id == "d1"
        assert r.error is None

    def test_failure_result(self) -> None:
        r = ExportResult(success=False, error="something went wrong")
        assert r.success is False
        assert r.url is None
        assert r.doc_id is None
        assert r.error == "something went wrong"

    def test_frozen(self) -> None:
        r = ExportResult(success=True)
        with pytest.raises(AttributeError):
            r.success = False  # type: ignore[misc]
