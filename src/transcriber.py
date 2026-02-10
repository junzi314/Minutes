"""Whisper-based audio transcription using faster-whisper."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel

from src.audio_source import SpeakerAudio
from src.config import WhisperConfig
from src.errors import TranscriptionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Segment:
    """A single transcription segment with speaker attribution."""

    start: float
    end: float
    text: str
    speaker: str


class Transcriber:
    """Manages a faster-whisper model and transcribes audio files.

    The model is loaded once and kept resident in VRAM for the lifetime
    of the Transcriber instance.
    """

    def __init__(self, cfg: WhisperConfig) -> None:
        self._cfg = cfg
        self._model: WhisperModel | None = None

    def load_model(self) -> None:
        """Load the Whisper model into memory (CPU or GPU).

        Call this once at startup. Subsequent calls are no-ops.
        """
        if self._model is not None:
            return

        logger.info(
            "Loading whisper model %s (device=%s, compute=%s)",
            self._cfg.model,
            self._cfg.device,
            self._cfg.compute_type,
        )
        t0 = time.monotonic()
        self._model = WhisperModel(
            self._cfg.model,
            device=self._cfg.device,
            compute_type=self._cfg.compute_type,
        )
        elapsed = time.monotonic() - t0
        logger.info("Whisper model loaded in %.1fs", elapsed)

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def transcribe_file(self, audio_path: Path, speaker_name: str) -> list[Segment]:
        """Transcribe a single audio file and return segments tagged with *speaker_name*."""
        if self._model is None:
            raise TranscriptionError("Model not loaded -- call load_model() first")

        path = Path(audio_path)
        if not path.exists():
            raise TranscriptionError(f"Audio file not found: {path}")

        logger.info("Transcribing %s (speaker=%s)", path.name, speaker_name)
        t0 = time.monotonic()

        try:
            segments_iter, info = self._model.transcribe(
                str(path),
                language=self._cfg.language,
                beam_size=self._cfg.beam_size,
                vad_filter=self._cfg.vad_filter,
            )
        except RuntimeError as exc:
            msg = str(exc)
            if "CUDA" in msg or "out of memory" in msg.lower():
                raise TranscriptionError(
                    f"GPU out of memory transcribing {path.name}. "
                    "Try a smaller model or reduce concurrent workload."
                ) from exc
            raise TranscriptionError(
                f"Whisper runtime error for {path.name}: {exc}"
            ) from exc
        except ValueError as exc:
            raise TranscriptionError(
                f"Invalid or corrupted audio file {path.name}: {exc}"
            ) from exc
        except Exception as exc:
            raise TranscriptionError(
                f"Whisper transcription failed for {path.name}: {exc}"
            ) from exc

        segments: list[Segment] = []
        for seg in segments_iter:
            text = seg.text.strip()
            if text:
                segments.append(Segment(
                    start=seg.start,
                    end=seg.end,
                    text=text,
                    speaker=speaker_name,
                ))

        elapsed = time.monotonic() - t0
        logger.info(
            "Transcribed %s: %d segments in %.1fs (lang=%s, prob=%.2f)",
            path.name,
            len(segments),
            elapsed,
            info.language,
            info.language_probability,
        )
        return segments

    def transcribe_all(self, tracks: list[SpeakerAudio]) -> list[Segment]:
        """Transcribe all speaker audio tracks sequentially.

        Returns the combined (unsorted) list of segments from all tracks.
        """
        all_segments: list[Segment] = []

        for i, track in enumerate(tracks, 1):
            logger.info(
                "Transcribing speaker %d/%d: %s",
                i,
                len(tracks),
                track.speaker.username,
            )
            segments = self.transcribe_file(
                track.file_path,
                speaker_name=track.speaker.username,
            )
            all_segments.extend(segments)

        logger.info(
            "Transcription complete: %d total segments from %d speakers",
            len(all_segments),
            len(tracks),
        )
        return all_segments
