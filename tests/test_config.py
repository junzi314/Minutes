"""Unit tests for src/config.py."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from src.config import Config, load
from src.errors import ConfigError


def _write_config(tmp_path: Path, yaml_content: str) -> Path:
    """Write a YAML config file and return its path."""
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(yaml_content))
    return p


def _write_env(tmp_path: Path, content: str) -> Path:
    """Write a .env file and return its path."""
    p = tmp_path / ".env"
    p.write_text(textwrap.dedent(content))
    return p


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove env vars that might interfere with tests."""
    for key in [
        "DISCORD_BOT_TOKEN", "DISCORD_TOKEN", "ANTHROPIC_API_KEY",
        "WHISPER_MODEL", "WHISPER_DEVICE", "GENERATOR_MODEL",
        "LOGGING_LEVEL",
    ]:
        monkeypatch.delenv(key, raising=False)


class TestLoadValidConfig:
    def test_load_minimal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token-123")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 123456789
              watch_channel_id: 111222333
              output_channel_id: 444555666
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert isinstance(cfg, Config)
        assert cfg.discord.token == "test-token-123"
        assert cfg.discord.guild_id == 123456789
        assert cfg.generator.api_key == "sk-test-key"
        assert cfg.whisper.model == "large-v3"  # default

    def test_all_defaults_applied(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.craig.bot_id == "272937604339466240"
        assert cfg.craig.domain == "craig.chat"
        assert cfg.whisper.language == "ja"
        assert cfg.merger.gap_merge_threshold_sec == 1.0
        assert cfg.poster.embed_color == 0x5865F2
        assert cfg.logging.level == "INFO"


class TestEnvOverrides:
    def test_whisper_model_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        monkeypatch.setenv("WHISPER_MODEL", "medium")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.whisper.model == "medium"

    def test_dotenv_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            """)
        env_path = _write_env(tmp_path, """
            DISCORD_BOT_TOKEN=from-dotenv
            ANTHROPIC_API_KEY=key-from-dotenv
            """)

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.discord.token == "from-dotenv"
        assert cfg.generator.api_key == "key-from-dotenv"

    def test_discord_token_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """DISCORD_TOKEN works when DISCORD_BOT_TOKEN is not set."""
        monkeypatch.setenv("DISCORD_TOKEN", "fallback-token")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.discord.token == "fallback-token"


class TestValidation:
    def test_missing_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="discord.token"):
            load(str(cfg_path), str(env_path))

    def test_invalid_guild_id(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 0
              watch_channel_id: 2
              output_channel_id: 3
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="guild_id"):
            load(str(cfg_path), str(env_path))

    def test_invalid_whisper_model(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            whisper:
              model: "not-a-real-model"
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="whisper.model"):
            load(str(cfg_path), str(env_path))

    def test_invalid_temperature(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            generator:
              temperature: 1.5
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="temperature"):
            load(str(cfg_path), str(env_path))

    def test_missing_config_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="Config file not found"):
            load(str(tmp_path / "nonexistent.yaml"))
