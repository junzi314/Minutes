"""Configuration loader: YAML + .env with env-var overrides and validation."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, fields
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.errors import ConfigError

logger = logging.getLogger(__name__)

VALID_WHISPER_MODELS = frozenset({
    "tiny", "tiny.en",
    "base", "base.en",
    "small", "small.en",
    "medium", "medium.en",
    "large-v1", "large-v2", "large-v3",
    "distil-large-v2", "distil-large-v3",
    "large-v3-turbo",
})


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GuildConfig:
    """Per-guild configuration (guild ID, watch channel, output channel)."""

    guild_id: int
    watch_channel_id: int
    output_channel_id: int


@dataclass(frozen=True)
class DiscordConfig:
    token: str
    guilds: tuple[GuildConfig, ...]
    error_mention_role_id: int | None = None

    def __post_init__(self) -> None:
        # Build O(1) lookup map (object.__setattr__ needed for frozen dataclass)
        guild_map = {g.guild_id: g for g in self.guilds}
        object.__setattr__(self, "_guild_map", guild_map)

    def get_guild(self, guild_id: int) -> GuildConfig | None:
        """Look up guild config by guild_id (O(1)). Returns None if not found."""
        return self._guild_map.get(guild_id)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class CraigConfig:
    bot_id: str = "272937604339466240"
    domain: str = "craig.chat"
    cook_format: str = "aac"
    cook_container: str = "zip"
    download_timeout_sec: int = 300
    poll_timeout_sec: int = 600
    max_retries: int = 2


@dataclass(frozen=True)
class WhisperConfig:
    model: str = "large-v3"
    language: str = "ja"
    device: str = "cuda"
    compute_type: str = "float16"
    beam_size: int = 5
    vad_filter: bool = True


@dataclass(frozen=True)
class MergerConfig:
    timestamp_format: str = "[{mm}:{ss}]"
    min_segment_chars: int = 1
    gap_merge_threshold_sec: float = 1.0


@dataclass(frozen=True)
class GeneratorConfig:
    api_key: str = ""
    model: str = "claude-sonnet-4-5-20250929"
    max_tokens: int = 4096
    temperature: float = 0.3
    prompt_template_path: str = "prompts/minutes.txt"
    max_retries: int = 2


@dataclass(frozen=True)
class PosterConfig:
    embed_color: int = 0x5865F2
    max_embed_length: int = 4000
    include_transcript: bool = False
    chunk_size: int = 1990
    mention_user_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class PipelineConfig:
    processing_timeout_sec: int = 3600


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/bot.log"
    max_bytes: int = 10_485_760
    backup_count: int = 5


@dataclass(frozen=True)
class GoogleDriveConfig:
    enabled: bool = False
    credentials_path: str = "credentials.json"
    folder_id: str = ""
    file_pattern: str = "craig_*.aac.zip"
    poll_interval_sec: int = 30
    processed_db_path: str = "processed_files.json"


@dataclass(frozen=True)
class Config:
    discord: DiscordConfig
    craig: CraigConfig
    whisper: WhisperConfig
    merger: MergerConfig
    generator: GeneratorConfig
    poster: PosterConfig
    logging: LoggingConfig
    google_drive: GoogleDriveConfig
    pipeline: PipelineConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Maps section names to their dataclass types.
_SECTION_CLASSES: dict[str, type] = {
    "craig": CraigConfig,
    "whisper": WhisperConfig,
    "merger": MergerConfig,
    "generator": GeneratorConfig,
    "poster": PosterConfig,
    "logging": LoggingConfig,
    "google_drive": GoogleDriveConfig,
    "pipeline": PipelineConfig,
}


def _coerce(value: str, target_type: type) -> object:
    """Coerce a string env-var value to the target field type."""
    if target_type is bool:
        return value.lower() in ("1", "true", "yes")
    if target_type is int:
        # int(value, 0) auto-detects base from prefix: 0x=hex, 0o=octal, 0b=binary
        return int(value, 0)
    if target_type is float:
        return float(value)
    return value


# Mapping from annotation string to Python type for coercion.
# Covers all field types used in the config dataclasses.
_TYPE_MAP: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "int | None": int,
}


def _resolve_field_type(annotation: str | type) -> type:
    """Resolve a field's type annotation to a concrete Python type for coercion.

    With ``from __future__ import annotations``, dataclass field types are
    stored as strings at runtime. This function handles both string and
    live-type annotations.
    """
    if isinstance(annotation, str):
        return _TYPE_MAP.get(annotation, str)

    # Live type (without __future__ annotations)
    import types as _types
    if isinstance(annotation, _types.UnionType):
        args = [a for a in annotation.__args__ if a is not type(None)]
        return args[0] if args else str
    return annotation  # type: ignore[return-value]


def _build_section(
    section_name: str,
    cls: type,
    yaml_values: dict,
) -> object:
    """Build a config dataclass from YAML values + env-var overrides.

    Env-var naming convention: ``SECTION_FIELD`` (uppercased).
    Example: ``WHISPER_MODEL=large-v2`` overrides whisper.model.
    """
    kwargs: dict[str, object] = {}
    for f in fields(cls):
        env_key = f"{section_name}_{f.name}".upper()
        env_val = os.environ.get(env_key)

        if env_val is not None:
            target = _resolve_field_type(f.type)
            kwargs[f.name] = _coerce(env_val, target)
        elif f.name in yaml_values:
            value = yaml_values[f.name]
            # YAML lists → frozen dataclass tuples
            if isinstance(value, list):
                value = tuple(value)
            kwargs[f.name] = value
        # else: rely on dataclass default

    return cls(**kwargs)


def _build_discord_section(yaml_section: dict) -> DiscordConfig:
    """Build DiscordConfig from YAML, supporting both old and new formats.

    Old format (single guild)::

        discord:
          guild_id: 123
          watch_channel_id: 456
          output_channel_id: 789

    New format (multi-guild)::

        discord:
          guilds:
            - guild_id: 123
              watch_channel_id: 456
              output_channel_id: 789
    """
    if not isinstance(yaml_section, dict):
        raise ConfigError(
            f"Config section 'discord' must be a mapping, got {type(yaml_section).__name__}"
        )

    error_mention_role_id = yaml_section.get("error_mention_role_id")

    if "guilds" in yaml_section:
        # New format: list of guild entries
        raw_guilds = yaml_section["guilds"]
        if not isinstance(raw_guilds, list):
            raise ConfigError("discord.guilds must be a list")
        guild_configs: list[GuildConfig] = []
        for i, entry in enumerate(raw_guilds):
            if not isinstance(entry, dict):
                raise ConfigError(f"discord.guilds[{i}] must be a mapping")
            guild_configs.append(GuildConfig(
                guild_id=entry.get("guild_id", 0),
                watch_channel_id=entry.get("watch_channel_id", 0),
                output_channel_id=entry.get("output_channel_id", 0),
            ))
        guilds = tuple(guild_configs)
    elif "guild_id" in yaml_section:
        # Old format (backward compat): wrap single guild into a list
        guilds = (GuildConfig(
            guild_id=yaml_section.get("guild_id", 0),
            watch_channel_id=yaml_section.get("watch_channel_id", 0),
            output_channel_id=yaml_section.get("output_channel_id", 0),
        ),)
    else:
        guilds = ()

    return DiscordConfig(
        token=yaml_section.get("token", ""),
        guilds=guilds,
        error_mention_role_id=error_mention_role_id,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate(cfg: Config) -> None:
    """Raise ConfigError for any invalid configuration values."""
    errors: list[str] = []

    # Discord
    if not cfg.discord.token:
        errors.append("discord.token is required (set DISCORD_BOT_TOKEN or DISCORD_TOKEN env var)")
    if not cfg.discord.guilds:
        errors.append("discord.guilds must contain at least one guild entry")
    seen_guild_ids: set[int] = set()
    for i, guild in enumerate(cfg.discord.guilds):
        prefix = f"discord.guilds[{i}]"
        if guild.guild_id <= 0:
            errors.append(f"{prefix}.guild_id must be a positive integer")
        elif guild.guild_id in seen_guild_ids:
            errors.append(f"{prefix}.guild_id {guild.guild_id} is duplicated")
        else:
            seen_guild_ids.add(guild.guild_id)
        if guild.watch_channel_id <= 0:
            errors.append(f"{prefix}.watch_channel_id must be a positive integer")
        if guild.output_channel_id <= 0:
            errors.append(f"{prefix}.output_channel_id must be a positive integer")

    # Whisper
    if cfg.whisper.model not in VALID_WHISPER_MODELS:
        errors.append(
            f"whisper.model '{cfg.whisper.model}' is not valid. "
            f"Choose from: {sorted(VALID_WHISPER_MODELS)}"
        )
    if cfg.whisper.beam_size < 1:
        errors.append("whisper.beam_size must be >= 1")

    # Generator
    if not (0.0 <= cfg.generator.temperature <= 1.0):
        errors.append("generator.temperature must be between 0.0 and 1.0")
    if cfg.generator.max_tokens < 1:
        errors.append("generator.max_tokens must be >= 1")

    # Craig
    if cfg.craig.download_timeout_sec < 1:
        errors.append("craig.download_timeout_sec must be >= 1")
    if cfg.craig.poll_timeout_sec < 1:
        errors.append("craig.poll_timeout_sec must be >= 1")
    if cfg.craig.max_retries < 0:
        errors.append("craig.max_retries must be >= 0")

    # Pipeline
    if cfg.pipeline.processing_timeout_sec < 1:
        errors.append("pipeline.processing_timeout_sec must be >= 1")

    # Poster
    if cfg.poster.max_embed_length < 1:
        errors.append("poster.max_embed_length must be >= 1")
    if cfg.poster.chunk_size < 1:
        errors.append("poster.chunk_size must be >= 1")

    # Google Drive (only validate when enabled)
    if cfg.google_drive.enabled:
        if not cfg.google_drive.folder_id:
            errors.append("google_drive.folder_id is required when google_drive.enabled is true")
        if cfg.google_drive.poll_interval_sec < 5:
            errors.append("google_drive.poll_interval_sec must be >= 5")

    if errors:
        raise ConfigError("Configuration validation failed:\n  - " + "\n  - ".join(errors))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load(config_path: str = "config.yaml", env_path: str = ".env") -> Config:
    """Load and validate configuration from *config_path* and *.env*.

    Precedence (highest wins):
      1. Environment variables  (``SECTION_FIELD``)
      2. YAML file values
      3. Dataclass defaults
    """
    # 1. Load .env (does NOT override existing env vars by default)
    env_file = Path(env_path)
    if env_file.exists():
        load_dotenv(env_file)
        logger.debug("Loaded .env from %s", env_file.resolve())

    # 2. Read YAML
    yaml_path = Path(config_path)
    if not yaml_path.exists():
        raise ConfigError(f"Config file not found: {yaml_path.resolve()}")

    with open(yaml_path, encoding="utf-8") as fh:
        raw: dict = yaml.safe_load(fh) or {}

    # 3. Build each section (except discord, which needs custom handling)
    sections: dict[str, object] = {}
    for section_name, cls in _SECTION_CLASSES.items():
        yaml_section = raw.get(section_name, {}) or {}
        if not isinstance(yaml_section, dict):
            raise ConfigError(f"Config section '{section_name}' must be a mapping, got {type(yaml_section).__name__}")
        sections[section_name] = _build_section(section_name, cls, yaml_section)

    # 3b. Build discord section with multi-guild support + backward compat
    sections["discord"] = _build_discord_section(raw.get("discord", {}) or {})

    # 4. Inject secrets that use non-standard env-var names
    # Token: DISCORD_BOT_TOKEN takes precedence, fallback to DISCORD_TOKEN
    token = os.environ.get("DISCORD_BOT_TOKEN") or os.environ.get("DISCORD_TOKEN") or ""
    dc: DiscordConfig = sections["discord"]  # type: ignore[assignment]
    sections["discord"] = DiscordConfig(
        token=token,
        guilds=dc.guilds,
        error_mention_role_id=dc.error_mention_role_id,
    )

    # API key: ANTHROPIC_API_KEY
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    if api_key:
        gen_section: dict = {}
        for f in fields(GeneratorConfig):
            gen_section[f.name] = getattr(sections["generator"], f.name)
        gen_section["api_key"] = api_key
        sections["generator"] = GeneratorConfig(**gen_section)

    # 5. Assemble top-level Config
    cfg = Config(**sections)  # type: ignore[arg-type]

    # 6. Validate
    _validate(cfg)

    logger.info("Configuration loaded successfully from %s", yaml_path.resolve())
    return cfg
