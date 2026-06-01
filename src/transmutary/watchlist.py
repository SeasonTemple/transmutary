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

from .effective_config import effective_repos

__all__ = ("effective_repos",)
