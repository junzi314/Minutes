"""Unit tests for zoom_slack_bot.py (entry point)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestParseArgs:
    def test_defaults(self) -> None:
        """Default arguments are correct."""
        with patch("sys.argv", ["zoom_slack_bot.py"]):
            from zoom_slack_bot import parse_args

            args = parse_args()
            assert args.config == "config_slack.yaml"
            assert args.log_level is None

    def test_custom_args(self) -> None:
        """Custom arguments are parsed."""
        with patch("sys.argv", ["zoom_slack_bot.py", "--config", "custom.yaml", "--log-level", "DEBUG"]):
            from zoom_slack_bot import parse_args

            args = parse_args()
            assert args.config == "custom.yaml"
            assert args.log_level == "DEBUG"


class TestSetupLogging:
    def test_creates_log_dir(self, tmp_path: Path) -> None:
        """Logging setup creates log directory if missing."""
        from zoom_slack_bot import setup_logging

        log_file = tmp_path / "logs" / "test.log"
        setup_logging(level="WARNING", log_file=str(log_file))

        assert log_file.parent.exists()

    def test_default_level(self, tmp_path: Path) -> None:
        """Default logging level is INFO."""
        import logging

        from zoom_slack_bot import setup_logging

        setup_logging(level="INFO", log_file=str(tmp_path / "test.log"))
        assert logging.getLogger().level == logging.INFO


class TestMainMissingConfig:
    def test_config_validation_error_exits(self) -> None:
        """Missing required config fields cause exit."""
        from src.errors import ConfigError

        with patch("src.slack_config.load_slack_config", side_effect=ConfigError("bot_token required")), \
             pytest.raises(ConfigError, match="bot_token"):
            import asyncio

            from zoom_slack_bot import run

            asyncio.run(run("nonexistent.yaml", None))
