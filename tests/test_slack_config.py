"""Unit tests for src/slack_config.py (Slack service configuration)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import DiarizationConfig, GeneratorConfig, GoogleDriveConfig, MergerConfig, PipelineConfig
from src.errors import ConfigError
from src.slack_config import (
    SlackConfig,
    SlackServiceConfig,
    ZoomConfig,
    load_slack_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(tmp_path: Path, yaml_content: str) -> Path:
    """Write a YAML config file and return its path."""
    import textwrap

    p = tmp_path / "config_slack.yaml"
    p.write_text(textwrap.dedent(yaml_content))
    return p


def _write_env(tmp_path: Path, content: str = "") -> Path:
    """Write a .env file and return its path."""
    p = tmp_path / ".env"
    p.write_text(content)
    return p


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove env vars that might interfere with tests."""
    for key in [
        "SLACK_BOT_TOKEN",
        "SLACK_CHANNEL_ID",
        "ANTHROPIC_API_KEY",
        "ZOOM_VTT_FILE_PATTERN",
        "ZOOM_AUDIO_FILE_PATTERN",
        "ZOOM_PAIR_TIMEOUT_SEC",
    ]:
        monkeypatch.delenv(key, raising=False)


# ===========================================================================
# Tests
# ===========================================================================


class TestLoadSlackConfigDefaults:
    def test_defaults_with_required_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Config loads with defaults when YAML is minimal and required env vars set."""
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C0123456789")

        cfg_path = _write_config(tmp_path, "")
        env_path = _write_env(tmp_path)

        cfg = load_slack_config(str(cfg_path), str(env_path))

        assert cfg.slack.bot_token == "xoxb-test-token"
        assert cfg.slack.channel_id == "C0123456789"
        assert cfg.slack.include_transcript is True
        assert cfg.slack.thread_replies is True
        assert cfg.zoom.vtt_file_pattern == "*.vtt"
        assert cfg.zoom.audio_file_pattern == "*.m4a"
        assert cfg.zoom.pair_timeout_sec == 300


class TestLoadSlackConfigFromYaml:
    def test_yaml_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Config reads values from YAML file."""
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C999")

        cfg_path = _write_config(
            tmp_path,
            """\
            zoom:
              vtt_file_pattern: "recording_*.vtt"
              pair_timeout_sec: 600
            merger:
              timestamp_format: "[{hh}:{mm}:{ss}]"
            """,
        )
        env_path = _write_env(tmp_path)

        cfg = load_slack_config(str(cfg_path), str(env_path))

        assert cfg.zoom.vtt_file_pattern == "recording_*.vtt"
        assert cfg.zoom.pair_timeout_sec == 600
        assert cfg.merger.timestamp_format == "[{hh}:{mm}:{ss}]"

    def test_slack_values_from_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Slack token and channel can come from YAML."""
        cfg_path = _write_config(
            tmp_path,
            """\
            slack:
              bot_token: "xoxb-yaml-token"
              channel_id: "C111222333"
              include_transcript: false
            """,
        )
        env_path = _write_env(tmp_path)

        cfg = load_slack_config(str(cfg_path), str(env_path))

        assert cfg.slack.bot_token == "xoxb-yaml-token"
        assert cfg.slack.channel_id == "C111222333"
        assert cfg.slack.include_transcript is False


class TestLoadSlackConfigEnvOverride:
    def test_env_overrides_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Environment variables take precedence over YAML."""
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env-wins")
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C_ENV")

        cfg_path = _write_config(
            tmp_path,
            """\
            slack:
              bot_token: "xoxb-yaml-loses"
              channel_id: "C_YAML"
            """,
        )
        env_path = _write_env(tmp_path)

        cfg = load_slack_config(str(cfg_path), str(env_path))

        assert cfg.slack.bot_token == "xoxb-env-wins"
        assert cfg.slack.channel_id == "C_ENV"

    def test_anthropic_key_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ANTHROPIC_API_KEY env var is injected into generator config."""
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-t")
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        cfg_path = _write_config(tmp_path, "")
        env_path = _write_env(tmp_path)

        cfg = load_slack_config(str(cfg_path), str(env_path))

        assert cfg.generator.api_key == "sk-test-key"


class TestLoadSlackConfigValidation:
    def test_missing_bot_token_raises(self, tmp_path: Path) -> None:
        """Empty bot_token raises ConfigError."""
        cfg_path = _write_config(
            tmp_path,
            """\
            slack:
              channel_id: "C123"
            """,
        )
        env_path = _write_env(tmp_path)

        with pytest.raises(ConfigError, match="bot_token"):
            load_slack_config(str(cfg_path), str(env_path))

    def test_missing_channel_id_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty channel_id raises ConfigError."""
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-t")

        cfg_path = _write_config(tmp_path, "")
        env_path = _write_env(tmp_path)

        with pytest.raises(ConfigError, match="channel_id"):
            load_slack_config(str(cfg_path), str(env_path))

    def test_pair_timeout_too_low_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pair_timeout_sec < 30 raises ConfigError."""
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-t")
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")

        cfg_path = _write_config(
            tmp_path,
            """\
            zoom:
              pair_timeout_sec: 10
            """,
        )
        env_path = _write_env(tmp_path)

        with pytest.raises(ConfigError, match="pair_timeout_sec"):
            load_slack_config(str(cfg_path), str(env_path))


class TestSlackConfigReusesExisting:
    def test_reuses_existing_dataclasses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SlackServiceConfig uses existing config dataclasses from src/config.py."""
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-t")
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")

        cfg_path = _write_config(tmp_path, "")
        env_path = _write_env(tmp_path)

        cfg = load_slack_config(str(cfg_path), str(env_path))

        assert isinstance(cfg.slack, SlackConfig)
        assert isinstance(cfg.zoom, ZoomConfig)
        assert isinstance(cfg.diarization, DiarizationConfig)
        assert isinstance(cfg.generator, GeneratorConfig)
        assert isinstance(cfg.merger, MergerConfig)
        assert isinstance(cfg.google_drive, GoogleDriveConfig)
        assert isinstance(cfg.pipeline, PipelineConfig)

    def test_missing_yaml_file_uses_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Config loads with defaults when YAML file doesn't exist."""
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-t")
        monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")

        cfg = load_slack_config(
            str(tmp_path / "nonexistent.yaml"),
            str(tmp_path / "nonexistent.env"),
        )

        assert cfg.slack.bot_token == "xoxb-t"
        assert cfg.zoom.pair_timeout_sec == 300
