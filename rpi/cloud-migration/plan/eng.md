# Cloud Migration (API Hybrid) -- Technical Specification

## Architecture Overview

### Current Architecture

```
bot.py
  -> Transcriber(WhisperConfig)       # GPU-dependent, faster-whisper
      -> load_model()                 # loads large-v3 into VRAM
      -> transcribe_all(tracks)       # returns list[Segment]
  -> pipeline.py
      -> _stage_transcribe(transcriber, tracks)  # asyncio.to_thread()
```

The GPU dependency is isolated to a single class (`Transcriber` in `src/transcriber.py`) and a single pipeline stage (`_stage_transcribe` in `src/pipeline.py`). The `Segment` dataclass is the contract between transcription and downstream stages (merger, generator, poster).

### Target Architecture

```
bot.py
  -> create_transcriber(WhisperConfig)   # factory function
      |
      +-- backend="local" --> Transcriber          # existing, unchanged
      +-- backend="api"   --> TranscriberAPI        # new, OpenAI STT
      |
      both implement:
        .load_model() / .load()
        .is_loaded -> bool
        .transcribe_file(path, speaker) -> list[Segment]
        .transcribe_all(tracks) -> list[Segment]

  -> pipeline.py                        # unchanged, uses duck typing
      -> _stage_transcribe(transcriber, tracks)  # works with either backend
```

### Key Design Decisions

1. **Duck typing over ABC**: The pipeline already calls `transcriber.transcribe_all()` and `transcriber.is_loaded`. Rather than introducing a formal ABC (which would require modifying the existing `Transcriber` class), the new `TranscriberAPI` implements the same public interface. This preserves backward compatibility with zero changes to existing code.

2. **Factory function in transcriber module**: A `create_transcriber()` function in `src/transcriber.py` returns either `Transcriber` or `TranscriberAPI` based on config. This keeps the import surface stable -- `bot.py` and `pipeline.py` only import from `src/transcriber`.

3. **Shared `Segment` dataclass**: Both backends produce identical `Segment` objects. No changes needed in merger, generator, or poster.

4. **Conditional import**: `TranscriberAPI` lives in `src/transcriber_api.py` and imports `openai` at module level. The factory function imports it lazily to avoid requiring the `openai` package when `backend: local`.

5. **Reuse existing `WhisperConfig`**: Extend with new fields rather than creating a separate config class. Fields irrelevant to the selected backend are silently ignored.

## Module Design

### `src/transcriber_api.py` (New)

```python
"""OpenAI Speech-to-Text API transcription backend."""

class TranscriberAPI:
    """Transcribes audio files via OpenAI's Whisper API.

    Implements the same public interface as Transcriber for seamless
    pipeline integration.
    """

    def __init__(self, cfg: WhisperConfig) -> None:
        self._cfg = cfg
        self._client: openai.OpenAI | None = None

    def load_model(self) -> None:
        """Initialize the OpenAI client. Named load_model for interface compat."""
        # Validates API key availability
        # Creates openai.OpenAI client instance
        pass

    @property
    def is_loaded(self) -> bool:
        return self._client is not None

    @property
    def backend_name(self) -> str:
        return "api"

    @property
    def model_name(self) -> str:
        return self._cfg.api_model

    def transcribe_file(self, audio_path: Path, speaker_name: str) -> list[Segment]:
        """Transcribe a single audio file via OpenAI API."""
        # 1. Open file
        # 2. Call client.audio.transcriptions.create() with verbose_json
        # 3. Parse response segments into Segment dataclass
        # 4. Log duration, segment count, cost estimate
        pass

    def transcribe_all(self, tracks: list[SpeakerAudio]) -> list[Segment]:
        """Transcribe all speaker audio tracks sequentially."""
        # Same structure as Transcriber.transcribe_all()
        pass
```

### OpenAI API Integration Details

**Endpoint**: `POST https://api.openai.com/v1/audio/transcriptions`

**Request**:
```python
response = client.audio.transcriptions.create(
    model="whisper-1",
    file=open(audio_path, "rb"),
    language=language,            # "ja", "en", etc. (None for auto)
    response_format="verbose_json",  # includes segment timestamps
    timestamp_granularities=["segment"],
)
```

**Response** (`verbose_json` format):
```json
{
  "text": "Full transcription text...",
  "language": "ja",
  "duration": 1800.0,
  "segments": [
    {
      "start": 0.0,
      "end": 3.5,
      "text": "Segment text..."
    }
  ]
}
```

**Mapping to `Segment`**:
```python
Segment(
    start=api_seg["start"],   # or api_seg.start with Pydantic model
    end=api_seg["end"],
    text=api_seg["text"].strip(),
    speaker=speaker_name,      # from Craig ZIP filename, same as local
)
```

### File Size Handling

OpenAI API limit: **25 MB per file**.

Craig AAC recordings at 48 kHz stereo:
- 30 min recording: ~14-15 MB (well within limit)
- 60 min recording: ~28-30 MB (may exceed)
- 90 min recording: ~42-45 MB (exceeds)

**Strategy**: Check file size before upload. If > 25 MB, raise `TranscriptionError` with a clear message suggesting local Whisper or shorter recordings. Future enhancement: split large files with FFmpeg (out of scope for v1).

### `src/transcriber.py` Modifications

Minimal changes to the existing file:

1. Add `backend_name` and `model_name` properties to existing `Transcriber` class (for status display)
2. Add `create_transcriber()` factory function

```python
# New properties on existing Transcriber class
@property
def backend_name(self) -> str:
    return "local"

@property
def model_name(self) -> str:
    return self._cfg.model

# New factory function
def create_transcriber(cfg: WhisperConfig) -> Transcriber | TranscriberAPI:
    """Create the appropriate transcriber based on config."""
    if cfg.backend == "api":
        from src.transcriber_api import TranscriberAPI
        return TranscriberAPI(cfg)
    return Transcriber(cfg)
```

### `src/config.py` Modifications

Extend `WhisperConfig` with new fields:

```python
@dataclass(frozen=True)
class WhisperConfig:
    # Existing fields (unchanged)
    model: str = "large-v3"
    language: str = "ja"
    device: str = "cuda"
    compute_type: str = "float16"
    beam_size: int = 5
    vad_filter: bool = True

    # New fields
    backend: str = "local"          # "local" or "api"
    api_model: str = "whisper-1"    # OpenAI model name
    api_max_retries: int = 2        # Retry count for API calls
    api_timeout_sec: int = 300      # Per-request timeout
```

Extend validation in `_validate()`:

```python
# Whisper backend validation
if cfg.whisper.backend not in ("local", "api"):
    errors.append("whisper.backend must be 'local' or 'api'")

if cfg.whisper.backend == "api":
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        errors.append("OPENAI_API_KEY is required when whisper.backend is 'api'")
    if cfg.whisper.api_timeout_sec < 10:
        errors.append("whisper.api_timeout_sec must be >= 10")
elif cfg.whisper.backend == "local":
    # Existing local whisper validation (model name, language, etc.)
    ...
```

### `bot.py` Modifications

Replace direct `Transcriber` instantiation with factory:

```python
# Before:
from src.transcriber import Transcriber
transcriber = Transcriber(cfg.whisper)
transcriber.load_model()

# After:
from src.transcriber import create_transcriber
transcriber = create_transcriber(cfg.whisper)
transcriber.load_model()
```

Update `/minutes status` command to use `backend_name` and `model_name` properties.

### `pipeline.py` Modifications

**None required.** The pipeline uses `transcriber.transcribe_all()` and `transcriber.is_loaded` which both backends implement. The type annotation can be relaxed from `Transcriber` to `Transcriber | TranscriberAPI` or a Protocol, but this is optional since Python uses duck typing.

For type safety, define a Protocol:

```python
# src/transcriber.py
from typing import Protocol

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

### `src/errors.py` Modifications

No new exception classes needed. `TranscriptionError` (stage="transcription") covers all transcription failures regardless of backend.

## Docker

### `Dockerfile.cpu` (New)

```dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -r -m -s /bin/false botuser

WORKDIR /app

COPY requirements-cpu.txt .
RUN pip install --no-cache-dir -r requirements-cpu.txt

COPY bot.py ./
COPY src/ src/
COPY prompts/ prompts/

RUN mkdir -p logs state && chown -R botuser:botuser /app

USER botuser

CMD ["python", "bot.py"]
```

### `requirements-cpu.txt` (New)

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

Note: `faster-whisper` is NOT included. The `openai` package replaces it.

### `docker-compose.cpu.yml` (New)

```yaml
services:
  bot:
    build:
      context: .
      dockerfile: Dockerfile.cpu
    container_name: discord-minutes-bot
    restart: on-failure
    volumes:
      - ./.env:/app/.env:ro
      - ./config.yaml:/app/config.yaml:ro
      - ./credentials.json:/app/credentials.json:ro
      - ./state:/app/state
      - ./logs:/app/logs
    # No GPU section -- runs on CPU only
```

### `docker-compose.yml` Updates

Update the existing GPU compose file to mount `state/` directory properly (replacing legacy `processed_files.json` mount):

```yaml
volumes:
  - ./.env:/app/.env:ro
  - ./config.yaml:/app/config.yaml:ro
  - ./credentials.json:/app/credentials.json:ro
  - ./state:/app/state              # NEW: replaces processed_files.json
  - ./logs:/app/logs
  - whisper-cache:/app/.cache/huggingface
```

## Testing Strategy

### Unit Tests (`tests/test_transcriber_api.py`)

| Test | Description |
|------|-------------|
| `test_not_loaded_by_default` | `TranscriberAPI.is_loaded` is False before `load_model()` |
| `test_load_model_creates_client` | `load_model()` creates OpenAI client |
| `test_load_model_idempotent` | Second `load_model()` call is no-op |
| `test_load_model_no_api_key_raises` | Missing `OPENAI_API_KEY` raises `TranscriptionError` |
| `test_transcribe_file_before_load_raises` | Calling `transcribe_file()` before `load_model()` raises |
| `test_transcribe_file_missing_file` | Non-existent file raises `TranscriptionError` |
| `test_transcribe_file_with_mock` | Mocked API returns proper `Segment` list |
| `test_transcribe_file_empty_segments_filtered` | Blank text segments are stripped |
| `test_transcribe_all_with_mock` | Multiple tracks produce combined segments |
| `test_auto_language_passes_none` | `language="auto"` sends no language param to API |
| `test_explicit_language_passes_through` | `language="ja"` passed to API |
| `test_file_too_large_raises` | File > 25 MB raises `TranscriptionError` |
| `test_api_error_retries` | Transient API errors trigger retry |
| `test_api_auth_error_no_retry` | 401 error raises immediately (no retry) |
| `test_backend_name_property` | `.backend_name` returns `"api"` |
| `test_model_name_property` | `.model_name` returns configured model |
| `test_cost_estimate_in_log` | Log output includes cost estimate |

### Unit Tests (`tests/test_transcriber.py` modifications)

| Test | Description |
|------|-------------|
| `test_backend_name_property` | Existing `Transcriber.backend_name` returns `"local"` |
| `test_model_name_property` | Existing `Transcriber.model_name` returns configured model |

### Factory Tests

| Test | Description |
|------|-------------|
| `test_create_transcriber_local` | `backend="local"` returns `Transcriber` instance |
| `test_create_transcriber_api` | `backend="api"` returns `TranscriberAPI` instance |
| `test_create_transcriber_invalid` | Invalid backend raises `ConfigError` |

### Config Tests (`tests/test_config.py` additions)

| Test | Description |
|------|-------------|
| `test_whisper_backend_default` | Missing `backend` field defaults to `"local"` |
| `test_whisper_backend_api_valid` | `backend: api` loads successfully |
| `test_whisper_backend_invalid` | `backend: foo` fails validation |
| `test_api_key_required_for_api_backend` | `backend: api` without `OPENAI_API_KEY` fails validation |
| `test_api_fields_ignored_for_local` | `api_model` etc. accepted but unused for local |
| `test_env_var_override_backend` | `WHISPER_BACKEND=api` overrides config |

### Pipeline Integration Tests

| Test | Description |
|------|-------------|
| `test_pipeline_with_api_transcriber` | Full pipeline with mocked `TranscriberAPI` |
| `test_stage_transcribe_works_with_api` | `_stage_transcribe` accepts `TranscriberAPI` |

## Dependency Changes

| Package | Current | After (GPU) | After (CPU) |
|---------|---------|-------------|-------------|
| `faster-whisper` | Required | Required | Not needed |
| `openai` | Not used | Optional | Required |
| `ctranslate2` | Required (via faster-whisper) | Required | Not needed |
| `torch` / `nvidia-*` | Required (via faster-whisper) | Required | Not needed |

The `openai` package is ~2 MB with minimal transitive dependencies (`httpx`, `pydantic`). It does not conflict with any existing dependencies.

## Rollback Plan

1. Set `whisper.backend: local` in config.yaml
2. Restart bot
3. Everything reverts to GPU transcription

No data migration, no state changes, no destructive operations. The switch is purely config-driven.

## Security Considerations

- `OPENAI_API_KEY` follows the same `.env` injection pattern as `ANTHROPIC_API_KEY`
- Audio files are uploaded to OpenAI for transcription (data leaves the system) -- operators should be aware of this privacy implication
- No new network ports opened; all communication is outbound HTTPS
- API key is masked in logs using the existing `_SensitiveMaskFilter` (pattern: `sk-` prefix)

## Performance Comparison

| Metric | Local Whisper (GPU) | OpenAI API |
|--------|-------------------|------------|
| 30-min file transcription | ~3-5 min (RTX 3060) | ~30-60 sec (API-side processing) |
| Network transfer | None (local) | ~15 MB upload + ~50 KB response |
| First-use latency | 4-8 sec model load | < 1 sec client init |
| Concurrent capacity | 1 file at a time (VRAM) | Rate-limited by API |
| Offline capability | Yes | No (requires internet) |
