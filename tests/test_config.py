"""U1 config tests — loading, validation, and credential redaction (KTD4)."""

from __future__ import annotations

import pytest

from transmutary.config import (
    ConfigError,
    Credentials,
    Settings,
    load_settings,
    parse_trend_scope,
    parse_watchlist,
)


def test_happy_load_settings(config_dir, fake_env):
    settings = load_settings(config_dir, env=fake_env)
    assert isinstance(settings, Settings)
    assert "example-org/upstream-cli" in settings.watchlist.repo_names()
    assert settings.watchlist.dependency_edges  # edges loaded
    assert settings.trend_scope.topics or settings.trend_scope.keywords
    assert settings.delivery.artifact_root
    assert settings.llm_base_url == "https://gateway.example.com/v1"


def test_dependency_edge_to_unknown_repo_errors():
    data = {
        "repos": [{"repo": "a/one"}],
        "dependency_edges": [{"from": "a/one", "to": "b/missing"}],
    }
    with pytest.raises(ConfigError) as exc:
        parse_watchlist(data)
    assert "b/missing" in str(exc.value)


def test_trend_scope_all_empty_errors():
    with pytest.raises(ConfigError):
        parse_trend_scope({"topics": [], "keywords": []})


def test_missing_credentials_errors(config_dir):
    with pytest.raises(ConfigError) as exc:
        load_settings(config_dir, env={})  # no credentials
    assert "Missing required credential" in str(exc.value)


def test_credentials_not_in_repr(fake_env):
    creds = Credentials.from_env(fake_env)
    text = repr(creds) + str(creds)
    for secret in (
        fake_env["TRANSMUTARY_GITHUB_TOKEN"],
        fake_env["TRANSMUTARY_SMTP_PASSWORD"],
        fake_env["TRANSMUTARY_RSS_TOKEN"],
        fake_env["TRANSMUTARY_LLM_API_KEY"],
    ):
        assert secret not in text


def test_settings_repr_excludes_all_credentials(config_dir, fake_env):
    settings = load_settings(config_dir, env=fake_env)
    text = repr(settings)
    for secret in (
        fake_env["TRANSMUTARY_GITHUB_TOKEN"],
        fake_env["TRANSMUTARY_SMTP_USER"],
        fake_env["TRANSMUTARY_SMTP_PASSWORD"],
        fake_env["TRANSMUTARY_RSS_TOKEN"],
        fake_env["TRANSMUTARY_LLM_API_KEY"],
    ):
        assert secret not in text
    # base_url is non-secret config and MAY appear.
    assert settings.credentials is not None


def test_credentials_accessors_return_raw(fake_env):
    creds = Credentials.from_env(fake_env)
    assert creds.github_token == fake_env["TRANSMUTARY_GITHUB_TOKEN"]
    assert creds.llm_api_key == fake_env["TRANSMUTARY_LLM_API_KEY"]
