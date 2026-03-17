# Cloud Migration (API Hybrid) -- UX Design

## Overview

This feature has **no end-user-facing changes**. Discord guild members interact with the bot identically regardless of whether transcription runs locally or via API. The UX considerations are entirely for the **bot operator/administrator**.

## Personas

### P1: Self-Hoster (Current User)

- Runs the bot on a local PC with GPU
- Comfortable with Docker, config files, and terminal commands
- Wants zero disruption to existing setup

### P2: Cloud Deployer (New Target User)

- Wants to run the bot on a cheap VPS without GPU
- May not have NVIDIA hardware at all
- Expects clear, step-by-step setup instructions
- Cost-conscious; needs visibility into API spend

## UX Flows

### Flow 1: Migrating from Local to API Backend

**Trigger**: Operator decides to switch from local Whisper to OpenAI API.

```
1. Operator obtains OpenAI API key
2. Adds OPENAI_API_KEY to .env
3. Edits config.yaml:
     whisper:
       backend: api          # <-- NEW (was absent or "local")
       api_model: whisper-1  # <-- NEW (optional, has default)
       language: ja          # <-- Unchanged, reused by API
4. Restarts bot
5. Bot logs confirm: "Transcription backend: OpenAI API (model=whisper-1)"
6. First recording processes successfully via API
```

**Error paths:**
- Missing `OPENAI_API_KEY` when `backend: api` -> clear error at startup: `"OPENAI_API_KEY is required when whisper.backend is 'api'"`
- Invalid API key -> error on first transcription with actionable message

### Flow 2: Fresh Cloud Deployment (CPU VPS)

**Trigger**: New operator sets up the bot on a VPS from scratch.

```
1. Provision VPS (e.g., Hetzner CX22: 2 vCPU, 4 GB RAM, ~$5/mo)
2. Install Docker + Docker Compose
3. Clone repository
4. Create .env with DISCORD_BOT_TOKEN, ANTHROPIC_API_KEY, OPENAI_API_KEY
5. Edit config.yaml with guild/channel IDs and whisper.backend: api
6. Run: docker compose -f docker-compose.cpu.yml up -d
7. Verify: /minutes status shows "Backend: api" and "GPU: not required"
8. Test: Process a Craig recording URL via /minutes process <url>
```

### Flow 3: Keeping Local Whisper (No Change)

**Trigger**: Existing operator upgrades bot code but wants to keep GPU transcription.

```
1. Pull latest code
2. No config changes needed (backend defaults to "local")
3. Restart bot
4. Everything works as before
```

## Configuration UX

### New Config Fields

Added to the existing `whisper:` section in `config.yaml`:

```yaml
whisper:
  # --- Existing fields (unchanged) ---
  model: "large-v3"
  language: "ja"
  device: "cuda"
  compute_type: "float16"
  beam_size: 5
  vad_filter: true

  # --- New fields ---
  # Transcription backend: "local" (GPU/CPU Whisper) or "api" (OpenAI STT API)
  # Default: "local" (backward compatible)
  backend: "local"

  # OpenAI API model (only used when backend: api)
  # Options: "whisper-1" (currently the only model)
  api_model: "whisper-1"

  # Maximum retries for API calls (only used when backend: api)
  api_max_retries: 2

  # Request timeout in seconds for API calls (only used when backend: api)
  api_timeout_sec: 300
```

**Design decisions:**
- New fields live under the existing `whisper:` section (not a new top-level section) because they control the same pipeline stage
- Fields specific to the API backend are prefixed with `api_` to clearly signal they are irrelevant when `backend: local`
- The `language` field is shared between both backends (OpenAI API also accepts ISO 639-1 codes)
- Fields `model`, `device`, `compute_type`, `beam_size`, `vad_filter` are silently ignored when `backend: api` (no warnings, no errors)

### Environment Variable Override

Follows the existing `SECTION_FIELD` convention:

| Env Var | Overrides | Example |
|---------|-----------|---------|
| `WHISPER_BACKEND` | `whisper.backend` | `api` |
| `WHISPER_API_MODEL` | `whisper.api_model` | `whisper-1` |
| `OPENAI_API_KEY` | (new secret injection) | `sk-...` |

### Validation Rules

When `whisper.backend == "api"`:
- `OPENAI_API_KEY` must be set (error at startup if missing)
- `whisper.api_model` must be a non-empty string
- `whisper.api_timeout_sec` must be >= 10

When `whisper.backend == "local"`:
- Existing validation unchanged (model name, language, beam_size, etc.)
- No requirement for `OPENAI_API_KEY`

## Status Command UX

### Current `/minutes status` Output

```
Uptime: 2h 15m 30s
Whisper model: large-v3 (loaded)
GPU: available
Generator: claude-sonnet-4-5-20250929 (ready)
Template: minutes
Watch channel: #voice-chat
Output channel: #meeting-minutes
```

### Updated `/minutes status` Output (API Backend)

```
Uptime: 2h 15m 30s
Transcription: OpenAI API (model=whisper-1)
GPU: not required
Generator: claude-sonnet-4-5-20250929 (ready)
Template: minutes
Watch channel: #voice-chat
Output channel: #meeting-minutes
```

### Updated `/minutes status` Output (Local Backend)

```
Uptime: 2h 15m 30s
Transcription: local Whisper large-v3 (loaded)
GPU: available
Generator: claude-sonnet-4-5-20250929 (ready)
Template: minutes
Watch channel: #voice-chat
Output channel: #meeting-minutes
```

**Changes:**
- "Whisper model" line renamed to "Transcription" for clarity
- Shows backend type and model
- "GPU" line shows "not required" instead of "not available" when using API backend (avoids implying something is broken)

## Logging UX

### API Backend Log Messages

```
INFO  Transcription backend: OpenAI API (model=whisper-1)
INFO  Transcribing 1-alice.aac (speaker=alice) via OpenAI API
INFO  Transcribed 1-alice.aac: 42 segments in 8.3s (API cost estimate: ~$0.18 for 30.0 min)
INFO  Transcribing 2-bob.aac (speaker=bob) via OpenAI API
INFO  Transcribed 2-bob.aac: 38 segments in 7.1s (API cost estimate: ~$0.15 for 25.0 min)
INFO  Transcription complete: 80 total segments from 2 speakers (total API cost estimate: ~$0.33)
```

### Local Backend Log Messages (Unchanged)

```
INFO  Loading whisper model large-v3 (device=cuda, compute=float16)
INFO  Whisper model loaded in 4.2s
INFO  Transcribing 1-alice.aac (speaker=alice)
INFO  Transcribed 1-alice.aac: 42 segments in 12.3s (lang=ja, prob=0.95)
```

## Error Messages

| Scenario | Message | Audience |
|----------|---------|----------|
| Missing OPENAI_API_KEY at startup | `Configuration validation failed: OPENAI_API_KEY is required when whisper.backend is 'api'` | Operator (stderr/log) |
| Invalid API key at transcription time | `OpenAI API authentication failed. Check OPENAI_API_KEY in .env` | Operator (log) + Discord error embed |
| API rate limit | `OpenAI API rate limited, retrying in {N}s (attempt {n}/{max})` | Operator (log only) |
| File too large for API (> 25 MB) | `Audio file {name} exceeds OpenAI API limit (25 MB). Consider shorter recordings or local Whisper backend.` | Operator (log) + Discord error embed |
| Network error during upload | `OpenAI API connection error for {name}, retrying... (attempt {n}/{max})` | Operator (log only) |
| All retries exhausted | `Transcription failed for {name} after {max} attempts: {error}` | Operator (log) + Discord error embed |

## Docker UX

### Two Compose Files

| File | Purpose | GPU Required |
|------|---------|-------------|
| `docker-compose.yml` | Existing GPU deployment (unchanged) | Yes |
| `docker-compose.cpu.yml` | New CPU-only deployment for API backend | No |

### CPU Compose Usage

```bash
# Build and start (CPU, API backend)
docker compose -f docker-compose.cpu.yml up -d --build

# View logs
docker compose -f docker-compose.cpu.yml logs -f

# Stop
docker compose -f docker-compose.cpu.yml down
```

### Two Dockerfiles

| File | Base Image | Size | Purpose |
|------|-----------|------|---------|
| `Dockerfile` | `nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04` | ~5 GB | Existing GPU image |
| `Dockerfile.cpu` | `python:3.12-slim` | ~300 MB | New CPU-only image |

## Migration Checklist (for Documentation)

A printable checklist for operators migrating from local to API:

```
[ ] 1. Obtain OpenAI API key from https://platform.openai.com/api-keys
[ ] 2. Add OPENAI_API_KEY=sk-... to .env
[ ] 3. Set whisper.backend: api in config.yaml
[ ] 4. (If Docker) Switch to docker-compose.cpu.yml
[ ] 5. (If Docker) Rebuild: docker compose -f docker-compose.cpu.yml up -d --build
[ ] 6. (If bare metal) Install: pip install openai
[ ] 7. Restart bot
[ ] 8. Run /minutes status to verify "Transcription: OpenAI API"
[ ] 9. Test with /minutes process <url> on a real recording
[ ] 10. Compare output quality with previous local-Whisper minutes
[ ] 11. (Optional) Remove faster-whisper and CUDA packages to free disk space
```
