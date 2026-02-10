"""Shared pytest fixtures and configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def craig_panel_ended() -> dict:
    """Raw gateway payload for a Craig panel in 'Recording ended' state."""
    return json.loads((FIXTURES_DIR / "craig_panel_ended.json").read_text())


@pytest.fixture
def craig_panel_recording() -> dict:
    """Raw gateway payload for a Craig panel in active recording state."""
    return json.loads((FIXTURES_DIR / "craig_panel_recording.json").read_text())


@pytest.fixture
def craig_users_response() -> dict:
    """Sample response from Craig Users API."""
    return json.loads((FIXTURES_DIR / "craig_users_response.json").read_text())
