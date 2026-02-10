"""Craig recording-ended detection from raw gateway payloads.

All functions operate on plain dicts (payload.data) rather than discord.py
types so they can be tested with JSON fixtures without importing discord.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

CRAIG_BOT_ID = "272937604339466240"

RECORDING_URL_PATTERN = re.compile(
    r"https?://(?P<domain>craig\.\w+)/rec/(?P<rec_id>[a-zA-Z0-9]+)\?key=(?P<key>[a-zA-Z0-9]+)"
)


@dataclass(frozen=True)
class DetectedRecording:
    rec_id: str
    access_key: str
    rec_url: str
    guild_id: int
    channel_id: int
    message_id: int
    craig_domain: str = "craig.chat"


def is_craig_message(payload_data: dict) -> bool:
    """Return True if the message was authored by Craig Bot."""
    author = payload_data.get("author")
    if not isinstance(author, dict):
        return False
    return author.get("id") == CRAIG_BOT_ID


def is_recording_ended(payload_data: dict) -> bool:
    """Return True if the components payload contains 'Recording ended'.

    Craig updates its recording panel message (Components V2) to include
    ``Recording ended.`` when the recording stops.  We serialize the
    components list to a JSON string and do a simple substring search
    to sidestep discord.py Components V2 parsing issues.
    """
    components = payload_data.get("components")
    if not components:
        return False
    try:
        serialized = json.dumps(components)
    except (TypeError, ValueError):
        return False
    return "Recording ended" in serialized


def extract_recording_info(
    payload_data: dict,
    channel_id: int,
    guild_id: int,
    message_id: int,
) -> DetectedRecording | None:
    """Extract recording URL from the full payload and return DetectedRecording.

    Searches the entire JSON-serialized payload for the Craig recording URL
    pattern, which may appear in components, embeds, or content fields.
    """
    try:
        serialized = json.dumps(payload_data)
    except (TypeError, ValueError):
        logger.warning("Failed to serialize payload_data to JSON")
        return None

    match = RECORDING_URL_PATTERN.search(serialized)
    if not match:
        logger.debug("No recording URL found in payload")
        return None

    domain = match.group("domain")
    rec_id = match.group("rec_id")
    key = match.group("key")
    rec_url = f"https://{domain}/rec/{rec_id}?key={key}"

    return DetectedRecording(
        rec_id=rec_id,
        access_key=key,
        rec_url=rec_url,
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
        craig_domain=domain,
    )


def parse_recording_ended(
    payload_data: dict,
    channel_id: int,
    guild_id: int,
    message_id: int,
    watch_channel_id: int,
) -> DetectedRecording | None:
    """Top-level detection: check all conditions and return DetectedRecording or None.

    Checks in order:
      1. Channel matches the configured watch channel
      2. Message is from Craig Bot
      3. Components indicate recording has ended
      4. A valid recording URL can be extracted
    """
    if channel_id != watch_channel_id:
        return None

    if not is_craig_message(payload_data):
        return None

    if not is_recording_ended(payload_data):
        return None

    recording = extract_recording_info(payload_data, channel_id, guild_id, message_id)
    if recording is None:
        logger.warning(
            "Craig recording-ended detected but no URL found "
            "(channel=%d, message=%d)",
            channel_id,
            message_id,
        )
    return recording
