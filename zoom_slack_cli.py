"""Quick CLI: Video file → Whisper transcribe → DiariZen diarize → minutes → Slack.

Temporary tool for testing the Slack pipeline with video files (no VTT).

Usage:
    python3 zoom_slack_cli.py "path/to/video.mp4"
    python3 zoom_slack_cli.py "path/to/video.mp4" --config config_slack.yaml --no-slack
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import logging
import sys
import tempfile
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def run(video_path: str, config_path: str, *, post_to_slack: bool = True) -> None:
    from src.audio_extractor import extract_audio
    from src.config import DiarizationConfig, MergerConfig, WhisperConfig
    from src.diarizer import DiariZenDiarizer
    from src.merger import merge_transcripts
    from src.segment_aligner import align_segments
    from src.slack_config import load_slack_config
    from src.transcriber import Transcriber

    video = Path(video_path)
    if not video.exists():
        logger.error("File not found: %s", video)
        sys.exit(1)

    logger.info("Input: %s (%.0f MB)", video.name, video.stat().st_size / 1_048_576)

    # Load config (for generator, merger, diarization, slack)
    # When --no-slack, inject dummy Slack values to pass validation
    import os
    if not post_to_slack:
        os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-dummy")
        os.environ.setdefault("SLACK_CHANNEL_ID", "C0000000000")
    cfg = load_slack_config(config_path)

    with tempfile.TemporaryDirectory(prefix="zoom_cli_") as tmp:
        tmp_path = Path(tmp)
        wav_path = tmp_path / "audio.wav"

        # Stage 1: Extract audio
        logger.info("Stage 1: Extracting audio → WAV 16kHz mono")
        await extract_audio(video, wav_path, timeout_sec=cfg.diarization.ffmpeg_timeout_sec)
        logger.info("  Audio: %.0f MB", wav_path.stat().st_size / 1_048_576)

        # Stage 2: Whisper transcription
        logger.info("Stage 2: Whisper transcription (large-v3, CUDA)")
        whisper_cfg = WhisperConfig()
        transcriber = Transcriber(whisper_cfg)
        transcriber.load_model()

        segments = transcriber.transcribe_file(wav_path, speaker_name="Speaker")
        logger.info("  Segments: %d", len(segments))

        # Free Whisper VRAM before diarization
        del transcriber
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except ImportError:
            pass

        # Stage 3: DiariZen diarization
        logger.info("Stage 3: DiariZen speaker diarization")
        diarizer = DiariZenDiarizer(cfg.diarization)
        diarizer.load_model()
        try:
            diar_segments = diarizer.diarize(wav_path)
            logger.info("  Diar segments: %d, speakers: %s",
                        len(diar_segments),
                        sorted({s.speaker for s in diar_segments}))
        except Exception as e:
            logger.warning("  Diarization failed (%s), falling back to single speaker", e)
            diar_segments = []
        finally:
            diarizer.unload_model()

        # Stage 4: Align segments
        logger.info("Stage 4: Aligning transcript with speaker labels")
        aligned = align_segments(segments, diar_segments, fallback_speaker="Speaker")
        speakers = sorted({str(s.speaker) for s in aligned})
        logger.info("  Speakers: %s", speakers)

        # Stage 5: Merge
        logger.info("Stage 5: Merging transcript")
        transcript = merge_transcripts(aligned, cfg.merger)
        logger.info("  Transcript: %d chars", len(transcript))

        # Save transcript locally
        transcript_file = tmp_path / "transcript.md"
        transcript_file.write_text(transcript, encoding="utf-8")

        # Stage 6: Generate minutes
        logger.info("Stage 6: Generating minutes via Claude API")
        from src.generator import MinutesGenerator

        generator = MinutesGenerator(cfg.generator)
        generator.load()

        today = datetime.now().strftime("%Y-%m-%d")
        minutes_md = await generator.generate(
            transcript=transcript,
            date=today,
            speakers=", ".join(speakers),
        )
        logger.info("  Minutes: %d chars", len(minutes_md))

        # Save minutes locally
        minutes_file = tmp_path / "minutes.md"
        minutes_file.write_text(minutes_md, encoding="utf-8")

        # Copy to working dir for reference
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        stem = video.stem[:50]
        (output_dir / f"{stem}_transcript.md").write_text(transcript, encoding="utf-8")
        (output_dir / f"{stem}_minutes.md").write_text(minutes_md, encoding="utf-8")
        logger.info("  Saved to output/%s_*.md", stem)

        # Stage 7: Post to Slack
        if post_to_slack:
            logger.info("Stage 7: Posting to Slack")
            from src.slack_poster import SlackPoster

            poster = SlackPoster(cfg.slack)
            ts = await poster.post_minutes_to_slack(
                minutes_md=minutes_md,
                title=f"Meeting Minutes — {today}",
                metadata={"source": video.name, "speakers": ", ".join(speakers)},
            )

            if cfg.slack.include_transcript:
                await poster.post_transcript_file(ts, transcript)

            logger.info("  Posted to Slack (ts=%s)", ts)
        else:
            logger.info("Stage 7: Skipped (--no-slack)")

    logger.info("Done!")


def main() -> None:
    parser = argparse.ArgumentParser(description="Video → Minutes → Slack (temporary CLI)")
    parser.add_argument("video", help="Path to video file (mp4, etc.)")
    parser.add_argument("--config", default="config_slack.yaml", help="Config file path")
    parser.add_argument("--no-slack", action="store_true", help="Skip Slack posting (local output only)")
    args = parser.parse_args()

    asyncio.run(run(args.video, args.config, post_to_slack=not args.no_slack))


if __name__ == "__main__":
    main()
