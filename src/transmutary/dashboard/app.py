"""Dashboard ASGI app + entrypoint (U3/U4).

A Starlette app (same stack as ``deliver/server.py``) that renders the view
models from :mod:`.data`. Security posture (KTD-Dash-5):

  * **Read-mostly by default** — public binds stay read-only unless explicitly
    opted into writes; localhost can promote/demote via protected POST forms.
  * **CSRF-protected writes** — promote/demote require double-submit CSRF,
    Origin/Referer checks, SameSite cookies, and server confirmation.
  * **Host-header allow-list** (R-D14) — rejects requests whose ``Host`` is not a
    localhost name, defeating DNS-rebinding of the localhost bind.
  * **Security response headers** (R-D18) — CSP / nosniff / frame-deny on every
    response, defence-in-depth behind autoescape.
  * **No debug, generic 500** (R-D17) — ``debug=False`` and a generic error body
    so a failure never leaks a filesystem path / config / traceback.

``jinja2`` is an optional dependency (the ``dashboard`` extra); it is import-guarded
so a core install fails with a clear install hint instead of an ImportError.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import sqlite3
import sys
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route

from ..config import Settings
from ..store.artifacts import ArtifactStore
from ..store.state import StateStore
from . import data, i18n
from .csrf import CSRF_COOKIE, CSRFMiddleware, _host_only, check_origin, verify_token
from .llms import render_llms_txt

try:  # jinja2 ships in the optional `dashboard` extra (KTD-Dash-2).
    import jinja2 as _jinja2
    from starlette.templating import Jinja2Templates
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    _jinja2 = None
    Jinja2Templates = None

logger = logging.getLogger("transmutary.dashboard")

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_DEFAULT_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})
_DEFAULT_PORT = 8787
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}

# CSP without a nonce — used on error responses (no inline script) and as the
# fail-closed fallback. script-src 'self' alone blocks inline scripts entirely.
_CSP_NO_NONCE = (
    "default-src 'self'; script-src 'self'; style-src 'self'; form-action 'self'"
)


def _csp_with_nonce(nonce: str) -> str:
    """CSP that precisely allows ONE inline script via its per-request nonce.

    Never opens unsafe-inline / unsafe-eval and never allows an external origin —
    the nonce is a precise allow, not a relaxation. Empty nonce → fall back to the
    no-nonce CSP (fail closed: the inline script is then blocked, never silently
    allowed via an empty/malformed nonce token).
    """
    if not nonce:
        return _CSP_NO_NONCE
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self'; form-action 'self'"
    )


class _MissingJinja(RuntimeError):
    """jinja2 (the `dashboard` extra) is not installed."""


def _require_jinja() -> None:
    if _jinja2 is None or Jinja2Templates is None:
        raise _MissingJinja(
            "the dashboard requires jinja2; install it with: "
            "pip install 'transmutary[dashboard]'"
        )


def _valid_repo(repo: str) -> bool:
    if "://" in repo:
        return False
    return bool(_REPO_RE.match(repo))


class HostAllowlistMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Host header is not in the allow-list (R-D14).

    The localhost bind is a network-layer control; a DNS-rebind still arrives over
    a legitimate localhost TCP connection, so the HTTP layer must check the Host
    header too. Only the hostname (port stripped) is compared.
    """

    def __init__(self, app, allowed_hosts: frozenset[str]) -> None:
        super().__init__(app)
        self._allowed = allowed_hosts

    async def dispatch(self, request: Request, call_next):
        hostname = _host_only(request.headers.get("host", ""))
        if hostname not in self._allowed:
            return PlainTextResponse("Bad Request", status_code=400)
        return await call_next(request)


class CSPNonceMiddleware(BaseHTTPMiddleware):
    """Generate a per-request CSP nonce and attach security headers (R-C1/R-C2/R-D18).

    The nonce is stored on ``request.state.csp_nonce`` so the template can stamp it
    onto the single trusted inline FOUC script. The response CSP allows exactly that
    nonce — never ``unsafe-inline``/``unsafe-eval``, never an external origin. nonce
    is 128-bit (``token_urlsafe(16)``), unique per request, never cached.
    """

    async def dispatch(self, request: Request, call_next):
        nonce = secrets.token_urlsafe(16)
        # Store on the ASGI scope (one dict shared with the downstream route's
        # Request) — BaseHTTPMiddleware does NOT propagate a request.state set here
        # to the route handler across Starlette versions, but the scope is shared.
        request.scope["csp_nonce"] = nonce
        response = await call_next(request)
        # Only set CSP if a handler did not already set it (e.g. the 500 handler
        # sets its own no-nonce CSP). setdefault preserves that.
        response.headers.setdefault("Content-Security-Policy", _csp_with_nonce(nonce))
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response


def make_dashboard_app(
    settings: Settings,
    store: StateStore,
    artifacts: ArtifactStore,
    *,
    allowed_hosts: frozenset[str] | None = None,
    write_store: StateStore | None = None,
    csrf_secure: bool = False,
) -> Starlette:
    """Build the dashboard ASGI app (no server bound)."""
    _require_jinja()
    templates = Jinja2Templates(directory=_TEMPLATES_DIR)  # autoescape on (R-D10)
    allowed = allowed_hosts if allowed_hosts is not None else _DEFAULT_ALLOWED_HOSTS

    def _lang(request: Request) -> str:
        """Resolve the request language from the cookie (whitelist, R-I4)."""
        return i18n.resolve_lang(request.cookies.get(i18n.LANG_COOKIE))

    def _wants_json(request: Request) -> bool:
        """Content negotiation: JSON if ?format=json or Accept: application/json."""
        if request.query_params.get("format") == "json":
            return True
        accept = request.headers.get("accept", "")
        return "application/json" in accept

    def _ctx(request: Request, extra: dict) -> dict:
        """Template context with i18n chrome strings + lang (R-I1/R-I3)."""
        lang = _lang(request)
        return {
            "lang": lang,
            "html_lang": i18n.HTML_LANG[lang],
            "t": i18n.messages_for(lang),
            "nonce": request.scope.get("csp_nonce", ""),
            "csrf_token": request.scope.get("csrf_token", ""),
            "writes_enabled": write_store is not None,
            **extra,
        }

    async def index(request: Request) -> Response:
        overview = data.build_overview(settings, store, artifacts)
        if _wants_json(request):
            return JSONResponse(overview.to_dict())
        return templates.TemplateResponse(
            request=request, name="index.html",
            context=_ctx(request, {"overview": overview}),
        )

    async def repo_page(request: Request) -> Response:
        repo = f"{request.path_params['owner']}/{request.path_params['repo']}"
        result = data.build_repo_runtime(settings, store, artifacts, repo)
        if result is None:
            return PlainTextResponse("Not Found", status_code=404)
        runtime, cards = result
        if _wants_json(request):
            return JSONResponse(
                {"runtime": runtime.to_dict(), "reports": [c.to_dict() for c in cards]}
            )
        return templates.TemplateResponse(
            request=request, name="repo.html",
            context=_ctx(request, {"runtime": runtime, "cards": cards}),
        )

    async def report_page(request: Request) -> Response:
        repo = f"{request.path_params['owner']}/{request.path_params['repo']}"
        filename = request.path_params["filename"]
        view = data.build_report_view(artifacts, repo, filename)
        if view is None:
            return PlainTextResponse("Not Found", status_code=404)
        if _wants_json(request):
            return JSONResponse(view.to_dict())
        return templates.TemplateResponse(
            request=request, name="report.html",
            context=_ctx(request, {"view": view}),
        )

    def _write_disabled() -> Response:
        return PlainTextResponse("Not Found", status_code=404)

    def _error(request: Request, key: str, status_code: int) -> Response:
        message = i18n.messages_for(_lang(request))[key]
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context=_ctx(request, {"message": message, "error_key": key}),
            status_code=status_code,
        )

    async def promote_confirm(request: Request) -> Response:
        if write_store is None:
            return _write_disabled()
        repo = request.query_params.get("repo", "")
        if not _valid_repo(repo):
            return _error(request, "error_invalid_repo", 400)
        return templates.TemplateResponse(
            request=request,
            name="confirm.html",
            context=_ctx(
                request,
                {
                    "action": "/promote",
                    "repo": repo,
                    "question": i18n.messages_for(_lang(request))["confirm_promote_q"],
                },
            ),
        )

    async def demote_confirm(request: Request) -> Response:
        if write_store is None:
            return _write_disabled()
        repo = request.query_params.get("repo", "")
        if not _valid_repo(repo):
            return _error(request, "error_invalid_repo", 400)
        return templates.TemplateResponse(
            request=request,
            name="confirm.html",
            context=_ctx(
                request,
                {
                    "action": "/demote",
                    "repo": repo,
                    "question": i18n.messages_for(_lang(request))["confirm_demote_q"],
                },
            ),
        )

    async def _write_action(request: Request, action: str) -> Response:
        if write_store is None:
            return _write_disabled()
        if not check_origin(request, allowed):
            return _error(request, "error_csrf", 403)
        body = (await request.body()).decode("utf-8", errors="replace")
        form = parse_qs(body, keep_blank_values=True)
        repo = form.get("repo", [""])[0]
        csrf_token = form.get("csrf_token", [""])[0]
        if not verify_token(csrf_token, request.cookies.get(CSRF_COOKIE)):
            return _error(request, "error_csrf", 403)
        if not _valid_repo(repo):
            return _error(request, "error_invalid_repo", 400)
        if action == "demote" and not write_store.is_promoted(repo):
            return _error(request, "error_not_promoted", 400)
        try:
            if action == "promote":
                write_store.promote_repo(repo)
            else:
                write_store.demote_repo(repo)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                return _error(request, "write_busy", 503)
            raise
        return RedirectResponse("/", status_code=303)

    async def promote_action(request: Request) -> Response:
        return await _write_action(request, "promote")

    async def demote_action(request: Request) -> Response:
        return await _write_action(request, "demote")

    async def healthz(request: Request) -> Response:
        return PlainTextResponse("ok")

    async def llms_txt(request: Request) -> Response:
        # Endpoint self-description for agents — NO private data (R-G1/KTD-G2).
        return PlainTextResponse(
            render_llms_txt(), media_type="text/markdown; charset=utf-8"
        )

    _CSS_PATH = os.path.join(_STATIC_DIR, "dashboard.css")
    _JS_PATH = os.path.join(_STATIC_DIR, "dashboard.js")

    async def stylesheet(request: Request) -> Response:
        # Fixed path (no path params) → no traversal surface; same-origin → CSP ok.
        return FileResponse(_CSS_PATH, media_type="text/css")

    async def script(request: Request) -> Response:
        return FileResponse(_JS_PATH, media_type="text/javascript")

    async def server_error(request: Request, exc: Exception) -> Response:
        # R-D17: never leak the exception detail / path / traceback.
        logger.error("dashboard request failed: %s", type(exc).__name__)
        # R-D18 + R-C1: error page has no inline script, so use the no-nonce CSP.
        # Set headers directly because ServerErrorMiddleware sits OUTSIDE the
        # nonce middleware and would otherwise miss them.
        headers = {"Content-Security-Policy": _CSP_NO_NONCE, **_SECURITY_HEADERS}
        return PlainTextResponse(
            "Internal Server Error", status_code=500, headers=headers
        )

    routes = [
        Route("/", index, methods=["GET"]),
        Route("/repo/{owner}/{repo}", repo_page, methods=["GET"]),
        Route("/report/{owner}/{repo}/{filename}", report_page, methods=["GET"]),
        Route("/promote", promote_confirm, methods=["GET"]),
        Route("/promote", promote_action, methods=["POST"]),
        Route("/demote", demote_confirm, methods=["GET"]),
        Route("/demote", demote_action, methods=["POST"]),
        Route("/healthz", healthz, methods=["GET"]),
        Route("/llms.txt", llms_txt, methods=["GET"]),
        Route("/static/dashboard.css", stylesheet, methods=["GET"]),
        Route("/static/dashboard.js", script, methods=["GET"]),
    ]
    middleware = [
        Middleware(HostAllowlistMiddleware, allowed_hosts=allowed),
        Middleware(CSRFMiddleware, enabled=write_store is not None, secure=csrf_secure),
        Middleware(CSPNonceMiddleware),
    ]
    return Starlette(
        debug=False,  # R-D17
        routes=routes,
        middleware=middleware,
        exception_handlers={500: server_error, Exception: server_error},
    )


# --- entrypoint (U4) ---------------------------------------------------------


def resolve_bind(host: str, port: int, *, allow_public: bool) -> tuple[str, int]:
    """Resolve the bind (host, port), enforcing the public opt-in hard gate (R-D16).

    A non-localhost host (e.g. ``0.0.0.0``) is REFUSED unless ``allow_public`` is
    explicitly set — a misconfigured public bind must not succeed on a mere log
    line, since it would expose private intelligence with no auth. When public is
    explicitly allowed, a warning is still emitted.
    """
    is_local = is_local_bind(host)
    if not is_local and not allow_public:
        raise SystemExit(
            f"refusing to bind dashboard to non-localhost host {host!r} without "
            f"--allow-public; the dashboard serves private intelligence with no "
            f"built-in auth. Re-run with --allow-public only behind an auth proxy."
        )
    if not is_local:
        logger.warning(
            "dashboard bound to PUBLIC host %s — it has no built-in auth; "
            "front it with an authenticating proxy (R-D16)",
            host,
        )
    return host, port


def is_local_bind(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1", "[::1]"}


def resolve_write_store(
    settings: Settings,
    *,
    is_local: bool,
    allow_public_writes: bool,
) -> StateStore | None:
    if not is_local and not allow_public_writes:
        return None
    if not is_local:
        logger.warning(
            "dashboard PUBLIC write endpoints enabled — no built-in identity auth; "
            "front with an HTTPS authenticating proxy plus rate limits/allowlists"
        )
    return StateStore(
        settings.delivery.state_db_path,
        read_only=False,
        busy_timeout_ms=3000,
    )


def _allowed_hosts_for(host: str) -> frozenset[str]:
    """Host-header allow-list for a given bind host (R-D14)."""
    if host in _DEFAULT_ALLOWED_HOSTS:
        return _DEFAULT_ALLOWED_HOSTS
    return _DEFAULT_ALLOWED_HOSTS | {host}


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - real server
    import argparse

    from ..config import load_settings

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="transmutary-dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument(
        "--allow-public",
        action="store_true",
        help="permit binding to a non-localhost host (no built-in auth — use a proxy)",
    )
    parser.add_argument(
        "--allow-public-writes",
        action="store_true",
        help=(
            "enable promote/demote endpoints on public binds; requires an HTTPS "
            "authenticating proxy with rate limits"
        ),
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    host, port = resolve_bind(args.host, args.port, allow_public=args.allow_public)
    _require_jinja()

    config_dir = os.environ.get("TRANSMUTARY_CONFIG_DIR", "config")
    # A read-only viewer needs no credentials — do not load them (avoids forcing
    # all TRANSMUTARY_* secrets into the process just to view the dashboard).
    settings = load_settings(config_dir, require_credentials=False)
    # Open the render store strictly read-only. Any enabled promote/demote writes
    # use the separate RW write_store handle below.
    store = StateStore(settings.delivery.state_db_path, read_only=True)
    write_store = resolve_write_store(
        settings,
        is_local=is_local_bind(host),
        allow_public_writes=args.allow_public_writes,
    )
    artifacts = ArtifactStore(settings.delivery.artifact_root)
    app = make_dashboard_app(
        settings,
        store,
        artifacts,
        allowed_hosts=_allowed_hosts_for(host),
        write_store=write_store,
        csrf_secure=not is_local_bind(host) and args.allow_public_writes,
    )

    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    main()
