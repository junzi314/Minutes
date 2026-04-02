# Technical Specification: Google Docs Export -- Gemini Meet 2-Tab Layout

**Feature Slug**: external-export (Phase 2: 2-tab upgrade)
**Date**: 2026-03-20
**Supersedes**: eng.md (2026-03-17, single-doc HTML upload -- now implemented)

---

## 1. Architecture Overview

### Current State (Implemented)

The exporter creates **two separate Google Docs documents** via Drive API HTML upload:
a transcript doc and a minutes doc that links to the transcript doc URL.

```
export(minutes_md, title, metadata, transcript_md)
  |
  +-- Upload transcript HTML -> standalone Google Doc (separate file)
  |
  +-- Upload minutes HTML (with link to transcript doc URL) -> Google Doc
  |
  v
ExportResult(doc_id, url)   # minutes doc only
```

**Problem**: Two separate files in Google Drive are cumbersome. Google Meet's Gemini
notes use a single document with tabs -- one for notes, one for transcript. This is
the UX we want to match.

### Target State (This Spec)

A single Google Docs document with two tabs, matching Gemini Meet's layout:

```
export(minutes_md, title, metadata, transcript_md)
|
+-- Step 1: Drive API HTML upload
|   Creates doc with default tab (t.0) containing minutes content
|   Reuse existing _md_to_html() + _upload_as_doc_sync()
|
+-- Step 2: Docs API batchUpdate
|   a. addDocumentTab -> creates "Transcript" tab, returns tabId
|   b. insertText -> writes transcript content into new tab
|   c. updateTextStyle + updateParagraphStyle -> format content
|
+-- Step 3: Update minutes tab
|   replaceAllText or findReplace to convert timestamp placeholders
|   into ?tab=t.{tabId} deep links (or pre-embed tab URL in HTML)
|
+-- Fallback: Step 2/3 failure -> single-tab doc (current behavior)
|
v
ExportResult(doc_id, url)   # single doc, two tabs
```

### Component Interaction

```
                  src/pipeline.py
                        |
            export(minutes_md, title, metadata, transcript_md)
                        |
                        v
              src/exporter.py
              GoogleDocsExporter
                        |
        +---------------+---------------+
        |               |               |
        v               v               v
  _build_drive()  _build_docs()   _md_to_html()
        |               |               |
        v               v               v
  Drive API v3    Docs API v1     markdown lib
  files.create    batchUpdate
        |               |
        +-------+-------+
                |
                v
          ExportResult
```

### Data Flow

```
minutes_md  --> _md_to_html() --> HTML --> Drive files.create --> doc_id, url
                                                                      |
transcript_md --> _transcript_to_docs_requests() --+                  |
                                                   |                  |
                   Docs API batchUpdate <----------+------ doc_id ----+
                   (addDocumentTab, insertText,
                    updateTextStyle, updateParagraphStyle)
                           |
                           v
                      tabId (e.g. "t.m15cusnl1y5v")
                           |
                           v
                   Docs API batchUpdate
                   (replaceAllText in default tab:
                    {{TRANSCRIPT_TAB_URL}} -> ?tab=t.{tabId})
```

---

## 2. API Design

### 2.1 Modified Class: `GoogleDocsExporter`

```python
class GoogleDocsExporter:
    """Export meeting minutes to Google Docs with optional transcript tab."""

    def __init__(self, cfg: ExportGoogleDocsConfig) -> None:
        self._cfg = cfg
        self._drive_service: Any = None   # Drive API v3 (existing)
        self._docs_service: Any = None    # Docs API v1 (NEW)
```

### 2.2 New Private Methods

#### `_build_docs_service() -> Any`

```python
def _build_docs_service(self) -> Any:
    """Build and cache the Google Docs API v1 service client.

    Shares credentials with Drive service. The drive.file scope is
    sufficient for Docs API operations on files created by the app.
    """
```

**Rationale**: `drive.file` scope grants both Drive and Docs API access for
files the application created. No new scopes or re-authorization needed.

#### `_add_transcript_tab_sync(doc_id: str, transcript_md: str) -> str | None`

```python
def _add_transcript_tab_sync(
    self,
    doc_id: str,
    transcript_md: str,
) -> str | None:
    """Add a transcript tab to an existing Google Docs document.

    Returns the tab ID (e.g. "t.m15cusnl1y5v") on success, None on failure.
    This method is synchronous and must be called via asyncio.to_thread().

    Steps:
      1. addDocumentTab with title "Transcript"
      2. Parse transcript_md into structured requests
      3. insertText + updateTextStyle + updateParagraphStyle
    """
```

#### `_build_transcript_requests(transcript_md: str, tab_id: str) -> list[dict]`

```python
def _build_transcript_requests(
    self,
    transcript_md: str,
    tab_id: str,
) -> list[dict]:
    """Convert transcript markdown to Google Docs API batchUpdate requests.

    Parses the transcript line-by-line:
      - "# heading"     -> insertText + updateParagraphStyle(HEADING_1)
      - "### HH:MM:SS"  -> insertText + updateParagraphStyle(HEADING_3)
      - "**Speaker:** text" -> insertText + updateTextStyle(bold) for speaker
      - "- item"         -> insertText (with bullet prefix character)
      - plain text       -> insertText + NORMAL_TEXT

    All requests are scoped to the specified tab_id.
    Returns a list of batchUpdate request dicts, ordered for reverse
    insertion (see Section 4: Offset Tracking Strategy).
    """
```

#### `_link_timestamps_to_tab_sync(doc_id: str, tab_id: str) -> None`

```python
def _link_timestamps_to_tab_sync(self, doc_id: str, tab_id: str) -> None:
    """Replace timestamp placeholders in the minutes tab with deep links.

    Uses replaceAllText to find all instances of {{TRANSCRIPT_TAB}}
    in the default tab (t.0) and replace with the tab URL parameter.
    Or, if using the pre-embed approach, this step is skipped.
    """
```

### 2.3 Modified Public Method

#### `export()` -- Updated Signature (No Change)

The existing signature already accepts `transcript_md`:

```python
async def export(
    self,
    minutes_md: str,
    title: str,
    metadata: dict[str, str] | None = None,
    transcript_md: str | None = None,
) -> ExportResult:
    """Export minutes to Google Docs with optional transcript tab.

    Behavior change:
    - OLD: transcript_md -> separate Google Doc, linked by URL
    - NEW: transcript_md -> second tab in same Google Doc

    Fallback: If tab creation fails, falls back to current behavior
    (separate transcript doc or minutes-only doc).
    """
```

### 2.4 ExportResult -- No Changes

```python
@dataclass(frozen=True)
class ExportResult:
    success: bool
    url: str | None = None
    doc_id: str | None = None
    error: str | None = None
```

---

## 3. Google Docs API Integration

### 3.1 Service Construction

```python
from googleapiclient.discovery import build

def _build_docs_service(self) -> Any:
    if self._docs_service is not None:
        return self._docs_service

    creds = self._load_oauth_credentials()
    if creds is None:
        creds = self._load_service_account_credentials()

    self._docs_service = build(
        "docs", "v1", credentials=creds, cache_discovery=False
    )
    return self._docs_service
```

**Scope verification**: The `drive.file` scope (`https://www.googleapis.com/auth/drive.file`)
is explicitly listed in the [Google Docs API OAuth scope documentation](https://developers.google.com/docs/api/reference/rest/v1/documents/batchUpdate#authorization-scopes)
as a valid scope for `documents.batchUpdate`. No additional scopes are needed.

### 3.2 addDocumentTab Request

**Request** (batchUpdate on the document):

```json
{
  "requests": [
    {
      "addDocumentTab": {
        "tabProperties": {
          "title": "Transcript"
        }
      }
    }
  ]
}
```

**Response** (nested inside `replies[0]`):

```json
{
  "replies": [
    {
      "addDocumentTab": {
        "tabProperties": {
          "tabId": "t.m15cusnl1y5v",
          "title": "Transcript",
          "index": 1
        }
      }
    }
  ],
  "documentId": "1BxR...",
  "writeControl": { "requiredRevisionId": "..." }
}
```

**Tab ID extraction**:

```python
response = docs_service.documents().batchUpdate(
    documentId=doc_id,
    body={"requests": [{"addDocumentTab": {"tabProperties": {"title": "Transcript"}}}]},
).execute()

tab_id = response["replies"][0]["addDocumentTab"]["tabProperties"]["tabId"]
# e.g. "t.m15cusnl1y5v"
```

### 3.3 insertText Request (Tab-Scoped)

All content insertion requests must include the `tabId` in the `location` field:

```json
{
  "insertText": {
    "text": "transcript line content\n",
    "location": {
      "segmentId": "",
      "index": 1,
      "tabId": "t.m15cusnl1y5v"
    }
  }
}
```

**Critical**: The `index` is a 1-based character offset within the tab's document body.
A newly created tab starts with index 1 (the implicit `\n` at position 0 is the
document preamble). See Section 8.1 for the offset tracking strategy.

### 3.4 updateTextStyle Request (Bold Speaker Names)

```json
{
  "updateTextStyle": {
    "textStyle": {
      "bold": true
    },
    "range": {
      "segmentId": "",
      "startIndex": 1,
      "endIndex": 12,
      "tabId": "t.m15cusnl1y5v"
    },
    "fields": "bold"
  }
}
```

### 3.5 updateParagraphStyle Request (Headings)

```json
{
  "updateParagraphStyle": {
    "paragraphStyle": {
      "namedStyleType": "HEADING_3"
    },
    "range": {
      "segmentId": "",
      "startIndex": 1,
      "endIndex": 10,
      "tabId": "t.m15cusnl1y5v"
    },
    "fields": "namedStyleType"
  }
}
```

**Named style types used**:

| Transcript Element | Named Style |
|--------------------|-------------|
| `# 文字起こし` | `HEADING_1` |
| `- 日時: ...` / `- 参加者: ...` | `NORMAL_TEXT` (with bullet prefix) |
| `### 00:03:00` | `HEADING_3` |
| `**Speaker:** text` | `NORMAL_TEXT` (speaker name bolded via updateTextStyle) |

### 3.6 replaceAllText Request (Timestamp Deep Links)

**Approach A: Placeholder replacement** (preferred if HTML upload preserves placeholders):

During `_md_to_html()`, timestamp references like `([12:34])` are rendered with a
placeholder URL: `href="{{TRANSCRIPT_TAB}}"`. After the transcript tab is created,
replace the placeholder:

```json
{
  "replaceAllText": {
    "containsText": {
      "text": "{{TRANSCRIPT_TAB}}",
      "matchCase": true
    },
    "replaceText": "https://docs.google.com/document/d/{doc_id}/edit?tab=t.{tab_id}",
    "tabsCriteria": {
      "tabIds": ["t.0"]
    }
  }
}
```

**Approach B: Pre-embed URL** (simpler fallback):

If `replaceAllText` does not work reliably on link targets inside the default tab,
skip deep linking entirely. Instead, the minutes footer already contains
"文字起こしを表示" -- point it to `?tab=t.{tabId}` using a Docs API `updateTextStyle`
with a `link.url` field after tab creation.

**Recommendation**: Start with Approach B (simpler, more reliable). Approach A can be
explored as an enhancement if users want inline timestamp links.

### 3.7 Full batchUpdate Sequence

The complete flow requires **three** batchUpdate calls (or two if we batch operations):

```
Call 1: addDocumentTab
  -> Extract tabId from response

Call 2: insertText + updateTextStyle + updateParagraphStyle (batched)
  -> Write transcript content into the new tab
  -> All requests in a single batchUpdate call (ordered carefully)

Call 3 (optional): replaceAllText or updateTextStyle
  -> Update minutes tab with link to transcript tab
```

**Batching optimization**: Calls 2 is a single batchUpdate with all content
insertion requests batched together. Google Docs API supports up to 200 requests
per batchUpdate call. A typical 1-hour meeting transcript has ~50-100 lines,
requiring ~100-200 requests (insertText + style updates). This fits within the
limit for most meetings. For very long meetings, split into multiple batchUpdate
calls of 200 requests each.

---

## 4. Formatting Strategy

### 4.1 Transcript Markdown Structure

The `format_transcript_markdown()` function in `src/merger.py` produces:

```markdown
# 文字起こし
- 日時: 2026-03-17 14:00
- 参加者: Alice, Bob

### 00:00:00

**Alice:** こんにちは、今日の議題について話しましょう。
**Bob:** はい、まずプロジェクトの進捗から。

### 00:03:00

**Alice:** フロントエンドの実装が完了しました。
```

### 4.2 Parsing Strategy: Line-by-Line Regex

```python
import re

_RE_H1 = re.compile(r"^# (.+)$")
_RE_H3 = re.compile(r"^### (.+)$")
_RE_SPEAKER = re.compile(r"^\*\*(.+?):\*\*\s*(.*)$")
_RE_META = re.compile(r"^- (.+)$")
```

For each line:
1. Match against patterns in priority order (H1 > H3 > speaker > meta > plain)
2. Generate the appropriate insertText request
3. Generate the corresponding style request (updateParagraphStyle or updateTextStyle)

### 4.3 Offset Tracking Strategy

Google Docs API uses character-based indexing. Each `insertText` shifts all
subsequent indices. There are two approaches:

**Approach A: Forward insertion with running offset** (chosen):

```python
offset = 1  # New tab starts at index 1

for line in transcript_lines:
    text = line + "\n"
    requests.append({
        "insertText": {
            "text": text,
            "location": {"segmentId": "", "index": offset, "tabId": tab_id},
        }
    })
    # Style requests use the range [offset, offset + len(text))
    # for paragraph style, [offset, offset + len(speaker_name)] for bold
    ...
    offset += len(text)
```

This approach inserts text sequentially, tracking the cumulative offset.
It is straightforward and produces requests in document order, making the
code easier to reason about and debug.

**Approach B: Reverse insertion** (alternative):

Insert text from the end of the document backward, so indices never shift.
More complex to implement and harder to debug; not recommended.

### 4.4 Speaker Name Bold Formatting

For a line like `**Alice:** Hello world\n`:

```python
speaker_name = "Alice:"
full_text = "Alice: Hello world\n"

# Insert the full text first
requests.append({"insertText": {"text": full_text, "location": {..., "index": offset}}})

# Bold only the speaker name portion
requests.append({
    "updateTextStyle": {
        "textStyle": {"bold": True},
        "range": {
            "startIndex": offset,
            "endIndex": offset + len(speaker_name),
            "tabId": tab_id,
        },
        "fields": "bold",
    }
})

offset += len(full_text)
```

### 4.5 Section Header Formatting

For `### 00:03:00\n`:

```python
header_text = "00:03:00\n"

requests.append({"insertText": {"text": header_text, "location": {..., "index": offset}}})
requests.append({
    "updateParagraphStyle": {
        "paragraphStyle": {"namedStyleType": "HEADING_3"},
        "range": {
            "startIndex": offset,
            "endIndex": offset + len(header_text),
            "tabId": tab_id,
        },
        "fields": "namedStyleType",
    }
})

offset += len(header_text)
```

### 4.6 Footer Styling

After all transcript content, append a disclaimer footer:

```python
footer = "\n---\nこの文字起こしはコンピュータが生成したものであり、誤りが含まれている可能性があります。\n"

requests.append({"insertText": {"text": footer, "location": {..., "index": offset}}})
requests.append({
    "updateTextStyle": {
        "textStyle": {"foregroundColor": {"color": {"rgbColor": {"red": 0.5, "green": 0.53, "blue": 0.55}}}},
        "range": {"startIndex": offset, "endIndex": offset + len(footer), "tabId": tab_id},
        "fields": "foregroundColor",
    }
})
```

---

## 5. Error Handling and Fallback

### 5.1 Fallback Hierarchy

```
Attempt 2-tab export
  |
  +-- Step 1 (Drive upload) fails
  |     -> Return ExportResult(success=False) with retry
  |
  +-- Step 1 succeeds, Step 2 (addDocumentTab) fails
  |     -> Return ExportResult(success=True) with minutes-only doc
  |     -> Log warning: "Transcript tab creation failed (non-critical)"
  |
  +-- Step 2 succeeds, Step 2 (content write) fails
  |     -> Return ExportResult(success=True) with minutes + empty tab
  |     -> Log warning: "Transcript content write failed (non-critical)"
  |
  +-- Step 3 (timestamp linking) fails
  |     -> Return ExportResult(success=True) with both tabs, no deep links
  |     -> Log warning: "Timestamp linking failed (non-critical)"
  |
  +-- All steps succeed
        -> Return ExportResult(success=True, url=..., doc_id=...)
```

**Key principle**: Steps 2 and 3 are **enhancement-only**. Their failure must never
cause the overall export to fail. The minutes document (Step 1) is the primary
deliverable. The transcript tab is a bonus.

### 5.2 Implementation

```python
async def export(
    self,
    minutes_md: str,
    title: str,
    metadata: dict[str, str] | None = None,
    transcript_md: str | None = None,
) -> ExportResult:
    # Step 1: Upload minutes as HTML (existing logic with retry)
    html = self._md_to_html(minutes_md)
    doc_id, url = None, None

    for attempt in range(1, self._cfg.max_retries + 1):
        try:
            doc_id, url = await asyncio.to_thread(
                self._upload_as_doc_sync, html, title
            )
            break
        except Exception as exc:
            # ... existing retry logic ...

    if doc_id is None:
        return ExportResult(success=False, error=last_error)

    # Step 2: Add transcript tab (non-critical)
    if transcript_md:
        try:
            tab_id = await asyncio.to_thread(
                self._add_transcript_tab_sync, doc_id, transcript_md,
            )
            if tab_id:
                logger.info("Transcript tab created: tab_id=%s", tab_id)
                # Step 3: Link timestamps (non-critical)
                try:
                    await asyncio.to_thread(
                        self._link_timestamps_to_tab_sync, doc_id, tab_id,
                    )
                except Exception:
                    logger.warning(
                        "Timestamp linking failed (non-critical)", exc_info=True,
                    )
        except Exception:
            logger.warning(
                "Transcript tab creation failed (non-critical)", exc_info=True,
            )

    return ExportResult(success=True, url=url, doc_id=doc_id)
```

### 5.3 Error Classification

| Error | Retryable | Action |
|-------|-----------|--------|
| Drive API 500/502/503 | Yes | Retry with backoff (existing) |
| Drive API 429 | Yes | Retry with backoff (existing) |
| Drive API 403 | Yes | Retry once (transient permission) |
| Drive API 400/404 | No | Fail immediately |
| Docs API 500 (tab creation) | No | Skip tab, return minutes-only doc |
| Docs API 400 (invalid request) | No | Skip tab, log details for debugging |
| Docs API 403 (scope issue) | No | Skip tab, log scope warning |
| Network timeout | Yes (Step 1) / No (Step 2-3) | Step 1 retries; Steps 2-3 skip |

---

## 6. Pipeline Integration

### 6.1 Current State (No Changes Needed)

The pipeline already passes `transcript_md` to the exporter:

```python
# src/pipeline.py, lines 211-225
if exporter is not None and cfg.export_google_docs.enabled:
    try:
        title = f"Meeting Minutes -- {date_str}"
        export_result = await exporter.export(
            minutes_md=minutes_md,
            title=title,
            metadata={"date": date_str, "speakers": speakers_str, "source": source_label},
            transcript_md=transcript_md,
        )
```

### 6.2 transcript_md Generation

The `transcript_md` variable is already populated when `cfg.poster.include_transcript`
is True (pipeline.py lines 147-151). However, for the 2-tab export, we want
`transcript_md` even when `include_transcript` is False (the user may want
a transcript tab in Google Docs without attaching it to Discord).

**Change needed**: Generate `transcript_md` when the exporter is enabled,
regardless of `poster.include_transcript`.

```python
# BEFORE (current):
transcript_md: str | None = None
if cfg.poster.include_transcript:
    transcript_md = format_transcript_markdown(transcript, date_str, speakers_str)

# AFTER:
transcript_md: str | None = None
if cfg.poster.include_transcript or (
    exporter is not None and cfg.export_google_docs.enabled
):
    transcript_md = format_transcript_markdown(transcript, date_str, speakers_str)
```

This is the **only pipeline change** required.

### 6.3 No Signature Changes

The `run_pipeline_from_tracks()` signature already accepts `exporter: object | None`.
No changes needed.

---

## 7. Testing Strategy

### 7.1 New Unit Tests: `tests/test_exporter.py`

#### Docs Service Tests

| Test | Description |
|------|-------------|
| `test_build_docs_service_caching` | Docs service built once and cached |
| `test_build_docs_service_shares_credentials` | Uses same credential loading as Drive |

#### Tab Creation Tests

| Test | Description |
|------|-------------|
| `test_add_transcript_tab_success` | Mock Docs API returns tabId, verify batchUpdate call |
| `test_add_transcript_tab_extracts_tab_id` | Verify correct extraction from response |
| `test_add_transcript_tab_api_error_returns_none` | Docs API error returns None (no raise) |

#### Transcript Request Building Tests

| Test | Description |
|------|-------------|
| `test_build_requests_heading_1` | `# 文字起こし` -> insertText + HEADING_1 |
| `test_build_requests_heading_3` | `### 00:03:00` -> insertText + HEADING_3 |
| `test_build_requests_speaker_bold` | `**Alice:** text` -> insertText + bold range |
| `test_build_requests_metadata_line` | `- 日時: ...` -> insertText (NORMAL_TEXT) |
| `test_build_requests_plain_text` | Unformatted line -> insertText (NORMAL_TEXT) |
| `test_build_requests_offset_tracking` | Verify cumulative offsets across multiple lines |
| `test_build_requests_empty_lines` | Blank lines produce insertText("\n") |
| `test_build_requests_unicode_offset` | Japanese text: offsets count characters correctly |
| `test_build_requests_full_transcript` | End-to-end with realistic transcript |
| `test_build_requests_tab_id_scoping` | All requests include the correct tabId |

#### Timestamp Linking Tests

| Test | Description |
|------|-------------|
| `test_link_timestamps_replaces_placeholder` | Verify replaceAllText or updateTextStyle call |
| `test_link_timestamps_failure_no_raise` | API error does not propagate |

#### Integration Tests (Export Flow)

| Test | Description |
|------|-------------|
| `test_export_with_transcript_creates_tab` | Full flow: upload + tab creation |
| `test_export_tab_failure_returns_success` | Tab creation fails, export still succeeds |
| `test_export_content_write_failure_returns_success` | Content write fails, export still succeeds |
| `test_export_without_transcript_no_tab` | transcript_md=None skips tab creation |
| `test_export_fallback_to_separate_doc` | If Docs API unavailable, fall back to separate doc |

### 7.2 Mock Patterns

#### Docs API Mock

```python
def _mock_docs_service(tab_id: str = "t.test123"):
    """Create a mock Google Docs API v1 service."""
    mock_execute = MagicMock(return_value={
        "replies": [{
            "addDocumentTab": {
                "tabProperties": {
                    "tabId": tab_id,
                    "title": "Transcript",
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
```

#### Combined Drive + Docs Mock

```python
def _setup_exporter_with_mocks(
    doc_id: str = "doc-123",
    url: str = "https://docs.google.com/document/d/doc-123/edit",
    tab_id: str = "t.test123",
) -> GoogleDocsExporter:
    exp = _make_exporter()
    exp._drive_service = _mock_drive_service(doc_id=doc_id, url=url)
    exp._docs_service = _mock_docs_service(tab_id=tab_id)
    return exp
```

### 7.3 Pipeline Test Additions

| Test | Description |
|------|-------------|
| `test_pipeline_generates_transcript_md_for_exporter` | transcript_md is generated when exporter is enabled, even if poster.include_transcript is False |

### 7.4 Estimated New Test Count: 18-22

---

## 8. Risk Mitigations

### 8.1 Offset Tracking Correctness

**Risk**: Off-by-one errors in character offset calculation cause garbled formatting.

**Mitigation**:
- Forward-insertion approach with a single `offset` accumulator variable
- Unit test `test_build_requests_offset_tracking` verifies offsets for a multi-line
  transcript with mixed content types
- Unit test `test_build_requests_unicode_offset` specifically tests that
  Japanese characters (which are 1 character in Python `len()` and 1 character
  in Docs API indexing) do not cause offset drift
- Helper function `_verify_offsets(requests)` can be used in debug mode to
  validate that all ranges are non-overlapping and contiguous

**Note on character counting**: Google Docs API counts characters using UTF-16
code units, which means surrogate pairs (emoji, some rare CJK characters) count
as 2. Standard Japanese (Hiragana, Katakana, CJK Unified Ideographs in BMP)
counts as 1. For safety, if the transcript contains emoji, use a UTF-16 length
function:

```python
def _utf16_len(text: str) -> int:
    """Count UTF-16 code units (Google Docs API character counting)."""
    return len(text.encode("utf-16-le")) // 2
```

### 8.2 API Rate Limits

**Risk**: batchUpdate with many requests hits rate limits or payload size limits.

**Mitigation**:
- Google Docs API allows up to 200 requests per batchUpdate call
- Google Docs API has a request body size limit of ~10 MB
- For a 1-hour meeting (~50-100 transcript lines), we need ~100-200 requests
  (1 insertText + 0-1 style updates per line). This fits in a single call.
- For meetings exceeding 200 requests, chunk into batches:
  ```python
  BATCH_SIZE = 200
  for i in range(0, len(requests), BATCH_SIZE):
      chunk = requests[i:i + BATCH_SIZE]
      docs_service.documents().batchUpdate(
          documentId=doc_id, body={"requests": chunk}
      ).execute()
  ```
- Rate limit: 300 requests per minute per user for Docs API. A single document
  export uses 2-4 API calls total, well within limits.

### 8.3 Tab ID Stability

**Risk**: Tab IDs change or become invalid between API calls.

**Mitigation**:
- Tab IDs are assigned by Google at creation time and are immutable for the
  lifetime of the document
- All operations on the tab happen within the same `_add_transcript_tab_sync()`
  call, minimizing the window for issues
- Tab ID format is `t.{random_string}` (e.g., `t.m15cusnl1y5v`)
- If the tab ID becomes invalid (unlikely), the batchUpdate call fails and we
  fall back to minutes-only (per Section 5)

### 8.4 Concurrent Access

**Risk**: Another user or process modifies the document between our API calls.

**Mitigation**:
- The document is created by our app moments before tab creation
- No sharing has occurred yet (the document is only in the app's Drive folder)
- Even if somehow modified, our batchUpdate calls operate on specific indices
  and would fail cleanly (triggering fallback)

### 8.5 Drive API HTML Upload Limitations

**Risk**: Google Drive's HTML-to-Docs conversion strips or mangles content
in the minutes tab, making `replaceAllText` fail to find placeholders.

**Mitigation**:
- Use Approach B for timestamp linking (footer link instead of inline placeholders)
  to avoid dependency on placeholder survival through HTML conversion
- The minutes tab content is already validated by existing tests
- HTML conversion has been verified in the Phase 1 PoC

### 8.6 Docs API Feature Availability

**Risk**: `addDocumentTab` is a relatively new API (added ~2024). It may have
undocumented limitations or regional availability issues.

**Mitigation**:
- The feature is documented in the official Docs API reference
- Fallback to current behavior (separate transcript doc) if the API call fails
- Feature flag at the tab-creation level: if `addDocumentTab` returns an error
  code indicating the feature is unavailable, log a one-time warning and disable
  tab creation for the session

### 8.7 Large Transcript Handling

**Risk**: Very long meetings (3+ hours) produce transcripts exceeding batchUpdate limits.

**Mitigation**:
- Chunked batchUpdate calls (Section 8.2)
- If total content exceeds 500 KB, truncate with a note:
  ```
  [... 文字起こしが長すぎるため省略されました。全文はDiscordの添付ファイルを参照してください。 ...]
  ```
- Log a warning for transcripts exceeding the threshold

---

## 9. File Changes Summary

| File | Change Type | Estimated Lines Changed |
|------|-------------|------------------------|
| `src/exporter.py` | Modify | +150 (new methods), ~20 (refactor existing) |
| `src/pipeline.py` | Modify | +3 (transcript_md generation condition) |
| `tests/test_exporter.py` | Modify | +200 (new test cases) |
| **Total** | | **~370** |

No changes to:
- `src/config.py` -- ExportGoogleDocsConfig already has all needed fields
- `src/errors.py` -- ExportError already exists
- `config.yaml` -- No new config keys needed
- `bot.py` -- Exporter wiring already in place
- `requirements.txt` -- `google-api-python-client` already installed

---

## 10. Implementation Phases

### Phase A: Docs API Service + Tab Creation (2-3 hours)

1. Add `_build_docs_service()` method
2. Add `_add_transcript_tab_sync()` method (tab creation only, no content)
3. Tests for service caching and tab creation

### Phase B: Transcript Content Writing (3-4 hours)

1. Add `_build_transcript_requests()` with line-by-line parsing
2. Add `_utf16_len()` helper
3. Integrate content writing into `_add_transcript_tab_sync()`
4. Tests for request building, offset tracking, Unicode handling

### Phase C: Integration + Fallback (2-3 hours)

1. Modify `export()` to use new tab-based flow
2. Remove old separate-transcript-doc logic (or keep as fallback)
3. Add pipeline change for transcript_md generation
4. Add `_link_timestamps_to_tab_sync()` (Approach B: footer link)
5. Integration tests and fallback tests

### Validation Gate

```bash
pytest tests/test_exporter.py -v    # All new + existing tests pass
pytest tests/test_pipeline.py -v    # No regressions
pytest -v                           # Full suite green
```

### Estimated Total: 7-10 hours

---

## 11. Rollback Plan

This change modifies the internal behavior of `GoogleDocsExporter.export()`.
The public interface is unchanged.

| Scenario | Action |
|----------|--------|
| Tab creation breaks all exports | Revert to previous exporter.py (separate-doc approach) |
| Tab creation works but content is garbled | Disable content writing, keep empty tab for manual use |
| Docs API scope issue | Revert; `drive.file` scope still works for Drive-only export |
| Performance regression | Tab creation adds ~1-2s; if unacceptable, make tab creation async/deferred |

**Zero-downtime rollback**: Since the export is already behind a feature flag
(`export_google_docs.enabled`), disabling it immediately stops all Google API
calls. Code rollback can happen independently.

---

## 12. Open Decisions

| # | Decision | Options | Recommendation | Status |
|---|----------|---------|----------------|--------|
| D1 | Timestamp deep linking approach | A: Placeholder replacement / B: Footer link only | B (footer link) -- simpler, more reliable | Recommended |
| D2 | Tab title language | "Transcript" (EN) / "文字起こし" (JA) | "Transcript" (JA) -- matches existing Gemini Meet convention for JP users | Pending |
| D3 | UTF-16 length handling | Always use `_utf16_len()` / Use `len()` with emoji guard | Always `_utf16_len()` for correctness | Recommended |
| D4 | Fallback behavior on Docs API failure | Separate transcript doc / Minutes-only doc | Minutes-only doc -- simpler, avoids confusion | Recommended |
| D5 | Rename default tab | Keep "Document" / Rename to "Notes" | Rename to "Notes" via updateDocumentTab -- matches Gemini Meet | Pending |
