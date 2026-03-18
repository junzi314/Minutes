"""Unit tests for src/generator.py."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import GeneratorConfig
from src.errors import GenerationError
from src.generator import MinutesGenerator, TemplateInfo, _parse_template_metadata


def _write_template(tmp_path: Path) -> Path:
    """Write a template file and return its path."""
    template = tmp_path / "minutes.txt"
    template.write_text(
        "# name: Standard\n# description: Standard template\n"
        "Date: {date}\nSpeakers: {speakers}\n"
        "Guild: {guild_name}\nChannel: {channel_name}\n"
        "Transcript:\n{transcript}",
        encoding="utf-8",
    )
    return template


def _make_cfg(tmp_path: Path, api_key: str = "sk-test", **kwargs) -> GeneratorConfig:
    """Create a GeneratorConfig with a real template file."""
    template = _write_template(tmp_path)
    defaults = dict(
        api_key=api_key,
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        temperature=0.3,
        prompt_template_path=str(template),
        max_retries=2,
    )
    defaults.update(kwargs)
    return GeneratorConfig(**defaults)


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

    def test_render_with_template_name(self, tmp_path: Path) -> None:
        """render_prompt uses the specified template."""
        cfg = _make_cfg(tmp_path)
        # Create a second template
        custom = tmp_path / "custom.txt"
        custom.write_text("CUSTOM: {transcript}", encoding="utf-8")

        gen = MinutesGenerator(cfg)
        gen.load()

        result = gen.render_prompt(
            transcript="hello",
            date="d",
            speakers="s",
            template_name="custom",
        )
        assert result == "CUSTOM: hello"


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


class TestListTemplates:
    def test_list_templates(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path)
        # Create a second template
        custom = tmp_path / "custom.txt"
        custom.write_text(
            "# name: Custom\n# description: A custom template\nContent",
            encoding="utf-8",
        )

        gen = MinutesGenerator(cfg)
        gen.load()

        templates = gen.list_templates()
        names = [t.name for t in templates]
        assert "minutes" in names
        assert "custom" in names
        assert all(isinstance(t, TemplateInfo) for t in templates)

    def test_list_templates_before_load(self) -> None:
        cfg = GeneratorConfig(api_key="sk-test")
        gen = MinutesGenerator(cfg)
        assert gen.list_templates() == []


class TestParseTemplateMetadata:
    def test_parse_both_fields(self, tmp_path: Path) -> None:
        p = tmp_path / "test.txt"
        p.write_text("# name: My Name\n# description: My Desc\nBody", encoding="utf-8")
        display, desc = _parse_template_metadata(p)
        assert display == "My Name"
        assert desc == "My Desc"

    def test_parse_no_metadata(self, tmp_path: Path) -> None:
        p = tmp_path / "test.txt"
        p.write_text("Just body content", encoding="utf-8")
        display, desc = _parse_template_metadata(p)
        assert display == ""
        assert desc == ""

    def test_parse_partial_metadata(self, tmp_path: Path) -> None:
        p = tmp_path / "test.txt"
        p.write_text("# name: Only Name\nBody", encoding="utf-8")
        display, desc = _parse_template_metadata(p)
        assert display == "Only Name"
        assert desc == ""


class TestOpenAICompat:
    def test_load_openai_compat(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path, api_key="", backend="openai_compat",
                        base_url="http://localhost:11434/v1")
        gen = MinutesGenerator(cfg)
        with patch("openai.OpenAI") as mock_cls:
            gen.load()
            mock_cls.assert_called_once_with(
                base_url="http://localhost:11434/v1",
                api_key="not-needed",
            )
        assert gen.is_loaded

    def test_load_openai_compat_missing_package(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path, api_key="", backend="openai_compat",
                        base_url="http://localhost:11434/v1")
        gen = MinutesGenerator(cfg)
        with patch.dict("sys.modules", {"openai": None}):
            with pytest.raises(GenerationError, match="pip install openai"):
                gen.load()

    @pytest.mark.asyncio
    async def test_generate_openai_compat_success(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path, api_key="", backend="openai_compat",
                        base_url="http://localhost:11434/v1")
        gen = MinutesGenerator(cfg)
        gen.load()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="# 議事録\nテスト"))]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)
        gen._openai_client.chat.completions.create = MagicMock(return_value=mock_response)

        result = await gen.generate("transcript", "date", "speakers")
        assert "議事録" in result

    @pytest.mark.asyncio
    async def test_generate_openai_compat_rate_limit(self, tmp_path: Path) -> None:
        import openai as openai_mod

        cfg = _make_cfg(tmp_path, api_key="", backend="openai_compat",
                        base_url="http://localhost:11434/v1")
        gen = MinutesGenerator(cfg)
        gen.load()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Success"))]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)

        rate_exc = openai_mod.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )

        gen._openai_client.chat.completions.create = MagicMock(
            side_effect=[rate_exc, mock_response]
        )

        result = await gen.generate("transcript", "date", "speakers")
        assert result == "Success"
        assert gen._openai_client.chat.completions.create.call_count == 2

    @pytest.mark.asyncio
    async def test_generate_openai_compat_client_error(self, tmp_path: Path) -> None:
        import openai as openai_mod

        cfg = _make_cfg(tmp_path, api_key="", backend="openai_compat",
                        base_url="http://localhost:11434/v1")
        gen = MinutesGenerator(cfg)
        gen.load()

        client_exc = openai_mod.APIStatusError(
            message="bad request",
            response=MagicMock(status_code=400, headers={}),
            body=None,
        )
        gen._openai_client.chat.completions.create = MagicMock(side_effect=client_exc)

        with pytest.raises(GenerationError, match="client error"):
            await gen.generate("transcript", "date", "speakers")

    @pytest.mark.asyncio
    async def test_generate_openai_compat_connection_error(self, tmp_path: Path) -> None:
        import openai as openai_mod

        cfg = _make_cfg(tmp_path, api_key="", backend="openai_compat",
                        base_url="http://localhost:11434/v1")
        gen = MinutesGenerator(cfg)
        gen.load()

        conn_exc = openai_mod.APIConnectionError(request=MagicMock())
        gen._openai_client.chat.completions.create = MagicMock(side_effect=conn_exc)

        with pytest.raises(GenerationError, match="failed after"):
            await gen.generate("transcript", "date", "speakers")

        assert gen._openai_client.chat.completions.create.call_count == 3

    @pytest.mark.asyncio
    async def test_generate_openai_compat_empty_response(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path, api_key="", backend="openai_compat",
                        base_url="http://localhost:11434/v1")
        gen = MinutesGenerator(cfg)
        gen.load()

        mock_response = MagicMock()
        mock_response.choices = []
        gen._openai_client.chat.completions.create = MagicMock(return_value=mock_response)

        with pytest.raises(GenerationError, match="empty response"):
            await gen.generate("transcript", "date", "speakers")

    @pytest.mark.asyncio
    async def test_generate_openai_compat_no_usage(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path, api_key="", backend="openai_compat",
                        base_url="http://localhost:11434/v1")
        gen = MinutesGenerator(cfg)
        gen.load()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Result"))]
        mock_response.usage = None
        gen._openai_client.chat.completions.create = MagicMock(return_value=mock_response)

        result = await gen.generate("transcript", "date", "speakers")
        assert result == "Result"


class TestLoadTemplate:
    def test_load_template_not_found(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path)
        gen = MinutesGenerator(cfg)
        gen.load()

        with pytest.raises(GenerationError, match="Template not found"):
            gen._load_template("nonexistent")

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path)
        gen = MinutesGenerator(cfg)
        gen.load()

        for bad_name in ["../etc/passwd", "foo/bar", "a\\b"]:
            with pytest.raises(GenerationError, match="Invalid template name"):
                gen._load_template(bad_name)

    def test_template_cached(self, tmp_path: Path) -> None:
        cfg = _make_cfg(tmp_path)
        gen = MinutesGenerator(cfg)
        gen.load()

        # First load reads from disk
        content1 = gen._load_template("minutes")
        # Modify file on disk
        (tmp_path / "minutes.txt").write_text("CHANGED", encoding="utf-8")
        # Second load should return cached content
        content2 = gen._load_template("minutes")
        assert content1 == content2
