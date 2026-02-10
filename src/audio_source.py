"""Abstract base class for audio acquisition and shared ZIP extraction."""

from __future__ import annotations

import io
import logging
import re
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Pattern for filenames inside Craig ZIP: {track}-{username}.{format}
ZIP_FILENAME_PATTERN = re.compile(
    r"^(\d+)-(.+)\.(aac|flac|ogg|mp3|wav)$"
)


@dataclass(frozen=True)
class SpeakerInfo:
    track: int
    username: str
    user_id: int


@dataclass(frozen=True)
class SpeakerAudio:
    speaker: SpeakerInfo
    file_path: Path


class AudioSource(ABC):
    @abstractmethod
    async def get_speakers(self) -> list[SpeakerInfo]:
        ...

    @abstractmethod
    async def download(self, dest_dir: Path) -> list[SpeakerAudio]:
        ...


def extract_speaker_zip(zip_bytes: bytes, dest_dir: Path) -> list[SpeakerAudio]:
    """Extract per-speaker audio files from a Craig ZIP archive.

    Parses ZIP entry filenames matching ``{track}-{username}.{ext}``.
    Includes Zip Slip protection to prevent path traversal attacks.

    Raises ``zipfile.BadZipFile`` on invalid ZIP data.
    """
    results: list[SpeakerAudio] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            match = ZIP_FILENAME_PATTERN.match(name)
            if not match:
                logger.debug("Skipping non-audio ZIP entry: %s", name)
                continue

            track_num = int(match.group(1))
            username = match.group(2)

            dest_file = dest_dir / name
            # Zip Slip protection: ensure extracted path stays inside dest_dir
            if not dest_file.resolve().is_relative_to(dest_dir.resolve()):
                logger.warning("Blocked Zip Slip attempt: %s", name)
                continue

            speaker = SpeakerInfo(
                track=track_num,
                username=username,
                user_id=0,
            )

            dest_file.write_bytes(zf.read(name))
            logger.debug("Extracted %s -> %s", name, dest_file)

            results.append(SpeakerAudio(speaker=speaker, file_path=dest_file))

    return results
