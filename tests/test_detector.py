"""Unit tests for src/detector.py."""

from __future__ import annotations

from src.detector import (
    CRAIG_BOT_ID,
    RECORDING_URL_PATTERN,
    DetectedRecording,
    extract_recording_info,
    is_craig_message,
    is_recording_ended,
    parse_recording_ended,
)

WATCH_CHANNEL = 1111111111111111111


# --- is_craig_message ---


def test_is_craig_message_true(craig_panel_ended: dict) -> None:
    assert is_craig_message(craig_panel_ended) is True


def test_is_craig_message_false_other_bot() -> None:
    payload = {"author": {"id": "99999999999999"}}
    assert is_craig_message(payload) is False


def test_is_craig_message_no_author() -> None:
    assert is_craig_message({}) is False


def test_is_craig_message_author_not_dict() -> None:
    assert is_craig_message({"author": "not-a-dict"}) is False


# --- is_recording_ended ---


def test_is_recording_ended_true(craig_panel_ended: dict) -> None:
    assert is_recording_ended(craig_panel_ended) is True


def test_is_recording_ended_false_active(craig_panel_recording: dict) -> None:
    assert is_recording_ended(craig_panel_recording) is False


def test_is_recording_ended_no_components() -> None:
    assert is_recording_ended({"components": []}) is False


def test_is_recording_ended_missing_key() -> None:
    assert is_recording_ended({}) is False


# --- extract_recording_info ---


def test_extract_recording_info_success(craig_panel_ended: dict) -> None:
    result = extract_recording_info(
        craig_panel_ended,
        channel_id=WATCH_CHANNEL,
        guild_id=9999999999999999999,
        message_id=1234567890123456789,
    )
    assert result is not None
    assert result.rec_id == "abc123def"
    assert result.access_key == "ABCDEF123456"
    assert result.craig_domain == "craig.chat"
    assert result.channel_id == WATCH_CHANNEL
    assert result.guild_id == 9999999999999999999
    assert result.message_id == 1234567890123456789
    assert "craig.chat/rec/abc123def" in result.rec_url


def test_extract_recording_info_no_url() -> None:
    payload = {"components": [{"type": 1, "content": "No URL here"}]}
    result = extract_recording_info(payload, 1, 1, 1)
    assert result is None


# --- RECORDING_URL_PATTERN ---


def test_url_pattern_craig_chat() -> None:
    match = RECORDING_URL_PATTERN.search(
        "https://craig.chat/rec/abc123?key=XYZ789"
    )
    assert match is not None
    assert match.group("domain") == "craig.chat"
    assert match.group("rec_id") == "abc123"
    assert match.group("key") == "XYZ789"


def test_url_pattern_craig_horse() -> None:
    match = RECORDING_URL_PATTERN.search(
        "https://craig.horse/rec/def456?key=ABC123"
    )
    assert match is not None
    assert match.group("domain") == "craig.horse"


def test_url_pattern_no_match() -> None:
    assert RECORDING_URL_PATTERN.search("https://example.com/rec/x?key=y") is None


# --- parse_recording_ended (integration of all checks) ---


def test_parse_recording_ended_success(craig_panel_ended: dict) -> None:
    result = parse_recording_ended(
        craig_panel_ended,
        channel_id=WATCH_CHANNEL,
        guild_id=9999999999999999999,
        message_id=1234567890123456789,
        watch_channel_id=WATCH_CHANNEL,
    )
    assert result is not None
    assert isinstance(result, DetectedRecording)
    assert result.rec_id == "abc123def"


def test_parse_recording_ended_wrong_channel(craig_panel_ended: dict) -> None:
    result = parse_recording_ended(
        craig_panel_ended,
        channel_id=WATCH_CHANNEL,
        guild_id=9999999999999999999,
        message_id=1234567890123456789,
        watch_channel_id=8888888888888888888,  # different channel
    )
    assert result is None


def test_parse_recording_ended_not_craig(craig_panel_ended: dict) -> None:
    craig_panel_ended["author"]["id"] = "999"
    result = parse_recording_ended(
        craig_panel_ended,
        channel_id=WATCH_CHANNEL,
        guild_id=9999999999999999999,
        message_id=1234567890123456789,
        watch_channel_id=WATCH_CHANNEL,
    )
    assert result is None


def test_parse_recording_ended_still_recording(
    craig_panel_recording: dict,
) -> None:
    result = parse_recording_ended(
        craig_panel_recording,
        channel_id=WATCH_CHANNEL,
        guild_id=9999999999999999999,
        message_id=1234567890123456789,
        watch_channel_id=WATCH_CHANNEL,
    )
    assert result is None
