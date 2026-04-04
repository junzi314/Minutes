"""Entry point for the Zoom Diarization Slack service.

Independent from the Discord Minutes Bot — runs as a separate process.

Usage:
    python3 zoom_slack_bot.py
    python3 zoom_slack_bot.py --config config_slack.yaml --log-level DEBUG
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import signal
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Zoom Diarization Slack Service — auto-generate meeting minutes from Zoom recordings"
    )
    parser.add_argument(
        "--config",
        default="config_slack.yaml",
        help="Path to YAML config file (default: config_slack.yaml)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Override log level",
    )
    return parser.parse_args()


def setup_logging(level: str = "INFO", log_file: str = "logs/zoom_slack.log") -> None:
    """Configure rotating file + console logging."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(log_level)

    # Rotating file handler
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(log_path),
        maxBytes=10_485_760,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
    )

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Silence noisy libraries
    logging.getLogger("slack_sdk").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)


async def run(config_path: str, log_level: str | None) -> None:
    """Main async entry point."""
    from src.generator import MinutesGenerator
    from src.slack_config import load_slack_config
    from src.slack_pipeline import run_slack_pipeline
    from src.slack_poster import SlackPoster
    from src.state_store import StateStore
    from src.zoom_drive_watcher import ZoomDriveWatcher

    # Load config
    cfg = load_slack_config(config_path=config_path)

    # Setup logging
    setup_logging(level=log_level or "INFO")

    logger.info("Zoom Diarization Slack Service starting")
    logger.info(
        "Config: drive_folder=%s, slack_channel=%s, diarization=%s",
        cfg.google_drive.folder_id or "(not set)",
        cfg.slack.channel_id,
        cfg.diarization.model if cfg.diarization.enabled else "disabled",
    )

    # Initialize components
    state_store = StateStore(
        Path(cfg.pipeline.state_dir),
        legacy_db_path=Path(cfg.pipeline.state_dir) / "nonexistent.json",
    )
    stale = state_store.cleanup_stale()
    if stale:
        logger.info("Cleaned up %d stale processing entries", stale)

    generator = MinutesGenerator(cfg.generator)
    generator.load()

    slack_poster = SlackPoster(cfg.slack)

    # Pair-ready callback
    async def on_pair_ready(audio_path: Path, vtt_path: Path, source_label: str) -> None:
        await run_slack_pipeline(
            audio_path=audio_path,
            vtt_path=vtt_path,
            cfg=cfg,
            generator=generator,
            slack_poster=slack_poster,
            state_store=state_store,
            source_label=source_label,
        )

    # Start watcher
    watcher = ZoomDriveWatcher(
        drive_cfg=cfg.google_drive,
        zoom_cfg=cfg.zoom,
        state_store=state_store,
        on_pair_ready=on_pair_ready,
    )

    # Graceful shutdown
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        watcher.stop()
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    watcher.start()
    logger.info("Zoom Diarization Slack Service running — press Ctrl+C to stop")

    await stop_event.wait()
    logger.info("Zoom Diarization Slack Service stopped")


def main() -> None:
    args = parse_args()

    # Early logging before config load
    setup_logging(level=args.log_level or "INFO")

    try:
        asyncio.run(run(args.config, args.log_level))
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    main()
