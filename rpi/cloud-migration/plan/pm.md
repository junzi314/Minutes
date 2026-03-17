# Cloud Migration (API Hybrid) -- Product Requirements

## Overview

Replace the local GPU-dependent Whisper transcription with OpenAI Speech-to-Text API, enabling the Discord Minutes Bot to run on an affordable CPU-only VPS ($7-17/month) instead of requiring a dedicated GPU machine ($50-400/month) or a constantly-running personal PC.

This is **Path B (API Hybrid)** from the research report -- the recommended approach for sporadic usage (< 20 meetings/month).

## Problem Statement

The bot currently requires an NVIDIA GPU (6+ GB VRAM) running 24/7 to process Craig recordings on demand. This creates three operational constraints:

1. **Hardware dependency**: A personal PC with an RTX 3060+ must remain powered on at all times
2. **Availability gap**: The bot goes offline when the PC is shut down, rebooted, or the owner travels
3. **Cost barrier to cloud**: GPU cloud instances cost $50-400/month, disproportionate for a bot that processes a few hours of audio per month

## Goals

| # | Goal | Success Metric |
|---|------|---------------|
| G1 | Eliminate GPU hardware requirement | Bot runs on CPU-only VPS with no CUDA dependency |
| G2 | Reduce cloud hosting cost to < $20/month | VPS ($5-15) + API costs ($2-5) < $20 for typical usage |
| G3 | Maintain transcription quality | Side-by-side comparison shows no user-noticeable degradation |
| G4 | Preserve backward compatibility | Operators with GPU hardware can still use local Whisper |
| G5 | Zero user-facing changes | Discord users see identical bot behavior |

## User Stories

### US-1: Cloud Operator (Primary)

> As a bot operator without GPU hardware, I want to deploy the bot on a cheap CPU VPS so that the bot is available 24/7 without requiring my personal PC.

**Acceptance Criteria:**
- `config.yaml` setting `whisper.backend: api` switches to OpenAI STT
- Bot starts without `faster-whisper` or CUDA installed
- Full pipeline (Craig download -> transcribe -> generate -> post) works end-to-end
- Docker image without CUDA is < 1 GB (vs current ~5 GB)

### US-2: GPU Operator (Backward Compat)

> As an existing operator with GPU hardware, I want my current setup to continue working with zero changes after upgrading.

**Acceptance Criteria:**
- Default `whisper.backend: local` preserves current behavior exactly
- All existing tests pass without modification
- `config.yaml` without `backend` field defaults to `local`

### US-3: Cost-Conscious Operator

> As an operator, I want to understand the cost implications of API transcription so I can budget accordingly.

**Acceptance Criteria:**
- `/minutes status` shows current transcription backend (local vs API)
- Logs include per-file API cost estimate (duration * rate)
- Documentation includes cost calculator for common usage patterns

### US-4: Quality-Sensitive Operator

> As an operator, I want to verify that API transcription quality matches local Whisper before switching permanently.

**Acceptance Criteria:**
- Both backends produce the same `Segment` data structure
- Merger and downstream pipeline stages work identically regardless of backend
- Operator can switch between backends with a config change and restart (no data migration)

## Scope

### In Scope

- New `TranscriberAPI` class implementing the same interface as `Transcriber`
- Config extension: `whisper.backend` field (`local` | `api`)
- OpenAI STT API integration (`openai` Python package)
- CPU-only Dockerfile (`Dockerfile.cpu`)
- CPU-only `docker-compose.cpu.yml`
- Updated `/minutes status` to show backend type
- Unit tests for `TranscriberAPI`
- Integration tests with mocked API
- Updated deployment documentation

### Out of Scope

- Deepgram integration (can be added later as a third backend)
- Serverless GPU option (Path C from research -- deferred)
- CI/CD pipeline (separate feature)
- Health check HTTP endpoint (separate feature)
- Automatic backend failover (local -> API or vice versa)
- Live switching without restart

## Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| API latency overhead | < 2x wall-clock time vs local Whisper for same audio |
| Error handling | Retry with exponential backoff (same pattern as Claude API) |
| File size limit | OpenAI API: 25 MB per file; split if needed |
| Concurrent uploads | Sequential per-speaker (same as current local transcription) |
| Secret management | `OPENAI_API_KEY` via `.env` (same pattern as `ANTHROPIC_API_KEY`) |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| API accuracy differs from local large-v3 | Low-Medium | Low | Test with 3-5 real recordings before switching |
| 25 MB file size limit exceeded | Low | Medium | Craig recordings are per-speaker AAC; 30-min file is ~15 MB |
| OpenAI API rate limits | Low | Low | Sequential processing; generous rate limits for Whisper API |
| Network outage during transcription | Low | Medium | Retry logic with backoff; partial failure leaves no bad state |
| OpenAI deprecates/changes Whisper API | Very Low | High | Interface abstraction allows swapping to Deepgram or local |

## Dependencies

- **External**: OpenAI API account with Whisper API access
- **Internal**: No blocking dependencies; all prerequisite features (Docker, state store) are complete
- **New package**: `openai>=1.0,<2.0` (only required when `backend: api`)

## Cost Estimate (Monthly)

| Component | 5 hrs audio/mo | 10 hrs audio/mo | 20 hrs audio/mo |
|-----------|---------------|-----------------|-----------------|
| VPS (Hetzner CX22, 2 vCPU / 4 GB) | $5 | $5 | $5 |
| OpenAI Whisper API ($0.006/min) | $1.80 | $3.60 | $7.20 |
| Anthropic Claude API (existing) | ~$2 | ~$4 | ~$8 |
| **Total** | **~$9** | **~$13** | **~$20** |

Compare: GPU VPS (Vast.ai cheapest) = $50-70/month regardless of usage.

## Timeline

Estimated effort: **3-5 working days** (see `eng.md` and `PLAN.md` for breakdown).
