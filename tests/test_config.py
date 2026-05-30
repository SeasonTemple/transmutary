"""U1 config tests — loading, validation, and credential redaction (KTD4)."""

from __future__ import annotations

import pytest

from transmutary.config import (
    ConfigError,
    Credentials,
    Settings,
    load_settings,
    parse_delivery,
    parse_trend_scope,
    parse_watchlist,
)

_REQUIRED_DELIVERY = {
    "state_db_path": "./var/state.sqlite3",
    "artifact_root": "./var/artifacts",
}


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


# --- U1: optional outbound-delivery fields ----------------------------------
def test_delivery_optional_fields_parsed():
    data = {
        **_REQUIRED_DELIVERY,
        "email_recipients": ["a@example.com", "b@example.com"],
        "smtp_host": "smtp.example.com",
        "feed_dir": "./var/feed",
    }
    d = parse_delivery(data)
    assert d.email_recipients == ["a@example.com", "b@example.com"]
    assert d.smtp_host == "smtp.example.com"
    assert d.feed_dir == "./var/feed"


def test_delivery_optional_fields_default_when_absent():
    # Edge / regression guard: with only the required keys, the new fields take
    # safe defaults and the required keys parse exactly as before.
    d = parse_delivery(dict(_REQUIRED_DELIVERY))
    assert d.email_recipients == []
    assert d.smtp_host is None
    assert d.feed_dir is None
    assert d.state_db_path == "./var/state.sqlite3"
    assert d.artifact_root == "./var/artifacts"
    assert d.token_max_age_days == 90
    assert d.digest_hour == 9


def test_delivery_email_recipients_single_string_normalized():
    d = parse_delivery({**_REQUIRED_DELIVERY, "email_recipients": "solo@example.com"})
    assert d.email_recipients == ["solo@example.com"]


def test_delivery_email_recipients_bad_type_errors():
    with pytest.raises(ConfigError):
        parse_delivery({**_REQUIRED_DELIVERY, "email_recipients": 42})


def test_delivery_example_yaml_still_loads(config_dir, fake_env):
    # The example yaml has the new fields commented out → defaults; load is green.
    settings = load_settings(config_dir, env=fake_env)
    assert settings.delivery.email_recipients == []
    assert settings.delivery.smtp_host is None
    assert settings.delivery.feed_dir is None
