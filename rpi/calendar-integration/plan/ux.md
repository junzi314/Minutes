# UX Design: Calendar Integration

**Feature Slug**: calendar-integration
**Date**: 2026-03-17

---

## 1. Design Principles

1. **Transparent augmentation**: Calendar data enriches minutes silently -- no new user actions required
2. **Graceful absence**: When no calendar event matches, the UX is identical to today's experience
3. **Non-blocking**: Calendar failures never prevent minutes generation
4. **Minimal configuration**: One config section with sensible defaults; advanced tuning optional

---

## 2. User Flows

### 2.1 Automatic Flow (Primary)

```
Recording ends -> Pipeline starts -> Calendar fetch (silent) -> Minutes generated
                                          |
                                          +-- Event found: title/attendees/description injected into prompt
                                          +-- No event: empty strings, prompt gracefully omits section
                                          +-- API error: warning logged, pipeline continues
```

No user interaction required. The feature is entirely passive.

### 2.2 Status Check Flow

```
User: /minutes calendar-status
Bot (ephemeral):
  Calendar integration: enabled
  Calendar ID: team-calendar@group.calendar.google.com
  Last fetch: 2026-03-17 14:30 (matched: "Weekly Standup")
  -- or --
  Calendar integration: disabled
```

### 2.3 Configuration Flow (Admin)

1. Admin enables `calendar.enabled: true` in config.yaml
2. Admin shares their Google Calendar with the Service Account email address
3. Admin sets `calendar.calendar_id` to the target calendar ID
4. Bot restart picks up the new config
5. Calendar data automatically appears in subsequent minutes

---

## 3. Minutes Output Changes

### 3.1 Discord Embed (When Event Matched)

**Before:**
```
+----------------------------------------------+
| 会議議事録 -- 2026-03-17 14:00               |
|                                              |
| 参加者: alice, bob, carol                    |
| まとめ: ...                                  |
| 次のステップ: ...                             |
| 詳細議事録は添付ファイルを参照                  |
+----------------------------------------------+
```

**After (with calendar event):**
```
+----------------------------------------------+
| 会議議事録 -- 2026-03-17 14:00               |
|                                              |
| 会議名: Weekly Sprint Review                 |  <-- NEW field
| 参加者: alice, bob, carol                    |
| まとめ: ...                                  |
| 次のステップ: ...                             |
| 詳細議事録は添付ファイルを参照                  |
+----------------------------------------------+
```

**After (without calendar event):**
Identical to "Before" -- no visible change.

### 3.2 Markdown File Header (When Event Matched)

**Before:**
```markdown
# 会議議事録
- 日時: 2026-03-17 14:00
- 参加者: alice, bob, carol
```

**After (with event):**
```markdown
# 会議議事録
- 日時: 2026-03-17 14:00
- 会議名: Weekly Sprint Review
- 参加者: alice, bob, carol
- アジェンダ: Sprint backlog review, demo, retrospective
```

The LLM receives event metadata and naturally incorporates it into the structured output. The prompt template includes conditional guidance:

```
## 会議情報
- 日時: {date}
- サーバー: {guild_name}
- チャンネル: {channel_name}
- 参加者: {speakers}
- 会議名: {event_title}
- カレンダー参加者: {event_attendees}
- アジェンダ/説明: {event_description}

Note: 会議名やアジェンダが空の場合は、その行を出力から省略してください。
```

### 3.3 Markdown File Header (Without Event)

The LLM sees empty strings for `{event_title}`, `{event_attendees}`, and `{event_description}`. The prompt instructs it to omit empty lines, so the output is identical to current behavior.

---

## 4. Prompt Template Changes

### Current Template Variables
```
{date}          -> "2026-03-17 14:00"
{guild_name}    -> "My Server"
{channel_name}  -> "meeting-room"
{speakers}      -> "alice, bob, carol"
{transcript}    -> "[00:00] alice: Hello..."
```

### New Template Variables
```
{event_title}       -> "Weekly Sprint Review" or ""
{event_attendees}   -> "alice@example.com, bob@example.com" or ""
{event_description} -> "Sprint backlog review, demo, retrospective" or ""
```

### Template Section Addition

Added between the meeting info section and transcript section:

```
- 会議名: {event_title}
- カレンダー参加者: {event_attendees}
- アジェンダ/説明: {event_description}

※ 会議名・カレンダー参加者・アジェンダが空欄の場合は、その項目を出力に含めないでください。
```

This instruction ensures the LLM omits empty fields rather than printing blank lines.

---

## 5. Error and Edge Case UX

### 5.1 Calendar API Timeout

**User sees**: Nothing different. Minutes are generated without calendar context.
**Log shows**: `WARNING: Calendar fetch timed out after 10s, continuing without event info`

### 5.2 No Matching Event

**User sees**: Standard minutes without event-specific fields.
**Log shows**: `INFO: No calendar event found for time window 14:00-15:30`

### 5.3 Multiple Matching Events

**User sees**: Minutes with the best-matching event's information.
**Log shows**: `INFO: Found 3 calendar events, selected "Weekly Standup" (overlap: 58min)`

### 5.4 Calendar Not Configured

**User sees**: Standard minutes (identical to current behavior).
**Log shows**: Nothing calendar-related (feature disabled).

### 5.5 Invalid Credentials

**User sees**: Standard minutes.
**Log shows**: `WARNING: Calendar credentials invalid, disabling calendar integration for this run`

---

## 6. Slash Command: /minutes calendar-status

### Response Format (Enabled, Working)

```
**カレンダー連携**: 有効
**カレンダーID**: `team@group.calendar.google.com`
**タイムゾーン**: Asia/Tokyo
**マッチ許容範囲**: 前後30分
**最終フェッチ**: 2026-03-17 14:30 (マッチ: "Weekly Standup")
```

### Response Format (Enabled, No Recent Fetch)

```
**カレンダー連携**: 有効
**カレンダーID**: `team@group.calendar.google.com`
**タイムゾーン**: Asia/Tokyo
**最終フェッチ**: まだ実行されていません
```

### Response Format (Disabled)

```
**カレンダー連携**: 無効
config.yamlの `calendar.enabled` を `true` に設定してください。
```

---

## 7. Configuration UX

### Minimal Configuration (Most Users)

```yaml
calendar:
  enabled: true
  calendar_id: "team-calendar@group.calendar.google.com"
```

Everything else uses sensible defaults:
- `credentials_path`: same as `google_drive.credentials_path` (or "credentials.json")
- `timezone`: "Asia/Tokyo"
- `match_tolerance_minutes`: 30
- `api_timeout_sec`: 10
- `max_retries`: 2

### Full Configuration (Power Users)

```yaml
calendar:
  enabled: true
  credentials_path: "credentials.json"
  calendar_id: "team-calendar@group.calendar.google.com"
  timezone: "Asia/Tokyo"
  match_tolerance_minutes: 60
  api_timeout_sec: 15
  max_retries: 3
```

---

## 8. Setup Guide (User-Facing Documentation)

### Prerequisites
1. Google Cloud project with Calendar API enabled
2. Service Account with `credentials.json` (same one used for Google Drive watcher)
3. Calendar API scope added to Service Account

### Steps
1. Go to Google Calendar > Settings > Share with specific people
2. Add the Service Account email (from credentials.json): `xxx@yyy.iam.gserviceaccount.com`
3. Set permission to "See all event details"
4. Copy the Calendar ID from Google Calendar settings
5. Update config.yaml:
   ```yaml
   calendar:
     enabled: true
     calendar_id: "YOUR_CALENDAR_ID_HERE"
   ```
6. Restart the bot

### Verification
Run `/minutes calendar-status` to confirm the integration is active.
