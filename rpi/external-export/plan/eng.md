# Technical Specification: External Export (Google Docs)

**Feature Slug**: external-export
**Date**: 2026-03-17

---

## 1. Architecture Overview

```
src/pipeline.py (orchestrator)
  |
  | post_minutes() complete -> discord.Message returned
  | archive.store() complete
  |
  v
src/exporter.py (new module)
  |
  | GoogleDocsExporter.export(minutes_md, title, metadata)
  |   1. _md_to_html(minutes_md) -> HTML string
  |   2. _upload_as_doc(html, title, folder_id) -> (doc_id, web_url)
  |
  v
Google Drive API v3: files.create
  (mediaBody=HTML, mimeType='application/vnd.google-apps.document')
  |
  v
ExportResult(success=True, url="https://docs.google.com/...")
```

## 2. New Module: `src/exporter.py`

### 2.1 Data Types

```python
@dataclass(frozen=True)
class ExportResult:
    """Result of an export operation."""
    success: bool
    url: str | None = None
    doc_id: str | None = None
    error: str | None = None
```

### 2.2 Class: `GoogleDocsExporter`

```python
class GoogleDocsExporter:
    """Export meeting minutes to Google Docs via Google Drive HTML upload.

    Uses the Drive API v3 files.create with mimeType conversion to
    automatically convert uploaded HTML to a native Google Docs document.
    """

    def __init__(self, cfg: ExportGoogleDocsConfig) -> None:
        self._cfg = cfg
        self._service: Any = None  # googleapiclient.discovery.Resource

    def _build_service(self) -> Any:
        """Build and cache the Google Drive API v3 service client.

        Scopes: ['https://www.googleapis.com/auth/drive.file']

        Reuses the same authentication pattern as DriveWatcher._build_service()
        but with a write-capable scope.
        """
        ...

    def _md_to_html(self, minutes_md: str) -> str:
        """Convert Markdown to HTML using the `markdown` library.

        Extensions enabled:
        - tables: for Markdown table support
        - fenced_code: for code blocks (edge case)

        Returns a complete HTML document with UTF-8 charset meta tag.
        """
        ...

    def _upload_as_doc_sync(self, html: str, title: str) -> tuple[str, str]:
        """Upload HTML to Drive as a Google Docs document (synchronous).

        Uses files.create with:
        - media_body: HTML content (as MediaInMemoryUpload)
        - mimeType: 'application/vnd.google-apps.document' (triggers conversion)
        - parents: [self._cfg.folder_id]
        - fields: 'id, webViewLink'

        Returns (doc_id, web_view_link).
        Must be called via asyncio.to_thread().
        """
        ...

    async def export(
        self,
        minutes_md: str,
        title: str,
        metadata: dict[str, str] | None = None,
    ) -> ExportResult:
        """Export minutes to Google Docs with retry.

        Retry strategy: up to max_retries attempts with exponential backoff.
        Non-retryable errors (4xx except 403/429) fail immediately.

        Returns ExportResult with success status and URL on success.
        Never raises -- returns ExportResult(success=False) on failure.
        """
        ...
```

### 2.3 Key Implementation Details

#### Markdown to HTML Conversion

```python
import markdown

def _md_to_html(self, minutes_md: str) -> str:
    md = markdown.Markdown(extensions=["tables", "fenced_code"])
    body = md.convert(minutes_md)
    return (
        '<!DOCTYPE html>'
        '<html><head><meta charset="utf-8"></head>'
        f'<body>{body}</body></html>'
    )
```

The `markdown` library handles:
- `# Heading` -> `<h1>Heading</h1>` (Google converts to Heading 1 style)
- `**bold**` -> `<strong>bold</strong>` (Google converts to bold)
- `- item` -> `<ul><li>item</li></ul>` (Google converts to bulleted list)
- `- [ ] item` -> Not natively supported; remains as text `[ ]` (acceptable for Phase 1)
- Tables -> `<table>` (Google converts to Docs table)

#### Google Drive Upload

```python
from googleapiclient.http import MediaInMemoryUpload

def _upload_as_doc_sync(self, html: str, title: str) -> tuple[str, str]:
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
    result = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
    ).execute()
    return result["id"], result["webViewLink"]
```

Setting `mimeType` to `application/vnd.google-apps.document` in the file metadata tells Google Drive to convert the uploaded HTML into a native Google Docs document.

#### Retry Logic

```python
async def export(self, minutes_md, title, metadata=None) -> ExportResult:
    html = self._md_to_html(minutes_md)
    last_error = None

    for attempt in range(1, self._cfg.max_retries + 1):
        try:
            doc_id, url = await asyncio.to_thread(
                self._upload_as_doc_sync, html, title
            )
            return ExportResult(success=True, url=url, doc_id=doc_id)
        except HttpError as exc:
            last_error = str(exc)
            status = exc.resp.status
            # Non-retryable client errors (except 403 permission, 429 rate limit)
            if 400 <= status < 500 and status not in (403, 429):
                break
            if attempt < self._cfg.max_retries:
                delay = 2 ** (attempt - 1)
                await asyncio.sleep(delay)
        except Exception as exc:
            last_error = str(exc)
            if attempt < self._cfg.max_retries:
                delay = 2 ** (attempt - 1)
                await asyncio.sleep(delay)

    return ExportResult(success=False, error=last_error)
```

## 3. Configuration Changes

### 3.1 New Dataclass: `ExportGoogleDocsConfig`

Location: `src/config.py`

```python
@dataclass(frozen=True)
class ExportGoogleDocsConfig:
    enabled: bool = False
    credentials_path: str = "credentials.json"
    folder_id: str = ""
    max_retries: int = 3
```

### 3.2 New Dataclass: `ExportConfig`

```python
@dataclass(frozen=True)
class ExportConfig:
    google_docs: ExportGoogleDocsConfig = field(
        default_factory=ExportGoogleDocsConfig
    )
```

### 3.3 Config Registration

Add to `_SECTION_CLASSES`:

```python
# Note: ExportConfig uses nested dataclass, requires custom build logic
```

Add to `Config`:

```python
@dataclass(frozen=True)
class Config:
    # ... existing fields ...
    export: ExportConfig
```

### 3.4 Config YAML Schema

```yaml
export:
  google_docs:
    enabled: false
    credentials_path: "credentials.json"
    folder_id: ""
    max_retries: 3
```

### 3.5 Validation Rules

Add to `_validate()`:

```python
if cfg.export.google_docs.enabled:
    if not cfg.export.google_docs.folder_id:
        errors.append(
            "export.google_docs.folder_id is required when "
            "export.google_docs.enabled is true"
        )
    if cfg.export.google_docs.max_retries < 1:
        errors.append("export.google_docs.max_retries must be >= 1")
```

### 3.6 Custom Section Builder

The `export` section uses a nested dataclass (`google_docs` inside `export`), which differs from the flat pattern used by other sections. A custom builder is needed:

```python
def _build_export_section(yaml_section: dict) -> ExportConfig:
    gd_raw = yaml_section.get("google_docs", {}) or {}
    gd_cfg = _build_section("export_google_docs", ExportGoogleDocsConfig, gd_raw)
    return ExportConfig(google_docs=gd_cfg)
```

This keeps the env-var override pattern working: `EXPORT_GOOGLE_DOCS_ENABLED=true` overrides `export.google_docs.enabled`.

## 4. Error Hierarchy

### New Exception

Location: `src/errors.py`

```python
class ExportError(MinutesBotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, stage="export")
```

Note: `ExportError` exists for internal classification but is **never propagated** to the pipeline. The `GoogleDocsExporter.export()` method catches all exceptions and returns `ExportResult(success=False)`.

## 5. Pipeline Integration

### Location: `src/pipeline.py`, `run_pipeline_from_tracks()`

After the existing archive block (lines 146-161), add:

```python
# Export to Google Docs (fault-tolerant)
if exporter is not None and cfg.export.google_docs.enabled:
    try:
        title = f"Meeting Minutes -- {date_str}"
        export_result = await exporter.export(
            minutes_md=minutes_md,
            title=title,
            metadata={
                "date": date_str,
                "speakers": speakers_str,
                "source": source_label,
            },
        )
        if export_result.success:
            logger.info(
                "Minutes exported to Google Docs: %s",
                export_result.url,
            )
        else:
            logger.warning(
                "Google Docs export failed (non-critical): %s",
                export_result.error,
            )
    except Exception:
        logger.warning(
            "Google Docs export raised unexpected error (non-critical)",
            exc_info=True,
        )
```

### Function Signature Change

```python
async def run_pipeline_from_tracks(
    tracks: list[SpeakerAudio],
    cfg: Config,
    transcriber: Transcriber,
    generator: MinutesGenerator,
    output_channel: OutputChannel,
    state_store: StateStore,
    source_label: str = "unknown",
    template_name: str = "minutes",
    archive: MinutesArchive | None = None,
    exporter: GoogleDocsExporter | None = None,  # NEW
) -> None:
```

Similarly update `run_pipeline()` to pass through the `exporter` parameter.

### Bot Integration

In `bot.py`, instantiate the exporter alongside the archive:

```python
# Initialise Google Docs exporter
exporter: GoogleDocsExporter | None = None
if cfg.export.google_docs.enabled:
    from src.exporter import GoogleDocsExporter
    exporter = GoogleDocsExporter(cfg.export.google_docs)
    logger.info("Google Docs exporter enabled (folder_id=%s)", cfg.export.google_docs.folder_id)
```

Pass `exporter` to the `MinutesBot` constructor and through to pipeline calls.

## 6. Scopes and Permissions

### Current State

| Component | Scope | Usage |
|-----------|-------|-------|
| `DriveWatcher` | `drive.readonly` | Read files from monitored folder |

### After Implementation

| Component | Scope | Usage |
|-----------|-------|-------|
| `DriveWatcher` | `drive.readonly` | Read files from monitored folder (unchanged) |
| `GoogleDocsExporter` | `drive.file` | Create files in the export folder |

The two components use **separate service instances** with separate scopes. `drive.file` only grants access to files created by the application, so it cannot read or modify user files. This maintains least-privilege.

If the same `credentials.json` is used for both, the Service Account must have both scopes enabled in GCP IAM. However, each code path requests only the scope it needs.

## 7. Dependencies

### New: `markdown` Library

```
pip install markdown
```

- **Version**: >=3.4 (latest stable)
- **Type**: Pure Python, no C extensions
- **Size**: ~300 KB installed
- **License**: BSD
- **Security**: No known CVEs in the past 3 years
- **Docker impact**: Negligible (< 1 MB layer)

Add to `requirements.txt`:
```
markdown>=3.4
```

### Existing (No Changes)

- `google-api-python-client` -- already in requirements.txt
- `google-auth` -- already in requirements.txt

## 8. Testing Strategy

### Unit Tests: `tests/test_exporter.py`

| Test | Description |
|------|-------------|
| `test_md_to_html_headings` | Verify `#`, `##`, `###` convert to `<h1>`, `<h2>`, `<h3>` |
| `test_md_to_html_bold` | Verify `**text**` converts to `<strong>text</strong>` |
| `test_md_to_html_lists` | Verify `- item` converts to `<ul><li>` |
| `test_md_to_html_tables` | Verify Markdown tables convert to `<table>` |
| `test_md_to_html_japanese` | Verify Japanese text in headings/lists is preserved |
| `test_md_to_html_full_minutes` | End-to-end with realistic minutes Markdown |
| `test_export_success` | Mock Drive API, verify `files.create` call and ExportResult |
| `test_export_retry_on_500` | Mock 500 error, verify retry with backoff |
| `test_export_no_retry_on_400` | Mock 400 error, verify immediate failure |
| `test_export_retry_on_403` | Mock 403, verify retry (permission may be transient) |
| `test_export_max_retries_exhausted` | Verify ExportResult(success=False) after max retries |
| `test_export_never_raises` | Verify no exception propagates even on unexpected error |
| `test_build_service_missing_creds` | Verify ExportError on missing credentials file |
| `test_build_service_caching` | Verify service object is built once and cached |

### Config Tests: `tests/test_config.py` (additions)

| Test | Description |
|------|-------------|
| `test_export_config_defaults` | Verify `enabled=False`, `max_retries=3` defaults |
| `test_export_config_from_yaml` | Load config with export section enabled |
| `test_export_validation_missing_folder` | Enabled without folder_id triggers error |
| `test_export_config_env_override` | `EXPORT_GOOGLE_DOCS_ENABLED=true` overrides YAML |

### Pipeline Tests: `tests/test_pipeline.py` (additions)

| Test | Description |
|------|-------------|
| `test_pipeline_with_export_success` | Export runs after post and archive |
| `test_pipeline_export_failure_nonfatal` | Pipeline completes despite export failure |
| `test_pipeline_export_disabled` | Export is skipped when config disabled |

### Estimated Test Count: 15-20 new tests

## 9. File Changes Summary

| File | Change Type | Estimated Lines |
|------|-------------|-----------------|
| `src/exporter.py` | **New** | ~120 |
| `src/config.py` | Modify | +25 |
| `src/errors.py` | Modify | +5 |
| `src/pipeline.py` | Modify | +25 |
| `bot.py` | Modify | +15 |
| `config.yaml` | Modify | +10 |
| `requirements.txt` | Modify | +1 |
| `tests/test_exporter.py` | **New** | ~150 |
| `tests/test_config.py` | Modify | +30 |
| `tests/test_pipeline.py` | Modify | +40 |
| **Total** | | **~420** |

## 10. API Reference

### Google Drive API v3: files.create

```
POST https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart

Request body (metadata part):
{
  "name": "Meeting Minutes -- 2026-03-17 14:00",
  "mimeType": "application/vnd.google-apps.document",
  "parents": ["FOLDER_ID"]
}

Request body (media part):
Content-Type: text/html
<html>...</html>

Response:
{
  "id": "1BxR...",
  "webViewLink": "https://docs.google.com/document/d/1BxR.../edit"
}
```

### Rate Limits

- Google Drive API: 20,000 queries per 100 seconds per project
- `files.create`: counted as 1 query per call
- Expected usage: 1-5 calls per day (one per meeting)

## 11. Rollback Plan

The feature is entirely opt-in (`export.google_docs.enabled: false` by default):

1. **Disable**: Set `export.google_docs.enabled: false` in config.yaml, restart bot
2. **Remove code**: Revert the commits (no schema migrations, no state changes)
3. **No data loss**: Export creates new documents in Drive; no existing data is modified
4. **No breaking changes**: Pipeline function signatures use `exporter=None` default
