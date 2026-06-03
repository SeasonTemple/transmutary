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
) -> tuple[str, str | None, str | None]:
    """Return ``(api_key, base_url, model)`` for LLM calls.

    Precedence: ``TRANSMUTARY_LLM_*`` env > ``config/llm.yaml`` fields.
    When ``require=False`` and both sources are empty, returns
    ``("", None, None)`` — used by the dashboard where LLM may be unconfigured.
    """
    import os

    env = os.environ if env is None else env
    env_key = env.get(ENV_LLM_API_KEY, "")
    env_url = env.get(ENV_LLM_BASE_URL) or None
    env_model = env.get("TRANSMUTARY_LLM_MODEL") or None

    yaml_cfg = settings.llm_config
    yaml_key = yaml_cfg.api_key if yaml_cfg is not None else ""
    yaml_url = yaml_cfg.base_url if yaml_cfg is not None else None
    yaml_model = yaml_cfg.model if yaml_cfg is not None else None

    api_key = env_key or yaml_key
    base_url = env_url if env_url is not None else yaml_url
    model = env_model if env_model is not None else yaml_model

    if require and not api_key:
        raise ConfigError(
            "LLM API key not configured: set TRANSMUTARY_LLM_API_KEY or "
            "configure via `transmutary config` / dashboard settings"
        )
    return (api_key, base_url, model)


__all__ = (
    "effective_delivery",
    "effective_dependency_edges",
    "effective_llm_config",
    "effective_repo_sources",
    "effective_repos",
    "effective_trend_scope",
)
