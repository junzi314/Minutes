"""Unit tests for src/config.py."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from src.config import Config, CalendarConfig, DiarizationConfig, DiscordConfig, ExportGoogleDocsConfig, GuildConfig, GuildDriveConfig, TranscriptGlossaryConfig, load
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
        "WHISPER_MODEL", "WHISPER_DEVICE", "WHISPER_BACKEND",
        "GENERATOR_MODEL", "LOGGING_LEVEL", "OPENAI_API_KEY",
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
        # Old single-guild format auto-wraps into guilds list
        assert len(cfg.discord.guilds) == 1
        assert cfg.discord.guilds[0].guild_id == 123456789
        assert cfg.discord.guilds[0].watch_channel_id == 111222333
        assert cfg.discord.guilds[0].output_channel_id == 444555666
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

    def test_empty_guilds_list(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guilds: []
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="at least one guild"):
            load(str(cfg_path), str(env_path))


class TestGeneratorBackend:
    def test_backend_default_is_claude(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        assert cfg.generator.backend == "claude"

    def test_invalid_generator_backend(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            generator:
              backend: "invalid"
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="generator.backend"):
            load(str(cfg_path), str(env_path))

    def test_openai_compat_requires_base_url(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            generator:
              backend: "openai_compat"
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="base_url is required"):
            load(str(cfg_path), str(env_path))

    def test_openai_compat_no_api_key_ok(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """openai_compat backend does not require ANTHROPIC_API_KEY."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            generator:
              backend: "openai_compat"
              base_url: "http://localhost:11434/v1"
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.generator.backend == "openai_compat"
        assert cfg.generator.base_url == "http://localhost:11434/v1"


class TestWhisperLanguageValidation:
    def test_invalid_whisper_language_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid language code should raise ConfigError."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            whisper:
              language: "xyz"
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="whisper.language"):
            load(str(cfg_path), str(env_path))

    def test_auto_whisper_language_accepted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """'auto' should be accepted as a valid language."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            whisper:
              language: "auto"
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.whisper.language == "auto"


class TestMultiGuild:
    def test_multi_guild_format(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guilds:
                - guild_id: 111
                  watch_channel_id: 222
                  output_channel_id: 333
                - guild_id: 444
                  watch_channel_id: 555
                  output_channel_id: 666
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert len(cfg.discord.guilds) == 2
        assert cfg.discord.guilds[0].guild_id == 111
        assert cfg.discord.guilds[0].watch_channel_id == 222
        assert cfg.discord.guilds[0].output_channel_id == 333
        assert cfg.discord.guilds[1].guild_id == 444
        assert cfg.discord.guilds[1].watch_channel_id == 555
        assert cfg.discord.guilds[1].output_channel_id == 666

    def test_backward_compat_single_guild(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Old single-guild format is automatically wrapped into guilds list."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 999
              watch_channel_id: 888
              output_channel_id: 777
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert len(cfg.discord.guilds) == 1
        assert cfg.discord.guilds[0].guild_id == 999
        assert cfg.discord.guilds[0].watch_channel_id == 888
        assert cfg.discord.guilds[0].output_channel_id == 777

    def test_get_guild_found(self) -> None:
        g1 = GuildConfig(guild_id=100, watch_channel_id=200, output_channel_id=300)
        g2 = GuildConfig(guild_id=400, watch_channel_id=500, output_channel_id=600)
        dc = DiscordConfig(token="tok", guilds=(g1, g2))
        assert dc.get_guild(400) is g2
        assert dc.get_guild(100) is g1

    def test_get_guild_not_found(self) -> None:
        g1 = GuildConfig(guild_id=100, watch_channel_id=200, output_channel_id=300)
        dc = DiscordConfig(token="tok", guilds=(g1,))
        assert dc.get_guild(999) is None

    def test_duplicate_guild_id_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guilds:
                - guild_id: 111
                  watch_channel_id: 222
                  output_channel_id: 333
                - guild_id: 111
                  watch_channel_id: 444
                  output_channel_id: 555
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="duplicated"):
            load(str(cfg_path), str(env_path))

    def test_guild_config_template_default(self) -> None:
        """GuildConfig template defaults to 'minutes'."""
        g = GuildConfig(guild_id=1, watch_channel_id=2, output_channel_id=3)
        assert g.template == "minutes"

    def test_guild_config_template_from_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Template field is read from YAML config."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guilds:
                - guild_id: 1
                  watch_channel_id: 2
                  output_channel_id: 3
                  template: todo-focused
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.discord.guilds[0].template == "todo-focused"

    def test_speaker_analytics_default_enabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """SpeakerAnalyticsConfig defaults to enabled."""
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
        assert cfg.speaker_analytics.enabled is True

    def test_speaker_analytics_disabled_from_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """speaker_analytics.enabled can be set to false via YAML."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            speaker_analytics:
              enabled: false
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.speaker_analytics.enabled is False

    def test_transcript_glossary_default_enabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """TranscriptGlossaryConfig defaults to enabled, case-insensitive."""
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
        assert cfg.transcript_glossary.enabled is True
        assert cfg.transcript_glossary.case_sensitive is False

    def test_transcript_glossary_disabled_from_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """transcript_glossary can be disabled via YAML."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            transcript_glossary:
              enabled: false
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.transcript_glossary.enabled is False

    def test_error_mention_role_shared(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """error_mention_role_id is shared across all guilds."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guilds:
                - guild_id: 1
                  watch_channel_id: 2
                  output_channel_id: 3
              error_mention_role_id: 12345
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.discord.error_mention_role_id == 12345
        assert len(cfg.discord.guilds) == 1


class TestPerGuildErrorRole:
    def test_guild_error_role_parsed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Per-guild error_mention_role_id is parsed from YAML."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guilds:
                - guild_id: 1
                  watch_channel_id: 2
                  output_channel_id: 3
                  error_mention_role_id: 111
                - guild_id: 4
                  watch_channel_id: 5
                  output_channel_id: 6
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.discord.guilds[0].error_mention_role_id == 111
        assert cfg.discord.guilds[1].error_mention_role_id is None

    def test_resolve_error_role_guild_level(self) -> None:
        """resolve_error_role returns guild-level value when set."""
        g1 = GuildConfig(guild_id=1, watch_channel_id=2, output_channel_id=3, error_mention_role_id=111)
        g2 = GuildConfig(guild_id=4, watch_channel_id=5, output_channel_id=6)
        dc = DiscordConfig(token="tok", guilds=(g1, g2), error_mention_role_id=999)
        assert dc.resolve_error_role(1) == 111

    def test_resolve_error_role_global_fallback(self) -> None:
        """resolve_error_role falls back to global when guild has no override."""
        g1 = GuildConfig(guild_id=1, watch_channel_id=2, output_channel_id=3)
        dc = DiscordConfig(token="tok", guilds=(g1,), error_mention_role_id=999)
        assert dc.resolve_error_role(1) == 999

    def test_resolve_error_role_both_none(self) -> None:
        """resolve_error_role returns None when neither guild nor global is set."""
        g1 = GuildConfig(guild_id=1, watch_channel_id=2, output_channel_id=3)
        dc = DiscordConfig(token="tok", guilds=(g1,))
        assert dc.resolve_error_role(1) is None

    def test_resolve_error_role_unknown_guild(self) -> None:
        """resolve_error_role falls back to global for unknown guild_id."""
        dc = DiscordConfig(token="tok", guilds=(), error_mention_role_id=999)
        assert dc.resolve_error_role(999999) == 999


class TestPerGuildDrive:
    def test_guild_drive_config_parsed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Per-guild google_drive sub-section is parsed."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guilds:
                - guild_id: 1
                  watch_channel_id: 2
                  output_channel_id: 3
                  google_drive:
                    enabled: true
                    folder_id: "folder_A"
                - guild_id: 4
                  watch_channel_id: 5
                  output_channel_id: 6
                  google_drive:
                    enabled: false
                - guild_id: 7
                  watch_channel_id: 8
                  output_channel_id: 9
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        # Guild 1: Drive enabled with folder_id
        assert cfg.discord.guilds[0].google_drive is not None
        assert cfg.discord.guilds[0].google_drive.enabled is True
        assert cfg.discord.guilds[0].google_drive.folder_id == "folder_A"
        # Guild 2: Drive disabled
        assert cfg.discord.guilds[1].google_drive is not None
        assert cfg.discord.guilds[1].google_drive.enabled is False
        # Guild 3: No google_drive section
        assert cfg.discord.guilds[2].google_drive is None

    def test_guild_drive_config_defaults(self) -> None:
        """GuildDriveConfig has correct defaults."""
        gd = GuildDriveConfig()
        assert gd.enabled is True
        assert gd.folder_id == ""

    def test_guild_config_new_fields_default(self) -> None:
        """GuildConfig new fields default to None."""
        g = GuildConfig(guild_id=1, watch_channel_id=2, output_channel_id=3)
        assert g.error_mention_role_id is None
        assert g.google_drive is None

    def test_backward_compat_no_new_fields(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Existing config without new fields still works."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guilds:
                - guild_id: 1
                  watch_channel_id: 2
                  output_channel_id: 3
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.discord.guilds[0].error_mention_role_id is None
        assert cfg.discord.guilds[0].google_drive is None

    def test_guild_drive_enabled_no_folder_id_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Guild Drive enabled with no folder_id (and no global fallback) is rejected."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guilds:
                - guild_id: 1
                  watch_channel_id: 2
                  output_channel_id: 3
                  google_drive:
                    enabled: true
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="folder_id"):
            load(str(cfg_path), str(env_path))

    def test_guild_drive_uses_global_folder_id_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Guild Drive enabled with no folder_id but global folder_id passes validation."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guilds:
                - guild_id: 1
                  watch_channel_id: 2
                  output_channel_id: 3
                  google_drive:
                    enabled: true
            google_drive:
              enabled: true
              folder_id: "global_folder"
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.discord.guilds[0].google_drive.enabled is True
        assert cfg.discord.guilds[0].google_drive.folder_id == ""
        # Global fallback provides the folder_id
        assert cfg.google_drive.folder_id == "global_folder"


class TestWhisperBackend:
    def test_backend_default_is_local(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        assert cfg.whisper.backend == "local"

    def test_backend_api_valid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            whisper:
              backend: api
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.whisper.backend == "api"

    def test_backend_invalid_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            whisper:
              backend: foo
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="backend"):
            load(str(cfg_path), str(env_path))

    def test_api_backend_requires_openai_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            whisper:
              backend: api
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
            load(str(cfg_path), str(env_path))


class TestExportGoogleDocsConfig:
    def test_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        assert cfg.export_google_docs.enabled is False
        assert cfg.export_google_docs.max_retries == 3

    def test_enabled_requires_folder_id(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            export_google_docs:
              enabled: true
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="folder_id"):
            load(str(cfg_path), str(env_path))


class TestCalendarConfig:
    def test_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        assert cfg.calendar.enabled is False
        assert cfg.calendar.calendar_id == "primary"
        assert cfg.calendar.timezone == "Asia/Tokyo"

    def test_from_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 1
              watch_channel_id: 2
              output_channel_id: 3
            calendar:
              enabled: true
              calendar_id: "team@group.calendar.google.com"
              timezone: "UTC"
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.calendar.enabled is True
        assert cfg.calendar.calendar_id == "team@group.calendar.google.com"
        assert cfg.calendar.timezone == "UTC"


class TestDiarizationConfig:
    def test_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """DiarizationConfig has sensible defaults when section is missing."""
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
        assert cfg.diarization.enabled is False
        assert cfg.diarization.model == "BUT-FIT/diarizen-wavlm-large-s80-md"
        assert cfg.diarization.device == "cuda"
        assert cfg.diarization.num_speakers == 0
        assert cfg.diarization.ffmpeg_timeout_sec == 300

    def test_from_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """DiarizationConfig loads from YAML."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token-123")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 123456789
              watch_channel_id: 111222333
              output_channel_id: 444555666
            diarization:
              enabled: true
              model: "custom-model"
              device: "cpu"
              num_speakers: 3
              ffmpeg_timeout_sec: 600
              drive_file_pattern: "*.mkv"
            """)
        env_path = _write_env(tmp_path, "")

        cfg = load(str(cfg_path), str(env_path))
        assert cfg.diarization.enabled is True
        assert cfg.diarization.model == "custom-model"
        assert cfg.diarization.device == "cpu"
        assert cfg.diarization.num_speakers == 3
        assert cfg.diarization.ffmpeg_timeout_sec == 600
        assert cfg.diarization.drive_file_pattern == "*.mkv"

    def test_validation_invalid_num_speakers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative num_speakers raises ConfigError."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token-123")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 123456789
              watch_channel_id: 111222333
              output_channel_id: 444555666
            diarization:
              enabled: true
              num_speakers: -1
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="num_speakers must be >= 0"):
            load(str(cfg_path), str(env_path))

    def test_validation_ffmpeg_timeout_too_low(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ffmpeg_timeout_sec below 10 raises ConfigError."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token-123")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        cfg_path = _write_config(tmp_path, """
            discord:
              guild_id: 123456789
              watch_channel_id: 111222333
              output_channel_id: 444555666
            diarization:
              enabled: true
              ffmpeg_timeout_sec: 5
            """)
        env_path = _write_env(tmp_path, "")

        with pytest.raises(ConfigError, match="ffmpeg_timeout_sec must be >= 10"):
            load(str(cfg_path), str(env_path))
