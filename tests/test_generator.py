"""Unit tests for src/generator.py."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import GeneratorConfig
from src.errors import GenerationError
from src.generator import MinutesGenerator


def _make_cfg(tmp_path: Path, api_key: str = "sk-test") -> GeneratorConfig:
    """Create a GeneratorConfig with a real template file."""
    template = tmp_path / "minutes.txt"
    template.write_text(
        "Date: {date}\nSpeakers: {speakers}\n"
        "Guild: {guild_name}\nChannel: {channel_name}\n"
        "Transcript:\n{transcript}",
        encoding="utf-8",
    )
    return GeneratorConfig(
        api_key=api_key,
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        temperature=0.3,
        prompt_template_path=str(template),
        max_retries=2,
    )


class TestGeneratorLoad:
    def test_load_success(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path)
        gen = MinutesGenerator(cfg)
        gen.load()
        assert gen.is_loaded

    def test_load_missing_template(self, tmp_path: Path) -> None:
        cfg = GeneratorConfig(
            api_key="sk-test",
            prompt_template_path=str(tmp_path / "nonexistent.txt"),
        )
        gen = MinutesGenerator(cfg)
        with pytest.raises(GenerationError, match="not found"):
            gen.load()

    def test_load_no_api_key(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path, api_key="")
        gen = MinutesGenerator(cfg)
        with pytest.raises(GenerationError, match="ANTHROPIC_API_KEY"):
            gen.load()

    def test_load_idempotent(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path)
        gen = MinutesGenerator(cfg)
        with patch("src.generator.anthropic.Anthropic") as mock_cls:
            gen.load()
            gen.load()  # second call should be no-op
            mock_cls.assert_called_once()


class TestRenderPrompt:
    def test_render_all_variables(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path)
        gen = MinutesGenerator(cfg)
        gen.load()

        result = gen.render_prompt(
            transcript="[00:00] Alice: Hello",
            date="2026-02-10",
            speakers="Alice, Bob",
            guild_name="TestServer",
            channel_name="general",
        )
        assert "2026-02-10" in result
        assert "Alice, Bob" in result
        assert "TestServer" in result
        assert "general" in result
        assert "[00:00] Alice: Hello" in result

    def test_render_before_load_raises(self) -> None:
        cfg = GeneratorConfig(api_key="sk-test")
        gen = MinutesGenerator(cfg)
        with pytest.raises(GenerationError, match="not loaded"):
            gen.render_prompt("t", "d", "s")


class TestGenerate:
    @pytest.mark.asyncio
    async def test_generate_success(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path)
        gen = MinutesGenerator(cfg)
        gen.load()

        # Mock the Anthropic client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="# 会議議事録\n## 要約\nテスト会議")]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        gen._client.messages.create = MagicMock(return_value=mock_response)

        result = await gen.generate(
            transcript="[00:00] Alice: テスト",
            date="2026-02-10",
            speakers="Alice",
        )
        assert "会議議事録" in result
        gen._client.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_before_load_raises(self) -> None:
        cfg = GeneratorConfig(api_key="sk-test")
        gen = MinutesGenerator(cfg)
        with pytest.raises(GenerationError, match="not loaded"):
            await gen.generate("t", "d", "s")

    @pytest.mark.asyncio
    async def test_generate_retries_on_rate_limit(self, tmp_path: Path) -> None:
        import anthropic as anthropic_mod

        cfg = _make_cfg(tmp_path)
        gen = MinutesGenerator(cfg)
        gen.load()

        # First call raises RateLimitError, second succeeds
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Success")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

        rate_limit_exc = anthropic_mod.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )

        gen._client.messages.create = MagicMock(
            side_effect=[rate_limit_exc, mock_response]
        )

        result = await gen.generate("transcript", "date", "speakers")
        assert result == "Success"
        assert gen._client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_generate_fails_on_client_error(self, tmp_path: Path) -> None:
        import anthropic as anthropic_mod

        cfg = _make_cfg(tmp_path)
        gen = MinutesGenerator(cfg)
        gen.load()

        client_exc = anthropic_mod.APIStatusError(
            message="bad request",
            response=MagicMock(status_code=400, headers={}),
            body=None,
        )

        gen._client.messages.create = MagicMock(side_effect=client_exc)

        with pytest.raises(GenerationError, match="client error"):
            await gen.generate("transcript", "date", "speakers")

    @pytest.mark.asyncio
    async def test_generate_exhausts_retries(self, tmp_path: Path) -> None:
        import anthropic as anthropic_mod

        cfg = _make_cfg(tmp_path)
        gen = MinutesGenerator(cfg)
        gen.load()

        conn_exc = anthropic_mod.APIConnectionError(request=MagicMock())

        gen._client.messages.create = MagicMock(side_effect=conn_exc)

        with pytest.raises(GenerationError, match="failed after"):
            await gen.generate("transcript", "date", "speakers")

        # Should have tried max_retries + 1 = 3 times
        assert gen._client.messages.create.call_count == 3
