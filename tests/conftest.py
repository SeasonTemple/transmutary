"""Shared test fixtures. All external IO is mocked — no real network calls."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make src/ importable without an editable install.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture(autouse=True)
def _reset_l3_budget():
    """Reset the process-wide L3 BudgetManager before/after each test.

    The daily cap in llm.py tracks spend in a module-global BudgetManager; without
    a reset, accrued cost would leak across tests. This keeps each test's budget
    state deterministic and isolated (no real network/disk).
    """
    from transmutary import llm

    llm.reset_budget_manager()
    yield
    llm.reset_budget_manager()


@pytest.fixture(autouse=True)
def _no_real_embeddings(monkeypatch):
    """Stub llm.embed so no test ever reaches a real embedding endpoint.

    The pipeline ticks bind the real ``llm.embed`` by default (embed_fn left
    unset). With fake creds + a fake base_url, a real ``litellm.embedding`` call
    blocks on connection timeout (~228s per test) — this is what made the suite
    take ~50 minutes. The L2 layer degrades to one-group-per-item when embed
    raises (zero-miss, KTD-B), so by default we raise to exercise that safe path
    with no network. Tests that specifically exercise L2 grouping override this
    with their own monkeypatch (a real-shaped deterministic stub), which wins
    because it is applied inside the test body, after this autouse fixture.
    """
    from transmutary import llm

    def _stub_embed(texts, **kwargs):
        raise llm.LLMError("embeddings stubbed in tests (no network)")

    monkeypatch.setattr(llm, "embed", _stub_embed)
    yield


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
