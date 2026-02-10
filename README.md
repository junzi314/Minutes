# Discord Meeting Minutes Bot

Craig Bot の録音終了を自動検知し、文字起こし・議事録生成・Discord投稿を行うボットです。

## Architecture

```
Craig Bot Recording End
        │
        ▼
 on_raw_message_update (detect Craig panel edit)
        │
        ▼
 Cook API → Download per-speaker AAC ZIP
        │
        ▼
 faster-whisper (large-v3, GPU) → Per-speaker segments
        │
        ▼
 Merger → Chronological transcript [MM:SS] Speaker: text
        │
        ▼
 Claude API → Structured Japanese minutes (Markdown)
        │
        ▼
 Discord Embed + .md file attachment
```

## Prerequisites

- Python 3.12+
- NVIDIA GPU with 6+ GB VRAM (tested: RTX 3060 12GB)
- CUDA driver (verify: `nvidia-smi`)
- Discord Bot application with `MESSAGE_CONTENT` intent enabled
- Anthropic API key
- Craig Bot invited to target Discord server

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd discord-minutes-bot

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # for testing

# Install CUDA runtime libraries (if not already available)
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12

# Set LD_LIBRARY_PATH for CUDA libs
export LD_LIBRARY_PATH="$(python3 -c 'import nvidia.cublas; print(nvidia.cublas.__path__[0])')/lib:$(python3 -c 'import nvidia.cudnn; print(nvidia.cudnn.__path__[0])')/lib:$LD_LIBRARY_PATH"
```

## Configuration

### 1. Create `.env` file

```bash
DISCORD_BOT_TOKEN=your-bot-token-here
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### 2. Edit `config.yaml`

```yaml
discord:
  guild_id: 123456789012345678       # Your Discord server ID
  watch_channel_id: 123456789012345678  # Channel where Craig posts
  output_channel_id: 123456789012345678 # Channel for minutes output
  error_mention_role_id: null          # Optional: role to ping on errors
```

### Configuration Reference

| Section | Field | Default | Description |
|---------|-------|---------|-------------|
| `discord.guild_id` | -- | **required** | Discord server ID |
| `discord.watch_channel_id` | -- | **required** | Channel to monitor for Craig |
| `discord.output_channel_id` | -- | **required** | Channel for minutes output |
| `discord.error_mention_role_id` | `null` | Optional role ID to mention on errors |
| `craig.domain` | `craig.chat` | Craig API domain |
| `craig.cook_format` | `aac` | Audio format (aac, flac, ogg) |
| `craig.download_timeout_sec` | `300` | Cook API timeout in seconds |
| `whisper.model` | `large-v3` | Whisper model size |
| `whisper.language` | `ja` | Transcription language |
| `whisper.device` | `cuda` | `cuda` or `cpu` |
| `whisper.compute_type` | `float16` | `float16`, `int8`, `float32` |
| `whisper.beam_size` | `5` | Beam search width |
| `whisper.vad_filter` | `true` | Skip silence with VAD |
| `merger.gap_merge_threshold_sec` | `1.0` | Merge same-speaker gap threshold |
| `generator.model` | `claude-sonnet-4-5-20250929` | Claude model for generation |
| `generator.max_tokens` | `4096` | Max output tokens |
| `generator.temperature` | `0.3` | Generation temperature |
| `poster.embed_color` | `0x5865F2` | Embed sidebar color |
| `logging.level` | `INFO` | Log level |
| `logging.file` | `logs/bot.log` | Log file path |

All config fields can be overridden via environment variables: `SECTION_FIELD` (e.g. `WHISPER_MODEL=medium`).

## Running

```bash
# Direct
python3 bot.py

# With log level override
python3 bot.py --log-level DEBUG

# With custom config
python3 bot.py --config /path/to/config.yaml
```

## Slash Commands

| Command | Description |
|---------|-------------|
| `/minutes status` | Show bot uptime, model status, GPU availability |
| `/minutes process <url>` | Manually process a Craig recording URL |

## systemd Service (Auto-start)

```bash
# Copy service file
mkdir -p ~/.config/systemd/user/
cp discord-minutes-bot.service ~/.config/systemd/user/

# Edit paths if needed
nano ~/.config/systemd/user/discord-minutes-bot.service

# Enable and start
systemctl --user daemon-reload
systemctl --user enable discord-minutes-bot
systemctl --user start discord-minutes-bot

# Check status
systemctl --user status discord-minutes-bot

# View logs
journalctl --user -u discord-minutes-bot -f
```

## Testing

```bash
# Run all unit tests (no GPU required)
python3 -m pytest tests/ -k "not GPU" -v

# Run GPU integration tests (requires CUDA)
LD_LIBRARY_PATH=... python3 -m pytest tests/ -k "GPU" -v

# Run everything
LD_LIBRARY_PATH=... python3 -m pytest tests/ -v
```

## Project Structure

```
discord-minutes-bot/
├── bot.py                  # Entry point: Discord client, slash commands
├── config.yaml             # Default configuration
├── .env                    # Secrets (gitignored)
├── requirements.txt        # Python dependencies
├── discord-minutes-bot.service  # systemd user service
├── src/
│   ├── errors.py           # Exception hierarchy
│   ├── config.py           # Config loader (YAML + .env)
│   ├── detector.py         # Craig recording-end detection
│   ├── audio_source.py     # AudioSource ABC
│   ├── craig_client.py     # Craig Cook API client
│   ├── transcriber.py      # faster-whisper wrapper
│   ├── merger.py           # Transcript merging
│   ├── generator.py        # Claude API minutes generation
│   ├── poster.py           # Discord embed + file posting
│   └── pipeline.py         # Pipeline orchestrator
├── prompts/
│   └── minutes.txt         # LLM prompt template
├── tests/                  # pytest test suite
├── samples/                # Sample audio for development
└── logs/                   # Runtime logs (gitignored)
```

## Troubleshooting

### `RuntimeError: Library libcublas.so.12 is not found`

Install CUDA runtime libraries and set `LD_LIBRARY_PATH`:

```bash
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
export LD_LIBRARY_PATH="$(python3 -c 'import nvidia.cublas; print(nvidia.cublas.__path__[0])')/lib:$(python3 -c 'import nvidia.cudnn; print(nvidia.cudnn.__path__[0])')/lib"
```

### Bot connects but doesn't detect Craig recordings

1. Verify `watch_channel_id` matches the channel where Craig posts
2. Ensure `MESSAGE_CONTENT` intent is enabled in the Discord Developer Portal
3. Check that Craig Bot is in the same server
4. Look for detection logs: `grep "Recording ended" logs/bot.log`

### Transcription produces empty results

1. Check GPU availability: `nvidia-smi`
2. Verify VRAM is sufficient (>6GB free)
3. Try a smaller model: set `WHISPER_MODEL=medium`
4. Check audio file exists and is valid

### Claude API errors

1. Verify `ANTHROPIC_API_KEY` in `.env`
2. Check API quota at console.anthropic.com
3. The bot retries up to 3 times with exponential backoff
