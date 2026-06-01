"""Delivery routes — the two inline branches of KTD1.

A :class:`DeliveryRoute` value is the per-repo path segment under
``_delivered/<route>/`` and the corresponding RSS feed name. Extracted
from :mod:`transmutary.deliver.stub` to its own leaf module so that
:mod:`transmutary.deliver.rss` can import the enum without dragging
:mod:`transmutary.deliver.stub` (which already imports ``rss``) into a
circular import.
"""

from __future__ import annotations

import enum


class DeliveryRoute(str, enum.Enum):
    IMMEDIATE = "immediate"  # high-risk → instant RSS + email (Phase 1)
    DIGEST = "digest"  # low-priority → daily digest


__all__ = ("DeliveryRoute",)
