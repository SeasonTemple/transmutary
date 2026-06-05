"""U1 config tests — loading, validation, and credential redaction (KTD4)."""

from __future__ import annotations

import pytest

from transmutary.config import (
    ConfigError,
    Credentials,
    Delivery,
    LLMConfig,
    RepoEntry,
    Settings,
    TrendScope,
    Watchlist,
    load_llm_config,
    load_settings,
    parse_delivery,
    parse_trend_scope,
    parse_watchlist,
    save_llm_config,
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
    # LLM key is NOT required — only GitHub/SMTP/RSS tokens are.
    assert "LLM" not in str(exc.value)


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


def test_llm_config_api_key_not_in_repr():
    """P0 fix: LLMConfig.api_key must not appear in repr (KTD4)."""
    cfg = LLMConfig(api_key="sk-super-secret-key-12345")
    text = repr(cfg) + str(cfg)
    assert "sk-super-secret-key-12345" not in text


def test_settings_llm_config_not_in_repr():
    """P0 fix: Settings.llm_config must not appear in repr (KTD4)."""
    cfg = LLMConfig(api_key="sk-secret", base_url="https://x")
    s = Settings(
        watchlist=Watchlist(repos=[RepoEntry(repo="a/b")], dependency_edges=[]),
        trend_scope=TrendScope(topics=["ai"], keywords=[]),
        delivery=Delivery(state_db_path=":memory:", artifact_root="/tmp/a",
                          token_max_age_days=90, digest_hour=9),
        llm_config=cfg,
    )
    text = repr(s) + str(s)
    assert "sk-secret" not in text
    assert "llm_config" not in text


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
    assert d.email_lang == "en"  # default
    assert d.security_interval_seconds == 300  # default
    assert d.release_issue_interval_seconds == 600  # default


def test_delivery_poll_intervals_parsed_and_clamped():
    from transmutary.config import (
        MAX_POLL_INTERVAL_SECONDS,
        MIN_POLL_INTERVAL_SECONDS,
        normalize_interval,
    )
    # valid passes through
    d = parse_delivery({
        **_REQUIRED_DELIVERY,
        "security_interval_seconds": 180,
        "release_issue_interval_seconds": 1800,
    })
    assert d.security_interval_seconds == 180
    assert d.release_issue_interval_seconds == 1800
    # below the 2min floor → clamped up to 120 (hard floor, not warned)
    assert parse_delivery(
        {**_REQUIRED_DELIVERY, "security_interval_seconds": 90}
    ).security_interval_seconds == MIN_POLL_INTERVAL_SECONDS == 120
    assert parse_delivery(
        {**_REQUIRED_DELIVERY, "security_interval_seconds": 30}
    ).security_interval_seconds == 120
    # above the 24h ceiling → clamped down
    assert parse_delivery(
        {**_REQUIRED_DELIVERY, "release_issue_interval_seconds": 999999}
    ).release_issue_interval_seconds == MAX_POLL_INTERVAL_SECONDS == 86400
    # non-numeric / missing → default
    assert parse_delivery(
        {**_REQUIRED_DELIVERY, "security_interval_seconds": "fast"}
    ).security_interval_seconds == 300
    # normalize_interval direct
    assert normalize_interval(None, default=600) == 600
    assert normalize_interval(50, default=600) == 120
    assert normalize_interval(300, default=600) == 300


def test_delivery_email_lang_parsed_and_whitelisted():
    assert parse_delivery({**_REQUIRED_DELIVERY, "email_lang": "zh"}).email_lang == "zh"
    # unsupported value normalizes to the default, never raises
    assert parse_delivery({**_REQUIRED_DELIVERY, "email_lang": "fr"}).email_lang == "en"
    assert parse_delivery({**_REQUIRED_DELIVERY, "email_lang": 42}).email_lang == "en"


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


# --- U1: LLM config file (load / save / permissions) --------------------------

def test_load_llm_config_file_not_found(config_dir):
    assert load_llm_config(config_dir) is None


def test_load_llm_config_happy(tmp_path):
    import os

    (tmp_path / "llm.yaml").write_text("api_key: sk-test123\nbase_url: https://llm.example.com\n")
    os.chmod(str(tmp_path / "llm.yaml"), 0o600)
    cfg = load_llm_config(str(tmp_path))
    assert cfg is not None
    assert cfg.api_key == "sk-test123"
    assert cfg.base_url == "https://llm.example.com"


def test_load_llm_config_no_base_url(tmp_path):
    import os

    (tmp_path / "llm.yaml").write_text("api_key: sk-key\n")
    os.chmod(str(tmp_path / "llm.yaml"), 0o600)
    cfg = load_llm_config(str(tmp_path))
    assert cfg is not None
    assert cfg.api_key == "sk-key"
    assert cfg.base_url is None


def test_load_llm_config_missing_api_key_tolerated(tmp_path):
    # A keyless yaml is now valid: env supplies the shared key (env wins, the
    # dashboard locks the field) or only per-tier keys are set. The "no key
    # anywhere" error is raised later by effective_llm_config(require=True).
    import os

    (tmp_path / "llm.yaml").write_text("base_url: https://x\n")
    os.chmod(str(tmp_path / "llm.yaml"), 0o600)
    cfg = load_llm_config(str(tmp_path))
    assert cfg is not None
    assert cfg.api_key == ""
    assert cfg.base_url == "https://x"


def test_load_llm_config_non_string_api_key_rejected(tmp_path):
    import os

    (tmp_path / "llm.yaml").write_text("api_key: 12345\n")
    os.chmod(str(tmp_path / "llm.yaml"), 0o600)
    with pytest.raises(ConfigError, match="api_key"):
        load_llm_config(str(tmp_path))


def test_load_llm_config_overly_broad_permissions(tmp_path):
    import os

    p = tmp_path / "llm.yaml"
    p.write_text("api_key: sk-key\n")
    os.chmod(str(p), 0o644)
    if os.name == "nt":
        pytest.skip("POSIX permissions only")
    with pytest.raises(ConfigError, match="permissions"):
        load_llm_config(str(tmp_path))


def test_save_llm_config_writes_0600(tmp_path):
    import os
    import stat

    cfg = LLMConfig(api_key="sk-newkey", base_url="https://llm.test")
    save_llm_config(str(tmp_path), cfg)
    raw = (tmp_path / "llm.yaml").read_text()
    assert "sk-newkey" in raw
    assert "https://llm.test" in raw
    if os.name != "nt":
        mode = stat.S_IMODE(os.stat(str(tmp_path / "llm.yaml")).st_mode)
        assert mode == 0o600


def test_save_then_load_roundtrip(tmp_path):
    cfg = LLMConfig(api_key="sk-rt", base_url="https://rt.test")
    save_llm_config(str(tmp_path), cfg)
    loaded = load_llm_config(str(tmp_path))
    assert loaded == cfg


def test_flat_yaml_has_no_tier_overrides(tmp_path):
    cfg = LLMConfig(api_key="sk-x", base_url="https://x.test", model_strong="gpt-4o")
    save_llm_config(str(tmp_path), cfg)
    loaded = load_llm_config(str(tmp_path))
    assert loaded.tier_overrides == {}


def test_per_tier_embed_override_roundtrip(tmp_path):
    from transmutary.config import TierOverride
    cfg = LLMConfig(
        api_key="sk-shared", base_url="https://chat.test", transport="anthropic",
        model_strong="MiniMax-M3",
        tier_overrides={
            "embed": TierOverride(
                base_url="https://embed.test", transport="openai", model="embo-01"
            )
        },
    )
    save_llm_config(str(tmp_path), cfg)
    loaded = load_llm_config(str(tmp_path))
    assert "embed" in loaded.tier_overrides
    ov = loaded.tier_overrides["embed"]
    assert ov.base_url == "https://embed.test"
    assert ov.transport == "openai"
    assert ov.model == "embo-01"
    assert ov.api_key is None  # not set → falls back to shared
    assert "strong" not in loaded.tier_overrides


def test_keyless_shared_with_per_tier_key_roundtrip(tmp_path):
    from transmutary.config import TierOverride
    # B+: shared key absent (env supplies it), embed carries its own per-tier key.
    # save must OMIT the empty shared key; load must NOT raise and must return "".
    cfg = LLMConfig(
        api_key="",
        tier_overrides={"embed": TierOverride(api_key="sk-embed-only")},
    )
    save_llm_config(str(tmp_path), cfg)
    text = (tmp_path / "llm.yaml").read_text()
    assert "api_key: ''" not in text and "api_key: \"\"" not in text  # omitted, not empty
    loaded = load_llm_config(str(tmp_path))
    assert loaded is not None
    assert loaded.api_key == ""
    assert loaded.tier_overrides["embed"].api_key == "sk-embed-only"


def test_per_tier_api_key_excluded_from_repr():
    from transmutary.config import TierOverride
    ov = TierOverride(api_key="sk-embed-secret", base_url="https://e.test")
    assert "sk-embed-secret" not in repr(ov)
    cfg = LLMConfig(api_key="sk-shared", tier_overrides={"embed": ov})
    assert "sk-embed-secret" not in repr(cfg)
    assert "sk-shared" not in repr(cfg)


def test_empty_tier_override_dropped(tmp_path):
    from transmutary.config import TierOverride
    cfg = LLMConfig(api_key="sk-x", tier_overrides={"embed": TierOverride()})
    save_llm_config(str(tmp_path), cfg)
    loaded = load_llm_config(str(tmp_path))
    assert loaded.tier_overrides == {}  # all-None override not persisted


def test_settings_llm_config_populated(config_dir, fake_env, tmp_path):
    """load_settings picks up llm.yaml when present."""
    import os

    # Copy example yamls into tmp_path so load_settings finds them.
    import shutil
    for name in ("watchlist", "trend_scope", "delivery"):
        src = os.path.join(config_dir, f"{name}.example.yaml")
        shutil.copy2(src, str(tmp_path / f"{name}.example.yaml"))
    save_llm_config(str(tmp_path), LLMConfig(api_key="sk-from-yaml"))
    settings = load_settings(str(tmp_path), env=fake_env)
    assert settings.llm_config is not None
    assert settings.llm_config.api_key == "sk-from-yaml"


def test_credentials_from_env_without_llm_key():
    """Credentials.from_env succeeds without LLM key (returns empty string)."""
    env = {
        "TRANSMUTARY_GITHUB_TOKEN": "ghp_x",
        "TRANSMUTARY_SMTP_USER": "u",
        "TRANSMUTARY_SMTP_PASSWORD": "p",
        "TRANSMUTARY_RSS_TOKEN": "r",
    }
    creds = Credentials.from_env(env)
    assert creds.llm_api_key == ""


def test_report_to_dict_excludes_llm_config():
    """LLMConfig must never appear in serialized report output (ADV-09)."""
    from transmutary.report.schema import Report, ReportKind, Severity

    r = Report(
        kind=ReportKind.DIAGNOSE,
        repo="a/b",
        title="t",
        body_md="body",
        severity=Severity.NORMAL,
        created_at="2026-01-01T00:00:00Z",
    )
    d = r.to_dict()
    assert "llm_config" not in d
