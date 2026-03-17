# Forum Channel Support - Implementation Plan

**Feature**: Forum channel support for minutes posting
**Date**: 2026-02-22
**Phases**: 1

## Overview

Add ForumChannel support to the minutes posting system. When `output_channel_id` points to a `ForumChannel`, create a new thread with the minutes title and post the content as the initial message. Maintain existing `TextChannel` behavior.

## Phase 1: Forum Channel Support

### Deliverables

1. **poster.py** - Update `post_minutes()`, `post_error()`, `send_status_update()` to handle `ForumChannel`
2. **pipeline.py** - Update `output_channel` type annotations
3. **bot.py** - Update `_get_output_channel_for_guild()` and `_launch_pipeline()` type annotations
4. **tests/test_poster.py** - Add tests for ForumChannel code paths

### Files to Modify

| File | Change |
|------|--------|
| `src/poster.py` | Add ForumChannel branching in post_minutes, post_error, send_status_update |
| `src/pipeline.py` | Update type annotations |
| `bot.py` | Update type annotations |
| `tests/test_poster.py` | Add ForumChannel tests |

### Design

- `post_minutes`: For `ForumChannel`, use `channel.create_thread(name=title, content=mention, embed=embed, file=file)` which returns `ThreadWithMessage(thread, message)`
- `post_error`: For `ForumChannel`, create error thread
- `send_status_update`: For `ForumChannel`, silently skip (forum channels don't support direct messages, and status messages are transient)
- Thread title format: `"会議議事録 — {date}"` (same as embed title)

### Validation Criteria

- [ ] ForumChannel posting creates a thread with correct title
- [ ] TextChannel posting unchanged
- [ ] Status updates gracefully skip for ForumChannel
- [ ] Error posting works for ForumChannel
- [ ] All existing tests still pass
- [ ] New tests for ForumChannel paths

### Status

- [x] Phase 1: Validated (PASS)
