"""Shared test fixtures. All external IO is mocked — no real network calls."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make src/ importable without an editable install.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture
def fake_env() -> dict:
    """A complete set of required credential env vars (fake values)."""
    return {
        "TRANSMUTARY_GITHUB_TOKEN": "ghp_faketokenvalue000000000000000000",
        "TRANSMUTARY_SMTP_USER": "mailer@example.com",
        "TRANSMUTARY_SMTP_PASSWORD": "smtp-secret-pw",
        "TRANSMUTARY_RSS_TOKEN": "rss-secret-token-xyz",
        "TRANSMUTARY_LLM_API_KEY": "sk-fakellmkey0000000000000000000000",
        "TRANSMUTARY_LLM_BASE_URL": "https://gateway.example.com/v1",
    }


@pytest.fixture
def config_dir() -> str:
    """Path to the repo's config/ dir (example YAMLs)."""
    return str(Path(__file__).resolve().parent.parent / "config")
