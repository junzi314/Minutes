"""Configuration for the Zoom Diarization Slack service."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.config import (
    DiarizationConfig,
    GeneratorConfig,
    GoogleDriveConfig,
    MergerConfig,
    PipelineConfig,
    _build_section,
)
from src.errors import ConfigError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Slack-service-specific config dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SlackConfig:
    """Slack Web API settings."""

    bot_token: str = ""
    channel_id: str = ""
    include_transcript: bool = True
    thread_replies: bool = True


@dataclass(frozen=True)
class ZoomConfig:
    """Zoom file pairing settings."""

    vtt_file_pattern: str = "*.vtt"
    audio_file_pattern: str = "*.m4a"
    pair_timeout_sec: int = 300


@dataclass(frozen=True)
class SlackServiceConfig:
    """Top-level config for the Zoom → Slack diarization service."""

    slack: SlackConfig
    zoom: ZoomConfig
    diarization: DiarizationConfig
    generator: GeneratorConfig
    merger: MergerConfig
    google_drive: GoogleDriveConfig
    pipeline: PipelineConfig


# Maps section names to their dataclass types (Slack service sections).
_SLACK_SECTION_CLASSES: dict[str, type] = {
    "slack": SlackConfig,
    "zoom": ZoomConfig,
    "diarization": DiarizationConfig,
    "generator": GeneratorConfig,
    "merger": MergerConfig,
    "google_drive": GoogleDriveConfig,
    "pipeline": PipelineConfig,
}


def _validate_slack_config(cfg: SlackServiceConfig) -> None:
    """Validate Slack service config. Raises ConfigError on invalid values."""
    if not cfg.slack.bot_token:
        raise ConfigError("slack.bot_token is required (set SLACK_BOT_TOKEN env var)")
    if not cfg.slack.channel_id:
        raise ConfigError("slack.channel_id is required (set SLACK_CHANNEL_ID env var)")
    if cfg.zoom.pair_timeout_sec < 30:
        raise ConfigError(
            f"zoom.pair_timeout_sec must be >= 30, got {cfg.zoom.pair_timeout_sec}"
        )


def load_slack_config(
    config_path: str = "config_slack.yaml",
    env_path: str = ".env",
) -> SlackServiceConfig:
    """Load Slack service config from YAML + environment variables.

    Precedence (highest to lowest):
    1. Environment variables (SECTION_FIELD, e.g. SLACK_BOT_TOKEN)
    2. YAML file values
    3. Dataclass defaults
    """
    from dotenv import load_dotenv

    # Load .env file (doesn't override existing env vars)
    env_file = Path(env_path)
    if env_file.exists():
        load_dotenv(env_path, override=False)

    # Read YAML
    yaml_data: dict = {}
    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file, encoding="utf-8") as fh:
            yaml_data = yaml.safe_load(fh) or {}

    # Build each section using the shared _build_section helper
    sections: dict[str, object] = {}
    for section_name, cls in _SLACK_SECTION_CLASSES.items():
        section_yaml = yaml_data.get(section_name, {})
        if not isinstance(section_yaml, dict):
            section_yaml = {}
        sections[section_name] = _build_section(section_name, cls, section_yaml)

    # Inject ANTHROPIC_API_KEY (non-standard env var name — not GENERATOR_API_KEY)
    gen_section: GeneratorConfig = sections["generator"]  # type: ignore[assignment]
    if not gen_section.api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            # Rebuild with api_key injected
            gen_yaml = yaml_data.get("generator", {})
            if not isinstance(gen_yaml, dict):
                gen_yaml = {}
            gen_yaml["api_key"] = api_key
            sections["generator"] = _build_section("generator", GeneratorConfig, gen_yaml)

    cfg = SlackServiceConfig(**sections)  # type: ignore[arg-type]
    _validate_slack_config(cfg)
    return cfg
