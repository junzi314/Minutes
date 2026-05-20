"""Export meeting minutes to Google Docs via Google Drive HTML upload."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import ExportGoogleDocsConfig
from src.errors import ExportError

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

TRANSCRIPT_TAB_PLACEHOLDER = "__TRANSCRIPT_TAB_URL__"


@dataclass(frozen=True)
class ExportResult:
    """Result of an export operation."""

    success: bool
    url: str | None = None
    doc_id: str | None = None
    error: str | None = None


class GoogleDocsExporter:
    """Export meeting minutes to Google Docs via Google Drive HTML upload."""

    def __init__(self, cfg: ExportGoogleDocsConfig) -> None:
        self._cfg = cfg
        self._service: Any = None
        self._docs_service: Any = None

    def _build_service(self) -> Any:
        """Build and cache the Google Drive API v3 service client.

        Uses OAuth2 user credentials (token.json) if available, falling back
        to service account credentials.  OAuth2 avoids the zero-quota issue
        that service accounts have on personal Google accounts.
        """
        if self._service is not None:
            return self._service

        from googleapiclient.discovery import build

        creds = self._load_oauth_credentials()
        if creds is None:
            creds = self._load_service_account_credentials()

        self._service = build(
            "drive", "v3", credentials=creds, cache_discovery=False
        )
        return self._service

    def _build_docs_service(self) -> Any:
        """Build and cache the Google Docs API v1 service client.

        Reuses the same OAuth2/SA credential loading as _build_service().
        drive.file scope is sufficient for Docs API operations on owned files.
        """
        if self._docs_service is not None:
            return self._docs_service

        from googleapiclient.discovery import build

        creds = self._load_oauth_credentials()
        if creds is None:
            creds = self._load_service_account_credentials()

        self._docs_service = build(
            "docs", "v1", credentials=creds, cache_discovery=False
        )
        return self._docs_service

    def _load_oauth_credentials(self) -> Any:
        """Load OAuth2 user credentials from token.json."""
        token_path = Path(self._cfg.oauth_token_path)
        if not token_path.exists():
            return None

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials

            creds = Credentials.from_authorized_user_file(
                str(token_path), _SCOPES
            )
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token_path.write_text(creds.to_json())
                logger.debug("OAuth token refreshed")
            return creds
        except Exception as exc:
            logger.warning("Failed to load OAuth credentials: %s", exc)
            return None

    def _load_service_account_credentials(self) -> Any:
        """Load service account credentials (fallback)."""
        creds_path = Path(self._cfg.credentials_path)
        if not creds_path.exists():
            raise ExportError(f"Credentials file not found: {creds_path}")

        from google.oauth2.service_account import Credentials

        return Credentials.from_service_account_file(
            str(creds_path), scopes=_SCOPES
        )

    def _md_to_html(
        self,
        minutes_md: str,
        transcript_doc_url: str | None = None,
    ) -> str:
        """Convert Markdown to styled HTML mimicking Google Meet Gemini notes.

        Google Docs ignores <style> tags, so all styling is applied inline.
        The output format mirrors Google Meet's "Take notes for me" feature.
        If *transcript_doc_url* is provided, timestamp references in the minutes
        become clickable links pointing to the transcript document.
        """
        import re

        import markdown

        md = markdown.Markdown(extensions=["tables", "fenced_code"])
        body = md.convert(minutes_md)

        # --- Inline styles (Google Docs ignores <style> blocks) ---

        body = body.replace(
            "<h1>",
            '<h1 style="font-size:22pt;color:#202124;font-weight:normal;'
            'margin-top:30px;margin-bottom:4px;">',
        )
        body = body.replace(
            "<h2>",
            '<h2 style="font-size:16pt;color:#202124;font-weight:normal;'
            'margin-top:26px;margin-bottom:8px;">',
        )
        body = body.replace(
            "<h3>",
            '<h3 style="font-size:12pt;color:#202124;font-weight:bold;'
            'margin-top:18px;margin-bottom:6px;">',
        )
        body = body.replace(
            "<li>",
            '<li style="margin-bottom:10px;line-height:1.6;">',
        )
        body = body.replace(
            "<p>",
            '<p style="line-height:1.6;margin-bottom:8px;color:#3c4043;">',
        )
        body = body.replace(
            "<table>",
            '<table style="border-collapse:collapse;width:100%;margin:12px 0;">',
        )
        body = body.replace(
            "<th>",
            '<th style="border:1px solid #dadce0;padding:8px 12px;'
            'background-color:#f1f3f4;font-weight:bold;">',
        )
        body = body.replace(
            "<td>",
            '<td style="border:1px solid #dadce0;padding:8px 12px;">',
        )

        # Checkbox conversion
        body = body.replace("[ ]", "\u2610")
        body = body.replace("[x]", "\u2611")

        # Convert timestamp references ([MM:SS]) to links to transcript doc
        if transcript_doc_url:
            def _link_timestamp(m: re.Match) -> str:
                ts = m.group(1)
                return (
                    f'(<a href="{transcript_doc_url}" '
                    f'style="color:#1a73e8;font-size:9pt;text-decoration:none;">'
                    f"[{ts}]</a>)"
                )

            body = re.sub(
                r"\(\[([\d:]+(?:\]-\[[\d:]+)?)\]\)",
                _link_timestamp,
                body,
            )
        else:
            # No transcript doc — just style timestamps as blue text
            body = re.sub(
                r"\(\[([\d:]+(?:\]-\[[\d:]+)?)\]\)",
                r'<span style="color:#1a73e8;font-size:9pt;">([\1])</span>',
                body,
            )

        # Add transcript link if available
        transcript_link = ""
        if transcript_doc_url:
            transcript_link = (
                '<p style="margin-top:20px;">'
                f'<a href="{transcript_doc_url}" '
                'style="color:#1a73e8;text-decoration:none;font-size:10pt;">'
                '\U0001f4d6 文字起こしを表示</a></p>'
            )

        # Add footer attribution
        footer = (
            f"{transcript_link}"
            '<hr style="border:none;border-top:1px solid #dadce0;margin-top:30px;">'
            '<p style="color:#80868b;font-size:9pt;line-height:1.4;">'
            'Discord Minutes Bot による自動生成議事録です。'
            ' 内容の正確性を確認してください。</p>'
        )

        return (
            "<!DOCTYPE html>"
            '<html><head><meta charset="utf-8"></head>'
            '<body style="font-family:\'Google Sans\',\'Noto Sans JP\',sans-serif;'
            f'font-size:11pt;color:#202124;line-height:1.6;">{body}{footer}'
            "</body></html>"
        )

    def _upload_as_doc_sync(self, html: str, title: str) -> tuple[str, str]:
        """Upload HTML to Drive as a Google Docs document (synchronous)."""
        from googleapiclient.http import MediaInMemoryUpload

        service = self._build_service()
        media = MediaInMemoryUpload(
            html.encode("utf-8"),
            mimetype="text/html",
            resumable=False,
        )
        file_metadata = {
            "name": title,
            "mimeType": "application/vnd.google-apps.document",
            "parents": [self._cfg.folder_id],
        }
        result = (
            service.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id, webViewLink",
            )
            .execute()
        )
        return result["id"], result["webViewLink"]

    # ------------------------------------------------------------------
    # Docs API tab methods (Phase 2: 2-tab Gemini format)
    # ------------------------------------------------------------------

    @staticmethod
    def _utf16_len(text: str) -> int:
        """Count UTF-16 code units (Google Docs API character counting)."""
        return len(text.encode("utf-16-le")) // 2

    def _add_transcript_tab_sync(self, doc_id: str) -> str:
        """Add a transcript tab to the document. Returns the new tabId."""
        docs_service = self._build_docs_service()
        result = docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={
                "requests": [
                    {
                        "addDocumentTab": {
                            "tabProperties": {
                                "title": "\U0001f4d6 文字起こし",
                            }
                        }
                    }
                ]
            },
        ).execute()
        return result["replies"][0]["addDocumentTab"]["tabProperties"]["tabId"]

    @staticmethod
    def _normalize_timestamp(ts: str) -> str:
        """Convert MM:SS or M:SS to HH:MM:SS for matching tab-2 headings."""
        parts = ts.split(":")
        if len(parts) == 2:
            return f"00:{parts[0].zfill(2)}:{parts[1].zfill(2)}"
        return ":".join(p.zfill(2) for p in parts)

    @staticmethod
    def _ts_to_seconds(ts: str) -> int:
        """Parse MM:SS or HH:MM:SS into total seconds."""
        parts = [int(p) for p in ts.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0] * 3600 + parts[1] * 60 + parts[2]

    def _build_transcript_requests(
        self, transcript_md: str, tab_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[int, int]]]:
        """Convert transcript markdown to Google Docs API batchUpdate requests.

        Parses line-by-line:
          - ``# heading``        -> insertText + HEADING_1
          - ``### HH:MM:SS``     -> insertText + HEADING_3
          - ``**Speaker:** text`` -> insertText + bold for speaker name
          - ``- metadata``       -> insertText (NORMAL_TEXT)
          - plain text / blank   -> insertText (NORMAL_TEXT)

        Returns (requests, heading_offsets) where heading_offsets maps
        ``HH:MM:SS`` timestamp strings to ``(start_index, end_index)`` tuples
        (excluding the trailing newline) for use with createNamedRange.
        """
        re_h1 = re.compile(r"^# (.+)$")
        re_h3 = re.compile(r"^### (.+)$")
        re_speaker = re.compile(r"^\*\*(.+?):\*\*\s*(.*)$")

        requests: list[dict[str, Any]] = []
        heading_offsets: dict[str, tuple[int, int]] = {}
        offset = 1  # New tab starts at index 1

        lines = transcript_md.splitlines()
        for line in lines:
            m_h1 = re_h1.match(line)
            m_h3 = re_h3.match(line)
            m_speaker = re_speaker.match(line)

            if m_h1:
                text = m_h1.group(1) + "\n"
                text_len = self._utf16_len(text)
                requests.append({
                    "insertText": {
                        "text": text,
                        "location": {"segmentId": "", "index": offset, "tabId": tab_id},
                    }
                })
                requests.append({
                    "updateParagraphStyle": {
                        "paragraphStyle": {"namedStyleType": "HEADING_1"},
                        "range": {
                            "segmentId": "", "startIndex": offset,
                            "endIndex": offset + text_len, "tabId": tab_id,
                        },
                        "fields": "namedStyleType",
                    }
                })
                offset += text_len

            elif m_h3:
                ts_text = m_h3.group(1)
                text = ts_text + "\n"
                text_len = self._utf16_len(text)
                heading_offsets[ts_text] = (offset, offset + text_len - 1)
                requests.append({
                    "insertText": {
                        "text": text,
                        "location": {"segmentId": "", "index": offset, "tabId": tab_id},
                    }
                })
                requests.append({
                    "updateParagraphStyle": {
                        "paragraphStyle": {"namedStyleType": "HEADING_3"},
                        "range": {
                            "segmentId": "", "startIndex": offset,
                            "endIndex": offset + text_len, "tabId": tab_id,
                        },
                        "fields": "namedStyleType",
                    }
                })
                offset += text_len

            elif m_speaker:
                speaker = m_speaker.group(1) + ": "
                spoken_text = m_speaker.group(2)
                full_text = speaker + spoken_text + "\n"
                text_len = self._utf16_len(full_text)
                speaker_len = self._utf16_len(speaker)
                requests.append({
                    "insertText": {
                        "text": full_text,
                        "location": {"segmentId": "", "index": offset, "tabId": tab_id},
                    }
                })
                requests.append({
                    "updateTextStyle": {
                        "textStyle": {"bold": True},
                        "range": {
                            "segmentId": "", "startIndex": offset,
                            "endIndex": offset + speaker_len, "tabId": tab_id,
                        },
                        "fields": "bold",
                    }
                })
                offset += text_len

            else:
                # Plain text, metadata lines (- 日時: ...), or blank lines
                text = line + "\n" if line else "\n"
                text_len = self._utf16_len(text)
                requests.append({
                    "insertText": {
                        "text": text,
                        "location": {"segmentId": "", "index": offset, "tabId": tab_id},
                    }
                })
                offset += text_len

        # Footer disclaimer
        footer = (
            "\n---\n"
            "この文字起こしはコンピュータが生成したものであり、"
            "誤りが含まれている可能性があります。\n"
        )
        footer_len = self._utf16_len(footer)
        requests.append({
            "insertText": {
                "text": footer,
                "location": {"segmentId": "", "index": offset, "tabId": tab_id},
            }
        })
        requests.append({
            "updateTextStyle": {
                "textStyle": {
                    "italic": True,
                    "foregroundColor": {
                        "color": {
                            "rgbColor": {"red": 0.502, "green": 0.525, "blue": 0.545}
                        }
                    },
                    "fontSize": {"magnitude": 9, "unit": "PT"},
                },
                "range": {
                    "segmentId": "", "startIndex": offset,
                    "endIndex": offset + footer_len, "tabId": tab_id,
                },
                "fields": "italic,foregroundColor,fontSize",
            }
        })

        return requests, heading_offsets

    def _write_transcript_content_sync(
        self, doc_id: str, tab_id: str, transcript_md: str,
    ) -> dict[str, tuple[int, int]]:
        """Write formatted transcript content into the specified tab.

        Returns heading_offsets mapping HH:MM:SS timestamp strings to
        (start_index, end_index) for subsequent named range creation.
        """
        requests, heading_offsets = self._build_transcript_requests(transcript_md, tab_id)
        if not requests:
            return {}

        # Google Docs API allows up to 200 requests per batchUpdate
        batch_size = 200
        docs_service = self._build_docs_service()
        for i in range(0, len(requests), batch_size):
            chunk = requests[i : i + batch_size]
            docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": chunk},
            ).execute()
        return heading_offsets

    def _fetch_heading_ids_sync(
        self, doc_id: str, tab_id: str,
    ) -> dict[str, str]:
        """Read back the transcript tab and extract heading IDs.

        Google Docs auto-assigns a ``headingId`` (e.g. ``h.xxxxx``) to each
        heading paragraph.  That ID is what URL fragments like
        ``#heading=h.xxxxx`` navigate to — named ranges are *not*
        addressable via URL.

        Returns a dict mapping heading text (e.g. ``00:01:24``) to
        ``headingId``.
        """
        docs_service = self._build_docs_service()
        doc = docs_service.documents().get(
            documentId=doc_id,
            includeTabsContent=True,
        ).execute()

        result: dict[str, str] = {}
        for tab in doc.get("tabs", []):
            if tab.get("tabProperties", {}).get("tabId") != tab_id:
                continue
            body_content = (
                tab.get("documentTab", {}).get("body", {}).get("content", [])
            )
            for element in body_content:
                paragraph = element.get("paragraph")
                if not paragraph:
                    continue
                style = paragraph.get("paragraphStyle", {})
                if style.get("namedStyleType", "") != "HEADING_3":
                    continue
                heading_id = style.get("headingId")
                if not heading_id:
                    continue
                text = "".join(
                    pe.get("textRun", {}).get("content", "")
                    for pe in paragraph.get("elements", [])
                ).strip()
                if text:
                    result[text] = heading_id
            break
        logger.debug("Fetched %d heading IDs from transcript tab", len(result))
        return result

    def _update_timestamp_links_sync(
        self,
        doc_id: str,
        tab_id: str,
        doc_url: str,
        heading_ids: dict[str, str] | None = None,
    ) -> None:
        """Add transcript tab links to timestamp text in the memo tab.

        Reads the memo tab (t.0), finds timestamp patterns like ``[01:29]``,
        and adds a hyperlink to the transcript tab using ``updateTextStyle``.
        When *heading_ids* is provided (mapping ``HH:MM:SS`` → ``h.xxx``),
        each timestamp is linked to the nearest preceding heading (floor
        match) via ``#heading=h.xxx``.  Exact matches resolve directly;
        floor match handles any off-boundary references.
        """
        # Build tab URL: strip existing query params, add ?tab=
        base_url = doc_url.split("?")[0]
        target_url = f"{base_url}?tab={tab_id}"

        # Pre-sort heading IDs by time for floor-match lookup
        sorted_headings: list[tuple[int, str]] = []
        if heading_ids:
            sorted_headings = sorted(
                (self._ts_to_seconds(ts), hid)
                for ts, hid in heading_ids.items()
            )

        docs_service = self._build_docs_service()

        # Read the memo tab content to find timestamp positions
        doc = docs_service.documents().get(
            documentId=doc_id,
            includeTabsContent=True,
        ).execute()

        # Find the default tab (t.0) body content
        tabs = doc.get("tabs", [])
        body_content = None
        for tab in tabs:
            props = tab.get("tabProperties", {})
            if props.get("tabId") == "t.0":
                body_content = tab.get("documentTab", {}).get("body", {}).get("content", [])
                break

        if not body_content:
            logger.warning("Could not read memo tab content for link update")
            return

        # Extract full text with character offsets
        ts_pattern = re.compile(r"\[([\d:]+)\]")
        requests: list[dict[str, Any]] = []

        for element in body_content:
            paragraph = element.get("paragraph")
            if not paragraph:
                continue
            for pe in paragraph.get("elements", []):
                text_run = pe.get("textRun")
                if not text_run:
                    continue
                text = text_run.get("content", "")
                start_idx = pe.get("startIndex", 0)
                for m in ts_pattern.finditer(text):
                    abs_start = start_idx + m.start()
                    abs_end = start_idx + m.end()

                    # Floor-match to nearest preceding heading
                    ts_secs = self._ts_to_seconds(m.group(1))
                    heading_id: str | None = None
                    for sec, hid in sorted_headings:
                        if sec <= ts_secs:
                            heading_id = hid
                        else:
                            break
                    if heading_id:
                        link_url = f"{base_url}?tab={tab_id}#heading={heading_id}"
                    else:
                        link_url = target_url

                    requests.append({
                        "updateTextStyle": {
                            "textStyle": {
                                "link": {"url": link_url},
                            },
                            "range": {
                                "segmentId": "",
                                "startIndex": abs_start,
                                "endIndex": abs_end,
                                "tabId": "t.0",
                            },
                            "fields": "link",
                        }
                    })

        if not requests:
            logger.info("No timestamps found in memo tab to link")
            return

        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests},
        ).execute()
        logger.info("Linked %d timestamps to transcript tab", len(requests))

    def _convert_checkboxes_sync(self, doc_id: str) -> None:
        """Convert Unicode checkbox characters (☐/☑) to native Google Docs checklists.

        Reads the memo tab, finds paragraphs containing ☐ or ☑, applies
        BULLET_CHECKBOX preset, and removes the Unicode characters.
        """
        docs_service = self._build_docs_service()
        doc = docs_service.documents().get(
            documentId=doc_id,
            includeTabsContent=True,
        ).execute()

        # Find the default tab (t.0)
        tabs = doc.get("tabs", [])
        body_content = None
        for tab in tabs:
            if tab.get("tabProperties", {}).get("tabId") == "t.0":
                body_content = tab.get("documentTab", {}).get("body", {}).get("content", [])
                break

        if not body_content:
            return

        checkbox_ranges: list[tuple[int, int]] = []  # (para_start, para_end)
        delete_ranges: list[tuple[int, int]] = []     # (char_start, char_end)

        for element in body_content:
            paragraph = element.get("paragraph")
            if not paragraph:
                continue
            para_start = element.get("startIndex", 0)
            para_end = element.get("endIndex", para_start)

            for pe in paragraph.get("elements", []):
                text_run = pe.get("textRun")
                if not text_run:
                    continue
                text = text_run.get("content", "")
                start_idx = pe.get("startIndex", 0)

                for i, ch in enumerate(text):
                    if ch in ("\u2610", "\u2611"):  # ☐ or ☑
                        checkbox_ranges.append((para_start, para_end))
                        # Delete the checkbox char (and trailing space if any)
                        del_end = start_idx + i + 1
                        if i + 1 < len(text) and text[i + 1] == " ":
                            del_end += 1
                        delete_ranges.append((start_idx + i, del_end))

        if not checkbox_ranges:
            return

        # Build requests: first apply checkbox bullets, then delete chars
        # Process deletions in reverse order to maintain valid indices
        requests: list[dict[str, Any]] = []

        # Apply checkbox bullet to each paragraph
        seen_ranges = set()
        for para_start, para_end in checkbox_ranges:
            if (para_start, para_end) in seen_ranges:
                continue
            seen_ranges.add((para_start, para_end))
            requests.append({
                "createParagraphBullets": {
                    "range": {
                        "segmentId": "",
                        "startIndex": para_start,
                        "endIndex": para_end,
                        "tabId": "t.0",
                    },
                    "bulletPreset": "BULLET_CHECKBOX",
                }
            })

        # Delete Unicode checkbox chars (reverse order to preserve indices)
        for del_start, del_end in sorted(delete_ranges, reverse=True):
            requests.append({
                "deleteContentRange": {
                    "range": {
                        "segmentId": "",
                        "startIndex": del_start,
                        "endIndex": del_end,
                        "tabId": "t.0",
                    }
                }
            })

        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests},
        ).execute()
        logger.info("Converted %d checkboxes to native checklists", len(delete_ranges))

    async def export(
        self,
        minutes_md: str,
        title: str,
        metadata: dict[str, str] | None = None,
        transcript_md: str | None = None,
    ) -> ExportResult:
        """Export minutes to Google Docs with optional transcript tab.

        Hybrid approach:
          Step 1: Drive API HTML upload → memo tab (default t.0)
          Step 2: Docs API addDocumentTab → 文字起こし tab
          Step 3: Docs API insertText + styles → transcript content
          Fallback: Steps 2-3 fail → memo-only doc (current behavior)

        Never raises — returns ExportResult.
        """
        # Step 1: Upload minutes as HTML (existing logic with retry)
        # Timestamps are styled as blue text; links added by Docs API after tab creation
        html = self._md_to_html(minutes_md)
        last_error = None
        doc_id: str | None = None
        url: str | None = None

        for attempt in range(1, self._cfg.max_retries + 1):
            try:
                t0 = time.monotonic()
                doc_id, url = await asyncio.to_thread(
                    self._upload_as_doc_sync, html, title
                )
                elapsed = time.monotonic() - t0
                logger.info(
                    "Minutes exported to Google Docs in %.1fs: %s",
                    elapsed,
                    url,
                )
                break
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "Export attempt %d/%d failed: %s",
                    attempt,
                    self._cfg.max_retries,
                    exc,
                )
                if hasattr(exc, "resp") and hasattr(exc.resp, "status"):
                    status = exc.resp.status
                    if 400 <= status < 500 and status not in (403, 429):
                        break
                if attempt < self._cfg.max_retries:
                    delay = 2 ** (attempt - 1)
                    await asyncio.sleep(delay)

        if doc_id is None:
            logger.error(
                "Export failed after %d attempts: %s",
                self._cfg.max_retries,
                last_error,
            )
            return ExportResult(success=False, error=last_error)

        # Steps 2-4: Add transcript tab + content + links (non-critical)
        if not transcript_md:
            logger.info("No transcript_md provided; skipping tab creation")
        else:
            tab_created = False
            try:
                tab_id = await asyncio.to_thread(
                    self._add_transcript_tab_sync, doc_id,
                )
                tab_created = True

                await asyncio.to_thread(
                    self._write_transcript_content_sync,
                    doc_id, tab_id, transcript_md,
                )

                heading_ids: dict[str, str] = {}
                try:
                    heading_ids = await asyncio.to_thread(
                        self._fetch_heading_ids_sync, doc_id, tab_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "Heading ID fetch failed (non-critical): %s", exc
                    )

                await asyncio.to_thread(
                    self._update_timestamp_links_sync,
                    doc_id, tab_id, url, heading_ids or None,
                )
                # Convert Unicode checkboxes to native Google Docs checklists
                try:
                    await asyncio.to_thread(
                        self._convert_checkboxes_sync, doc_id,
                    )
                except Exception as exc:
                    logger.warning("Checkbox conversion failed (non-critical): %s", exc)

                logger.info(
                    "2-tab doc created: doc=%s memo=t.0 transcript=%s",
                    doc_id, tab_id,
                )
            except Exception as exc:
                if tab_created:
                    logger.warning(
                        "Transcript tab added but content/links failed: %s", exc,
                    )
                else:
                    logger.warning(
                        "Transcript tab creation failed, memo-only doc: %s", exc,
                    )

        return ExportResult(success=True, url=url, doc_id=doc_id)
