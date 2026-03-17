# UX Design: External Export (Google Docs)

**Feature Slug**: external-export
**Date**: 2026-03-17

---

## 1. User Flows

### Flow 1: Automatic Export (Happy Path)

```
Meeting ends
  |
  v
[Existing pipeline: transcribe -> merge -> generate]
  |
  v
Minutes posted to Discord (Embed + .md file)
  |
  v
Archive stored (existing, fault-tolerant)
  |
  v
[NEW] Export to Google Docs (fault-tolerant, fire-and-forget)
  |  1. Convert Markdown -> HTML
  |  2. Upload HTML to Google Drive with Docs mime type
  |  3. Google Drive auto-converts HTML -> Google Docs
  |  4. Retrieve webViewLink
  |
  v
[STRETCH] Edit Discord message to add Google Docs link
  |
  v
Done -- user sees Google Docs link in Discord + document in Drive folder
```

### Flow 2: Export Disabled (Default)

```
Meeting ends
  |
  v
[Existing pipeline: transcribe -> merge -> generate]
  |
  v
Minutes posted to Discord (Embed + .md file)
  |
  v
Archive stored
  |
  v
(Export step skipped entirely -- no API calls, no logs beyond DEBUG)
  |
  v
Done
```

### Flow 3: Export Failure

```
[NEW] Export to Google Docs
  |
  v
Attempt 1 fails (API error / timeout / auth issue)
  |
  v
Retry with backoff (up to 3 attempts)
  |
  v
All retries exhausted
  |
  v
Log WARNING: "Google Docs export failed (non-critical): {error details}"
  |
  v
Done -- Discord post is unaffected, no user-visible error
```

## 2. Configuration UX

### config.yaml Addition

The export section follows established patterns in the codebase (mirrors `google_drive:` and `minutes_archive:` sections):

```yaml
# External export (Google Docs)
export:
  google_docs:
    # Enable automatic export to Google Docs after minutes generation
    enabled: false
    # Path to service account JSON key file (can reuse google_drive credentials)
    credentials_path: "credentials.json"
    # Google Drive folder ID where exported Docs will be created
    # Get from folder URL: https://drive.google.com/drive/folders/{THIS_ID}
    folder_id: ""
    # Maximum retry attempts for export API calls
    max_retries: 3
```

### Setup Guide (Summary)

For a user enabling this feature for the first time:

1. **GCP Console**: Enable Google Drive API (if not already enabled for Drive watcher)
2. **Service Account**: Add `drive.file` scope (does not affect existing `drive.readonly` usage)
3. **Google Drive**: Create an "Minutes Export" folder, share it with the Service Account email (Editor role)
4. **config.yaml**: Set `export.google_docs.enabled: true` and paste the `folder_id`
5. **Restart bot**: The next generated minutes will auto-export

### Validation Messages

When configuration is invalid, the bot should produce clear error messages at startup:

| Condition | Error Message |
|-----------|--------------|
| Enabled but no `folder_id` | `export.google_docs.folder_id is required when export.google_docs.enabled is true` |
| Enabled but credentials file missing | `export.google_docs.credentials_path: file not found at {path}` |
| Invalid `folder_id` at runtime | `Google Docs export failed: Folder not found or not accessible (folder_id={id})` |

## 3. Discord Embed Changes

### Current Embed (No Change to Default Behavior)

```
+------------------------------------------+
| Meeting Minutes -- 2026-03-17 14:00      |
+------------------------------------------+
| Participants                             |
| user1, user2, user3                      |
|                                          |
| Summary                                  |
| Discussion about project timeline...     |
|                                          |
| Next Steps                               |
| - [ ] user1: Complete design doc         |
|                                          |
| Speaker Statistics                       |
| user1: 45.2% | user2: 32.1% | ...       |
+------------------------------------------+
| See attached file for full minutes       |
+------------------------------------------+
```

### Embed with Export Link (Stretch Goal)

```
+------------------------------------------+
| Meeting Minutes -- 2026-03-17 14:00      |
+------------------------------------------+
| Participants                             |
| user1, user2, user3                      |
|                                          |
| Summary                                  |
| Discussion about project timeline...     |
|                                          |
| Next Steps                               |
| - [ ] user1: Complete design doc         |
|                                          |
| Speaker Statistics                       |
| user1: 45.2% | user2: 32.1% | ...       |
|                                          |
| Google Docs                              |  <-- NEW (only when export succeeds)
| https://docs.google.com/document/d/...   |
+------------------------------------------+
| See attached file for full minutes       |
+------------------------------------------+
```

The "Google Docs" field is added via message edit after the export completes. If the export fails or is disabled, this field is absent -- users see the same Embed as before.

## 4. Google Docs Document Format

The exported Google Docs document will contain the same content as the `.md` file, converted through HTML. The conversion preserves:

| Markdown Element | Google Docs Rendering |
|------------------|----------------------|
| `# Heading 1` | Heading 1 style |
| `## Heading 2` | Heading 2 style |
| `**bold text**` | Bold text |
| `- bullet item` | Bulleted list |
| `- [ ] todo item` | Checkbox list item (if supported by conversion) |
| `[MM:SS]` timestamps | Plain text (no conversion needed) |
| Tables | HTML table (converted to Docs table) |

### Document Title Convention

```
Meeting Minutes -- {date_str}
```

Example: `Meeting Minutes -- 2026-03-17 14:00`

This matches the Discord Embed title for consistency.

## 5. Logging UX

Operators monitoring the bot logs will see:

### Success Case
```
INFO  src.exporter: Starting Google Docs export (folder_id=18Jp...)
INFO  src.exporter: Markdown -> HTML conversion complete (2847 chars)
INFO  src.exporter: Google Docs created: "Meeting Minutes -- 2026-03-17 14:00" (doc_id=1BxR...)
INFO  src.exporter: Export complete in 3.2s (url=https://docs.google.com/document/d/1BxR.../edit)
```

### Failure Case
```
WARNING src.exporter: Google Docs export attempt 1/3 failed: HttpError 403 "insufficientPermissions"
WARNING src.exporter: Google Docs export attempt 2/3 failed: HttpError 403 "insufficientPermissions"
WARNING src.exporter: Google Docs export attempt 3/3 failed: HttpError 403 "insufficientPermissions"
WARNING src.exporter: Google Docs export failed after 3 attempts (non-critical): insufficientPermissions
```

### Disabled Case
```
(No log output -- export step is silently skipped)
```

## 6. Error Handling Philosophy

The export feature strictly follows the project's **graceful degradation** principle:

1. Export failure never raises an exception that propagates to the pipeline
2. Export failure never prevents or delays Discord posting
3. Export failure never causes the pipeline to be marked as "failed"
4. Export is a best-effort operation that logs warnings on failure
5. The user experience for Discord minutes posting is identical whether export is enabled, disabled, or broken

This matches the existing pattern used by `MinutesArchive` in `pipeline.py` lines 146-161.
