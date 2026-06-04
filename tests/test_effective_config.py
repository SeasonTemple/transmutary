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
    llm_env_locks,
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
    r = effective_llm_config(
        s,
        env={"TRANSMUTARY_LLM_API_KEY": "env-key", "TRANSMUTARY_LLM_BASE_URL": "https://env"},
    )
    key, url, _ = r["strong"]
    assert key == "env-key"
    assert url == "https://env"


def test_llm_yaml_used_when_env_missing():
    s = _llm_settings(llm_config=LLMConfig(api_key="yaml-key", base_url="https://yaml"))
    key, url, _ = effective_llm_config(s, env={})["strong"]
    assert key == "yaml-key"
    assert url == "https://yaml"


def test_llm_both_missing_require_true_errors():
    s = _llm_settings()
    with pytest.raises(ConfigError, match="LLM API key"):
        effective_llm_config(s, env={}, require=True)


def test_llm_both_missing_require_false_returns_empty():
    s = _llm_settings()
    r = effective_llm_config(s, env={}, require=False)
    key, url, _ = r["strong"]
    assert key == ""
    assert url is None
    assert set(r.keys()) == {"strong", "cheap", "embed"}


def test_llm_env_key_only_yaml_url_used():
    s = _llm_settings(llm_config=LLMConfig(api_key="yaml-key", base_url="https://yaml"))
    key, url, _ = effective_llm_config(s, env={"TRANSMUTARY_LLM_API_KEY": "env-key"})["strong"]
    assert key == "env-key"
    assert url == "https://yaml"


def test_llm_per_tier_model_env_overrides():
    s = _llm_settings(llm_config=LLMConfig(
        api_key="k", model_strong="yaml-strong", model_cheap="yaml-cheap",
    ))
    r = effective_llm_config(s, env={"TRANSMUTARY_LLM_STRONG_MODEL": "env-strong"})
    assert r["strong"][2] == "env-strong"
    assert r["cheap"][2] == "yaml-cheap"


def test_llm_legacy_model_env_alias_still_works():
    # Back-compat: old TRANSMUTARY_LLM_MODEL_STRONG keeps working.
    s = _llm_settings(llm_config=LLMConfig(api_key="k"))
    r = effective_llm_config(s, env={"TRANSMUTARY_LLM_MODEL_STRONG": "legacy-strong"})
    assert r["strong"][2] == "legacy-strong"


def test_llm_per_tier_model_defaults():
    s = _llm_settings(llm_config=LLMConfig(api_key="k"))
    r = effective_llm_config(s, env={})
    assert r["strong"][2] == "gpt-4o"
    assert r["cheap"][2] == "gpt-4o-mini"
    assert r["embed"][2] == "text-embedding-3-small"


def test_llm_transport_prefixes_bare_yaml_model():
    s = _llm_settings(llm_config=LLMConfig(
        api_key="k", transport="openai", model_strong="MiniMax-M3",
    ))
    assert effective_llm_config(s, env={})["strong"][2] == "openai/MiniMax-M3"


def test_llm_passes_through_user_written_vendor_prefix():
    s = _llm_settings(llm_config=LLMConfig(
        api_key="k", transport="openai", model_strong="minimax/MiniMax-M3",
    ))
    assert effective_llm_config(s, env={})["strong"][2] == "minimax/MiniMax-M3"


def test_llm_transport_does_not_double_prefix_already_prefixed():
    s = _llm_settings(llm_config=LLMConfig(
        api_key="k", transport="openai", model_strong="openai/MiniMax-M3",
    ))
    assert effective_llm_config(s, env={})["strong"][2] == "openai/MiniMax-M3"


def test_llm_transport_env_over_yaml():
    s = _llm_settings(llm_config=LLMConfig(
        api_key="k", transport="openai", model_strong="MiniMax-M3",
    ))
    r = effective_llm_config(s, env={"TRANSMUTARY_LLM_TRANSPORT": "anthropic"})
    assert r["strong"][2] == "anthropic/MiniMax-M3"


# --- per-tier overrides (the new capability) --------------------------------

def test_embed_tier_yaml_override_independent_provider():
    from transmutary.config import TierOverride
    s = _llm_settings(llm_config=LLMConfig(
        api_key="shared-key", base_url="https://chat.test", transport="anthropic",
        model_strong="MiniMax-M3", model_embed="ignored",
        tier_overrides={"embed": TierOverride(
            base_url="https://embed.test", transport="openai", model="embo-01",
        )},
    ))
    r = effective_llm_config(s, env={})
    # strong: shared key/url + anthropic prefix
    assert r["strong"] == ("shared-key", "https://chat.test", "anthropic/MiniMax-M3")
    # embed: key falls back to shared, url+transport+model from override
    ekey, eurl, emodel = r["embed"]
    assert ekey == "shared-key"
    assert eurl == "https://embed.test"
    assert emodel == "openai/embo-01"


def test_embed_tier_separate_key():
    from transmutary.config import TierOverride
    s = _llm_settings(llm_config=LLMConfig(
        api_key="shared-key", base_url="https://chat.test",
        tier_overrides={"embed": TierOverride(api_key="embed-key", base_url="https://e.test")},
    ))
    assert effective_llm_config(s, env={})["embed"][0] == "embed-key"
    assert effective_llm_config(s, env={})["strong"][0] == "shared-key"


def test_env_per_tier_beats_yaml_per_tier():
    from transmutary.config import TierOverride
    s = _llm_settings(llm_config=LLMConfig(
        api_key="k", tier_overrides={"embed": TierOverride(base_url="https://yaml-embed.test")},
    ))
    r = effective_llm_config(s, env={"TRANSMUTARY_LLM_EMBED_BASE_URL": "https://env-embed.test"})
    assert r["embed"][1] == "https://env-embed.test"


def test_embed_empty_does_not_error_when_strong_present():
    # require=True with strong key but no embed-specific creds → no error (embed
    # falls back to shared/strong; pipeline degrades to full L3 only if embed call
    # actually fails at runtime).
    s = _llm_settings(llm_config=LLMConfig(api_key="strong-only"))
    r = effective_llm_config(s, require=True, env={})
    assert r["strong"][0] == "strong-only"
    assert r["embed"][0] == "strong-only"  # falls back to shared


def test_yaml_per_tier_key_not_overridden_by_env_shared_key():
    # Regression: a shared env key must NOT clobber a yaml per-tier key, else a
    # GLM-embed tier gets forced onto the shared (MiniMax) chat key → auth error.
    from transmutary.config import TierOverride
    s = _llm_settings(llm_config=LLMConfig(
        api_key="yaml-shared",
        tier_overrides={"embed": TierOverride(
            api_key="glm-embed-key", base_url="https://glm.test", transport="openai",
        )},
    ))
    r = effective_llm_config(s, env={"TRANSMUTARY_LLM_API_KEY": "env-shared-minimax"})
    # strong falls back to the env shared key (no per-tier override)
    assert r["strong"][0] == "env-shared-minimax"
    # embed keeps its yaml per-tier key — env shared does NOT win over it
    assert r["embed"][0] == "glm-embed-key"
    assert r["embed"][1] == "https://glm.test"


# --- llm_env_locks: per-UI-field env provenance (B+ env=lock layer) ---------
def test_llm_env_locks_empty_when_no_env():
    assert llm_env_locks(env={}) == {}


def test_llm_env_locks_shared_key():
    assert llm_env_locks(env={"TRANSMUTARY_LLM_API_KEY": "sk-x"}) == {
        "api_key": "TRANSMUTARY_LLM_API_KEY"
    }


def test_llm_env_locks_per_tier_base_url():
    locks = llm_env_locks(env={"TRANSMUTARY_LLM_EMBED_BASE_URL": "https://glm.test"})
    assert locks == {"embed_base_url": "TRANSMUTARY_LLM_EMBED_BASE_URL"}


def test_llm_env_locks_transport_alias_reports_actual_var():
    # transport has three aliases; the lock reports whichever is actually set.
    locks = llm_env_locks(env={"TRANSMUTARY_LLM_PROVIDER": "minimax"})
    assert locks == {"transport": "TRANSMUTARY_LLM_PROVIDER"}


def test_llm_env_locks_shared_model_both_aliases():
    # shared model_<tier> field is shadowed by EITHER env per-tier OR legacy alias.
    assert llm_env_locks(env={"TRANSMUTARY_LLM_MODEL_STRONG": "m"}) == {
        "model_strong": "TRANSMUTARY_LLM_MODEL_STRONG"
    }
    # per-tier env var takes precedence as the reported source.
    assert (
        llm_env_locks(env={"TRANSMUTARY_LLM_STRONG_MODEL": "m"})["model_strong"]
        == "TRANSMUTARY_LLM_STRONG_MODEL"
    )


def test_llm_env_locks_empty_string_is_not_set():
    assert llm_env_locks(env={"TRANSMUTARY_LLM_API_KEY": "   "}) == {}
