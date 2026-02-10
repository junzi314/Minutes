"""Craig recording download client using the v1 Job API.

Verified API flow (from browser DevTools observation):
  1. POST /api/v1/recordings/{rec_id}/job?key={key}  ->  start the cook job (format + container)
  2. GET  /api/v1/recordings/{rec_id}/job?key={key}   ->  poll until job.status == "complete"
  3. GET  /dl/{outputFileName}                         ->  download the cooked ZIP file
"""

from __future__ import annotations

import asyncio
import logging
import zipfile
from pathlib import Path

import aiohttp

from src.audio_source import AudioSource, SpeakerAudio, extract_speaker_zip
from src.config import CraigConfig
from src.detector import DetectedRecording
from src.errors import AudioAcquisitionError, CookTimeoutError

logger = logging.getLogger(__name__)

# Polling interval for job readiness checks (seconds)
_JOB_POLL_INTERVAL = 2


class CraigClient(AudioSource):
    """Download per-speaker audio files from Craig via the Job API.

    The Job API flow:
      1. POST /api/v1/recordings/{rec_id}/job?key={key}  ->  start the cook job
      2. GET  /api/v1/recordings/{rec_id}/job?key={key}   ->  poll until job.status == "complete"
      3. GET  /dl/{job.outputFileName}                     ->  download the cooked ZIP
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        recording: DetectedRecording,
        cfg: CraigConfig,
    ) -> None:
        self._session = session
        self._recording = recording
        self._cfg = cfg
        self._base_url = f"https://{recording.craig_domain}"

    # ------------------------------------------------------------------
    # AudioSource interface
    # ------------------------------------------------------------------

    async def get_speakers(self) -> list[SpeakerInfo]:
        """Extract speaker info from ZIP filenames after download.

        This is a no-op before download — call download() first, which
        returns SpeakerAudio objects containing SpeakerInfo.
        """
        raise NotImplementedError(
            "get_speakers() is not available as a standalone call. "
            "Speaker info is extracted from ZIP filenames during download()."
        )

    async def download(self, dest_dir: Path) -> list[SpeakerAudio]:
        """Start the cook job, poll until complete, and download per-speaker audio files.

        Flow:
          1. POST to start the cook job (format + container)
          2. Poll GET /api/v1/recordings/{rec_id}/job until complete
          3. Download ZIP from /dl/{outputFileName}
          4. Extract per-speaker audio files
        """
        dest_dir.mkdir(parents=True, exist_ok=True)

        job_url = (
            f"{self._base_url}/api/v1/recordings/{self._recording.rec_id}"
            f"/job?key={self._recording.access_key}"
        )

        # Step 1: Start the cook job
        await self._start_job(job_url)

        # Step 2: Poll until job completes -> returns outputFileName
        output_filename = await self._poll_until_complete(job_url)

        # Step 3: Download the cooked ZIP file
        dl_url = f"{self._base_url}/dl/{output_filename}"
        logger.info("Downloading cooked file from %s", dl_url)
        zip_bytes = await self._download_bytes(dl_url)

        # Step 4: Extract ZIP
        results = self._extract_zip(zip_bytes, dest_dir)

        if not results:
            raise AudioAcquisitionError(
                f"No audio files found in ZIP for recording {self._recording.rec_id}"
            )

        logger.info(
            "Downloaded %d audio files for recording %s",
            len(results),
            self._recording.rec_id,
        )
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _start_job(self, job_url: str) -> None:
        """POST to start the cook job with configured format and container.

        The Craig web UI sends this POST when the user clicks an audio format
        button.  Without it, GET polling never sees a running job.
        """
        payload = {
            "type": "recording",
            "options": {
                "format": self._cfg.cook_format,
                "container": self._cfg.cook_container,
                "dynaudnorm": False,
            },
        }
        logger.info(
            "Starting cook job for recording %s (format=%s, container=%s)",
            self._recording.rec_id,
            self._cfg.cook_format,
            self._cfg.cook_container,
        )
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with self._session.post(
                job_url, json=payload, timeout=timeout,
            ) as resp:
                if resp.status in (200, 201):
                    logger.info("Cook job started (HTTP %d)", resp.status)
                else:
                    resp_text = await resp.text()
                    logger.warning(
                        "Cook job start returned HTTP %d: %s",
                        resp.status,
                        resp_text[:200],
                    )
        except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
            # Non-fatal: the job may already be running from a previous attempt.
            logger.warning("Cook job start request failed (non-fatal): %s", exc)

    async def _poll_until_complete(self, job_url: str) -> str:
        """Poll GET /api/v1/recordings/{rec_id}/job until job.status == "complete".

        Returns the outputFileName from the job response.

        Response structure:
        {
          "job": {
            "id": "...",
            "status": "complete",
            "outputFileName": "xxx.aac.zip",
            ...
          }
        }
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._cfg.poll_timeout_sec

        logger.info(
            "Polling job status for recording %s (timeout=%ds)",
            self._recording.rec_id,
            self._cfg.poll_timeout_sec,
        )

        while loop.time() < deadline:
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with self._session.get(job_url, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        job = data.get("job") or {}

                        status = job.get("status", "")
                        logger.debug(
                            "Job poll: status=%s, state=%s",
                            status,
                            job.get("state", {}).get("type", "unknown"),
                        )

                        if status == "complete":
                            filename = job.get("outputFileName")
                            if filename:
                                logger.info("Job complete, output: %s", filename)
                                return filename
                            raise AudioAcquisitionError(
                                f"Job complete but no outputFileName in response: {data}"
                            )

                        if status in ("error", "failed"):
                            raise AudioAcquisitionError(
                                f"Cook job failed with status '{status}': {data}"
                            )

                    else:
                        resp_text = await resp.text()
                        logger.warning(
                            "Job poll returned HTTP %d: %s",
                            resp.status, resp_text[:200],
                        )

            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                logger.warning("Job poll error: %s", exc)

            await asyncio.sleep(_JOB_POLL_INTERVAL)

        raise CookTimeoutError(
            f"Job polling timed out after {self._cfg.poll_timeout_sec}s "
            f"for recording {self._recording.rec_id}"
        )

    async def _download_bytes(self, url: str) -> bytes:
        """Download raw bytes from a URL with retry logic."""
        last_exc: Exception | None = None

        for attempt in range(1, self._cfg.max_retries + 2):  # +2: 1 initial + max_retries
            try:
                timeout = aiohttp.ClientTimeout(total=self._cfg.download_timeout_sec)
                async with self._session.get(url, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        logger.debug("Downloaded %d bytes from %s", len(data), url)
                        return data

                    resp_text = await resp.text()
                    last_exc = AudioAcquisitionError(
                        f"Download failed HTTP {resp.status} from {url}: {resp_text[:500]}"
                    )
                    logger.warning(
                        "Download attempt %d/%d: HTTP %d from %s",
                        attempt, self._cfg.max_retries + 1, resp.status, url,
                    )

            except asyncio.TimeoutError:
                last_exc = CookTimeoutError(f"Download timed out from {url}")
                logger.warning(
                    "Download attempt %d/%d timed out for %s",
                    attempt, self._cfg.max_retries + 1, url,
                )
            except aiohttp.ClientError as exc:
                last_exc = AudioAcquisitionError(f"Download client error from {url}: {exc}")
                logger.warning(
                    "Download attempt %d/%d client error for %s: %s",
                    attempt, self._cfg.max_retries + 1, url, exc,
                )

            if attempt <= self._cfg.max_retries:
                delay = 2 ** (attempt - 1)
                await asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]

    @staticmethod
    def _extract_zip(zip_bytes: bytes, dest_dir: Path) -> list[SpeakerAudio]:
        """Extract per-speaker audio files from a ZIP archive.

        Delegates to the shared ``extract_speaker_zip`` utility.
        """
        try:
            return extract_speaker_zip(zip_bytes, dest_dir)
        except zipfile.BadZipFile as exc:
            raise AudioAcquisitionError(f"Invalid ZIP response from Job API: {exc}") from exc
