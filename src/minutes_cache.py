"""Cache for generated meeting minutes stored in processed_files.json.

Avoids redundant Claude API calls when the same transcript is re-processed
(e.g. after a Discord message is deleted and the pipeline re-triggers).

Cache entries are stored under the ``"minutes_cache"`` key in the JSON file,
keyed by a SHA-256 hash of the transcript text.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _cache_key(transcript: str) -> str:
    """Compute a deterministic cache key from the transcript text."""
    return hashlib.sha256(transcript.encode("utf-8")).hexdigest()


class MinutesCache:
    """Minutes cache backed by a JSON file (shared with processed-files DB)."""

    _SECTION = "minutes_cache"

    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                self._path.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                return
            except PermissionError:
                if attempt < max_retries:
                    import time
                    time.sleep(0.5)
                else:
                    logger.warning(
                        "Failed to save minutes cache after %d attempts (PermissionError)",
                        max_retries,
                    )

    def get(self, transcript: str) -> str | None:
        """Return cached minutes markdown, or None on cache miss."""
        key = _cache_key(transcript)
        cache = self._load().get(self._SECTION, {})
        minutes_md = cache.get(key)
        if minutes_md is not None:
            logger.info("Minutes cache hit (key=%s…)", key[:12])
        return minutes_md

    def put(self, transcript: str, minutes_md: str) -> None:
        """Store generated minutes in the cache."""
        key = _cache_key(transcript)
        data = self._load()
        cache = data.setdefault(self._SECTION, {})
        cache[key] = minutes_md
        self._save(data)
        logger.info("Minutes cached (key=%s…)", key[:12])
