"""Effective config tests — YAML base + SQLite admin overrides."""

from __future__ import annotations

import pytest

from transmutary.config import (
    ConfigError,
    Delivery,
    DependencyEdge,
    LLMConfig,
    RepoEntry,
    Settings,
    TrendScope,
    Watchlist,
)
from transmutary.effective_config import (
    effective_delivery,
    effective_dependency_edges,
    effective_llm_config,
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


# --- effective_llm_config ---------------------------------------------------

def _llm_settings(**overrides) -> Settings:
    defaults = dict(
        watchlist=Watchlist(
            repos=[RepoEntry(repo="a/b")], dependency_edges=[],
        ),
        trend_scope=TrendScope(topics=["ai"], keywords=[]),
        delivery=Delivery(
            state_db_path=":memory:",
            artifact_root="/tmp/a",
            token_max_age_days=90,
            digest_hour=9,
        ),
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_llm_env_wins_over_yaml():
    s = _llm_settings(llm_config=LLMConfig(api_key="yaml-key", base_url="https://yaml"))
    key, url, models = effective_llm_config(
        s,
        env={"TRANSMUTARY_LLM_API_KEY": "env-key", "TRANSMUTARY_LLM_BASE_URL": "https://env"},
    )
    assert key == "env-key"
    assert url == "https://env"


def test_llm_yaml_used_when_env_missing():
    s = _llm_settings(llm_config=LLMConfig(api_key="yaml-key", base_url="https://yaml"))
    key, url, models = effective_llm_config(s, env={})
    assert key == "yaml-key"
    assert url == "https://yaml"


def test_llm_both_missing_require_true_errors():
    s = _llm_settings()
    with pytest.raises(ConfigError, match="LLM API key"):
        effective_llm_config(s, env={}, require=True)


def test_llm_both_missing_require_false_returns_empty():
    s = _llm_settings()
    key, url, models = effective_llm_config(s, env={}, require=False)
    assert key == ""
    assert url is None
    assert isinstance(models, dict)


def test_llm_env_key_only_yaml_url_used():
    s = _llm_settings(llm_config=LLMConfig(api_key="yaml-key", base_url="https://yaml"))
    key, url, models = effective_llm_config(
        s, env={"TRANSMUTARY_LLM_API_KEY": "env-key"}
    )
    assert key == "env-key"
    assert url == "https://yaml"


def test_llm_per_tier_model_env_overrides():
    s = _llm_settings(llm_config=LLMConfig(
        api_key="k", model_strong="yaml-strong", model_cheap="yaml-cheap",
    ))
    key, url, models = effective_llm_config(
        s, env={"TRANSMUTARY_LLM_MODEL_STRONG": "env-strong"},
    )
    assert models["strong"] == "env-strong"
    assert models["cheap"] == "yaml-cheap"


def test_llm_per_tier_model_defaults():
    s = _llm_settings(llm_config=LLMConfig(api_key="k"))
    key, url, models = effective_llm_config(s, env={})
    assert models["strong"] == "gpt-4o"
    assert models["cheap"] == "gpt-4o-mini"
    assert models["embed"] == "text-embedding-3-small"


def test_llm_transport_prefixes_bare_yaml_model():
    s = _llm_settings(llm_config=LLMConfig(
        api_key="k", transport="openai", model_strong="MiniMax-M3",
    ))
    key, url, models = effective_llm_config(s, env={})
    assert models["strong"] == "openai/MiniMax-M3"


def test_llm_passes_through_user_written_vendor_prefix():
    # User writes the full LiteLLM model name — no auto-prefixing.
    s = _llm_settings(llm_config=LLMConfig(
        api_key="k", transport="openai", model_strong="minimax/MiniMax-M3",
    ))
    key, url, models = effective_llm_config(s, env={})
    assert models["strong"] == "minimax/MiniMax-M3"


def test_llm_transport_does_not_double_prefix_already_prefixed():
    s = _llm_settings(llm_config=LLMConfig(
        api_key="k", transport="openai", model_strong="openai/MiniMax-M3",
    ))
    key, url, models = effective_llm_config(s, env={})
    assert models["strong"] == "openai/MiniMax-M3"


def test_llm_transport_env_over_yaml():
    s = _llm_settings(llm_config=LLMConfig(
        api_key="k", transport="openai", model_strong="MiniMax-M3",
    ))
    key, url, models = effective_llm_config(
        s, env={"TRANSMUTARY_LLM_TRANSPORT": "anthropic"},
    )
    assert models["strong"] == "anthropic/MiniMax-M3"
