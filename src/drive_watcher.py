"""Google Drive folder watcher for Craig recording ZIP files.

Polls a Google Drive folder for new ZIP files matching a filename pattern,
downloads them, extracts per-speaker audio tracks, and invokes a callback
to trigger the transcription/minutes pipeline.

Authentication uses a service-account JSON key with drive.readonly scope.
"""

from __future__ import annotations

import asyncio
import fnmatch
import io
import json
import logging
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.audio_source import SpeakerAudio, extract_speaker_zip
from src.config import GoogleDriveConfig
from src.errors import DriveWatchError

logger = logging.getLogger(__name__)

# Google Drive API scopes required for read-only access.
_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Type alias for the callback invoked when new tracks are found.
OnNewTracksCallback = Callable[
    [list[SpeakerAudio], str, Path],
    Awaitable[None],
]


class DriveWatcher:
    """Monitors a Google Drive folder for new Craig recording ZIPs.

    Usage::

        watcher = DriveWatcher(cfg, on_new_tracks=my_callback)
        watcher.start()   # launches polling task
        ...
        watcher.stop()    # cancels polling task
    """

    def __init__(
        self,
        cfg: GoogleDriveConfig,
        on_new_tracks: OnNewTracksCallback,
    ) -> None:
        self._cfg = cfg
        self._on_new_tracks = on_new_tracks
        self._task: asyncio.Task[None] | None = None
        self._processed: dict[str, dict[str, str]] = {}
        self._service: Any = None  # googleapiclient.discovery.Resource

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the polling task is currently running."""
        return self._task is not None and not self._task.done()

    @property
    def processed_count(self) -> int:
        """Number of files that have been processed."""
        return len(self._processed)

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
    # Processed-file database
    # ------------------------------------------------------------------

    def _load_processed_db(self) -> None:
        """Load the processed-files JSON database from disk."""
        db_path = Path(self._cfg.processed_db_path)
        if db_path.exists():
            try:
                data = json.loads(db_path.read_text(encoding="utf-8"))
                self._processed = data.get("processed", {})
                logger.info(
                    "Loaded processed DB: %d entries from %s",
                    len(self._processed),
                    db_path,
                )
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "Failed to load processed DB from %s, starting fresh: %s",
                    db_path,
                    exc,
                )
                self._processed = {}
        else:
            self._processed = {}
            logger.debug("No processed DB found at %s, starting fresh", db_path)

    def _save_processed_db(self) -> None:
        """Persist the processed-files database to disk."""
        db_path = Path(self._cfg.processed_db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"processed": self._processed}
        db_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.debug("Saved processed DB (%d entries) to %s", len(self._processed), db_path)

    def _mark_processed(self, file_id: str, file_name: str) -> None:
        """Record a file as successfully processed and save to disk."""
        self._processed[file_id] = {
            "name": file_name,
            "status": "success",
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_processed_db()

    def _mark_failed(self, file_id: str, file_name: str, error: str) -> None:
        """Record a file as failed to prevent reprocessing loops."""
        self._processed[file_id] = {
            "name": file_name,
            "status": "error",
            "error": error,
            "failed_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_processed_db()
        logger.info(
            "Marked file %s (%s) as failed to prevent reprocessing",
            file_name,
            file_id,
        )

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
        #   name contains 'craig_' and name contains '.aac.zip'
        # Combined with parent folder and mimeType constraints.
        query_parts: list[str] = [
            f"'{self._cfg.folder_id}' in parents",
            "trashed = false",
            "mimeType = 'application/zip'",
        ]

        # Convert glob pattern to Drive API name-contains clauses.
        # Split on '*' and '?' wildcards, keep non-empty literal segments.
        pattern = self._cfg.file_pattern
        literal_segments = re.split(r"[*?]+", pattern)
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
          2. Skip already-processed files
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

        # Load previously processed files
        self._load_processed_db()

        loop = asyncio.get_running_loop()

        while True:
            try:
                # List files (synchronous, run in executor)
                files = await loop.run_in_executor(None, self._list_files_sync)

                # Filter out already-processed files
                new_files = [
                    f for f in files if f["id"] not in self._processed
                ]

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
                        self._mark_failed(file_id, file_name, str(exc))
                    except Exception as exc:
                        logger.exception(
                            "Unexpected error processing Drive file %s (%s): %s",
                            file_name,
                            file_id,
                            exc,
                        )
                        self._mark_failed(file_id, file_name, str(exc))

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
        logger.info("Processing Drive file: %s (%s)", file_name, file_id)

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
                self._mark_processed(file_id, file_name)
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
            self._mark_processed(file_id, file_name)
            logger.info("Successfully processed Drive file: %s", file_name)

        except Exception:
            # Re-raise so the caller (_watch_loop) can log and continue
            raise
        finally:
            # Clean up temporary directory
            try:
                tmp_dir_obj.cleanup()
            except OSError as exc:
                logger.debug("Temp dir cleanup failed (may already be removed): %s", exc)
