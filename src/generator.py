"""Minutes generation via LLM API (Claude or OpenAI-compatible)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import anthropic

from src.config import GeneratorConfig
from src.errors import GenerationError

logger = logging.getLogger(__name__)


class _ApiRetryable(Exception):
    """Backend-agnostic retryable API error."""


@dataclass
class TemplateInfo:
    """Metadata about an available prompt template."""

    name: str          # file stem: "minutes", "todo-focused"
    display_name: str  # from "# name:" comment, or name
    description: str   # from "# description:" comment
    path: Path


def _parse_template_metadata(path: Path) -> tuple[str, str]:
    """Extract name and description from template file header comments.

    Format::

        # name: 表示名
        # description: 説明文

    Returns (display_name, description), either may be empty string.
    """
    display_name = ""
    description = ""
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped.startswith("#"):
                break
            if stripped.startswith("# name:"):
                display_name = stripped[len("# name:"):].strip()
            elif stripped.startswith("# description:"):
                description = stripped[len("# description:"):].strip()
    return display_name, description


class MinutesGenerator:
    """Renders a prompt template and calls the Claude API to generate minutes."""

    def __init__(self, cfg: GeneratorConfig) -> None:
        self._cfg = cfg
        self._templates: dict[str, str] = {}
        self._prompts_dir: Path | None = None
        self._client: anthropic.Anthropic | None = None
        self._openai_client: object | None = None

    def load(self) -> None:
        """Load the default prompt template and initialise the API client.

        Call once at startup.  Subsequent calls are no-ops.
        """
        if self._templates:
            return

        path = Path(self._cfg.prompt_template_path)
        if not path.exists():
            raise GenerationError(f"Prompt template not found: {path}")

        self._prompts_dir = path.parent
        self._templates[path.stem] = path.read_text(encoding="utf-8")
        logger.debug("Loaded prompt template from %s (%d chars)", path, len(self._templates[path.stem]))

        if self._cfg.backend == "claude":
            if not self._cfg.api_key:
                raise GenerationError("ANTHROPIC_API_KEY is not set")
            self._client = anthropic.Anthropic(api_key=self._cfg.api_key)
        elif self._cfg.backend == "openai_compat":
            try:
                import openai
            except ImportError:
                raise GenerationError(
                    "openai package is required for openai_compat backend. "
                    "Install with: pip install openai"
                )
            self._openai_client = openai.OpenAI(
                base_url=self._cfg.base_url,
                api_key=self._cfg.api_key or "not-needed",
            )
        logger.info(
            "MinutesGenerator initialised (backend=%s, model=%s)",
            self._cfg.backend, self._cfg.model,
        )

    def _load_template(self, name: str) -> str:
        """Load and cache a template by name. Raises GenerationError if not found."""
        if name in self._templates:
            return self._templates[name]

        if ".." in name or "/" in name or "\\" in name:
            raise GenerationError(f"Invalid template name: {name}")

        if self._prompts_dir is None:
            raise GenerationError("Generator not loaded -- call load() first")

        path = self._prompts_dir / f"{name}.txt"
        if not path.exists():
            raise GenerationError(f"Template not found: {name}")

        content = path.read_text(encoding="utf-8")
        self._templates[name] = content
        logger.debug("Loaded template '%s' from %s (%d chars)", name, path, len(content))
        return content

    def list_templates(self) -> list[TemplateInfo]:
        """Scan prompts/ directory for available templates."""
        if self._prompts_dir is None:
            return []
        templates = []
        for p in sorted(self._prompts_dir.glob("*.txt")):
            name = p.stem
            display_name, description = _parse_template_metadata(p)
            templates.append(TemplateInfo(
                name=name,
                display_name=display_name or name,
                description=description or "",
                path=p,
            ))
        return templates

    @property
    def is_loaded(self) -> bool:
        return bool(self._templates) and (
            self._client is not None or self._openai_client is not None
        )

    def render_prompt(
        self,
        transcript: str,
        date: str,
        speakers: str,
        guild_name: str = "",
        channel_name: str = "",
        template_name: str = "minutes",
        event_title: str = "",
        event_attendees: str = "",
        event_description: str = "",
    ) -> str:
        """Fill in template variables and return the rendered prompt.

        Uses simple string replacement instead of str.format() to avoid
        breakage from literal braces in user-supplied values (guild names,
        transcript text, etc.).
        """
        template = self._load_template(template_name)

        replacements = {
            "{transcript}": transcript,
            "{date}": date,
            "{speakers}": speakers,
            "{guild_name}": guild_name,
            "{channel_name}": channel_name,
            "{event_title}": event_title,
            "{event_attendees}": event_attendees,
            "{event_description}": event_description,
        }
        result = template
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value)
        return result

    async def _call_api(self, prompt: str) -> str:
        """Dispatch to the configured backend."""
        if self._cfg.backend == "claude":
            return await self._call_claude_api(prompt)
        return await self._call_openai_api(prompt)

    async def _call_claude_api(self, prompt: str) -> str:
        try:
            response = await asyncio.to_thread(
                self._client.messages.create,
                model=self._cfg.model,
                max_tokens=self._cfg.max_tokens,
                temperature=self._cfg.temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text
            logger.info(
                "Claude: %d input tokens, %d output tokens",
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            return text
        except anthropic.RateLimitError as exc:
            raise _ApiRetryable(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            if 400 <= exc.status_code < 500 and exc.status_code != 429:
                raise GenerationError(
                    f"Claude API client error (HTTP {exc.status_code}): {exc.message}"
                ) from exc
            raise _ApiRetryable(str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise _ApiRetryable(str(exc)) from exc

    async def _call_openai_api(self, prompt: str) -> str:
        import openai

        try:
            response = await asyncio.to_thread(
                self._openai_client.chat.completions.create,
                model=self._cfg.model,
                max_tokens=self._cfg.max_tokens,
                temperature=self._cfg.temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            if not response.choices:
                raise GenerationError("API returned empty response (no choices)")
            text = response.choices[0].message.content or ""
            if response.usage:
                logger.info(
                    "OpenAI-compat: %d input tokens, %d output tokens",
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens,
                )
            return text
        except openai.RateLimitError as exc:
            raise _ApiRetryable(str(exc)) from exc
        except openai.APIStatusError as exc:
            if 400 <= exc.status_code < 500 and exc.status_code != 429:
                raise GenerationError(
                    f"API client error (HTTP {exc.status_code}): {exc.message}"
                ) from exc
            raise _ApiRetryable(str(exc)) from exc
        except openai.APIConnectionError as exc:
            raise _ApiRetryable(str(exc)) from exc

    async def generate(
        self,
        transcript: str,
        date: str,
        speakers: str,
        guild_name: str = "",
        channel_name: str = "",
        template_name: str = "minutes",
        event_title: str = "",
        event_attendees: str = "",
        event_description: str = "",
    ) -> str:
        """Generate meeting minutes from a transcript.

        Retries on transient API errors with exponential backoff.
        Returns the generated minutes as a Markdown string.
        """
        if not self.is_loaded:
            raise GenerationError("Generator not loaded -- call load() first")

        prompt = self.render_prompt(
            transcript=transcript,
            date=date,
            speakers=speakers,
            guild_name=guild_name,
            channel_name=channel_name,
            template_name=template_name,
            event_title=event_title,
            event_attendees=event_attendees,
            event_description=event_description,
        )

        last_exc: Exception | None = None
        max_attempts = self._cfg.max_retries + 1

        for attempt in range(1, max_attempts + 1):
            try:
                t0 = time.monotonic()
                logger.info(
                    "Calling %s API (attempt %d/%d, model=%s)",
                    self._cfg.backend,
                    attempt,
                    max_attempts,
                    self._cfg.model,
                )
                text = await self._call_api(prompt)
                elapsed = time.monotonic() - t0
                logger.info(
                    "API responded in %.1fs (%d chars)", elapsed, len(text),
                )
                return text

            except _ApiRetryable as exc:
                last_exc = exc
                logger.warning(
                    "Retryable error on attempt %d/%d: %s",
                    attempt, max_attempts, exc,
                )

            # Exponential backoff before next retry
            if attempt < max_attempts:
                delay = 2 ** (attempt - 1)
                logger.debug("Retrying in %ds...", delay)
                await asyncio.sleep(delay)

        raise GenerationError(
            f"API failed after {max_attempts} attempts: {last_exc}"
        ) from last_exc
