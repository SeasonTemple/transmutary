"""ASGI server for the private RSS feed + Authorization-header auth (U15, R20/R21).

Serves the per-feed Atom XML over an ASGI app (starlette + uvicorn). Auth is the
crux of R20:

  * Authentication is an HTTP ``Authorization: Bearer <token>`` header. The token
    is **NEVER in the URL** (no query string, no path) — neither requests nor the
    rendered feed carry it (R20). The path identifies only the feed name.
  * Each subscriber has an independent token. Tokens are stored HASHED in the U2
    ``subscriber_token`` table with ``revoked`` and ``expires_at`` columns, so a
    single subscriber's token can be revoked or expire without affecting others
    (R20).
  * Server logs redact the token (R21): we log only the subscriber + a short token
    fingerprint prefix, never the bearer value.

The app is constructed with an injected feed-source callable and a U2 StateStore
so tests can drive it with an in-memory DB and no real network/uvicorn.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

logger = logging.getLogger("transmutary.deliver.server")

# Feeds that may be served (matches the two inline routes; KTD1).
_VALID_FEEDS = frozenset({"immediate", "digest"})


def hash_token(token: str) -> str:
    """Stable hash of a subscriber token (what we persist; never the raw token)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_fingerprint(token: str) -> str:
    """Short, non-reversible fingerprint for logs (R21 — never the raw token)."""
    return hash_token(token)[:8]


def _extract_bearer(request: Request) -> str | None:
    """Pull the bearer token from the Authorization header (R20 — header only).

    A token presented in the query string or path is explicitly ignored: auth is
    header-only, so a tokenized URL never authenticates.
    """
    auth = request.headers.get("authorization", "")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() not in ("bearer", "basic"):
        return None
    if parts[0].lower() == "basic":
        # Basic auth: token is the password component (user:token). Decoded but
        # never logged.
        import base64

        try:
            decoded = base64.b64decode(parts[1]).decode("utf-8")
        except Exception:  # noqa: BLE001
            return None
        return decoded.split(":", 1)[1] if ":" in decoded else decoded
    return parts[1].strip()


class FeedAuth:
    """Authenticates a request against the U2 subscriber_token table (R20)."""

    def __init__(self, store, *, now: Callable[[], float] | None = None) -> None:
        self._store = store
        import time

        self._now = now or time.time

    def authenticate(self, token: str | None) -> tuple[bool, str]:
        """Return (ok, reason). Checks existence, revocation, and expiry (R20)."""
        if not token:
            return False, "missing token"
        record = self._store.get_subscriber_token(hash_token(token))
        if record is None:
            return False, "unknown token"
        if record.revoked:
            return False, "revoked token"
        if record.expires_at is not None and self._now() >= record.expires_at:
            return False, "expired token"
        return True, record.subscriber


def make_app(store, feed_source: Callable[[str], str], *, now=None) -> Starlette:
    """Build the ASGI feed app.

    Args:
        store: U2 StateStore (holds the subscriber_token table).
        feed_source: callable ``feed_name -> Atom XML string`` (injected so tests
            don't need real report generation).
        now: clock seam for expiry checks (tests freeze it).
    """
    auth = FeedAuth(store, now=now)

    async def feed_endpoint(request: Request) -> Response:
        feed_name = request.path_params["feed"]
        token = _extract_bearer(request)
        ok, reason = auth.authenticate(token)
        if not ok:
            # R21: log subscriber/fingerprint context, NEVER the raw token.
            fp = _token_fingerprint(token) if token else "none"
            logger.warning(
                "feed auth rejected feed=%s reason=%s token_fp=%s", feed_name, reason, fp
            )
            return PlainTextResponse("Unauthorized", status_code=401)
        if feed_name not in _VALID_FEEDS:
            return PlainTextResponse("Not Found", status_code=404)
        logger.info("feed served feed=%s subscriber=%s token_fp=%s",
                    feed_name, reason, _token_fingerprint(token))
        xml = feed_source(feed_name)
        return Response(content=xml, media_type="application/atom+xml")

    routes = [Route("/feed/{feed}", feed_endpoint, methods=["GET"])]
    return Starlette(routes=routes)
