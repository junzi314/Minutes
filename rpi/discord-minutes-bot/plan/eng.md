# Technical Specification: Discord Meeting Auto-Minutes Generation System

**Version:** 1.0
**Date:** 2026-02-10
**Status:** Pre-Implementation
**Upstream Documents:** `docs/requirements.md`, `rpi/discord-minutes-bot/research/RESEARCH.md`, `reports/product-viability-analysis.md`

---

## 1. System Architecture

### 1.1 Overview

Single Python process running a discord.py bot. Linear pipeline triggered by Craig Bot recording events. No database, no Docker, no microservices, no web framework.

```
                          Discord Gateway (WebSocket)
                                    |
                                    v
                    +-------------------------------+
                    |           bot.py              |
                    |  (argparse, logging, bot.run) |
                    +-------------------------------+
                                    |
                    +-------------------------------+
                    |        detector.py            |
                    |  on_raw_message_update        |
                    |  Craig panel "Recording ended" |
                    +-------------------------------+
                                    |
                                    v
                    +-------------------------------+
                    |        pipeline.py            |
                    |  Orchestrator: asyncio.Task   |
                    +-------------------------------+
                         |      |      |       |
                         v      v      v       v
                    +------+------+------+------+
                    |craig |trans |merger|gener |
                    |client|criber|      |ator  |
                    +------+------+------+------+
                                                |
                                                v
                                    +------------------+
                                    |    poster.py     |
                                    | Embed + file send|
                                    +------------------+
```

### 1.2 Module Diagram

```
bot.py
  imports: config, detector, pipeline

config.py
  imports: (stdlib only: yaml, dotenv, dataclasses, os, pathlib)

detector.py
  imports: config
  produces: DetectedRecording

pipeline.py
  imports: audio_source, craig_client, transcriber, merger, generator, poster, errors
  consumes: DetectedRecording
  produces: PipelineResult

audio_source.py
  imports: (ABC only)
  defines: AudioSource interface

craig_client.py
  imports: audio_source, config
  implements: AudioSource
  produces: list[SpeakerAudio]

transcriber.py
  imports: config
  consumes: SpeakerAudio
  produces: SpeakerTranscript

merger.py
  imports: (pure function, no external imports beyond dataclasses)
  consumes: list[SpeakerTranscript]
  produces: str (merged transcript)

generator.py
  imports: config
  consumes: str (merged transcript)
  produces: str (minutes markdown)

poster.py
  imports: config
  consumes: str (minutes markdown)
  produces: list[int] (message IDs)

errors.py
  imports: (stdlib only)
```

### 1.3 Data Flow

```
on_raw_message_update (Craig panel edited to "Recording ended")
    |
    v
detector.parse_recording_ended(payload) -> DetectedRecording
    |
    v
pipeline.run(detected) -> PipelineResult
    |
    +---> craig_client.get_speakers(rec_id, key) -> list[SpeakerInfo]
    |
    +---> craig_client.download(rec_id, key) -> list[SpeakerAudio]
    |       (POST /cook -> ZIP -> extract to temp dir)
    |
    +---> transcriber.transcribe(speaker_audio) -> SpeakerTranscript
    |       (sequential per speaker, asyncio.to_thread for GPU work)
    |
    +---> merger.merge(transcripts) -> str
    |       (timestamp-sorted interleaving)
    |
    +---> generator.generate(merged_transcript) -> str
    |       (Anthropic API: prompt template + transcript -> minutes)
    |
    +---> poster.post(channel, minutes_md) -> list[int]
    |       (Embed summary + .md file attachment)
    |
    +---> cleanup temp dir (finally block)
```

### 1.4 Startup Sequence

```python
# bot.py pseudocode
def main():
    args = parse_args()                          # --config, --log-level
    setup_logging(args.log_level)                # rotating file + stderr
    cfg = config.load(args.config)               # YAML + .env -> Config
    model = transcriber.load_model(cfg.whisper)   # preload into VRAM (~15-30s)
    bot = create_bot(cfg, model)                  # discord.py Bot instance
    bot.run(cfg.discord.token)                    # blocking; runs event loop
```

The faster-whisper model is loaded once at startup and kept resident in VRAM for the lifetime of the process. This eliminates the 15-30 second cold-start penalty on each pipeline run.

---

## 2. Data Structures

All dataclasses are frozen (immutable) to prevent accidental mutation during pipeline processing.

### 2.1 DetectedRecording

Produced by `detector.py` when a Craig panel edit is detected.

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class DetectedRecording:
    rec_id: str           # Craig recording ID (alphanumeric, e.g. "abc123def")
    access_key: str       # Per-recording access key from Craig URL
    rec_url: str          # Full URL: https://{domain}/rec/{id}?key={key}
    guild_id: int         # Discord server ID where the recording happened
    channel_id: int       # Text channel ID where the Craig panel was posted
    message_id: int       # Message ID of the Craig recording panel
    craig_domain: str     # Craig server domain (default: "craig.chat")
```

### 2.2 SpeakerInfo

Speaker metadata from the Craig users API.

```python
@dataclass(frozen=True)
class SpeakerInfo:
    track: int            # Track number in the Craig ZIP (1-indexed)
    username: str         # Discord display name at recording time
    user_id: int          # Discord user snowflake ID
```

### 2.3 SpeakerAudio

A speaker's audio file on disk, ready for transcription.

```python
from pathlib import Path


@dataclass(frozen=True)
class SpeakerAudio:
    speaker: SpeakerInfo  # Who this audio belongs to
    file_path: Path       # Absolute path to the extracted audio file in temp dir
```

### 2.4 TranscriptSegment

A single continuous utterance from faster-whisper output.

```python
@dataclass(frozen=True)
class TranscriptSegment:
    start: float          # Segment start time in seconds from recording start
    end: float            # Segment end time in seconds from recording start
    text: str             # Transcribed text content (stripped, non-empty)
```

### 2.5 SpeakerTranscript

All segments for a single speaker.

```python
@dataclass(frozen=True)
class SpeakerTranscript:
    speaker: SpeakerInfo
    segments: list[TranscriptSegment]  # Ordered by start time within this speaker
```

### 2.6 PipelineResult

Summary metrics returned after a successful pipeline run.

```python
@dataclass(frozen=True)
class PipelineResult:
    rec_id: str                    # Craig recording ID
    speaker_count: int             # Number of speakers processed
    audio_duration_sec: float      # Total audio duration across all speakers
    transcription_time_sec: float  # Wall-clock time for all transcriptions
    generation_time_sec: float     # Wall-clock time for LLM generation
    total_time_sec: float          # Wall-clock time from pipeline start to post
    minutes_char_count: int        # Character count of the generated minutes
    message_ids: list[int]         # Discord message IDs of posted messages
```

### 2.7 Config

Top-level configuration dataclass (loaded from YAML + .env).

```python
@dataclass(frozen=True)
class DiscordConfig:
    token: str                     # Bot token (from .env: DISCORD_TOKEN)
    guild_id: int                  # Target server ID
    watch_channel_id: int          # Channel where Craig posts recording panels
    output_channel_id: int         # Channel where minutes are posted
    error_mention_role_id: int | None  # Role ID to mention on error (optional)

@dataclass(frozen=True)
class CraigConfig:
    bot_id: str                    # Craig Bot user ID: "272937604339466240"
    domain: str                    # API domain: "craig.chat"
    cook_format: str               # Audio format: "aac"
    cook_container: str            # Container format: "zip"
    download_timeout_sec: int      # Timeout for cook + download: 300
    max_retries: int               # Retry count for download: 2

@dataclass(frozen=True)
class WhisperConfig:
    model: str                     # Model name: "large-v3"
    language: str                  # Language code: "ja"
    device: str                    # Device: "cuda"
    compute_type: str              # Precision: "float16"
    beam_size: int                 # Beam search width: 5
    vad_filter: bool               # Silero VAD filter: True

@dataclass(frozen=True)
class MergerConfig:
    timestamp_format: str          # Format string for timestamps: "[{mm}:{ss}]"
    min_segment_chars: int         # Drop segments shorter than this: 1
    gap_merge_threshold_sec: float # Merge same-speaker segments closer than: 1.0

@dataclass(frozen=True)
class GeneratorConfig:
    api_key: str                   # Anthropic API key (from .env: ANTHROPIC_API_KEY)
    model: str                     # Model ID: "claude-sonnet-4-5-20250929"
    max_tokens: int                # Max response tokens: 4096
    temperature: float             # Temperature: 0.3
    prompt_template_path: str      # Path to prompt file: "prompts/minutes.txt"
    max_retries: int               # Retry count for API calls: 2

@dataclass(frozen=True)
class PosterConfig:
    embed_color: int               # Embed color hex: 0x5865F2
    max_embed_length: int          # Max chars in embed description: 4000
    include_transcript: bool       # Attach raw transcript file: False
    chunk_size: int                # Max chars per Discord message: 1990

@dataclass(frozen=True)
class LoggingConfig:
    level: str                     # Log level: "INFO"
    file: str                      # Log file path: "logs/bot.log"
    max_bytes: int                 # Max log file size: 10_485_760 (10MB)
    backup_count: int              # Number of rotated log files: 5

@dataclass(frozen=True)
class Config:
    discord: DiscordConfig
    craig: CraigConfig
    whisper: WhisperConfig
    merger: MergerConfig
    generator: GeneratorConfig
    poster: PosterConfig
    logging: LoggingConfig
```

---

## 3. Module Interfaces

### 3.1 config.py -- Configuration Loader

Loads configuration from YAML file with .env secret interpolation and environment variable overrides.

```python
def load(config_path: str = "config.yaml", env_path: str = ".env") -> Config:
    """Load configuration from YAML file and .env secrets.

    Resolution order (highest priority wins):
    1. Environment variables (format: SECTION_KEY, e.g. WHISPER_MODEL)
    2. .env file values (for secrets: DISCORD_TOKEN, ANTHROPIC_API_KEY)
    3. config.yaml values
    4. Built-in defaults

    Args:
        config_path: Path to the YAML configuration file.
        env_path: Path to the .env file containing secrets.

    Returns:
        Frozen Config dataclass.

    Raises:
        FileNotFoundError: If config_path does not exist.
        ValueError: If required fields are missing or invalid.
    """
    ...
```

**Validation rules applied at load time:**

| Field | Rule |
|-------|------|
| `discord.token` | Non-empty string, starts with expected prefix |
| `discord.guild_id` | Positive integer |
| `discord.watch_channel_id` | Positive integer |
| `discord.output_channel_id` | Positive integer |
| `craig.bot_id` | Non-empty string of digits |
| `craig.download_timeout_sec` | Range 30-600 |
| `whisper.model` | One of: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `whisper.device` | One of: `cuda`, `cpu` |
| `whisper.compute_type` | One of: `float16`, `int8`, `float32` |
| `generator.api_key` | Non-empty string |
| `generator.temperature` | Range 0.0-1.0 |
| `poster.max_embed_length` | Range 100-4096 |
| `poster.chunk_size` | Range 100-2000 |

### 3.2 detector.py -- Craig Recording Detection

Monitors Discord gateway events for Craig Bot recording panel edits.

```python
import discord
import json
import re

CRAIG_BOT_ID: str = "272937604339466240"

# Regex for Craig recording URLs.
# Matches: https://craig.chat/rec/abc123?key=ABCDEF
#          https://craig.horse/rec/abc123?key=ABCDEF
RECORDING_URL_PATTERN: re.Pattern = re.compile(
    r"https?://(?P<domain>craig\.\w+)/rec/(?P<rec_id>[a-zA-Z0-9]+)\?key=(?P<key>[a-zA-Z0-9]+)"
)


def is_craig_message(payload: discord.RawMessageUpdateEvent) -> bool:
    """Check if the raw message update is from Craig Bot.

    Args:
        payload: Raw gateway MESSAGE_UPDATE event.

    Returns:
        True if the message author is Craig Bot.
    """
    ...


def is_recording_ended(payload: discord.RawMessageUpdateEvent) -> bool:
    """Check if the Craig panel was edited to the 'Recording ended' state.

    Detection strategy: serialize the components array to JSON and check
    for the presence of "Recording ended" as a substring. This avoids
    needing to parse Components V2 structures, which may not be fully
    supported by the installed discord.py version.

    Args:
        payload: Raw gateway MESSAGE_UPDATE event from Craig Bot.

    Returns:
        True if the message contains "Recording ended" in its components.
    """
    ...


def extract_recording_info(
    payload: discord.RawMessageUpdateEvent,
) -> DetectedRecording | None:
    """Extract recording ID, access key, and metadata from a Craig panel.

    Searches the raw payload JSON for URLs matching the Craig recording
    URL pattern. Falls back to searching embeds, content, and component
    text fields.

    Args:
        payload: Raw gateway MESSAGE_UPDATE event from Craig Bot.

    Returns:
        DetectedRecording if extraction succeeded, None otherwise.
    """
    ...


def parse_recording_ended(
    payload: discord.RawMessageUpdateEvent,
    watch_channel_id: int,
) -> DetectedRecording | None:
    """Top-level detection function. Combines all checks.

    Called from the bot's on_raw_message_update event handler.

    Args:
        payload: Raw gateway MESSAGE_UPDATE event.
        watch_channel_id: Channel ID to monitor (ignore events from other channels).

    Returns:
        DetectedRecording if a Craig recording end was detected, None otherwise.
    """
    ...
```

### 3.3 audio_source.py -- Audio Acquisition ABC

Abstract base class for pluggable audio acquisition. Craig is the primary implementation; the interface allows future alternatives (manual upload, other recording bots).

```python
from abc import ABC, abstractmethod
from pathlib import Path


class AudioSource(ABC):
    """Abstract interface for acquiring per-speaker audio files."""

    @abstractmethod
    async def get_speakers(self) -> list[SpeakerInfo]:
        """Retrieve the list of speakers in the recording.

        Returns:
            List of SpeakerInfo with track number, username, and user ID.

        Raises:
            AudioAcquisitionError: If the speaker list cannot be retrieved.
        """
        ...

    @abstractmethod
    async def download(self, dest_dir: Path) -> list[SpeakerAudio]:
        """Download per-speaker audio files to the destination directory.

        Args:
            dest_dir: Directory to write audio files into. Must exist.

        Returns:
            List of SpeakerAudio with speaker info and file paths.

        Raises:
            AudioAcquisitionError: If download or extraction fails.
            CookTimeoutError: If the cook job exceeds the configured timeout.
        """
        ...
```

### 3.4 craig_client.py -- Craig Cook API Client

Concrete implementation of AudioSource using the Craig Cook API.

```python
import aiohttp
import zipfile
import io
import re
from pathlib import Path

# Craig ZIP filename pattern: {track_number}-{username}.{format}
# Example: "1-shake344.aac", "2-john_doe.aac"
ZIP_FILENAME_PATTERN: re.Pattern = re.compile(
    r"^(?P<track>\d+)-(?P<username>.+)\.(?P<format>\w+)$"
)


class CraigClient(AudioSource):
    """Craig Bot Cook API client for downloading per-speaker recordings.

    Lifecycle: one instance per pipeline run. Created with a specific
    recording ID and access key, used to download that recording, then
    discarded.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        recording: DetectedRecording,
        cfg: CraigConfig,
    ) -> None:
        """Initialize the Craig client for a specific recording.

        Args:
            session: Shared aiohttp session (managed by the bot).
            recording: Detected recording metadata.
            cfg: Craig-specific configuration.
        """
        ...

    async def get_speakers(self) -> list[SpeakerInfo]:
        """GET /api/recording/{id}/users?key={key}

        Returns:
            List of SpeakerInfo parsed from the Craig users API response.

        Raises:
            AudioAcquisitionError: On HTTP error or unexpected response format.
        """
        ...

    async def download(self, dest_dir: Path) -> list[SpeakerAudio]:
        """Cook and download per-speaker audio files.

        Steps:
        1. POST /api/recording/{id}/cook?key={key}&format={fmt}&container=zip
        2. Read the response body as a ZIP file
        3. Extract each track to dest_dir
        4. Match filenames to speaker info using ZIP_FILENAME_PATTERN

        Retry policy: up to cfg.max_retries attempts with exponential backoff
        (1s, 2s base delays).

        Args:
            dest_dir: Directory to extract audio files into.

        Returns:
            List of SpeakerAudio with paths to extracted files.

        Raises:
            AudioAcquisitionError: On HTTP error, invalid ZIP, or extraction failure.
            CookTimeoutError: If the cook request exceeds cfg.download_timeout_sec.
        """
        ...

    async def get_duration(self) -> float:
        """GET /api/recording/{id}/duration?key={key}

        Returns:
            Recording duration in seconds.

        Raises:
            AudioAcquisitionError: On HTTP error or unexpected response format.
        """
        ...
```

**Craig Cook API Reference (source-verified from cook.ts):**

| Endpoint | Method | Parameters | Response |
|----------|--------|-----------|----------|
| `/api/recording/{id}/users?key={key}` | GET | None | JSON array: `[{"id": int, "username": str, "track": int}, ...]` |
| `/api/recording/{id}/duration?key={key}` | GET | None | JSON: `{"duration": float}` |
| `/api/recording/{id}/cook?key={key}&format={fmt}&container={ctr}` | POST | `format`: aac, flac, oggflac, wav8, mp3; `container`: zip, ogg, matroska | Binary: ZIP/Ogg/Matroska file |

### 3.5 transcriber.py -- faster-whisper Integration

Manages the faster-whisper model lifecycle and performs per-speaker transcription.

```python
import asyncio
from faster_whisper import WhisperModel
from pathlib import Path


_gpu_lock: asyncio.Lock = asyncio.Lock()


def load_model(cfg: WhisperConfig) -> WhisperModel:
    """Load the faster-whisper model into GPU memory.

    Called once at bot startup. The returned model is kept resident
    in VRAM for the lifetime of the process.

    Args:
        cfg: Whisper configuration (model name, device, compute type).

    Returns:
        Loaded WhisperModel instance.

    Raises:
        TranscriptionError: If model loading fails (CUDA unavailable,
            insufficient VRAM, model download failure).
    """
    ...


async def transcribe(
    model: WhisperModel,
    audio: SpeakerAudio,
    cfg: WhisperConfig,
) -> SpeakerTranscript:
    """Transcribe a single speaker's audio file.

    Runs the transcription in a thread pool via asyncio.to_thread to
    avoid blocking the Discord event loop. Acquires _gpu_lock to
    serialize GPU access (prevents concurrent transcription requests
    from overlapping if a second recording ends while the first is
    still processing).

    Args:
        model: Pre-loaded WhisperModel instance.
        audio: Speaker audio file to transcribe.
        cfg: Whisper configuration (language, beam size, VAD filter).

    Returns:
        SpeakerTranscript with all segments for this speaker.

    Raises:
        TranscriptionError: If transcription fails (corrupt audio,
            CUDA error, etc.).
    """
    ...


def _transcribe_sync(
    model: WhisperModel,
    file_path: Path,
    language: str,
    beam_size: int,
    vad_filter: bool,
) -> list[TranscriptSegment]:
    """Synchronous transcription worker. Runs inside asyncio.to_thread.

    Args:
        model: Pre-loaded WhisperModel.
        file_path: Path to the audio file.
        language: Language code (e.g. "ja").
        beam_size: Beam search width.
        vad_filter: Whether to enable Silero VAD filtering.

    Returns:
        List of TranscriptSegment ordered by start time.
    """
    ...
```

**Model lifecycle:**

| Event | Action |
|-------|--------|
| Bot startup | `load_model()` -- loads large-v3 into VRAM (~3 GB, ~15-30s) |
| Pipeline run (per speaker) | `transcribe()` -- inference using resident model |
| Bot shutdown | Model released with process exit (no explicit unload needed) |

**Performance characteristics (RTX 3060 12GB, large-v3, float16):**

| Audio Duration | Expected Transcription Time | Speed Ratio |
|---|---|---|
| 15 min (1 speaker) | ~1-1.5 min | 10-15x real-time |
| 1 hour (1 speaker) | ~4-6 min | 10-15x real-time |
| 1 hour meeting (5 speakers, sequential) | ~20-30 min total | 10-15x per track |

### 3.6 merger.py -- Transcript Merge

Pure function with no side effects. Interleaves per-speaker transcripts into a single chronologically ordered transcript.

```python
def merge(transcripts: list[SpeakerTranscript], cfg: MergerConfig) -> str:
    """Merge per-speaker transcripts into a single chronological transcript.

    Algorithm:
    1. Flatten all segments from all speakers, tagging each with speaker name.
    2. Sort by segment start time (stable sort preserves speaker order for
       simultaneous segments).
    3. Optionally merge consecutive segments from the same speaker that are
       closer than cfg.gap_merge_threshold_sec apart.
    4. Format each segment as: [MM:SS] username: text
    5. Join with newlines.

    Args:
        transcripts: Per-speaker transcripts from the transcription stage.
        cfg: Merger configuration (timestamp format, merge threshold).

    Returns:
        Merged transcript as a single string. Example:
        "[00:15] tanaka: Let's start with progress updates.\\n"
        "[00:32] suzuki: My tasks from last week are complete.\\n"
        "[01:05] tanaka: Thank you. About the next milestone..."

    Raises:
        ValueError: If transcripts list is empty.
    """
    ...


def _format_timestamp(seconds: float, fmt: str) -> str:
    """Format a time in seconds to the configured timestamp format.

    Args:
        seconds: Time in seconds.
        fmt: Format string with {mm} and {ss} placeholders.

    Returns:
        Formatted timestamp string, e.g. "[05:32]".
    """
    ...
```

### 3.7 generator.py -- LLM Minutes Generation

Calls the Anthropic API with the merged transcript and a prompt template to generate structured meeting minutes.

```python
import anthropic


async def generate(
    transcript: str,
    cfg: GeneratorConfig,
) -> str:
    """Generate structured meeting minutes from a merged transcript.

    Steps:
    1. Read the prompt template from cfg.prompt_template_path.
    2. Interpolate the transcript into the template using str.format().
    3. Call the Anthropic messages API with the populated prompt.
    4. Return the response content as a string.

    Retry policy: up to cfg.max_retries attempts with exponential backoff
    (1s, 2s base delays). Retries on: rate limit (429), server error (500+),
    connection error.

    Args:
        transcript: Merged chronological transcript string.
        cfg: Generator configuration (API key, model, max_tokens, temperature).

    Returns:
        Generated meeting minutes in Markdown format.

    Raises:
        GenerationError: If the API call fails after all retries, or if the
            response is empty or malformed.
    """
    ...


def _load_prompt_template(path: str) -> str:
    """Load and validate the prompt template file.

    The template must contain a {transcript} placeholder.

    Args:
        path: File path to the prompt template.

    Returns:
        Template string.

    Raises:
        FileNotFoundError: If the template file does not exist.
        ValueError: If the template does not contain {transcript}.
    """
    ...
```

**Prompt template design (`prompts/minutes.txt`):**

The prompt template uses `str.format()` with a single `{transcript}` placeholder. No Jinja2 or other templating engines. The template instructs the LLM to produce minutes in the following structure:

```
# Meeting Minutes
- Date: YYYY-MM-DD HH:MM - HH:MM
- Participants: (list)
- Recording duration: (minutes)

## Summary (3-5 lines)

## Agenda / Topics

## Discussion Details
### 1. (topic)

## Decisions

## Action Items / TODO
| Owner | Task | Deadline |
|-------|------|----------|

## Concerns / Risks
```

The prompt explicitly instructs:
- Do not fabricate information not present in the transcript
- Mark uncertain attributions with "(uncertain)"
- Use participant names exactly as they appear in the transcript
- Include a disclaimer: "This document was generated automatically by AI"

### 3.8 poster.py -- Discord Output

Formats the generated minutes as a Discord embed (summary) plus a Markdown file attachment (full minutes).

```python
import discord
import io


async def post(
    channel: discord.TextChannel,
    minutes_md: str,
    recording: DetectedRecording,
    result_meta: dict,
    cfg: PosterConfig,
) -> list[int]:
    """Post meeting minutes to a Discord channel.

    Posts two items:
    1. An embed message containing the summary section extracted from the
       minutes, plus metadata (date, participants, duration, processing time).
    2. A .md file attachment containing the full minutes.

    If the embed description exceeds cfg.max_embed_length, it is truncated
    with "... (see attached file for full minutes)".

    If cfg.include_transcript is True, also attaches the raw transcript
    as a .txt file.

    Args:
        channel: Discord text channel to post to.
        minutes_md: Full meeting minutes in Markdown format.
        recording: Original recording detection metadata.
        result_meta: Pipeline metrics dict (duration, speaker_count, etc.).
        cfg: Poster configuration.

    Returns:
        List of Discord message IDs for the posted messages.

    Raises:
        PostingError: If sending messages fails after retry.
    """
    ...


def _extract_summary(minutes_md: str) -> str:
    """Extract the summary section from the generated minutes.

    Looks for content between '## Summary' (or equivalent) and the next
    '##' heading. Falls back to the first 500 characters if no summary
    section is found.

    Args:
        minutes_md: Full minutes Markdown text.

    Returns:
        Summary text for the embed description.
    """
    ...


def _build_embed(
    summary: str,
    recording: DetectedRecording,
    result_meta: dict,
    cfg: PosterConfig,
) -> discord.Embed:
    """Build a Discord embed for the minutes summary.

    Fields:
    - Title: "Meeting Minutes -- YYYY/MM/DD"
    - Description: Summary text (truncated to max_embed_length)
    - Color: cfg.embed_color
    - Fields: Participants, Duration, Processing Time
    - Footer: "AI-generated -- verify against attached transcript"

    Args:
        summary: Extracted summary text.
        recording: Recording metadata.
        result_meta: Pipeline metrics.
        cfg: Poster configuration.

    Returns:
        Constructed discord.Embed instance.
    """
    ...


def _chunk_message(text: str, max_length: int) -> list[str]:
    """Split a long message into chunks that fit Discord's message limit.

    Splits on newline boundaries to avoid breaking mid-sentence.
    Each chunk is at most max_length characters.

    Args:
        text: Text to split.
        max_length: Maximum characters per chunk.

    Returns:
        List of text chunks.
    """
    ...
```

### 3.9 pipeline.py -- Orchestrator

Wires all stages together. Manages temp directory lifecycle and error boundaries.

```python
import asyncio
import tempfile
import time
import logging
from pathlib import Path

logger: logging.Logger = logging.getLogger(__name__)


class Pipeline:
    """Pipeline orchestrator. One instance per bot (reusable across runs)."""

    def __init__(
        self,
        model: WhisperModel,
        session: aiohttp.ClientSession,
        bot: discord.Bot,
        cfg: Config,
    ) -> None:
        """Initialize the pipeline with shared resources.

        Args:
            model: Pre-loaded faster-whisper model.
            session: Shared aiohttp session.
            bot: Discord bot instance (for channel access).
            cfg: Full application configuration.
        """
        ...

    async def run(self, detected: DetectedRecording) -> PipelineResult:
        """Execute the full minutes generation pipeline.

        This is the main error boundary. All stage errors are caught here,
        reported to Discord, logged, and the temp directory is cleaned up.

        Steps:
        1. Post "Processing started" status message.
        2. Create temp directory.
        3. Get speaker list from Craig API.
        4. Download per-speaker audio files.
        5. Transcribe each speaker sequentially.
        6. Merge transcripts.
        7. Generate minutes via LLM.
        8. Post minutes to Discord.
        9. Clean up temp directory.
        10. Return PipelineResult with metrics.

        On error at any step:
        - Log the full traceback.
        - Post an error embed to the output channel with the failure stage
          and error message.
        - Mention the error role if configured.
        - Clean up temp directory (finally block).

        Args:
            detected: Recording metadata from the detector.

        Returns:
            PipelineResult with timing and output metrics.

        Raises:
            Nothing. All exceptions are caught and reported to Discord.
            The method returns only on success; on failure it logs and
            reports but does not propagate.
        """
        ...

    async def _report_error(
        self,
        stage: str,
        error: Exception,
        detected: DetectedRecording,
    ) -> None:
        """Post an error notification to the output channel.

        Args:
            stage: Pipeline stage name where the error occurred.
            error: The exception that was raised.
            detected: Recording metadata for context.
        """
        ...

    async def _post_progress(
        self,
        channel: discord.TextChannel,
        message: str,
        status_message: discord.Message | None = None,
    ) -> discord.Message:
        """Post or update a progress status message.

        Args:
            channel: Channel to post in.
            message: Status text.
            status_message: Existing message to edit (if updating).

        Returns:
            The posted or edited message.
        """
        ...
```

**Pipeline invocation from bot.py:**

```python
@bot.event
async def on_raw_message_update(payload: discord.RawMessageUpdateEvent):
    detected = detector.parse_recording_ended(payload, cfg.discord.watch_channel_id)
    if detected is None:
        return

    logger.info("Recording ended detected: rec_id=%s", detected.rec_id)

    # Fire-and-forget: pipeline runs as a background task
    # so the event handler returns immediately
    asyncio.create_task(
        pipeline.run(detected),
        name=f"pipeline-{detected.rec_id}",
    )
```

### 3.10 errors.py -- Exception Hierarchy

```python
class MinutesBotError(Exception):
    """Base exception for all bot errors."""

    def __init__(self, message: str, stage: str = "unknown") -> None:
        self.stage = stage
        super().__init__(message)


class DetectionError(MinutesBotError):
    """Failed to parse Craig recording panel."""

    def __init__(self, message: str) -> None:
        super().__init__(message, stage="detection")


class AudioAcquisitionError(MinutesBotError):
    """Failed to download or extract audio files."""

    def __init__(self, message: str) -> None:
        super().__init__(message, stage="audio_acquisition")


class CookTimeoutError(AudioAcquisitionError):
    """Craig Cook API request exceeded timeout."""

    pass


class TranscriptionError(MinutesBotError):
    """Failed to transcribe audio."""

    def __init__(self, message: str) -> None:
        super().__init__(message, stage="transcription")


class GenerationError(MinutesBotError):
    """Failed to generate minutes via LLM."""

    def __init__(self, message: str) -> None:
        super().__init__(message, stage="generation")


class PostingError(MinutesBotError):
    """Failed to post minutes to Discord."""

    def __init__(self, message: str) -> None:
        super().__init__(message, stage="posting")
```

---

## 4. Craig Bot Integration

### 4.1 Detection Strategy

Craig Bot (ID `272937604339466240`) posts a Components V2 recording panel to the text channel when a user starts a recording with `/join`. When the recording is stopped (via `/stop` or `/leave`), Craig **edits** this same message -- the panel title changes to "Recording ended." and the color is removed.

Our bot detects this edit via `on_raw_message_update`, which receives the raw Discord gateway `MESSAGE_UPDATE` payload regardless of whether the message is in discord.py's internal cache. This is critical for robustness across bot restarts.

**Detection algorithm:**

```
1. Receive on_raw_message_update payload
2. Check: payload.channel_id == config.watch_channel_id?
   NO  -> discard
3. Check: payload.data["author"]["id"] == "272937604339466240"?
   NO  -> discard
4. Serialize payload.data["components"] to JSON string
5. Check: "Recording ended" in serialized_json?
   NO  -> discard (panel is still in active recording state)
6. Extract recording URL from serialized JSON via regex
7. Parse rec_id, access_key, domain from URL
8. Construct DetectedRecording and trigger pipeline
```

**Why string matching instead of structured parsing:**

Discord Components V2 is a newer feature. discord.py support was merged into the dev branch (PR #10166, August 2025) but may not be in a stable release at the time of implementation. By working with the raw JSON payload and using string matching, the detection logic is completely independent of discord.py's Components V2 support level. This is a deliberate design choice to maximize robustness.

### 4.2 URL Extraction

The Craig panel contains URLs in the format:

```
https://{domain}/rec/{rec_id}?key={access_key}
```

Known domains: `craig.chat`, `craig.horse`. The regex pattern handles any `craig.*` domain.

Extraction falls back through multiple sources in order:
1. Components array (primary -- contains buttons/links)
2. Embed fields (fallback -- older Craig versions)
3. Message content (fallback -- plain text)

### 4.3 Cook API Usage

After detection, the pipeline uses the Cook API to download per-speaker audio.

**Request flow:**

```
1. GET /api/recording/{id}/users?key={key}
   -> [{"id": 123, "username": "shake344", "track": 1}, ...]

2. GET /api/recording/{id}/duration?key={key}
   -> {"duration": 3542.5}

3. POST /api/recording/{id}/cook?key={key}&format=aac&container=zip
   -> (binary ZIP response, may take 10-60s depending on recording length)

4. Extract ZIP -> per-speaker files: "1-shake344.aac", "2-john_doe.aac", ...
```

**Format choice: AAC** (not FLAC)

The sample file `samples/1-shake344.aac` (14.3 MB for a recording) confirms that AAC LC at 48kHz produces audio of sufficient quality for speech-to-text. AAC is 5-7x smaller than FLAC with negligible transcription accuracy difference for speech. This translates to faster downloads and less temp disk usage.

---

## 5. Audio Pipeline

### 5.1 faster-whisper Integration

faster-whisper uses PyAV internally for audio decoding. PyAV bundles FFmpeg libraries in its Python package. This means:

- AAC, FLAC, Ogg, MP3, and WAV files are all decoded natively by faster-whisper
- No standalone FFmpeg installation is required for transcription
- No explicit format conversion step (e.g. AAC to WAV) is needed

The pipeline feeds the Craig-downloaded AAC files directly to `model.transcribe()`.

### 5.2 Sequential Processing

Speakers are transcribed one at a time, not in parallel. This is correct because:

1. The GPU is fully utilized during transcription of a single track (large-v3 saturates RTX 3060 compute)
2. Parallel transcription would compete for VRAM and risk OOM errors
3. Sequential processing simplifies error handling and progress reporting

The order of processing does not matter for correctness (merger sorts by timestamp), but speakers are processed in track-number order for predictable progress reporting.

### 5.3 Model Lifecycle

```
Bot startup
    |
    v
load_model("large-v3", device="cuda", compute_type="float16")
    |  (15-30 seconds, ~3 GB VRAM allocated)
    v
Model resident in VRAM
    |
    +--- Pipeline run 1: transcribe(speaker_1), transcribe(speaker_2), ...
    |
    +--- Pipeline run 2: transcribe(speaker_1), transcribe(speaker_2), ...
    |
    +--- (model stays loaded between runs)
    |
    v
Bot shutdown (process exit, VRAM released by OS)
```

### 5.4 GPU Serialization

An `asyncio.Lock` guards GPU access. If a second recording ends while the first is still being transcribed, the second pipeline's transcription stage waits for the lock. This prevents GPU memory contention.

```python
async def transcribe(model, audio, cfg):
    async with _gpu_lock:
        segments = await asyncio.to_thread(
            _transcribe_sync,
            model, audio.file_path, cfg.language, cfg.beam_size, cfg.vad_filter
        )
    return SpeakerTranscript(speaker=audio.speaker, segments=segments)
```

The `asyncio.to_thread` call moves the CPU-blocking `model.transcribe()` invocation off the event loop thread, keeping the Discord WebSocket connection responsive during transcription.

---

## 6. LLM Integration

### 6.1 Anthropic SDK Usage

Direct use of the `anthropic` Python SDK. No LangChain, no LlamaIndex, no agent framework. The interaction is a single prompt-response call.

```python
client = anthropic.Anthropic(api_key=cfg.api_key)

message = client.messages.create(
    model=cfg.model,
    max_tokens=cfg.max_tokens,
    temperature=cfg.temperature,
    messages=[
        {
            "role": "user",
            "content": prompt_with_transcript,
        }
    ],
)

minutes_md = message.content[0].text
```

The `anthropic` SDK is synchronous by default. The `generate()` function uses `asyncio.to_thread` to run the API call without blocking the event loop.

### 6.2 Prompt Template Design

The prompt template is stored in `prompts/minutes.txt` and loaded at generation time. It uses `str.format()` with a single `{transcript}` placeholder.

Template requirements:
- Must contain `{transcript}` exactly once
- Must instruct the model to produce output in Markdown format
- Must instruct the model not to fabricate information
- Must instruct the model to include an AI-generated disclaimer
- Must specify the output structure (summary, agenda, discussion, decisions, action items, risks)

The template is a plain text file, not embedded in code, so it can be iterated without code changes.

### 6.3 Retry Logic

Retries are implemented at the `generate()` function level, not by the SDK's built-in retry mechanism (for explicit control over retry conditions and logging).

| Condition | Action |
|-----------|--------|
| HTTP 429 (rate limit) | Retry after `Retry-After` header value, up to max_retries |
| HTTP 500+ (server error) | Retry with exponential backoff (1s, 2s), up to max_retries |
| Connection error | Retry with exponential backoff (1s, 2s), up to max_retries |
| HTTP 400 (bad request) | Do not retry; raise GenerationError immediately |
| HTTP 401 (auth error) | Do not retry; raise GenerationError immediately |
| Empty response content | Do not retry; raise GenerationError immediately |

### 6.4 Token Budget

| Meeting Length | Estimated Transcript Tokens | Model | Max Output Tokens | Estimated Cost |
|---|---|---|---|---|
| 30 min, 3 speakers | ~15,000-25,000 | claude-sonnet-4-5-20250929 | 4,096 | ~$0.05 |
| 1 hour, 5 speakers | ~30,000-60,000 | claude-sonnet-4-5-20250929 | 4,096 | ~$0.09-0.15 |
| 2 hours, 8 speakers | ~60,000-120,000 | claude-sonnet-4-5-20250929 | 4,096 | ~$0.20-0.40 |

If the transcript exceeds the model's context window (200K tokens for Sonnet), the pipeline raises a `GenerationError` rather than silently truncating. This scenario is unlikely for meetings under 4 hours.

---

## 7. Error Handling

### 7.1 Exception Hierarchy

```
MinutesBotError (base)
    stage: str           -- pipeline stage where the error occurred
    |
    +-- DetectionError
    |     stage = "detection"
    |
    +-- AudioAcquisitionError
    |     stage = "audio_acquisition"
    |     |
    |     +-- CookTimeoutError
    |           stage = "audio_acquisition"
    |
    +-- TranscriptionError
    |     stage = "transcription"
    |
    +-- GenerationError
    |     stage = "generation"
    |
    +-- PostingError
          stage = "posting"
```

### 7.2 Error Boundaries

There are two error boundaries in the system:

**Boundary 1: `pipeline.run()`** (inner boundary)

Catches all `MinutesBotError` and unexpected exceptions during the pipeline stages. On error:
1. Logs the full traceback at ERROR level
2. Posts an error embed to the output channel with stage name and error message
3. Mentions the configured error role (if set)
4. Cleans up the temp directory in a `finally` block
5. Does NOT re-raise (pipeline failures are reported, not propagated)

**Boundary 2: `on_raw_message_update` handler** (outer boundary)

Catches any exception that escapes the detector (before the pipeline is launched). On error:
1. Logs at ERROR level
2. Does NOT post to Discord (detection failure means we may not know the correct channel)
3. Does NOT re-raise (event handler exceptions should not crash the bot)

### 7.3 Retry Policies

| Operation | Max Retries | Backoff | Conditions |
|-----------|-------------|---------|------------|
| Craig Cook API (download) | 2 | Exponential: 1s, 2s | HTTP 5xx, connection error, timeout |
| Craig Users API | 2 | Exponential: 1s, 2s | HTTP 5xx, connection error |
| Anthropic API | 2 | Exponential: 1s, 2s; respect Retry-After for 429 | HTTP 429, 5xx, connection error |
| Discord message send | 1 | Fixed: 2s | HTTP 5xx, connection error |
| Transcription | 0 | N/A | GPU errors are not transient; fail fast |

### 7.4 Temp Directory Cleanup

```python
async def run(self, detected: DetectedRecording) -> PipelineResult:
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"minutes-{detected.rec_id}-"))
    try:
        # ... pipeline stages ...
    except Exception:
        # ... error reporting ...
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

The `finally` block ensures cleanup even on unexpected exceptions, KeyboardInterrupt, or asyncio cancellation. `ignore_errors=True` prevents cleanup failures from masking the original error.

---

## 8. Configuration Schema

### 8.1 config.yaml

```yaml
# Discord bot settings
discord:
  guild_id: 123456789012345678          # Target server (required)
  watch_channel_id: 123456789012345678  # Craig panel channel (required)
  output_channel_id: 123456789012345678 # Minutes output channel (required)
  error_mention_role_id: null           # Role to mention on error (optional)

# Craig Bot integration
craig:
  bot_id: "272937604339466240"          # Craig Bot user ID (default)
  domain: "craig.chat"                  # Craig API domain (default)
  cook_format: "aac"                    # Audio format: aac|flac|mp3 (default: aac)
  cook_container: "zip"                 # Container: zip|ogg|matroska (default: zip)
  download_timeout_sec: 300             # Cook + download timeout (default: 300)
  max_retries: 2                        # Download retry count (default: 2)

# faster-whisper settings
whisper:
  model: "large-v3"                     # Model: large-v3|medium|small|base|tiny
  language: "ja"                        # Language code (default: ja)
  device: "cuda"                        # Device: cuda|cpu (default: cuda)
  compute_type: "float16"              # Precision: float16|int8|float32 (default: float16)
  beam_size: 5                          # Beam search width (default: 5)
  vad_filter: true                      # Silero VAD filter (default: true)

# Transcript merger settings
merger:
  timestamp_format: "[{mm}:{ss}]"       # Timestamp display format
  min_segment_chars: 1                  # Drop segments shorter than this
  gap_merge_threshold_sec: 1.0          # Merge same-speaker gap threshold

# LLM generation settings
generator:
  model: "claude-sonnet-4-5-20250929"   # Anthropic model ID
  max_tokens: 4096                      # Max response tokens (default: 4096)
  temperature: 0.3                      # Temperature (default: 0.3)
  prompt_template_path: "prompts/minutes.txt"  # Prompt template file
  max_retries: 2                        # API retry count (default: 2)

# Discord output formatting
poster:
  embed_color: 0x5865F2                 # Embed color (Discord blurple)
  max_embed_length: 4000                # Max embed description chars
  include_transcript: false             # Attach raw transcript file
  chunk_size: 1990                      # Max chars per message chunk

# Logging
logging:
  level: "INFO"                         # Log level: DEBUG|INFO|WARNING|ERROR
  file: "logs/bot.log"                  # Log file path
  max_bytes: 10485760                   # Max file size before rotation (10MB)
  backup_count: 5                       # Number of rotated log files
```

### 8.2 .env

```bash
# Required secrets (never commit to git)
DISCORD_TOKEN=your_bot_token_here
ANTHROPIC_API_KEY=your_api_key_here
```

### 8.3 Environment Variable Overrides

Any configuration value can be overridden by an environment variable using the format `SECTION_KEY`. Environment variables take highest priority.

| Environment Variable | Overrides |
|---------------------|-----------|
| `DISCORD_TOKEN` | `discord.token` (special: only via .env, not in YAML) |
| `ANTHROPIC_API_KEY` | `generator.api_key` (special: only via .env, not in YAML) |
| `WHISPER_MODEL` | `whisper.model` |
| `WHISPER_DEVICE` | `whisper.device` |
| `WHISPER_COMPUTE_TYPE` | `whisper.compute_type` |
| `GENERATOR_MODEL` | `generator.model` |
| `LOGGING_LEVEL` | `logging.level` |

### 8.4 Validation

Config validation runs at load time. If validation fails, the process exits with a clear error message indicating which field is invalid and why. The bot does not start with invalid configuration.

---

## 9. Testing Plan

### 9.1 Test Infrastructure

| Tool | Purpose |
|------|---------|
| pytest | Test runner |
| pytest-asyncio | Async test support |
| aioresponses | Mock aiohttp HTTP requests |
| unittest.mock | Standard mocking |

### 9.2 Test Matrix by Module

#### config.py

| Test | Type | What It Verifies |
|------|------|-----------------|
| `test_load_valid_config` | Unit | Complete config loads without error |
| `test_missing_required_field` | Unit | ValueError raised for missing guild_id |
| `test_env_override` | Unit | Environment variable overrides YAML value |
| `test_dotenv_secrets` | Unit | DISCORD_TOKEN loaded from .env |
| `test_invalid_whisper_model` | Unit | ValueError for unsupported model name |
| `test_invalid_temperature_range` | Unit | ValueError for temperature > 1.0 |
| `test_default_values` | Unit | Defaults applied when keys are absent from YAML |

#### detector.py

| Test | Type | Fixture |
|------|------|---------|
| `test_is_craig_message_true` | Unit | `craig_panel_ended.json` |
| `test_is_craig_message_false_other_bot` | Unit | Payload with different author ID |
| `test_is_recording_ended_true` | Unit | `craig_panel_ended.json` |
| `test_is_recording_ended_false` | Unit | `craig_panel_recording.json` (active state) |
| `test_extract_recording_info` | Unit | `craig_panel_ended.json` |
| `test_extract_recording_info_no_url` | Unit | Payload without URL |
| `test_parse_recording_ended_wrong_channel` | Unit | Payload with non-matching channel_id |
| `test_recording_url_regex_variants` | Unit | URLs with different Craig domains |

#### craig_client.py

| Test | Type | Mock |
|------|------|------|
| `test_get_speakers` | Unit | aioresponses: mock GET /users |
| `test_get_speakers_http_error` | Unit | aioresponses: return 500 |
| `test_download_success` | Unit | aioresponses: mock POST /cook returning test ZIP |
| `test_download_timeout` | Unit | aioresponses: simulate timeout |
| `test_download_retry_on_500` | Unit | aioresponses: 500 then 200 |
| `test_extract_tracks_valid_zip` | Unit | In-memory ZIP with AAC files |
| `test_extract_tracks_empty_zip` | Unit | In-memory ZIP with no matching files |
| `test_zip_filename_pattern` | Unit | Regex against various filename formats |

#### transcriber.py

| Test | Type | Requirement |
|------|------|-------------|
| `test_transcribe_sample_aac` | GPU | Requires `samples/1-shake344.aac` + CUDA |
| `test_transcribe_produces_segments` | GPU | Verify segments have start < end, non-empty text |
| `test_transcribe_invalid_file` | Unit | TranscriptionError on nonexistent file |
| `test_gpu_lock_serialization` | Unit | Verify lock prevents concurrent transcription |

GPU tests are marked with `@pytest.mark.gpu` and skipped when CUDA is unavailable:

```python
import pytest

gpu = pytest.mark.gpu
skip_no_gpu = pytest.mark.skipif(
    not torch.cuda.is_available(),
    reason="CUDA not available"
)
```

#### merger.py

| Test | Type | What It Verifies |
|------|------|-----------------|
| `test_merge_single_speaker` | Unit | Output contains timestamps and speaker name |
| `test_merge_two_speakers_interleaved` | Unit | Segments sorted by start time |
| `test_merge_simultaneous_segments` | Unit | Stable sort preserves order |
| `test_merge_gap_threshold` | Unit | Consecutive same-speaker segments merged |
| `test_merge_empty_segments_dropped` | Unit | Segments with empty text excluded |
| `test_merge_empty_transcripts_raises` | Unit | ValueError on empty input list |
| `test_format_timestamp` | Unit | Various second values formatted correctly |

#### generator.py

| Test | Type | Mock |
|------|------|------|
| `test_generate_success` | Unit | Mock anthropic client, return canned response |
| `test_generate_retry_on_429` | Unit | Mock 429 then success |
| `test_generate_retry_on_500` | Unit | Mock 500 then success |
| `test_generate_no_retry_on_400` | Unit | Mock 400, verify immediate failure |
| `test_generate_empty_response` | Unit | Mock empty content, verify GenerationError |
| `test_load_prompt_template` | Unit | Read test template file |
| `test_load_prompt_template_missing_placeholder` | Unit | Template without {transcript} |

#### poster.py

| Test | Type | What It Verifies |
|------|------|-----------------|
| `test_extract_summary` | Unit | Correct section extraction from sample minutes |
| `test_extract_summary_no_heading` | Unit | Fallback to first 500 chars |
| `test_build_embed` | Unit | Embed has correct title, color, fields |
| `test_chunk_message_short` | Unit | No chunking needed for short text |
| `test_chunk_message_long` | Unit | Correct split on newline boundaries |
| `test_chunk_message_no_newlines` | Unit | Hard split at max_length |

#### pipeline.py (integration)

| Test | Type | What It Verifies |
|------|------|-----------------|
| `test_pipeline_success` | Integration | Full pipeline with mocked AudioSource and mocked Anthropic |
| `test_pipeline_download_failure` | Integration | Error reported to Discord, temp dir cleaned |
| `test_pipeline_transcription_failure` | Integration | Error reported, temp dir cleaned |
| `test_pipeline_generation_failure` | Integration | Error reported, temp dir cleaned |
| `test_pipeline_temp_cleanup` | Integration | Temp dir removed even on exception |

### 9.3 Test Fixtures

| File | Contents | Source |
|------|----------|--------|
| `tests/fixtures/craig_panel_ended.json` | Raw gateway payload for a Craig panel in "Recording ended" state | Captured during Phase 0 live validation |
| `tests/fixtures/craig_panel_recording.json` | Raw gateway payload for a Craig panel in active recording state | Captured during Phase 0 live validation |
| `tests/fixtures/craig_users_response.json` | Sample response from GET /users API | Captured during Phase 0 live validation |
| `samples/1-shake344.aac` | Real Craig recording audio file (14.3 MB, AAC LC 48kHz stereo) | Already present in repository |

---

## 10. Dependencies

### 10.1 Python Packages

| Package | Version Constraint | Purpose | Rationale |
|---------|-------------------|---------|-----------|
| discord.py | `>=2.3,<3.0` | Discord bot framework | Stable v2 API; v3 is not yet released |
| faster-whisper | `>=1.0,<2.0` | Local speech-to-text | Best-in-class local inference via CTranslate2 |
| anthropic | `>=0.40,<1.0` | Anthropic API client | Direct SDK; avoids LangChain overhead |
| aiohttp | `>=3.9,<4.0` | Async HTTP client for Craig API | Transitive dep of discord.py; explicit for Craig client |
| PyYAML | `>=6.0,<7.0` | YAML configuration parsing | Standard YAML library for Python |
| python-dotenv | `>=1.0,<2.0` | .env file loading | Standard .env library for Python |

### 10.2 System Dependencies

| Package | Required | Purpose |
|---------|----------|---------|
| CUDA Toolkit + cuDNN | Yes (for GPU mode) | GPU acceleration for faster-whisper |
| FFmpeg | No (Phase 1-3) | Optional: silence trimming, audio normalization (Phase 4+) |

### 10.3 Dev Dependencies

| Package | Version Constraint | Purpose |
|---------|-------------------|---------|
| pytest | `>=8.0,<9.0` | Test runner |
| pytest-asyncio | `>=0.23,<1.0` | Async test support |
| aioresponses | `>=0.7,<1.0` | Mock aiohttp requests in tests |

### 10.4 requirements.txt

```
discord.py>=2.3,<3.0
faster-whisper>=1.0,<2.0
anthropic>=0.40,<1.0
aiohttp>=3.9,<4.0
PyYAML>=6.0,<7.0
python-dotenv>=1.0,<2.0
```

---

## 11. Security Considerations

### 11.1 Secret Management

| Secret | Storage | Access |
|--------|---------|--------|
| `DISCORD_TOKEN` | `.env` file | Loaded by python-dotenv at startup |
| `ANTHROPIC_API_KEY` | `.env` file | Loaded by python-dotenv at startup |
| Craig access keys | Transient (in memory) | Extracted from gateway payload, used for one pipeline run, not persisted |

**File permissions:** The `.env` file must have restrictive permissions:

```bash
chmod 600 .env
```

**Git exclusion:** `.env` must be listed in `.gitignore`. The bot validates at startup that `.env` is not tracked by git and logs a WARNING if it is.

### 11.2 Data Flow Privacy

```
                     LOCAL MACHINE                    EXTERNAL
                  +------------------+
Craig Bot ------->| Discord Gateway  |
(recording link)  | (WebSocket)      |
                  +--------+---------+
                           |
                  +--------v---------+
                  | Audio Download   |-----------> Craig Cook API
                  | (temp files)     |<----------- (HTTPS, per-recording key)
                  +--------+---------+
                           |
                  +--------v---------+
                  | Transcription    |  (LOCAL GPU, no external calls)
                  | (faster-whisper) |
                  +--------+---------+
                           |
                  +--------v---------+
                  | LLM Generation   |-----------> Anthropic API
                  | (transcript text)|<----------- (HTTPS, text only)
                  +--------+---------+
                           |
                  +--------v---------+
                  | Discord Posting  |-----------> Discord API
                  | (minutes text)   |             (HTTPS)
                  +------------------+
```

**What stays local:** Audio files. They are downloaded to a temp directory, processed by faster-whisper on the local GPU, and deleted after the pipeline completes. Audio is never sent to an external API.

**What leaves the machine:**
1. **Transcript text** is sent to the Anthropic API for minutes generation. Anthropic's API data policy states that API inputs are not used for model training.
2. **Minutes text** is sent to Discord for posting. It is visible to members of the output channel.
3. **Craig API requests** include the recording ID and access key. These are transmitted over HTTPS.

### 11.3 Bot Permissions

The Discord bot requires these permissions (and no others):

| Permission | Reason |
|------------|--------|
| Read Messages / View Channels | Read Craig panel messages in the watch channel |
| Read Message History | Access message edit events |
| Send Messages | Post minutes and error notifications |
| Embed Links | Post embed messages |
| Attach Files | Attach .md minutes file |

The bot does NOT require:
- Administrator
- Manage Messages
- Manage Channels
- Manage Server
- Voice channel permissions (Craig handles recording)

### 11.4 Intents

The bot requires these Discord gateway intents:

| Intent | Privileged | Reason |
|--------|-----------|--------|
| `guilds` | No | Receive guild and channel metadata |
| `guild_messages` | No | Receive message events in guild channels |
| `message_content` | Yes | Access message content for URL extraction |

`message_content` is a privileged intent that must be enabled in the Discord Developer Portal. Without it, message content fields are empty and URL extraction fails.

---

## 12. File Structure

```
discord-minutes-bot/
├── bot.py                    # Entry point: argparse, logging, bot.run()
├── config.yaml               # All non-secret tunables
├── .env                      # Secrets (DISCORD_TOKEN, ANTHROPIC_API_KEY)
├── .gitignore                # Excludes .env, logs/, __pycache__, etc.
├── requirements.txt          # Pinned production dependencies
├── requirements-dev.txt      # Dev/test dependencies
├── prompts/
│   └── minutes.txt           # LLM prompt template (str.format with {transcript})
├── src/
│   ├── __init__.py
│   ├── config.py             # Config loader: YAML + .env -> frozen Config dataclass
│   ├── detector.py           # Craig panel detection via on_raw_message_update
│   ├── audio_source.py       # ABC for audio acquisition
│   ├── craig_client.py       # Concrete AudioSource: Craig Cook API
│   ├── transcriber.py        # faster-whisper model lifecycle + transcription
│   ├── merger.py             # Timestamp-based transcript merge (pure function)
│   ├── generator.py          # Anthropic SDK: prompt + API call
│   ├── poster.py             # Discord embed formatting + chunked posting
│   ├── pipeline.py           # Orchestrator: wires stages, temp dir, error boundary
│   └── errors.py             # Custom exception hierarchy
├── tests/
│   ├── conftest.py           # Shared fixtures, markers, mock factories
│   ├── test_config.py
│   ├── test_detector.py
│   ├── test_craig_client.py
│   ├── test_transcriber.py
│   ├── test_merger.py
│   ├── test_generator.py
│   ├── test_poster.py
│   ├── test_pipeline.py
│   └── fixtures/
│       ├── craig_panel_ended.json
│       ├── craig_panel_recording.json
│       └── craig_users_response.json
├── samples/
│   └── 1-shake344.aac       # Real Craig recording (14.3MB, AAC LC 48kHz stereo)
└── logs/                     # Runtime logs (gitignored)
```

---

## Appendix A: Performance Budget

Target: recording end to minutes posted in under 15 minutes for a 1-hour meeting with 5 speakers.

| Stage | Expected Duration | Notes |
|-------|------------------|-------|
| Detection + pipeline start | < 2s | Event-driven, near-instant |
| Craig Users API | 1-3s | Single HTTP GET |
| Craig Cook + Download | 30-120s | Depends on recording length and network |
| ZIP extraction | < 2s | In-memory extraction to disk |
| Transcription (5 speakers, ~1hr each) | 5-10 min | Sequential, ~10-15x real-time on RTX 3060 |
| Transcript merge | < 1s | Pure computation, trivial |
| LLM generation | 10-30s | Single API call |
| Discord posting | 2-5s | Embed + file upload |
| **Total** | **7-13 min** | **Within 15 min budget** |

For a 2-hour meeting with 8 speakers, transcription dominates at 30-60 minutes. This exceeds the 15-minute target, which is expected and accepted. Progress messages keep the user informed.

## Appendix B: config.yaml Defaults Reference

| Key | Default | Type | Required |
|-----|---------|------|----------|
| `discord.guild_id` | (none) | int | Yes |
| `discord.watch_channel_id` | (none) | int | Yes |
| `discord.output_channel_id` | (none) | int | Yes |
| `discord.error_mention_role_id` | `null` | int or null | No |
| `craig.bot_id` | `"272937604339466240"` | str | No |
| `craig.domain` | `"craig.chat"` | str | No |
| `craig.cook_format` | `"aac"` | str | No |
| `craig.cook_container` | `"zip"` | str | No |
| `craig.download_timeout_sec` | `300` | int | No |
| `craig.max_retries` | `2` | int | No |
| `whisper.model` | `"large-v3"` | str | No |
| `whisper.language` | `"ja"` | str | No |
| `whisper.device` | `"cuda"` | str | No |
| `whisper.compute_type` | `"float16"` | str | No |
| `whisper.beam_size` | `5` | int | No |
| `whisper.vad_filter` | `true` | bool | No |
| `merger.timestamp_format` | `"[{mm}:{ss}]"` | str | No |
| `merger.min_segment_chars` | `1` | int | No |
| `merger.gap_merge_threshold_sec` | `1.0` | float | No |
| `generator.model` | `"claude-sonnet-4-5-20250929"` | str | No |
| `generator.max_tokens` | `4096` | int | No |
| `generator.temperature` | `0.3` | float | No |
| `generator.prompt_template_path` | `"prompts/minutes.txt"` | str | No |
| `generator.max_retries` | `2` | int | No |
| `poster.embed_color` | `0x5865F2` | int | No |
| `poster.max_embed_length` | `4000` | int | No |
| `poster.include_transcript` | `false` | bool | No |
| `poster.chunk_size` | `1990` | int | No |
| `logging.level` | `"INFO"` | str | No |
| `logging.file` | `"logs/bot.log"` | str | No |
| `logging.max_bytes` | `10485760` | int | No |
| `logging.backup_count` | `5` | int | No |

## Appendix C: Discord Embed Limits

These Discord API limits constrain the poster module:

| Field | Limit |
|-------|-------|
| Embed title | 256 characters |
| Embed description | 4096 characters |
| Field name | 256 characters |
| Field value | 1024 characters |
| Number of fields | 25 |
| Footer text | 2048 characters |
| Author name | 256 characters |
| Total embed size | 6000 characters |
| Message content | 2000 characters |
| File attachment | 25 MB (non-Nitro) |
| Embeds per message | 10 |
