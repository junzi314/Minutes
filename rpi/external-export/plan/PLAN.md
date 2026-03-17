# Implementation Plan: External Export (Google Docs)

**Feature Slug**: external-export
**Date**: 2026-03-17
**Estimated Total Effort**: 8-12 hours (PoC + 3 phases)
**Breaking Changes**: None
**Feature Flag**: `export.google_docs.enabled` (default: `false`)

---

## Prerequisites (Before Implementation)

### Condition C1: PoC -- HTML-to-Docs Conversion Quality

**Effort**: 2-3 hours
**Blocking**: Yes (must pass before Phase 1)

Write a standalone Python script that:

1. Takes a realistic minutes Markdown sample (copy from an actual generated `.md` file or use the prompt template structure)
2. Converts it to HTML using the `markdown` library with `tables` and `fenced_code` extensions
3. Uploads the HTML to Google Drive with `mimeType='application/vnd.google-apps.document'`
4. Opens the resulting Google Docs URL and manually inspects:
   - Japanese headings render as Heading 1 / Heading 2 styles
   - Bulleted lists render as Google Docs bullet lists
   - Bold text renders as bold
   - Tables render as Google Docs tables (if present)
   - `- [ ]` checkbox items render acceptably (plain text is acceptable)
   - Timestamps like `[12:34]` are preserved as plain text

**GO criteria**: Headings, lists, and bold text convert correctly. Tables and checkboxes are acceptable as plain text or HTML fallback.
**NO-GO action**: Fall back to Google Docs API `batchUpdate` approach (adds ~4 hours of extra implementation) or DEFER the feature.

### Condition C2: Service Account Scope Verification

**Effort**: 30 minutes
**Blocking**: Yes

1. In GCP Console, navigate to the Service Account used by `credentials.json`
2. Verify that `drive.file` scope can be added (or is already available via domain-wide delegation)
3. Enable the Google Drive API in the GCP project (may already be enabled for Drive watcher)
4. Test that the Service Account can create a file in a shared folder

**GO criteria**: Service Account can create files with `drive.file` scope.

### Condition C3: Dependency Audit

**Effort**: 15 minutes
**Blocking**: No (recommended)

1. `pip install markdown` in the development virtualenv
2. Check version and license: `pip show markdown`
3. Verify Docker image build with the new dependency
4. Check for known CVEs: `pip audit` (if available)

---

## Phase 1: Core Module (`src/exporter.py`)

**Effort**: 2-3 hours
**Dependencies**: PoC (C1) passed, C2 confirmed
**Deliverables**: `src/exporter.py`, `tests/test_exporter.py`

### Task 1.1: Add `ExportError` to Error Hierarchy

**File**: `src/errors.py`
**Lines**: +5

Add `ExportError` exception class following the existing pattern:

```python
class ExportError(MinutesBotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, stage="export")
```

### Task 1.2: Create `ExportResult` Dataclass and `GoogleDocsExporter` Class

**File**: `src/exporter.py` (new)
**Lines**: ~120

Implement the following:

1. `ExportResult` frozen dataclass with `success`, `url`, `doc_id`, `error` fields
2. `GoogleDocsExporter.__init__(cfg)` -- stores config, initializes service cache
3. `GoogleDocsExporter._build_service()` -- builds Google Drive API v3 client with `drive.file` scope, caches on instance (same pattern as `DriveWatcher._build_service()`)
4. `GoogleDocsExporter._md_to_html(minutes_md)` -- converts Markdown to HTML with `markdown` library (extensions: `tables`, `fenced_code`), wraps in `<!DOCTYPE html>` with UTF-8 meta
5. `GoogleDocsExporter._upload_as_doc_sync(html, title)` -- synchronous Drive API `files.create` with HTML media body and Google Docs mime type conversion; returns `(doc_id, webViewLink)`
6. `GoogleDocsExporter.export(minutes_md, title, metadata)` -- async entry point with retry logic, calls `_md_to_html` then `asyncio.to_thread(_upload_as_doc_sync)`, returns `ExportResult`

Key design decisions:
- `export()` **never raises** -- all errors are caught and returned as `ExportResult(success=False)`
- Retry on 5xx, 403, 429; immediate failure on other 4xx
- Exponential backoff: 1s, 2s, 4s

### Task 1.3: Write Unit Tests for Exporter

**File**: `tests/test_exporter.py` (new)
**Lines**: ~150

Test categories:
- **HTML conversion tests** (no mocking needed): headings, bold, lists, tables, Japanese text, full minutes sample
- **Export success/failure tests** (mock `googleapiclient`): successful upload, retry on 500, no retry on 400, retry on 403/429, max retries exhausted, never-raises guarantee
- **Service build tests** (mock credentials): missing credentials file, caching behavior

### Validation Gate 1

```
pytest tests/test_exporter.py -v
```

All tests pass. The exporter module works in isolation with mocked Google API.

---

## Phase 2: Configuration and Validation

**Effort**: 1-2 hours
**Dependencies**: Phase 1 complete
**Deliverables**: Config changes, config tests

### Task 2.1: Add `ExportGoogleDocsConfig` and `ExportConfig` Dataclasses

**File**: `src/config.py`
**Lines**: +15

```python
@dataclass(frozen=True)
class ExportGoogleDocsConfig:
    enabled: bool = False
    credentials_path: str = "credentials.json"
    folder_id: str = ""
    max_retries: int = 3

@dataclass(frozen=True)
class ExportConfig:
    google_docs: ExportGoogleDocsConfig = field(
        default_factory=ExportGoogleDocsConfig
    )
```

### Task 2.2: Register `export` Section in Config Loader

**File**: `src/config.py`
**Lines**: +15

The `export` section uses a nested dataclass, which requires a custom builder function (similar to `_build_discord_section`):

```python
def _build_export_section(yaml_section: dict) -> ExportConfig:
    gd_raw = yaml_section.get("google_docs", {}) or {}
    gd_cfg = _build_section("export_google_docs", ExportGoogleDocsConfig, gd_raw)
    return ExportConfig(google_docs=gd_cfg)
```

Add to the `load()` function after standard sections are built, before assembling the top-level `Config`.

Update the `Config` dataclass to include `export: ExportConfig`.

### Task 2.3: Add Validation Rules

**File**: `src/config.py`, in `_validate()`
**Lines**: +8

When `export.google_docs.enabled` is `true`, validate:
- `folder_id` is non-empty
- `max_retries` >= 1

### Task 2.4: Add `export` Section to `config.yaml`

**File**: `config.yaml`
**Lines**: +10

Add the disabled-by-default export section with comments explaining each field.

### Task 2.5: Add `markdown` to Requirements

**File**: `requirements.txt`
**Lines**: +1

```
markdown>=3.4
```

### Task 2.6: Write Config Tests

**File**: `tests/test_config.py` (additions)
**Lines**: +30

- Test default values when no export section in YAML
- Test loading with export enabled and all fields populated
- Test validation error when enabled without folder_id
- Test env var override: `EXPORT_GOOGLE_DOCS_ENABLED`

### Validation Gate 2

```
pytest tests/test_config.py -v
pytest tests/test_exporter.py -v
```

All tests pass. Configuration loads correctly with and without the export section.

---

## Phase 3: Pipeline Integration and Bot Wiring

**Effort**: 2-3 hours
**Dependencies**: Phase 2 complete
**Deliverables**: Pipeline changes, bot changes, integration tests

### Task 3.1: Add Exporter Parameter to Pipeline Functions

**File**: `src/pipeline.py`
**Lines**: +5

Add `exporter: GoogleDocsExporter | None = None` parameter to:
- `run_pipeline_from_tracks()`
- `run_pipeline()`

The `run_pipeline()` function passes `exporter` through to `run_pipeline_from_tracks()`.

### Task 3.2: Add Export Stage to Pipeline

**File**: `src/pipeline.py`
**Lines**: +20

After the archive block (lines 146-161), add the export block. This follows the exact same fault-tolerant pattern:

```python
# Export to Google Docs (fault-tolerant)
if exporter is not None and cfg.export.google_docs.enabled:
    try:
        export_title = f"Meeting Minutes -- {date_str}"
        export_result = await exporter.export(
            minutes_md=minutes_md,
            title=export_title,
            metadata={
                "date": date_str,
                "speakers": speakers_str,
                "source": source_label,
            },
        )
        if export_result.success:
            logger.info(
                "Minutes exported to Google Docs: %s", export_result.url
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

### Task 3.3: Wire Exporter in `bot.py`

**File**: `bot.py`
**Lines**: +15

1. In `main()`, after archive initialization:
   ```python
   exporter: GoogleDocsExporter | None = None
   if cfg.export.google_docs.enabled:
       from src.exporter import GoogleDocsExporter
       exporter = GoogleDocsExporter(cfg.export.google_docs)
       logger.info("Google Docs exporter enabled")
   ```

2. Add `exporter` attribute to `MinutesBot.__init__()`

3. Pass `exporter=self.exporter` in all `run_pipeline()` and `run_pipeline_from_tracks()` calls:
   - `_launch_pipeline()` (Craig detection flow)
   - `_on_drive_tracks()` (Drive watcher flow)

### Task 3.4: Write Pipeline Integration Tests

**File**: `tests/test_pipeline.py` (additions)
**Lines**: +40

- `test_pipeline_with_export_success`: Mock exporter returns success; verify it is called after archive
- `test_pipeline_export_failure_nonfatal`: Mock exporter returns failure; verify pipeline still completes
- `test_pipeline_export_exception_nonfatal`: Mock exporter raises exception; verify pipeline still completes
- `test_pipeline_export_disabled`: Set `enabled=False`; verify exporter is not called
- `test_pipeline_export_none`: Pass `exporter=None`; verify no errors

### Validation Gate 3

```
pytest -v
```

All existing tests pass (no regressions). All new tests pass. Total test count increases by 15-20.

---

## Phase Summary

| Phase | Deliverables | Effort | Cumulative Tests |
|-------|-------------|--------|-----------------|
| PoC | Standalone validation script | 2-3h | 0 |
| Phase 1 | `src/exporter.py`, `tests/test_exporter.py` | 2-3h | +12-14 |
| Phase 2 | Config changes, `config.yaml`, requirements.txt | 1-2h | +4 |
| Phase 3 | Pipeline + bot integration, integration tests | 2-3h | +5 |
| **Total** | | **7-11h** | **+21-23** |

---

## Stretch Goal: Export Link in Discord Embed

**Effort**: 1-2 hours (separate PR recommended)
**Dependencies**: Phase 3 complete and validated in production

If the export succeeds and returns a URL, edit the Discord message to add a "Google Docs" field to the Embed:

1. `exporter.export()` returns `ExportResult` with `url`
2. If `success` and `url`, edit the posted message to add an Embed field
3. Message editing uses the same `_send_with_retry` pattern from `poster.py`
4. Editing failure is non-critical (logged as warning)

This is intentionally a separate PR because:
- It requires passing the `discord.Message` back from the posting stage
- Message editing adds latency visible to users
- It should be validated that export works reliably before adding visible UI changes

---

## Rollout Plan

1. **Feature flag**: `export.google_docs.enabled: false` is the default in `config.yaml`. Existing deployments are unaffected.
2. **Testing**: Run all tests (`pytest -v`), verify no regressions.
3. **Manual QA**: Enable in a test guild, trigger a pipeline run, verify Google Docs document is created.
4. **Production**: Enable in production `config.yaml`, monitor logs for export success/failure.
5. **Rollback**: Set `enabled: false` and restart. No data loss, no state cleanup needed.

## Rollback Plan

| Scenario | Action |
|----------|--------|
| Export causes errors | Set `export.google_docs.enabled: false`, restart bot |
| Export adds unacceptable latency | Same as above (export is fire-and-forget but may delay pipeline completion logging) |
| Google API quota issues | Same as above, or reduce `max_retries` to 1 |
| Need to fully remove code | Revert the PRs; `exporter=None` default means no code path changes are needed |

---

## Open Decisions

| # | Decision | Options | Recommendation | Status |
|---|----------|---------|----------------|--------|
| D1 | Scope for Service Account | `drive.file` (app-only) vs `drive` (full) | `drive.file` -- least privilege | Pending C2 |
| D2 | Same credentials.json or separate? | Reuse vs dedicated | Reuse -- simpler setup, same Service Account | Pending C2 |
| D3 | Export link in Embed | Phase 1 vs separate PR | Separate PR -- validate export stability first | Decided |
| D4 | Checkbox handling | `- [ ]` as text vs custom HTML | Text (Phase 1), custom HTML if users request | Decided |
| D5 | Document naming convention | Date only vs date + guild + channel | Date only for Phase 1 | Decided |
