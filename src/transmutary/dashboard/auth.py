"""Dashboard admin authentication helpers.

The dashboard's settings pages are higher-risk than promote/demote: they mutate
runtime configuration. This module keeps the first auth model deliberately small:
one env-provided admin token establishes a signed, HttpOnly session cookie. The
raw token is never persisted.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from starlette.requests import Request
from starlette.responses import Response

ADMIN_TOKEN_ENV = "TRANSMUTARY_ADMIN_TOKEN"
ADMIN_SESSION_COOKIE = "tmtry-admin"


def token_configured(token: str | None) -> bool:
    return bool(token)


def verify_admin_token(candidate: str | None, expected: str | None) -> bool:
    if not candidate or not expected:
        return False
    return hmac.compare_digest(candidate, expected)


def _sign(nonce: str, admin_token: str) -> str:
    return hmac.new(admin_token.encode(), nonce.encode(), hashlib.sha256).hexdigest()


def issue_session(admin_token: str) -> str:
    nonce = secrets.token_urlsafe(24)
    return f"{nonce}.{_sign(nonce, admin_token)}"


def verify_session(value: str | None, admin_token: str | None) -> bool:
    if not value or not admin_token or "." not in value:
        return False
    nonce, sig = value.split(".", 1)
    if not nonce or not sig:
        return False
    return hmac.compare_digest(sig, _sign(nonce, admin_token))


def is_admin(request: Request, admin_token: str | None) -> bool:
    return verify_session(request.cookies.get(ADMIN_SESSION_COOKIE), admin_token)


def set_admin_cookie(response: Response, session: str, *, secure: bool) -> None:
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        session,
        httponly=True,
        samesite="strict",
        path="/",
        secure=secure,
    )


def clear_admin_cookie(response: Response, *, secure: bool) -> None:
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/", secure=secure)
