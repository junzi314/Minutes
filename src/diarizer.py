"""Speaker diarization using DiariZen with Protocol interface."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.config import DiarizationConfig
from src.errors import DiarizationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiarSegment:
    """A speaker diarization segment (no text content)."""

    start: float  # seconds
    end: float  # seconds
    speaker: str  # e.g., "Speaker_0", "Speaker_1"


@runtime_checkable
class Diarizer(Protocol):
    """Abstract interface for speaker diarization backends."""

    @property
    def is_loaded(self) -> bool: ...

    def load_model(self) -> None: ...

    def diarize(self, audio_path: Path) -> list[DiarSegment]: ...

    def unload_model(self) -> None: ...


class DiariZenDiarizer:
    """DiariZen-based speaker diarization (WavLM backbone).

    Uses BUT-FIT/diarizen-wavlm-large-s80-md model.
    VRAM usage: ~340 MB (measured on RTX 3060).
    """

    def __init__(self, cfg: DiarizationConfig) -> None:
        self._cfg = cfg
        self._pipeline: object | None = None

    @property
    def is_loaded(self) -> bool:
        return self._pipeline is not None

    def load_model(self) -> None:
        if self._pipeline is not None:
            return

        logger.info(
            "Loading DiariZen model: %s (device=%s)",
            self._cfg.model,
            self._cfg.device,
        )
        t0 = time.monotonic()

        try:
            from diarizen.pipelines.inference import DiariZenPipeline
        except ImportError as exc:
            raise DiarizationError(
                "DiariZen is not installed. Install with: "
                "pip install git+https://github.com/BUTSpeechFIT/DiariZen.git"
            ) from exc

        try:
            self._pipeline = DiariZenPipeline.from_pretrained(self._cfg.model)
        except Exception as exc:
            raise DiarizationError(
                f"Failed to load DiariZen model '{self._cfg.model}': {exc}"
            ) from exc

        elapsed = time.monotonic() - t0
        logger.info("DiariZen model loaded in %.1fs", elapsed)
        _log_vram_usage("after DiariZen load")

    def diarize(self, audio_path: Path) -> list[DiarSegment]:
        if self._pipeline is None:
            raise DiarizationError(
                "DiariZen model not loaded -- call load_model() first"
            )

        if not audio_path.exists():
            raise DiarizationError(f"Audio file not found: {audio_path}")

        logger.info("Diarizing %s", audio_path.name)
        t0 = time.monotonic()

        try:
            result = self._pipeline(
                str(audio_path),
                sess_name=audio_path.stem,
            )
        except RuntimeError as exc:
            msg = str(exc)
            if "CUDA" in msg or "out of memory" in msg.lower():
                raise DiarizationError(
                    f"GPU out of memory during diarization of {audio_path.name}. "
                    "Try running on CPU or reducing concurrent workload."
                ) from exc
            raise DiarizationError(
                f"DiariZen runtime error for {audio_path.name}: {exc}"
            ) from exc
        except Exception as exc:
            raise DiarizationError(
                f"DiariZen diarization failed for {audio_path.name}: {exc}"
            ) from exc

        segments: list[DiarSegment] = []
        for segment, _track, speaker_label in result.itertracks(yield_label=True):
            segments.append(
                DiarSegment(
                    start=segment.start,
                    end=segment.end,
                    speaker=str(speaker_label),
                )
            )

        segments.sort(key=lambda s: s.start)

        elapsed = time.monotonic() - t0
        speakers = {s.speaker for s in segments}
        logger.info(
            "Diarization complete: %d segments, %d speakers in %.1fs",
            len(segments),
            len(speakers),
            elapsed,
        )
        _log_vram_usage("after diarization inference")
        return segments

    def unload_model(self) -> None:
        if self._pipeline is None:
            return

        logger.info("Unloading DiariZen model")
        del self._pipeline
        self._pipeline = None

        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                logger.debug("CUDA cache cleared after DiariZen unload")
        except ImportError:
            pass

        _log_vram_usage("after DiariZen unload")


def _log_vram_usage(context: str) -> None:
    """Log current VRAM usage (best-effort, no-op if torch unavailable)."""
    try:
        import torch

        if torch.cuda.is_available():
            allocated = torch.cuda.memory_allocated() / 1024**2
            reserved = torch.cuda.memory_reserved() / 1024**2
            logger.info(
                "[VRAM %s] allocated=%.0f MB, reserved=%.0f MB",
                context,
                allocated,
                reserved,
            )
    except ImportError:
        pass
