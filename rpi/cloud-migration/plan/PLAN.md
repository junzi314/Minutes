# Cloud Migration (API Hybrid) -- Implementation Plan

## Summary

Replace GPU-dependent local Whisper with OpenAI Speech-to-Text API as a config-selectable backend, then provide CPU-only Docker artifacts for cheap VPS deployment. Estimated effort: **3-5 working days** across 4 phases.

## Prerequisites

- OpenAI API account with API key (for integration testing)
- Research report reviewed: `rpi/cloud-migration/research/RESEARCH.md`
- Related specs: `pm.md`, `ux.md`, `eng.md` in this directory

## Phase 1: Config Extension & Transcriber Protocol (Day 1)

**Goal**: Extend the config system and establish the interface contract for pluggable transcription backends.

### Task 1.1: Extend `WhisperConfig` with new fields

**File**: `src/config.py`

Add the following fields to `WhisperConfig`:
```python
backend: str = "local"          # "local" or "api"
api_model: str = "whisper-1"    # OpenAI model name
api_max_retries: int = 2        # Retry count for API calls
api_timeout_sec: int = 300      # Per-request timeout (seconds)
```

Add validation rules to `_validate()`:
- `whisper.backend` must be `"local"` or `"api"`
- When `backend == "api"`: require `OPENAI_API_KEY` env var, `api_timeout_sec >= 10`
- When `backend == "local"`: existing validation unchanged (model name, language, etc.)
- Move model-name and language validation inside the `backend == "local"` branch so API users are not forced to set valid local model names

**Acceptance**: Existing tests pass. New config fields accepted. Invalid backend rejected.

### Task 1.2: Add `backend_name` / `model_name` properties to `Transcriber`

**File**: `src/transcriber.py`

Add two read-only properties:
```python
@property
def backend_name(self) -> str:
    return "local"

@property
def model_name(self) -> str:
    return self._cfg.model
```

These properties enable `/minutes status` to display backend info polymorphically.

**Acceptance**: Properties return correct values. No behavioral changes.

### Task 1.3: Define `TranscriberProtocol`

**File**: `src/transcriber.py`

Add a `typing.Protocol` class that documents the interface contract:
```python
class TranscriberProtocol(Protocol):
    @property
    def is_loaded(self) -> bool: ...
    @property
    def backend_name(self) -> str: ...
    @property
    def model_name(self) -> str: ...
    def load_model(self) -> None: ...
    def transcribe_file(self, audio_path: Path, speaker_name: str) -> list[Segment]: ...
    def transcribe_all(self, tracks: list[SpeakerAudio]) -> list[Segment]: ...
```

Update type hints in `pipeline.py` to use `TranscriberProtocol` instead of `Transcriber` (optional but recommended for correctness).

**Acceptance**: mypy/pyright passes (if configured). No runtime behavioral changes.

### Task 1.4: Write config tests

**File**: `tests/test_config.py`

Add tests for:
- `backend` defaults to `"local"` when absent
- `backend: api` is accepted
- `backend: invalid` is rejected
- `OPENAI_API_KEY` required when `backend: api`
- API-specific fields have sensible defaults
- `WHISPER_BACKEND` env var override works
- Local-specific validation (model name) skipped when `backend: api`

**Acceptance**: All new tests pass. All existing tests pass.

---

## Phase 2: TranscriberAPI Implementation (Day 2)

**Goal**: Implement the OpenAI STT API backend with full error handling.

### Task 2.1: Create `src/transcriber_api.py`

**File**: `src/transcriber_api.py` (new)

Implement `TranscriberAPI` class with:

```python
class TranscriberAPI:
    def __init__(self, cfg: WhisperConfig) -> None: ...
    def load_model(self) -> None: ...    # init OpenAI client
    @property
    def is_loaded(self) -> bool: ...
    @property
    def backend_name(self) -> str: ...   # returns "api"
    @property
    def model_name(self) -> str: ...     # returns cfg.api_model
    def transcribe_file(self, audio_path: Path, speaker_name: str) -> list[Segment]: ...
    def transcribe_all(self, tracks: list[SpeakerAudio]) -> list[Segment]: ...
```

Key implementation details:
- `load_model()`: Read `OPENAI_API_KEY` from env, create `openai.OpenAI(api_key=...)`, log backend info
- `transcribe_file()`:
  1. Check file exists
  2. Check file size < 25 MB (raise `TranscriptionError` if exceeded)
  3. Resolve language (`"auto"` -> no language param)
  4. Call `client.audio.transcriptions.create(model=..., file=..., response_format="verbose_json", timestamp_granularities=["segment"])` with retry logic
  5. Parse response segments into `Segment(start, end, text.strip(), speaker_name)`
  6. Filter empty-text segments
  7. Log: segment count, elapsed time, cost estimate (`duration * 0.006 / 60`)
- `transcribe_all()`: Iterate tracks sequentially (same as `Transcriber.transcribe_all`)
- Error handling:
  - `openai.AuthenticationError` -> `TranscriptionError("OpenAI API authentication failed...")`
  - `openai.RateLimitError` -> retry with exponential backoff
  - `openai.APIStatusError` (5xx) -> retry with exponential backoff
  - `openai.APIConnectionError` -> retry with exponential backoff
  - Non-retryable 4xx -> raise `TranscriptionError` immediately
  - All retries exhausted -> raise `TranscriptionError`

**Acceptance**: All public methods work with mocked OpenAI client. Error paths tested.

### Task 2.2: Add factory function `create_transcriber()`

**File**: `src/transcriber.py`

```python
def create_transcriber(cfg: WhisperConfig) -> Transcriber | TranscriberAPI:
    if cfg.backend == "api":
        from src.transcriber_api import TranscriberAPI
        return TranscriberAPI(cfg)
    if cfg.backend == "local":
        return Transcriber(cfg)
    raise ConfigError(f"Unknown whisper backend: {cfg.backend}")
```

Lazy import of `TranscriberAPI` ensures `openai` package is only imported when needed.

**Acceptance**: Factory returns correct type. `local` does not import `openai`. Invalid backend raises.

### Task 2.3: Write TranscriberAPI unit tests

**File**: `tests/test_transcriber_api.py` (new)

Test matrix (see `eng.md` for full list):
- Interface conformance (same methods as `Transcriber`)
- Mock-based transcription with segment parsing
- Empty/blank segment filtering
- Language handling (auto vs explicit)
- File size validation (> 25 MB)
- Retry behavior on transient errors
- No-retry on auth errors
- Cost estimate logging
- `backend_name` / `model_name` properties

**Acceptance**: 15+ tests, all passing. 100% branch coverage of `transcriber_api.py`.

---

## Phase 3: Integration & Bot Wiring (Day 3)

**Goal**: Wire everything together so the bot uses the factory and status display is updated.

### Task 3.1: Update `bot.py` to use `create_transcriber()`

**File**: `bot.py`

Replace:
```python
from src.transcriber import Transcriber
transcriber = Transcriber(cfg.whisper)
transcriber.load_model()
```

With:
```python
from src.transcriber import create_transcriber
transcriber = create_transcriber(cfg.whisper)
transcriber.load_model()
```

**Acceptance**: Bot starts successfully with both `backend: local` and `backend: api`.

### Task 3.2: Update `/minutes status` command

**File**: `bot.py`, inside `minutes_status` command handler

Replace the current "Whisper model" and "GPU" lines with backend-aware display:
- `backend == "api"`: `Transcription: OpenAI API (model=whisper-1)` + `GPU: not required`
- `backend == "local"`: `Transcription: local Whisper large-v3 (loaded)` + `GPU: available/not available`

Use `transcriber.backend_name` and `transcriber.model_name` properties.

**Acceptance**: Status output matches spec in `ux.md`.

### Task 3.3: Update `pipeline.py` type hints (optional)

**File**: `src/pipeline.py`

Change `transcriber: Transcriber` parameter hints to accept both backends. Options:
- `transcriber: Transcriber | TranscriberAPI` (explicit union)
- `transcriber: TranscriberProtocol` (Protocol-based)
- Leave as `Transcriber` and rely on duck typing (simplest, works fine)

Recommended: Use the Protocol import for correctness without runtime cost.

**Acceptance**: No runtime behavioral changes. Type checker passes.

### Task 3.4: Update log masking for OpenAI API keys

**File**: `bot.py`

Add OpenAI key pattern to `_SENSITIVE_PATTERNS`:
```python
r"|(sk-[a-zA-Z0-9]{20,})"  # OpenAI API keys
```

Note: The existing pattern for Anthropic keys (`sk-ant-...`) is a subset. Need to ensure the broader `sk-` pattern does not over-match. Use a more specific pattern if needed: `sk-proj-[a-zA-Z0-9_-]{20,}` (OpenAI project keys).

**Acceptance**: OpenAI API keys are masked in log output.

### Task 3.5: Pipeline integration tests

**File**: `tests/test_pipeline.py`

Add test:
- `test_stage_transcribe_with_api_backend`: Mock `TranscriberAPI` in place of `Transcriber`, verify pipeline runs identically

**Acceptance**: Pipeline works with both backend types.

---

## Phase 4: Docker & Documentation (Day 4-5)

**Goal**: Provide CPU-only deployment artifacts and documentation.

### Task 4.1: Create `Dockerfile.cpu`

**File**: `Dockerfile.cpu` (new)

Based on `python:3.12-slim`. No CUDA. Install only CPU dependencies (`requirements-cpu.txt`). Include FFmpeg for potential audio preprocessing. Target image size: < 500 MB.

**Acceptance**: Image builds. Container starts with `backend: api` config. Full pipeline works.

### Task 4.2: Create `requirements-cpu.txt`

**File**: `requirements-cpu.txt` (new)

Same as `requirements.txt` but replacing `faster-whisper` with `openai`:
```
discord.py>=2.3,<3.0
openai>=1.0,<2.0
anthropic>=0.40,<1.0
aiohttp>=3.9,<4.0
PyYAML>=6.0,<7.0
python-dotenv>=1.0,<2.0
google-api-python-client>=2.0,<3.0
google-auth>=2.0,<3.0
```

**Acceptance**: `pip install -r requirements-cpu.txt` succeeds without GPU libraries.

### Task 4.3: Create `docker-compose.cpu.yml`

**File**: `docker-compose.cpu.yml` (new)

CPU-only compose without GPU reservation. Mounts `state/` directory. No `whisper-cache` volume needed.

**Acceptance**: `docker compose -f docker-compose.cpu.yml up -d --build` starts bot successfully.

### Task 4.4: Update existing `docker-compose.yml`

**File**: `docker-compose.yml`

Replace legacy `processed_files.json` mount with `state/` directory mount:
```yaml
# Before:
- ./processed_files.json:/app/processed_files.json
# After:
- ./state:/app/state
```

**Acceptance**: Existing GPU deployment works with updated compose file.

### Task 4.5: Add `openai` to main `requirements.txt` as optional

**File**: `requirements.txt`

Add `openai` as an optional dependency with comment:
```
# Optional: required only when whisper.backend=api
openai>=1.0,<2.0
```

This ensures `pip install -r requirements.txt` on a GPU machine also gets the `openai` package (allows switching backends without reinstalling). The package is lightweight (~2 MB).

**Acceptance**: `pip install -r requirements.txt` succeeds on both GPU and CPU environments.

### Task 4.6: Update `config.yaml` with commented examples

**File**: `config.yaml`

Add commented-out API backend fields under `whisper:` section:
```yaml
whisper:
  # Transcription backend: "local" (self-hosted Whisper) or "api" (OpenAI STT)
  # backend: "api"
  # api_model: "whisper-1"
  # api_max_retries: 2
  # api_timeout_sec: 300
```

**Acceptance**: Config file is self-documenting for operators.

---

## Validation Gates

Each phase has an explicit validation gate before proceeding:

### Gate 1 (after Phase 1)
```bash
pytest tests/test_config.py -v          # All pass (including new backend tests)
pytest tests/test_transcriber.py -v     # All pass (existing + new property tests)
```

### Gate 2 (after Phase 2)
```bash
pytest tests/test_transcriber_api.py -v  # All pass (15+ tests)
pytest tests/test_transcriber.py -v      # All still pass
```

### Gate 3 (after Phase 3)
```bash
pytest -v                               # Full test suite passes (150+ tests)
# Manual: start bot with backend=api and mock OPENAI_API_KEY, check logs
```

### Gate 4 (after Phase 4)
```bash
docker build -f Dockerfile.cpu -t minutes-bot:cpu .    # Builds < 500 MB
docker compose -f docker-compose.cpu.yml config        # Valid compose
# Manual: end-to-end test with real OpenAI API key + Craig recording
```

## File Change Summary

| File | Change Type | Phase |
|------|------------|-------|
| `src/config.py` | Modified | 1 |
| `src/transcriber.py` | Modified | 1, 2 |
| `src/transcriber_api.py` | **New** | 2 |
| `bot.py` | Modified | 3 |
| `src/pipeline.py` | Modified (type hints only) | 3 |
| `tests/test_config.py` | Modified | 1 |
| `tests/test_transcriber.py` | Modified | 1 |
| `tests/test_transcriber_api.py` | **New** | 2 |
| `tests/test_pipeline.py` | Modified | 3 |
| `Dockerfile.cpu` | **New** | 4 |
| `requirements-cpu.txt` | **New** | 4 |
| `docker-compose.cpu.yml` | **New** | 4 |
| `docker-compose.yml` | Modified | 4 |
| `requirements.txt` | Modified | 4 |
| `config.yaml` | Modified | 4 |

**Total**: 9 modified files, 5 new files, 0 deleted files.

## Risk Mitigations

| Risk | Mitigation | Phase |
|------|-----------|-------|
| `openai` package conflicts | Pin version `>=1.0,<2.0`; test in CI | 4 |
| `faster-whisper` import fails on CPU-only env | Lazy import via factory; `TranscriberAPI` never imports `faster_whisper` | 2 |
| OpenAI API response format changes | Pin API model `whisper-1`; version-guard response parsing | 2 |
| Test suite becomes slow with real API calls | All API tests use mocks; no network calls in CI | 2-3 |
| Existing tests break | Run full test suite at every gate | All |

## Out of Scope (Future Work)

- Deepgram as a third backend option
- Automatic large-file splitting via FFmpeg
- Backend failover (local -> API on GPU error)
- Live backend switching via slash command (without restart)
- CI/CD pipeline for automated deployment
- Health check HTTP endpoint
- Cost tracking dashboard
