"""Google Drive folder watcher for Craig recording ZIP files and video/audio files.

Polls a Google Drive folder for new files matching configurable patterns,
downloads them, and invokes callbacks to trigger processing pipelines.

Two watcher classes:
  - DriveWatcher: Craig recording ZIPs → extract tracks → pipeline
  - VideoDriveWatcher: Video/audio files → diarization pipeline

Authentication uses a service-account JSON key with drive.readonly scope.
"""

from __future__ import annotations

import asyncio
import fnmatch
import io
import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.audio_source import SpeakerAudio, extract_speaker_zip
from src.config import DiarizationConfig, GoogleDriveConfig
from src.errors import DriveWatchError
from src.state_store import StateStore, extract_rec_id

logger = logging.getLogger(__name__)

# Google Drive API scopes required for read-only access.
_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Type alias for the callback invoked when new tracks are found.
OnNewTracksCallback = Callable[
    [list[SpeakerAudio], str, Path],
    Awaitable[None],
]

# Type alias for the callback invoked when new video/audio files are found.
OnNewVideoCallback = Callable[
    [Path, str],
    Awaitable[None],
]


class DriveWatcher:
    """Monitors a Google Drive folder for new Craig recording ZIPs.

    Usage::

        watcher = DriveWatcher(cfg, state_store, on_new_tracks=my_callback)
        watcher.start()   # launches polling task
        ...
        watcher.stop()    # cancels polling task
    """

    def __init__(
        self,
        cfg: GoogleDriveConfig,
        state_store: StateStore,
        on_new_tracks: OnNewTracksCallback,
    ) -> None:
        self._cfg = cfg
        self._state_store = state_store
        self._on_new_tracks = on_new_tracks
        self._task: asyncio.Task[None] | None = None
        self._service: Any = None  # googleapiclient.discovery.Resource

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the polling task is currently running."""
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Create the background polling task.

        Must be called from within a running event loop (e.g. inside
        a discord.py on_ready handler).
        """
        if self._task is not None and not self._task.done():
            logger.warning("DriveWatcher.start() called but task already running")
            return

        self._task = asyncio.create_task(
            self._watch_loop(), name="drive-watcher"
        )
        logger.info("DriveWatcher polling task started")

    def stop(self) -> None:
        """Cancel the background polling task."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            logger.info("DriveWatcher polling task cancelled")
        self._task = None

    # ------------------------------------------------------------------
    # Google Drive API (synchronous — run in executor)
    # ------------------------------------------------------------------

    def _build_service(self) -> Any:
        """Build and cache the Google Drive API v3 service client.

        Returns the service object, or raises DriveWatchError on failure.
        """
        if self._service is not None:
            return self._service

        creds_path = Path(self._cfg.credentials_path)
        if not creds_path.exists():
            raise DriveWatchError(
                f"Service-account credentials not found: {creds_path.resolve()}"
            )

        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build

            credentials = Credentials.from_service_account_file(
                str(creds_path), scopes=_SCOPES
            )
            self._service = build("drive", "v3", credentials=credentials)
            logger.info("Google Drive API service built successfully")
            return self._service
        except Exception as exc:
            raise DriveWatchError(
                f"Failed to build Google Drive service: {exc}"
            ) from exc

    def _list_files_sync(self) -> list[dict[str, str]]:
        """List files in the configured folder matching the file pattern.

        Returns a list of dicts with keys: id, name, mimeType.
        This is a synchronous call — must be run in an executor.
        """
        service = self._build_service()

        if not self._cfg.folder_id:
            raise DriveWatchError("google_drive.folder_id is not configured")

        # Build the Drive API query.
        # For a pattern like "craig_*.aac.zip", we use:
        #   name contains 'craig' and name contains '.aac.zip'
        # Combined with parent folder and mimeType constraints.
        # Note: Google Drive reports ZIP files as either 'application/zip'
        # or 'application/x-zip-compressed' depending on the uploader,
        # so we accept both.
        query_parts: list[str] = [
            f"'{self._cfg.folder_id}' in parents",
            "trashed = false",
            "(mimeType = 'application/zip' or mimeType = 'application/x-zip-compressed')",
        ]

        # Convert glob pattern to Drive API name-contains clauses.
        # Split on wildcards and character classes, keep non-empty literal segments.
        pattern = self._cfg.file_pattern
        literal_segments = re.split(r"[*?]+|\[.*?\]", pattern)
        for segment in literal_segments:
            if segment:
                query_parts.append(f"name contains '{segment}'")

        query = " and ".join(query_parts)
        logger.debug("Drive API query: %s", query)

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

                files = response.get("files", [])
                # Apply local fnmatch filtering for exact glob match,
                # since Drive API 'contains' is a substring check.
                for f in files:
                    if fnmatch.fnmatch(f["name"], self._cfg.file_pattern):
                        results.append(f)

                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            logger.debug("Drive listing returned %d matching files", len(results))
            return results

        except Exception as exc:
            raise DriveWatchError(
                f"Failed to list files in folder {self._cfg.folder_id}: {exc}"
            ) from exc

    def _download_file_sync(self, file_id: str, file_name: str) -> bytes:
        """Download a file's content by ID.

        Returns the raw bytes. This is a synchronous call.
        """
        service = self._build_service()

        try:
            request = service.files().get_media(fileId=file_id)
            buffer = io.BytesIO()

            # Use MediaIoBaseDownload for chunked download.
            from googleapiclient.http import MediaIoBaseDownload

            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    logger.debug(
                        "Download %s: %.0f%%",
                        file_name,
                        status.progress() * 100,
                    )

            data = buffer.getvalue()
            logger.info("Downloaded %s (%d bytes)", file_name, len(data))
            return data

        except Exception as exc:
            raise DriveWatchError(
                f"Failed to download file {file_name} ({file_id}): {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # ZIP extraction (delegates to shared utility)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_zip(zip_bytes: bytes, dest_dir: Path) -> list[SpeakerAudio]:
        """Extract per-speaker audio files from a Craig ZIP archive.

        Delegates to the shared ``extract_speaker_zip`` utility.
        """
        try:
            return extract_speaker_zip(zip_bytes, dest_dir)
        except zipfile.BadZipFile as exc:
            raise DriveWatchError(f"Invalid ZIP file: {exc}") from exc

    # ------------------------------------------------------------------
    # Main polling loop
    # ------------------------------------------------------------------

    async def _watch_loop(self) -> None:
        """Poll the Google Drive folder indefinitely for new files.

        Runs as an asyncio task. On each tick:
          1. List files in the configured folder
          2. Skip already-processed files (via StateStore)
          3. Download, extract, and invoke the callback for new files
          4. Mark files as processed on success
        """
        logger.info(
            "DriveWatcher loop starting (folder_id=%s, interval=%ds, pattern=%s)",
            self._cfg.folder_id,
            self._cfg.poll_interval_sec,
            self._cfg.file_pattern,
        )

        # Pre-flight validation
        if not self._cfg.folder_id:
            logger.error("google_drive.folder_id is empty, watch loop will not run")
            return

        creds_path = Path(self._cfg.credentials_path)
        if not creds_path.exists():
            logger.error(
                "Credentials file not found at %s, watch loop will not run",
                creds_path.resolve(),
            )
            return

        loop = asyncio.get_running_loop()

        while True:
            try:
                # List files (synchronous, run in executor)
                files = await loop.run_in_executor(None, self._list_files_sync)

                # Filter out already-known files via StateStore
                new_files: list[dict[str, str]] = []
                for f in files:
                    rec_id = extract_rec_id(f["name"]) or f["id"]
                    if not self._state_store.is_known(rec_id):
                        new_files.append(f)

                if new_files:
                    logger.info(
                        "Found %d new file(s) in Drive folder: %s",
                        len(new_files),
                        [f["name"] for f in new_files],
                    )

                for file_info in new_files:
                    file_id = file_info["id"]
                    file_name = file_info["name"]

                    try:
                        await self._process_file(loop, file_id, file_name)
                    except DriveWatchError as exc:
                        logger.error(
                            "Failed to process Drive file %s (%s): %s",
                            file_name,
                            file_id,
                            exc,
                        )
                    except Exception as exc:
                        logger.exception(
                            "Unexpected error processing Drive file %s (%s): %s",
                            file_name,
                            file_id,
                            exc,
                        )

            except asyncio.CancelledError:
                logger.info("DriveWatcher loop cancelled")
                raise
            except DriveWatchError as exc:
                logger.error("Drive watch error during polling: %s", exc)
            except Exception as exc:
                logger.exception("Unexpected error in drive watch loop: %s", exc)

            await asyncio.sleep(self._cfg.poll_interval_sec)

    async def _process_file(
        self,
        loop: asyncio.AbstractEventLoop,
        file_id: str,
        file_name: str,
    ) -> None:
        """Download a single Drive file, extract tracks, and invoke the callback."""
        rec_id = extract_rec_id(file_name) or file_id

        if not self._state_store.mark_processing(
            rec_id, source="drive", source_id=file_id, file_name=file_name
        ):
            logger.info(
                "Skipping %s (%s) -- already known as rec_id=%s",
                file_name,
                file_id,
                rec_id,
            )
            return

        logger.info("Processing Drive file: %s (%s) rec_id=%s", file_name, file_id, rec_id)
        # Download (synchronous, run in executor)
        zip_bytes = await loop.run_in_executor(
            None, self._download_file_sync, file_id, file_name
        )

        # Extract to a temporary directory.
        # The callback runs synchronously (awaited) within this function,
        # so the temp dir is alive for the entire pipeline execution.
        # Cleanup happens in the finally block after the callback returns.
        tmp_dir_obj = tempfile.TemporaryDirectory(prefix=f"drive-{file_id[:8]}-")
        tmp_path = Path(tmp_dir_obj.name)

        try:
            tracks = self._extract_zip(zip_bytes, tmp_path)

            if not tracks:
                logger.warning(
                    "No audio tracks found in ZIP %s (%s), marking as processed",
                    file_name,
                    file_id,
                )
                self._state_store.mark_success(rec_id)
                tmp_dir_obj.cleanup()
                return

            logger.info(
                "Extracted %d tracks from %s: %s",
                len(tracks),
                file_name,
                [t.speaker.username for t in tracks],
            )

            source_label = f"drive:{file_name}"

            # Invoke the callback. The callback receives tmp_path so it
            # can keep the directory alive during pipeline processing.
            # After the callback completes, we clean up.
            await self._on_new_tracks(tracks, source_label, tmp_path)

            # Mark as processed only after successful callback
            self._state_store.mark_success(rec_id)
            logger.info("Successfully processed Drive file: %s", file_name)

        except Exception as exc:
            self._state_store.mark_failed(rec_id, str(exc))
            raise
        finally:
            # Clean up temporary directory
            try:
                tmp_dir_obj.cleanup()
            except OSError as exc:
                logger.debug("Temp dir cleanup failed (may already be removed): %s", exc)


# ---------------------------------------------------------------------------
# Shared Drive API utilities
# ---------------------------------------------------------------------------

def _build_drive_service(credentials_path: str) -> Any:
    """Build a Google Drive API v3 service client from a service-account key.

    Raises DriveWatchError if the credentials file is missing or invalid.
    """
    creds_path = Path(credentials_path)
    if not creds_path.exists():
        raise DriveWatchError(
            f"Service-account credentials not found: {creds_path.resolve()}"
        )

    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        credentials = Credentials.from_service_account_file(
            str(creds_path), scopes=_SCOPES
        )
        service = build("drive", "v3", credentials=credentials)
        logger.info("Google Drive API service built successfully")
        return service
    except Exception as exc:
        raise DriveWatchError(
            f"Failed to build Google Drive service: {exc}"
        ) from exc


def _download_drive_file(service: Any, file_id: str, file_name: str) -> bytes:
    """Download a file's content by ID. Returns raw bytes."""
    try:
        request = service.files().get_media(fileId=file_id)
        buffer = io.BytesIO()

        from googleapiclient.http import MediaIoBaseDownload

        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                logger.debug(
                    "Download %s: %.0f%%",
                    file_name,
                    status.progress() * 100,
                )

        data = buffer.getvalue()
        logger.info("Downloaded %s (%d bytes)", file_name, len(data))
        return data

    except Exception as exc:
        raise DriveWatchError(
            f"Failed to download file {file_name} ({file_id}): {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# VideoDriveWatcher — video/audio file watcher for diarization pipeline
# ---------------------------------------------------------------------------


class VideoDriveWatcher:
    """Monitors a Google Drive folder for new video/audio files.

    Unlike DriveWatcher which handles Craig ZIPs, this watcher downloads
    raw video/audio files and passes them to a diarization callback.

    Usage::

        watcher = VideoDriveWatcher(drive_cfg, diar_cfg, state_store, on_new_video=cb)
        watcher.start()
        ...
        watcher.stop()
    """

    def __init__(
        self,
        drive_cfg: GoogleDriveConfig,
        diar_cfg: DiarizationConfig,
        state_store: StateStore,
        on_new_video: OnNewVideoCallback,
    ) -> None:
        self._drive_cfg = drive_cfg
        self._diar_cfg = diar_cfg
        self._state_store = state_store
        self._on_new_video = on_new_video
        self._task: asyncio.Task[None] | None = None
        self._service: Any = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            logger.warning("VideoDriveWatcher.start() called but task already running")
            return

        self._task = asyncio.create_task(
            self._watch_loop(), name="video-drive-watcher"
        )
        logger.info("VideoDriveWatcher polling task started")

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            logger.info("VideoDriveWatcher polling task cancelled")
        self._task = None

    def _get_service(self) -> Any:
        if self._service is None:
            self._service = _build_drive_service(self._drive_cfg.credentials_path)
        return self._service

    def _list_files_sync(self) -> list[dict[str, str]]:
        """List video/audio files in the configured folder."""
        service = self._get_service()

        if not self._drive_cfg.folder_id:
            raise DriveWatchError("google_drive.folder_id is not configured")

        # Build mimeType OR query from diarization config
        mime_types = self._diar_cfg.drive_mime_types
        mime_clauses = " or ".join(f"mimeType = '{mt}'" for mt in mime_types)

        query_parts: list[str] = [
            f"'{self._drive_cfg.folder_id}' in parents",
            "trashed = false",
            f"({mime_clauses})",
        ]
        query = " and ".join(query_parts)
        logger.debug("VideoDriveWatcher query: %s", query)

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

                files = response.get("files", [])
                for f in files:
                    if fnmatch.fnmatch(f["name"], self._diar_cfg.drive_file_pattern):
                        results.append(f)

                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            logger.debug("VideoDriveWatcher listing returned %d matching files", len(results))
            return results

        except Exception as exc:
            raise DriveWatchError(
                f"Failed to list video files in folder {self._drive_cfg.folder_id}: {exc}"
            ) from exc

    async def _watch_loop(self) -> None:
        """Poll for new video/audio files indefinitely."""
        logger.info(
            "VideoDriveWatcher loop starting (folder_id=%s, interval=%ds, pattern=%s)",
            self._drive_cfg.folder_id,
            self._drive_cfg.poll_interval_sec,
            self._diar_cfg.drive_file_pattern,
        )

        if not self._drive_cfg.folder_id:
            logger.error("google_drive.folder_id is empty, video watch loop will not run")
            return

        creds_path = Path(self._drive_cfg.credentials_path)
        if not creds_path.exists():
            logger.error(
                "Credentials file not found at %s, video watch loop will not run",
                creds_path.resolve(),
            )
            return

        loop = asyncio.get_running_loop()

        while True:
            try:
                files = await loop.run_in_executor(None, self._list_files_sync)

                new_files: list[dict[str, str]] = []
                for f in files:
                    file_key = f"video:{f['id']}"
                    if not self._state_store.is_known(file_key):
                        new_files.append(f)

                if new_files:
                    logger.info(
                        "Found %d new video/audio file(s): %s",
                        len(new_files),
                        [f["name"] for f in new_files],
                    )

                for file_info in new_files:
                    try:
                        await self._process_file(loop, file_info["id"], file_info["name"])
                    except DriveWatchError as exc:
                        logger.error(
                            "Failed to process video file %s (%s): %s",
                            file_info["name"], file_info["id"], exc,
                        )
                    except Exception as exc:
                        logger.exception(
                            "Unexpected error processing video file %s (%s): %s",
                            file_info["name"], file_info["id"], exc,
                        )

            except asyncio.CancelledError:
                logger.info("VideoDriveWatcher loop cancelled")
                raise
            except DriveWatchError as exc:
                logger.error("Video watch error during polling: %s", exc)
            except Exception as exc:
                logger.exception("Unexpected error in video watch loop: %s", exc)

            await asyncio.sleep(self._drive_cfg.poll_interval_sec)

    async def _process_file(
        self,
        loop: asyncio.AbstractEventLoop,
        file_id: str,
        file_name: str,
    ) -> None:
        """Download a video/audio file and invoke the diarization callback."""
        file_key = f"video:{file_id}"

        if not self._state_store.mark_processing(
            file_key, source="drive", source_id=file_id, file_name=file_name
        ):
            logger.info(
                "Skipping %s (%s) -- already known",
                file_name, file_id,
            )
            return

        logger.info("Processing video file: %s (%s)", file_name, file_id)

        service = self._get_service()
        file_bytes = await loop.run_in_executor(
            None, _download_drive_file, service, file_id, file_name
        )

        tmp_dir_obj = tempfile.TemporaryDirectory(prefix=f"video-{file_id[:8]}-")
        tmp_path = Path(tmp_dir_obj.name)

        try:
            # Write raw file to temp dir (no ZIP extraction)
            dest_file = tmp_path / file_name
            dest_file.write_bytes(file_bytes)

            source_label = f"diarization:{file_name}"
            await self._on_new_video(dest_file, source_label)

            self._state_store.mark_success(file_key)
            logger.info("Successfully processed video file: %s", file_name)

        except Exception as exc:
            self._state_store.mark_failed(file_key, str(exc))
            raise
        finally:
            try:
                tmp_dir_obj.cleanup()
            except OSError as exc:
                logger.debug("Temp dir cleanup failed: %s", exc)
