# Research Report: Cloud Migration (VPS/クラウド移行)

## Executive Summary

This feature proposes migrating the Discord Minutes Bot from a local PC to a cloud VPS with GPU, eliminating the requirement for a constantly-running personal machine. The codebase is already Docker-ready with NVIDIA CUDA support, making the migration technically straightforward. However, the cost analysis reveals a significant ongoing expense ($30-400+/month) for a bot with sporadic usage patterns, and a viable alternative exists: using cloud speech-to-text APIs instead of self-hosted Whisper to run the bot on a cheap CPU-only VPS ($5-15/month). The recommendation is **CONDITIONAL GO** -- proceed only if the usage frequency justifies dedicated GPU costs, or pivot the architecture to a hybrid cloud API approach first.

## Recommendation

- **Decision**: CONDITIONAL GO
- **Confidence**: High
- **Rationale**: The Docker infrastructure is already complete, so the GPU cloud deployment path is technically trivial (est. 1-2 days of work). However, the economic case is weak for sporadic usage. The bot processes recordings only when meetings happen (likely a few times per week at most), yet a GPU instance must run 24/7 to stay responsive. The conditions below outline two viable paths, and the choice depends on usage volume and budget tolerance.

### Conditions for Proceeding

1. **Path A (GPU Cloud)**: Proceed if monthly meeting frequency exceeds ~20 sessions/month AND the operator accepts $30-100+/month in cloud costs. Best for teams with daily standups or frequent meetings.
2. **Path B (API Hybrid -- Recommended)**: First migrate transcription to a cloud STT API (OpenAI Whisper API at $0.006/min or Deepgram at $0.0043/min), then deploy the bot on a $5-15/month CPU-only VPS. This is far more cost-effective for sporadic usage and eliminates the GPU dependency entirely. Estimated monthly cost: $10-20/month total.
3. **Path C (Status Quo + Deferred)**: If the local PC setup is working adequately, defer the migration until it becomes a genuine pain point.

## Feature Overview

| Field | Value |
|-------|-------|
| **Name** | VPS/クラウド移行 (Cloud Migration) |
| **Type** | Infrastructure / DevOps |
| **Target Components** | Dockerfile, docker-compose.yml, deployment docs |
| **Priority** | Ext-8 (future extension candidate) |
| **Original Scope** | Out of scope for initial development |

### Goals

1. Eliminate the requirement for a constantly-running local PC
2. Enable 24/7 bot availability without personal hardware dependency
3. Provide reproducible deployment via Docker on cloud infrastructure
4. Establish secure secret management for cloud environments

## Requirements Summary

### Must Have
- GPU-enabled VPS/cloud deployment procedure documentation
- Cloud-optimized docker-compose.yml adjustments
- Secure environment variable and credential management

### Nice to Have
- CI/CD pipeline (GitHub Actions to cloud deploy)
- Health check endpoint
- Resource monitoring (GPU utilization, memory, disk)
- Cost optimization (spot instances, auto-scaling)

### Non-Functional
- Post-deployment verification checklist
- Rollback procedure
- Monthly cost estimate with breakdown

### Data Persistence
- `state/` directory (processing.json, minutes_cache.json, guild_settings.json, minutes_archive.db)
- `logs/` directory
- Whisper model cache (~3 GB for large-v3)

## Product Analysis

### User Value

**Direct users**: Bot administrators (likely 1-2 people per deployment)
**Indirect users**: All guild members who benefit from meeting minutes

The primary value is operational convenience -- removing the constraint that someone's personal PC must be running at all times. This is meaningful when:
- The PC owner travels or the machine needs maintenance
- Multiple guilds rely on the bot (SLA expectations)
- The bot needs to respond to recordings at any hour

**Impact assessment**: Medium. The bot already works; this changes where it runs, not what it does. The user-facing behavior is identical per the REQUEST.md ("ユーザー向けの変更なし").

### Strategic Alignment

Cloud migration is a natural maturation step for any self-hosted bot, but it is explicitly marked as "今後の拡張候補（スコープ外）" (future extension candidate, out of scope). This signals it is not urgent. The Docker support (Phase 6) was completed as an enabler, making this a "when needed" rather than "needed now" feature.

### Priority Recommendation

**Low-Medium priority**. Pursue only when local PC availability becomes a genuine operational constraint. The cost-benefit ratio favors the hybrid API approach (Path B) over dedicated GPU cloud deployment for most small-team use cases.

## Technical Discovery

### Current Architecture Analysis

The codebase is well-structured for cloud deployment:

1. **Docker support is complete** (`Dockerfile` + `docker-compose.yml`):
   - Base image: `nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04`
   - Non-root user (`botuser`) for security
   - Named volume for Whisper model cache
   - GPU reservation via `nvidia-container-toolkit`

2. **Configuration is externalized** (`config.yaml` + `.env`):
   - Secrets loaded from environment variables (DISCORD_BOT_TOKEN, ANTHROPIC_API_KEY)
   - Config file mounted as read-only volume
   - Google Drive credentials mounted as read-only volume

3. **State is file-based and portable** (`state/` directory):
   - `processing.json` -- dedup tracking
   - `minutes_cache.json` -- LLM response cache
   - `guild_settings.json` -- per-guild template overrides
   - `minutes_archive.db` -- SQLite archive (via MinutesArchive)
   - Atomic writes with `.tmp` + `os.replace()` pattern

4. **GPU dependency is isolated to one module** (`src/transcriber.py`):
   - Only `Transcriber` class uses faster-whisper / CUDA
   - Model loaded once at startup, kept resident in VRAM
   - Config supports both `cuda` and `cpu` device modes
   - VRAM requirement: ~6 GB for large-v3

5. **No hardcoded paths** -- all paths come from config or are relative to working directory

### Integration Points for Cloud Migration

| Component | Cloud Impact | Notes |
|-----------|-------------|-------|
| `Dockerfile` | Minimal changes | Already cloud-ready |
| `docker-compose.yml` | Add state volume, health check | Currently mounts `processed_files.json` (legacy) |
| `config.yaml` | No changes needed | Env var overrides already supported |
| `.env` | Replace with cloud secret manager | Currently file-based |
| `credentials.json` | Replace with cloud secret manager | Google Service Account key |
| `state/` directory | Persistent volume needed | 4 files, small footprint |
| `logs/` directory | Persistent volume or log aggregation | Currently file-based rotation |
| Whisper model cache | Persistent volume (~3 GB) | Already handled via named volume |

### Reusable Patterns

- Environment variable override pattern in `config.py` (`SECTION_FIELD` convention) maps directly to cloud secret injection
- `WhisperConfig.device` already supports `"cpu"` -- switching to CPU mode requires only a config change
- Restart policy (`restart: on-failure`) in docker-compose.yml provides basic resilience

### Technical Constraints from Code

1. **Whisper model loading is synchronous and blocking** (`bot.py` line 602-603): `transcriber.load_model()` runs before the event loop starts. On first boot (cold cache), downloading large-v3 takes several minutes.

2. **State store uses local filesystem atomics** (`state_store.py`): The `os.replace()` pattern works on all POSIX systems but warns about DrvFs (Windows mounts). Cloud volumes (EBS, persistent disks) are fine.

3. **No health check endpoint exists**: The bot is a long-running Discord gateway client with no HTTP server. Adding one requires a small async HTTP server (e.g., aiohttp).

4. **Single-process, single-GPU design**: The pipeline runs one transcription at a time sequentially. No multi-GPU or distributed processing support.

## Technical Analysis

### Option 1: GPU Cloud VPS (Always-On)

Deploy the existing Docker image on a GPU-enabled cloud instance running 24/7.

**Providers and estimated monthly costs** (always-on, based on 2026 pricing):

| Provider | GPU | VRAM | Monthly Cost | Notes |
|----------|-----|------|-------------|-------|
| RunPod (Community) | RTX 3090 | 24 GB | ~$137/mo | $0.19/hr, cheapest viable |
| RunPod (Community) | RTX 4090 | 24 GB | ~$245/mo | $0.34/hr, overkill |
| Vast.ai (Marketplace) | RTX 3060 | 12 GB | ~$50-70/mo | Variable pricing, minimum viable |
| Vast.ai (Marketplace) | RTX 3090 | 24 GB | ~$90-130/mo | Variable pricing |
| AWS EC2 g4dn.xlarge | T4 | 16 GB | ~$384/mo | On-demand, $226/mo reserved |
| GCP n1 + T4 | T4 | 16 GB | ~$250-350/mo | Depends on region |
| Lambda Cloud | A10 | 24 GB | ~$260/mo | $0.36/hr |

**Pros**:
- Zero code changes required (Docker image runs as-is)
- Identical performance to local setup
- Whisper model stays resident in VRAM (fast response)

**Cons**:
- High ongoing cost ($50-400/mo) for a bot that may process only a few recordings per week
- GPU idle >95% of the time for typical usage
- Vast.ai/RunPod Community instances can be preempted

**Complexity**: Simple (1-2 days)

### Option 2: Hybrid Cloud API (Recommended for Cost)

Replace self-hosted faster-whisper with a cloud speech-to-text API, then deploy on a cheap CPU-only VPS.

**Architecture change**:
- Replace `src/transcriber.py` with a new `src/transcriber_api.py` that calls OpenAI Whisper API or Deepgram
- Deploy on any $5-15/month CPU VPS (DigitalOcean, Hetzner, Linode)
- Remove CUDA/GPU dependency entirely

**Cost estimate** (per month, assuming 10 meetings x 30 min average = 5 hours of audio):
- OpenAI Whisper API: 300 min x $0.006/min = $1.80/mo
- Deepgram batch: 300 min x $0.0043/min = $1.29/mo
- CPU VPS (2 vCPU, 4 GB RAM): $5-15/mo
- **Total: ~$7-17/month**

**Pros**:
- 5-20x cheaper than GPU cloud for typical usage
- Eliminates GPU/CUDA dependency and complexity
- VPS options are abundant and reliable
- Scales linearly with usage (no idle GPU cost)
- Smaller Docker image (no CUDA runtime, ~500 MB vs ~5 GB)

**Cons**:
- Requires new code (TranscriberAPI class, ~100-200 lines)
- Adds external API dependency for transcription (network latency, rate limits)
- Loses speaker-per-file transcription advantage (API receives merged audio or individual files)
- Slightly different accuracy characteristics vs. self-hosted large-v3
- Per-file upload overhead for multi-speaker Craig ZIPs

**Complexity**: Medium (3-5 days, new transcriber module + testing)

### Option 3: Serverless GPU (Pay-per-Use)

Use RunPod Serverless or Modal to run Whisper only when needed, with zero cost during idle periods.

**Architecture change**:
- Package faster-whisper as a serverless function
- Bot sends audio files to serverless endpoint, receives transcription
- Bot itself runs on cheap CPU VPS

**Cost estimate** (same 5 hours/month assumption):
- RunPod Serverless: ~$0.00026/sec for T4 = ~$4.68/mo for 5 hours compute
- Modal: ~$0.000578/sec for T4 = ~$10.40/mo
- CPU VPS: $5-15/mo
- **Total: ~$10-25/month**

**Pros**:
- Pay only for actual GPU usage
- Keeps self-hosted Whisper quality
- No GPU idle costs

**Cons**:
- Cold start latency (10-60 seconds for model loading)
- Requires packaging Whisper as a separate service
- More complex architecture (two components instead of one)
- Debugging is harder across service boundaries

**Complexity**: Complex (5-8 days)

### Recommended Approach

**For immediate need**: Option 1 (GPU Cloud VPS) with Vast.ai or RunPod Community -- minimal effort, works today.

**For cost optimization**: Option 2 (Hybrid Cloud API) -- best long-term economics for sporadic usage.

**Not recommended**: Option 3 adds architectural complexity without sufficient benefit over Option 2.

## Implementation Estimate

### Path A: GPU Cloud VPS (Option 1)

| Phase | Effort | Description |
|-------|--------|-------------|
| 1. Provider selection | 2 hours | Compare Vast.ai vs RunPod vs Lambda for target GPU |
| 2. docker-compose.yml updates | 2 hours | Add state volume, update legacy mounts, add health check |
| 3. Secret management | 2 hours | Document cloud-specific secret injection (env vars or provider secrets) |
| 4. Deployment docs | 4 hours | Step-by-step guide, verification checklist, rollback procedure |
| 5. Testing on cloud | 4 hours | End-to-end pipeline test with real recording |
| **Total** | **~2 days** | |

### Path B: Hybrid Cloud API (Option 2)

| Phase | Effort | Description |
|-------|--------|-------------|
| 1. TranscriberAPI module | 8 hours | OpenAI Whisper API client, same Segment interface |
| 2. Config extension | 2 hours | Add `whisper.backend: "local" | "api"` config option |
| 3. CPU Dockerfile | 2 hours | Slim image without CUDA |
| 4. Tests | 4 hours | Unit tests for new transcriber, integration test |
| 5. VPS deployment | 4 hours | Deploy to DigitalOcean/Hetzner, configure secrets |
| 6. Documentation | 4 hours | Deployment guide, cost comparison |
| **Total** | **~3-5 days** | |

### Dependencies

- **Internal**: None (Docker support already complete)
- **External (Path A)**: Cloud GPU provider account, payment method
- **External (Path B)**: OpenAI API key or Deepgram API key, CPU VPS account

## Risks and Mitigations

### R1: GPU Cloud Cost Exceeds Budget (Path A)
- **Likelihood**: High (for sporadic usage)
- **Impact**: High (ongoing monthly cost with no meeting volume to justify it)
- **Mitigation**: Start with Vast.ai spot pricing (cheapest), set budget alerts, evaluate Path B if costs are unjustified after 1 month

### R2: Cloud Instance Preemption (Path A, Vast.ai/RunPod Community)
- **Likelihood**: Medium
- **Impact**: Medium (bot goes offline temporarily, misses recordings)
- **Mitigation**: Use `restart: always` policy, pick providers with SLA guarantees (Secure Cloud tier), or accept occasional missed recordings

### R3: Whisper API Accuracy Differs from Self-Hosted (Path B)
- **Likelihood**: Low-Medium
- **Impact**: Low (OpenAI Whisper API uses similar models; Deepgram is competitive)
- **Mitigation**: Run parallel comparison on 3-5 real recordings before switching. Fall back to self-hosted if quality degrades.

### R4: State Data Loss on Cloud Volume Failure
- **Likelihood**: Low
- **Impact**: Medium (lose dedup state and minutes cache; archive DB loss is worse)
- **Mitigation**: Regular backups of `state/` directory. SQLite archive is the most valuable -- add periodic backup to object storage.

### R5: Network Latency Increases Pipeline Time (Path B)
- **Likelihood**: Medium
- **Impact**: Low (users already wait minutes for transcription; API latency adds seconds, not minutes)
- **Mitigation**: Use batch/async API endpoints where available. Pipeline already has generous timeout (3600s).

### R6: Vendor Lock-in
- **Likelihood**: Low
- **Impact**: Low (Docker portability means switching providers is straightforward)
- **Mitigation**: Keep provider-specific config in docker-compose.yml and deployment docs, not in application code.

## Cost Summary

| Scenario | Monthly Cost | Notes |
|----------|-------------|-------|
| **Current (local PC)** | ~$0 (electricity only) | Requires PC always on |
| **Path A: GPU VPS (Vast.ai)** | $50-130/mo | Cheapest GPU always-on |
| **Path A: GPU VPS (RunPod)** | $137-245/mo | More reliable than Vast.ai |
| **Path A: GPU VPS (AWS g4dn)** | $226-384/mo | Enterprise reliability |
| **Path B: API Hybrid** | $7-17/mo | Best for sporadic usage |
| **Path C: Serverless GPU** | $10-25/mo | Complex architecture |

For reference: 10 meetings/month x 30 min = 5 hours of audio to transcribe. Self-hosted Whisper large-v3 on GPU processes this in ~30-90 minutes of GPU time. The remaining ~99% of the month, the GPU sits idle.

## Next Steps

Based on the CONDITIONAL GO recommendation:

1. **Decide on path**: Evaluate current meeting frequency and budget tolerance
   - If >20 meetings/month and budget allows $100+/mo: Path A (GPU Cloud)
   - If <20 meetings/month or budget-conscious: Path B (API Hybrid) -- recommended
   - If local PC is working fine: Defer entirely (Path C)

2. **If Path A chosen**:
   - Create accounts on Vast.ai and RunPod
   - Test deploy existing Docker image on each
   - Benchmark end-to-end pipeline latency
   - Write deployment documentation

3. **If Path B chosen**:
   - Create a separate REQUEST.md for "Cloud STT API backend" feature
   - Implement `TranscriberAPI` with same `Segment` interface as current `Transcriber`
   - Add `whisper.backend` config option for selecting local vs API
   - Create slim CPU-only Dockerfile
   - Deploy to affordable VPS provider

4. **Regardless of path**:
   - Update `docker-compose.yml` to mount `state/` as a persistent volume (currently only mounts legacy `processed_files.json`)
   - Add basic health check endpoint to bot.py
   - Document secret management for cloud environments
