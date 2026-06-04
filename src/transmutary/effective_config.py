"""Effective runtime configuration.

YAML remains the bootstrap/base configuration. Dashboard-admin writes live in the
state DB as non-secret overrides. This module is the single composition point so
service scheduling, pipeline ticks, dashboard read models, and future CLI helpers
do not each invent their own merge rules.
"""

from __future__ import annotations

from .config import (
    ENV_LLM_API_KEY,
    ENV_LLM_BASE_URL,
    ConfigError,
    Delivery,
    DependencyEdge,
    Settings,
    TrendScope,
)
from .store.state import StateStore


def effective_repo_sources(settings: Settings, store: StateStore | None) -> dict[str, str]:
    """Return repo -> source label for the effective watchlist.

    Precedence is intentionally stable for display and destructive-action guards:
    YAML config wins over admin overrides, which win over promoted rows.
    """
    sources = {repo: "config" for repo in settings.watchlist.repo_names()}
    if store is None:
        return dict(sorted(sources.items()))

    for repo in store.list_admin_repos():
        sources.setdefault(repo, "admin")
    for repo in store.list_promoted():
        sources.setdefault(repo, "promoted")
    return dict(sorted(sources.items()))


def effective_repos(settings: Settings, store: StateStore | None) -> list[str]:
    """The deterministic effective repo list: config ∪ admin ∪ promoted."""
    return list(effective_repo_sources(settings, store).keys())


def effective_dependency_edges(
    settings: Settings,
    store: StateStore | None,
) -> list[DependencyEdge]:
    """Return YAML dependency edges plus validated admin dependency edges."""
    repos = set(effective_repos(settings, store))
    edges = list(settings.watchlist.dependency_edges)
    if store is not None:
        for edge in store.list_admin_dependency_edges():
            for endpoint, label in ((edge.from_repo, "from"), (edge.to_repo, "to")):
                if endpoint not in repos:
                    raise ConfigError(
                        f"Admin dependency edge {label!r} endpoint {endpoint!r} "
                        f"(edge {edge.from_repo} -> {edge.to_repo}) "
                        "does not reference an effective tracked repo"
                    )
            edges.append(DependencyEdge(from_repo=edge.from_repo, to_repo=edge.to_repo))
    return sorted(edges, key=lambda e: (e.from_repo, e.to_repo))


def effective_trend_scope(settings: Settings, store: StateStore | None) -> TrendScope:
    """Return base trend scope plus dashboard-admin topics/keywords."""
    topics = set(settings.trend_scope.topics)
    keywords = set(settings.trend_scope.keywords)
    if store is not None:
        topics.update(store.list_admin_trend_topics())
        keywords.update(store.list_admin_trend_keywords())
    return TrendScope(topics=sorted(topics), keywords=sorted(keywords))


def effective_delivery(settings: Settings, store: StateStore | None) -> Delivery:
    """Return delivery config with safe dashboard-admin preferences applied."""
    base = settings.delivery
    if store is None:
        return base
    prefs = store.get_admin_delivery_preferences()
    return Delivery(
        state_db_path=base.state_db_path,
        artifact_root=base.artifact_root,
        token_max_age_days=base.token_max_age_days,
        digest_hour=(
            base.digest_hour if prefs.digest_hour is None else prefs.digest_hour
        ),
        email_recipients=(
            list(base.email_recipients)
            if prefs.email_recipients is None
            else list(prefs.email_recipients)
        ),
        smtp_host=base.smtp_host,
        feed_dir=base.feed_dir,
    )


def effective_llm_config(
    settings: Settings,
    *,
    env: dict[str, str] | None = None,
    require: bool = True,
) -> dict[str, tuple[str, str | None, str]]:
    """Return per-tier ``{tier: (api_key, base_url, model)}`` for LLM calls.

    Tiers are ``strong`` / ``cheap`` / ``embed``. Each field resolves independently
    by precedence (most specific wins):

        env per-tier   (TRANSMUTARY_LLM_<TIER>_<FIELD>)
      > env shared     (TRANSMUTARY_LLM_<FIELD>)
      > yaml per-tier  (tiers.<tier>.<field>)
      > yaml shared    (top-level <field>)
      > built-in default (model only)

    ``model`` has the transport prefix applied (bare name → ``<transport>/<name>``;
    a name already containing ``/`` passes through). ``require=True`` only requires
    the STRONG tier to have an api_key — embed may be empty (pipeline degrades to
    full L3). When ``require=False`` an empty strong key yields ``""`` (dashboard
    may be unconfigured).
    """
    import os

    from .llm import DEFAULT_TIER_MODELS, ModelTier

    env = os.environ if env is None else env
    yaml_cfg = settings.llm_config
    overrides = yaml_cfg.tier_overrides if yaml_cfg is not None else {}

    # Shared (non-tier) sources.
    env_key_shared = env.get(ENV_LLM_API_KEY) or None
    env_url_shared = env.get(ENV_LLM_BASE_URL) or None
    env_tx_shared = (
        env.get("TRANSMUTARY_LLM_TRANSPORT")
        or env.get("TRANSMUTARY_LLM_PROVIDER")
        or env.get("TRANSMUTARY_LLM_VENDOR")
        or None
    )
    yaml_key_shared = yaml_cfg.api_key if yaml_cfg is not None else None
    yaml_url_shared = yaml_cfg.base_url if yaml_cfg is not None else None
    yaml_tx_shared = yaml_cfg.transport if yaml_cfg is not None else None

    _model_default = {
        "strong": DEFAULT_TIER_MODELS[ModelTier.STRONG],
        "cheap": DEFAULT_TIER_MODELS[ModelTier.CHEAP],
        "embed": DEFAULT_TIER_MODELS[ModelTier.EMBED],
    }
    _yaml_model_shared = {
        "strong": yaml_cfg.model_strong if yaml_cfg is not None else None,
        "cheap": yaml_cfg.model_cheap if yaml_cfg is not None else None,
        "embed": yaml_cfg.model_embed if yaml_cfg is not None else None,
    }
    # Back-compat env aliases: TRANSMUTARY_LLM_MODEL_<TIER> (old MODEL-first form).
    _env_model_legacy = {
        "strong": env.get("TRANSMUTARY_LLM_MODEL_STRONG") or None,
        "cheap": env.get("TRANSMUTARY_LLM_MODEL_CHEAP") or None,
        "embed": env.get("TRANSMUTARY_LLM_MODEL_EMBED") or None,
    }

    def _resolve(tier: str) -> tuple[str, str | None, str]:
        ov = overrides.get(tier)
        up = tier.upper()
        # Precedence: a per-tier value (env OR yaml) is more specific than any
        # shared value, so it ALWAYS wins. Within each scope, env beats yaml.
        #   env per-tier > yaml per-tier > env shared > yaml shared > default
        # (A shared TRANSMUTARY_LLM_API_KEY must NOT override a yaml per-tier key —
        #  that would force e.g. a GLM-embed tier back onto the shared chat key.)
        key = (
            (env.get(f"TRANSMUTARY_LLM_{up}_API_KEY") or None)
            or (ov.api_key if ov else None)
            or env_key_shared
            or yaml_key_shared
            or ""
        )
        url = (
            (env.get(f"TRANSMUTARY_LLM_{up}_BASE_URL") or None)
            or (ov.base_url if ov else None)
            or env_url_shared
            or yaml_url_shared
        )
        transport = (
            (env.get(f"TRANSMUTARY_LLM_{up}_TRANSPORT") or None)
            or (ov.transport if ov else None)
            or env_tx_shared
            or yaml_tx_shared
        )
        model = (
            (env.get(f"TRANSMUTARY_LLM_{up}_MODEL") or None)
            or (ov.model if ov else None)
            or _env_model_legacy[tier]
            or _yaml_model_shared[tier]
            or _model_default[tier]
        )
        # Apply transport prefix to bare model names.
        if transport and "/" not in model:
            prefix = transport if transport.endswith("/") else transport + "/"
            model = prefix + model
        return (key, url, model)

    result = {tier: _resolve(tier) for tier in ("strong", "cheap", "embed")}

    if require and not result["strong"][0]:
        raise ConfigError(
            "LLM API key not configured: set TRANSMUTARY_LLM_API_KEY or "
            "configure via `transmutary config` / dashboard settings"
        )
    return result


__all__ = (
    "effective_delivery",
    "effective_dependency_edges",
    "effective_llm_config",
    "effective_repo_sources",
    "effective_repos",
    "effective_trend_scope",
)
