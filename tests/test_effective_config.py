"""Effective config tests — YAML base + SQLite admin overrides."""

from __future__ import annotations

import pytest

from transmutary.config import (
    ConfigError,
    Delivery,
    DependencyEdge,
    RepoEntry,
    Settings,
    TrendScope,
    Watchlist,
)
from transmutary.effective_config import (
    effective_delivery,
    effective_dependency_edges,
    effective_repo_sources,
    effective_repos,
    effective_trend_scope,
)
from transmutary.store.state import StateStore


def _settings() -> Settings:
    return Settings(
        watchlist=Watchlist(
            repos=[RepoEntry(repo="acme/cli"), RepoEntry(repo="acme/gateway")],
            dependency_edges=[
                DependencyEdge(from_repo="acme/cli", to_repo="acme/gateway")
            ],
        ),
        trend_scope=TrendScope(topics=["ai"], keywords=["llm"]),
        delivery=Delivery(
            state_db_path=":memory:",
            artifact_root="/tmp/artifacts",
            token_max_age_days=90,
            digest_hour=9,
            email_recipients=["base@example.com"],
            smtp_host="smtp.example.com",
            feed_dir="/tmp/feed",
        ),
    )


@pytest.fixture
def store():
    s = StateStore(":memory:")
    yield s
    s.close()


def test_effective_repos_config_only_degrades_without_store():
    assert effective_repos(_settings(), None) == ["acme/cli", "acme/gateway"]
    assert effective_repo_sources(_settings(), None) == {
        "acme/cli": "config",
        "acme/gateway": "config",
    }


def test_effective_repos_merge_config_admin_and_promoted(store):
    settings = _settings()
    store.add_admin_repo("admin/repo")
    store.promote_repo("hot/candidate")
    store.promote_repo("acme/cli")
    assert effective_repos(settings, store) == [
        "acme/cli",
        "acme/gateway",
        "admin/repo",
        "hot/candidate",
    ]
    assert effective_repo_sources(settings, store) == {
        "acme/cli": "config",
        "acme/gateway": "config",
        "admin/repo": "admin",
        "hot/candidate": "promoted",
    }


def test_effective_dependency_edges_include_admin_edges(store):
    settings = _settings()
    store.add_admin_repo("admin/repo")
    store.add_admin_dependency_edge("admin/repo", "acme/cli")
    edges = effective_dependency_edges(settings, store)
    assert edges == [
        DependencyEdge(from_repo="acme/cli", to_repo="acme/gateway"),
        DependencyEdge(from_repo="admin/repo", to_repo="acme/cli"),
    ]


def test_effective_dependency_edges_reject_unknown_admin_endpoint(store):
    store.add_admin_dependency_edge("ghost/repo", "acme/cli")
    with pytest.raises(ConfigError) as exc:
        effective_dependency_edges(_settings(), store)
    assert "ghost/repo" in str(exc.value)


def test_effective_trend_scope_additive_merge(store):
    store.set_admin_trend_scope(topics=["agent", "ai"], keywords=["rag"])
    scope = effective_trend_scope(_settings(), store)
    assert scope.topics == ["agent", "ai"]
    assert scope.keywords == ["llm", "rag"]


def test_effective_delivery_admin_overrides_safe_mutable_fields(store):
    store.set_admin_delivery_preferences(
        email_recipients=["admin@example.com"],
        digest_hour=17,
    )
    delivery = effective_delivery(_settings(), store)
    assert delivery.email_recipients == ["admin@example.com"]
    assert delivery.digest_hour == 17
    assert delivery.state_db_path == ":memory:"
    assert delivery.artifact_root == "/tmp/artifacts"
    assert delivery.smtp_host == "smtp.example.com"
    assert delivery.feed_dir == "/tmp/feed"


def test_effective_delivery_partial_admin_values(store):
    store.set_admin_delivery_preferences(email_recipients=None, digest_hour=6)
    delivery = effective_delivery(_settings(), store)
    assert delivery.email_recipients == ["base@example.com"]
    assert delivery.digest_hour == 6
