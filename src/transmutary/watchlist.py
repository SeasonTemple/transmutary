"""Effective watchlist — the single source of truth for the observed repo set.

The effective watchlist is the union of repos declared in the config
``watchlist`` block and repos the user has promoted through the dashboard,
sorted deterministically. It depends on both :mod:`transmutary.config` and
:mod:`transmutary.store.state` but on neither, so the cross-layer import
from :mod:`transmutary.dashboard.data` into :mod:`transmutary.service`
that previously hosted this function goes away: both callers now import
from this leaf module.
"""

from __future__ import annotations

from .config import Settings
from .store.state import StateStore


def effective_repos(settings: Settings, store: StateStore | None) -> list[str]:
    """The single source of truth for the observed repo set (F4, KTD-D).

    The effective watchlist = config ``watchlist`` repos ∪ promoted repos, with
    duplicates removed and a deterministic (sorted) order so registration and
    reconcile never diverge. ``store=None`` (no state store available, e.g. a fake
    runtime in a unit test) degrades to config-only, preserving backward compat.
    """
    repos = set(settings.watchlist.repo_names())
    if store is not None:
        repos.update(store.list_promoted())
    return sorted(repos)


__all__ = ("effective_repos",)
