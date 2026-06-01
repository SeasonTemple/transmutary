"""CSRF primitives for dashboard write endpoints.

The dashboard has no session store, so write forms use a double-submit token:
the middleware sets a SameSite=Strict cookie and templates echo the same value
into a hidden form field. POST handlers compare both values and also enforce an
Origin/Referer host check.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

CSRF_COOKIE = "tmtry-csrf"


def _host_only(host: str) -> str:
    """Extract hostname from Host/Origin netloc, preserving bracketed IPv6."""
    if not host:
        return ""
    if host.startswith("["):
        end = host.find("]")
        if end != -1:
            return host[: end + 1]
        return host
    if host.count(":") == 1:
        return host.rsplit(":", 1)[0]
    return host


def issue_token() -> str:
    return secrets.token_urlsafe(32)


def verify_token(form_token: str | None, cookie_token: str | None) -> bool:
    if not form_token or not cookie_token:
        return False
    return secrets.compare_digest(form_token, cookie_token)


def _host_from_url(value: str) -> str:
    if value == "null":
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    return _host_only(parsed.netloc)


def check_origin(request: Request, allowed_hosts: frozenset[str]) -> bool:
    origin = request.headers.get("origin")
    if origin and origin != "null":
        return _host_from_url(origin) in allowed_hosts
    referer = request.headers.get("referer")
    if referer:
        return _host_from_url(referer) in allowed_hosts
    return False


class CSRFMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, enabled: bool = True, secure: bool = False) -> None:
        super().__init__(app)
        self._enabled = enabled
        self._secure = secure

    async def dispatch(self, request: Request, call_next):
        if not self._enabled:
            return await call_next(request)
        token = request.cookies.get(CSRF_COOKIE)
        should_set = False
        if not token:
            token = issue_token()
            should_set = True
        request.scope["csrf_token"] = token
        response = await call_next(request)
        if should_set:
            response.set_cookie(
                CSRF_COOKIE,
                token,
                samesite="strict",
                httponly=True,
                path="/",
                secure=self._secure,
            )
        return response
