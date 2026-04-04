"""Google Drive watcher for Zoom recordings with m4a + VTT file pairing."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.config import GoogleDriveConfig
from src.drive_watcher import _build_drive_service, _download_drive_file
from src.errors import DriveWatchError
from src.slack_config import ZoomConfig
from src.state_store import StateStore

logger = logging.getLogger(__name__)

# Callback signature: (audio_path, vtt_path, source_label) -> None
# IMPORTANT: Paths point to a temp directory that is cleaned up after the
# callback returns.  The callback must finish all file I/O before returning.
OnPairReadyCallback = Callable[[Path, Path, str], Awaitable[None]]


@dataclass
class _PendingPair:
    """Tracks partially received file pairs."""

    audio_id: str | None = None
    audio_name: str | None = None
    vtt_id: str | None = None
    vtt_name: str | None = None
    first_seen: float = field(default_factory=time.monotonic)

    @property
    def is_complete(self) -> bool:
        return self.audio_id is not None and self.vtt_id is not None


class ZoomDriveWatcher:
    """Monitors Google Drive for Zoom m4a + VTT file pairs.

    Files are paired by common basename prefix (before the file extension).
    Once both files are detected, they are downloaded and the callback is invoked.
    Unpaired files are dropped after pair_timeout_sec.

    Usage::

        watcher = ZoomDriveWatcher(drive_cfg, zoom_cfg, state_store, on_pair_ready=cb)
        watcher.start()
        ...
        watcher.stop()
    """

    def __init__(
        self,
        drive_cfg: GoogleDriveConfig,
        zoom_cfg: ZoomConfig,
        state_store: StateStore,
        on_pair_ready: OnPairReadyCallback,
    ) -> None:
        self._drive_cfg = drive_cfg
        self._zoom_cfg = zoom_cfg
        self._state_store = state_store
        self._on_pair_ready = on_pair_ready
        self._task: asyncio.Task[None] | None = None
        self._service: Any = None
        self._pair_buffer: dict[str, _PendingPair] = {}

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            logger.warning("ZoomDriveWatcher.start() called but task already running")
            return
        self._task = asyncio.create_task(self._watch_loop(), name="zoom-drive-watcher")
        logger.info("ZoomDriveWatcher polling task started")

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            logger.info("ZoomDriveWatcher polling task cancelled")
        self._task = None

    def _get_service(self) -> Any:
        if self._service is None:
            self._service = _build_drive_service(self._drive_cfg.credentials_path)
        return self._service

    @staticmethod
    def _pair_key(file_name: str) -> str:
        """Extract pairing key from filename by stripping the last extension."""
        return Path(file_name).stem

    def _list_files_sync(self) -> list[dict[str, str]]:
        """List audio and VTT files in the configured folder."""
        service = self._get_service()

        if not self._drive_cfg.folder_id:
            raise DriveWatchError("google_drive.folder_id is not configured")

        query_parts: list[str] = [
            f"'{self._drive_cfg.folder_id}' in parents",
            "trashed = false",
        ]
        query = " and ".join(query_parts)

        try:
            results: list[dict[str, str]] = []
            page_token: str | None = None

            while True:
                response = (
                    service.files()
                    .list(
                        q=query,
                        spaces="drive",
                        fields="nextPageToken, files(id, name, mimeType)",
                        pageToken=page_token,
                        pageSize=100,
                    )
                    .execute()
                )

                for f in response.get("files", []):
                    name = f["name"]
                    if fnmatch.fnmatch(name, self._zoom_cfg.audio_file_pattern) or \
                       fnmatch.fnmatch(name, self._zoom_cfg.vtt_file_pattern):
                        results.append(f)

                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            return results

        except Exception as exc:
            raise DriveWatchError(
                f"Failed to list files in folder {self._drive_cfg.folder_id}: {exc}"
            ) from exc

    def _classify_and_buffer(self, file_info: dict[str, str]) -> None:
        """Add a file to the pairing buffer."""
        name = file_info["name"]
        file_id = file_info["id"]

        is_audio = fnmatch.fnmatch(name, self._zoom_cfg.audio_file_pattern)
        is_vtt = fnmatch.fnmatch(name, self._zoom_cfg.vtt_file_pattern)

        if not is_audio and not is_vtt:
            return

        key = self._pair_key(name)

        if key not in self._pair_buffer:
            self._pair_buffer[key] = _PendingPair()

        pair = self._pair_buffer[key]

        if is_audio:
            pair.audio_id = file_id
            pair.audio_name = name
        elif is_vtt:
            pair.vtt_id = file_id
            pair.vtt_name = name

    def _expire_stale_pairs(self) -> list[str]:
        """Remove pairs that have timed out. Returns expired keys."""
        now = time.monotonic()
        timeout = self._zoom_cfg.pair_timeout_sec
        expired: list[str] = []

        for key, pair in list(self._pair_buffer.items()):
            if now - pair.first_seen > timeout:
                logger.warning(
                    "Pair timeout for '%s' (audio=%s, vtt=%s) after %ds",
                    key,
                    pair.audio_name or "missing",
                    pair.vtt_name or "missing",
                    timeout,
                )
                expired.append(key)
                del self._pair_buffer[key]

        return expired

    async def _watch_loop(self) -> None:
        """Poll for new Zoom files and process completed pairs."""
        logger.info(
            "ZoomDriveWatcher loop starting (folder_id=%s, interval=%ds)",
            self._drive_cfg.folder_id,
            self._drive_cfg.poll_interval_sec,
        )

        if not self._drive_cfg.folder_id:
            logger.error("google_drive.folder_id is empty, watch loop will not run")
            return

        creds_path = Path(self._drive_cfg.credentials_path)
        if not creds_path.exists():
            logger.error(
                "Credentials file not found at %s, watch loop will not run",
                creds_path.resolve(),
            )
            return

        loop = asyncio.get_running_loop()

        while True:
            try:
                files = await loop.run_in_executor(None, self._list_files_sync)

                # Filter to unknown files and add to buffer
                for f in files:
                    file_key = f"zoom:{f['id']}"
                    if not self._state_store.is_known(file_key):
                        self._classify_and_buffer(f)

                # Check for completed pairs
                for key in list(self._pair_buffer.keys()):
                    pair = self._pair_buffer[key]
                    if pair.is_complete:
                        del self._pair_buffer[key]
                        try:
                            await self._process_pair(loop, pair, key)
                        except Exception as exc:
                            logger.error("Failed to process pair '%s': %s", key, exc)

                # Expire timed-out pairs
                self._expire_stale_pairs()

            except asyncio.CancelledError:
                logger.info("ZoomDriveWatcher loop cancelled")
                raise
            except DriveWatchError as exc:
                logger.error("Watch error during polling: %s", exc)
            except Exception as exc:
                logger.exception("Unexpected error in watch loop: %s", exc)

            await asyncio.sleep(self._drive_cfg.poll_interval_sec)

    async def _process_pair(
        self,
        loop: asyncio.AbstractEventLoop,
        pair: _PendingPair,
        pair_key: str,
    ) -> None:
        """Download both files and invoke the callback."""
        assert pair.audio_id and pair.audio_name
        assert pair.vtt_id and pair.vtt_name

        state_key = f"zoom_pair:{pair_key}"

        if not self._state_store.mark_processing(
            state_key,
            source="zoom_drive",
            source_id=f"{pair.audio_id}+{pair.vtt_id}",
            file_name=f"{pair.audio_name} + {pair.vtt_name}",
        ):
            logger.info("Skipping pair '%s' — already known", pair_key)
            return

        logger.info("Processing Zoom pair: %s + %s", pair.audio_name, pair.vtt_name)

        service = self._get_service()

        audio_bytes = await loop.run_in_executor(
            None, _download_drive_file, service, pair.audio_id, pair.audio_name
        )
        vtt_bytes = await loop.run_in_executor(
            None, _download_drive_file, service, pair.vtt_id, pair.vtt_name
        )

        # Mark individual files as known to prevent re-buffering
        self._state_store.mark_success(f"zoom:{pair.audio_id}")
        self._state_store.mark_success(f"zoom:{pair.vtt_id}")

        tmp_dir_obj = tempfile.TemporaryDirectory(prefix=f"zoom-{pair_key[:16]}-")
        tmp_path = Path(tmp_dir_obj.name)

        try:
            audio_path = tmp_path / pair.audio_name
            audio_path.write_bytes(audio_bytes)

            vtt_path = tmp_path / pair.vtt_name
            vtt_path.write_bytes(vtt_bytes)

            source_label = f"zoom:{pair.audio_name}"
            await self._on_pair_ready(audio_path, vtt_path, source_label)

            self._state_store.mark_success(state_key)
            logger.info("Successfully processed Zoom pair: %s", pair_key)

        except Exception as exc:
            self._state_store.mark_failed(state_key, str(exc))
            raise
        finally:
            try:
                tmp_dir_obj.cleanup()
            except OSError as exc:
                logger.debug("Temp dir cleanup failed: %s", exc)
