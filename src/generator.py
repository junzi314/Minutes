"""Minutes generation via Anthropic Claude API."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import anthropic

from src.config import GeneratorConfig
from src.errors import GenerationError

logger = logging.getLogger(__name__)


class MinutesGenerator:
    """Renders a prompt template and calls the Claude API to generate minutes."""

    def __init__(self, cfg: GeneratorConfig) -> None:
        self._cfg = cfg
        self._template: str | None = None
        self._client: anthropic.Anthropic | None = None

    def load(self) -> None:
        """Load the prompt template and initialise the API client.

        Call once at startup.  Subsequent calls are no-ops.
        """
        if self._template is not None:
            return

        path = Path(self._cfg.prompt_template_path)
        if not path.exists():
            raise GenerationError(f"Prompt template not found: {path}")

        self._template = path.read_text(encoding="utf-8")
        logger.debug("Loaded prompt template from %s (%d chars)", path, len(self._template))

        if not self._cfg.api_key:
            raise GenerationError("ANTHROPIC_API_KEY is not set")

        self._client = anthropic.Anthropic(api_key=self._cfg.api_key)
        logger.info("MinutesGenerator initialised (model=%s)", self._cfg.model)

    @property
    def is_loaded(self) -> bool:
        return self._template is not None and self._client is not None

    def render_prompt(
        self,
        transcript: str,
        date: str,
        speakers: str,
        guild_name: str = "",
        channel_name: str = "",
    ) -> str:
        """Fill in template variables and return the rendered prompt.

        Uses simple string replacement instead of str.format() to avoid
        breakage from literal braces in user-supplied values (guild names,
        transcript text, etc.).
        """
        if self._template is None:
            raise GenerationError("Template not loaded -- call load() first")

        result = self._template
        replacements = {
            "{transcript}": transcript,
            "{date}": date,
            "{speakers}": speakers,
            "{guild_name}": guild_name,
            "{channel_name}": channel_name,
        }
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value)
        return result

    async def generate(
        self,
        transcript: str,
        date: str,
        speakers: str,
        guild_name: str = "",
        channel_name: str = "",
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
        )

        last_exc: Exception | None = None
        max_attempts = self._cfg.max_retries + 1

        for attempt in range(1, max_attempts + 1):
            try:
                t0 = time.monotonic()
                logger.info(
                    "Calling Claude API (attempt %d/%d, model=%s)",
                    attempt,
                    max_attempts,
                    self._cfg.model,
                )

                # Run synchronous Anthropic call in a thread to avoid
                # blocking the async event loop.
                response = await asyncio.to_thread(
                    self._client.messages.create,
                    model=self._cfg.model,
                    max_tokens=self._cfg.max_tokens,
                    temperature=self._cfg.temperature,
                    messages=[{"role": "user", "content": prompt}],
                )

                elapsed = time.monotonic() - t0
                text = response.content[0].text
                logger.info(
                    "Claude API responded in %.1fs (%d chars, %d input tokens, %d output tokens)",
                    elapsed,
                    len(text),
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
                return text

            except anthropic.RateLimitError as exc:
                last_exc = exc
                logger.warning(
                    "Rate limited on attempt %d/%d: %s",
                    attempt, max_attempts, exc,
                )
            except anthropic.APIStatusError as exc:
                last_exc = exc
                # 4xx (except 429) are not retryable
                if 400 <= exc.status_code < 500 and exc.status_code != 429:
                    raise GenerationError(
                        f"Claude API client error (HTTP {exc.status_code}): {exc.message}"
                    ) from exc
                logger.warning(
                    "API error on attempt %d/%d (HTTP %d): %s",
                    attempt, max_attempts, exc.status_code, exc.message,
                )
            except anthropic.APIConnectionError as exc:
                last_exc = exc
                logger.warning(
                    "Connection error on attempt %d/%d: %s",
                    attempt, max_attempts, exc,
                )

            # Exponential backoff before next retry
            if attempt < max_attempts:
                delay = 2 ** (attempt - 1)
                logger.debug("Retrying in %ds...", delay)
                await asyncio.sleep(delay)

        raise GenerationError(
            f"Claude API failed after {max_attempts} attempts: {last_exc}"
        ) from last_exc
