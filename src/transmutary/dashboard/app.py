"""Read-only dashboard ASGI app + entrypoint (U3/U4).

A Starlette app (same stack as ``deliver/server.py``) that renders the view
models from :mod:`.data`. Security posture (KTD-Dash-5):

  * **GET-only** — no mutation endpoint exists (R-D7).
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
import secrets
import sys

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Route

from ..config import Settings
from ..store.artifacts import ArtifactStore
from ..store.state import StateStore
from . import data

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

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

# CSP without a nonce — used on error responses (no inline script) and as the
# fail-closed fallback. script-src 'self' alone blocks inline scripts entirely.
_CSP_NO_NONCE = "default-src 'self'; script-src 'self'; style-src 'self'"


def _csp_with_nonce(nonce: str) -> str:
    """CSP that precisely allows ONE inline script via its per-request nonce.

    Never opens unsafe-inline / unsafe-eval and never allows an external origin —
    the nonce is a precise allow, not a relaxation. Empty nonce → fall back to the
    no-nonce CSP (fail closed: the inline script is then blocked, never silently
    allowed via an empty/malformed nonce token).
    """
    if not nonce:
        return _CSP_NO_NONCE
    return f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self'"


class _MissingJinja(RuntimeError):
    """jinja2 (the `dashboard` extra) is not installed."""


def _require_jinja() -> None:
    if _jinja2 is None or Jinja2Templates is None:
        raise _MissingJinja(
            "the dashboard requires jinja2; install it with: "
            "pip install 'transmutary[dashboard]'"
        )


def _host_only(host: str) -> str:
    """Extract the hostname from a Host header, robust to IPv6 literals (R-D14).

    ``[::1]:8787`` and ``[::1]`` → ``[::1]``; ``127.0.0.1:8787`` → ``127.0.0.1``;
    bare ``::1`` (no brackets) is returned as-is. Only a trailing ``:port`` after a
    bracketed IPv6 or on a plain host is stripped — never an interior IPv6 colon.
    """
    if not host:
        return ""
    if host.startswith("["):
        end = host.find("]")
        if end != -1:
            return host[: end + 1]  # keep the brackets to match the allowlist
        return host
    # Plain host or IPv4: strip a single trailing :port if present. A bare IPv6
    # like '::1' has multiple colons and no port → leave it untouched.
    if host.count(":") == 1:
        return host.rsplit(":", 1)[0]
    return host


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
        request.state.csp_nonce = nonce
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
) -> Starlette:
    """Build the read-only dashboard ASGI app (no server bound)."""
    _require_jinja()
    templates = Jinja2Templates(directory=_TEMPLATES_DIR)  # autoescape on (R-D10)
    allowed = allowed_hosts if allowed_hosts is not None else _DEFAULT_ALLOWED_HOSTS

    async def index(request: Request) -> Response:
        overview = data.build_overview(settings, store, artifacts)
        return templates.TemplateResponse(
            request, "index.html", {"overview": overview}
        )

    async def repo_page(request: Request) -> Response:
        repo = f"{request.path_params['owner']}/{request.path_params['repo']}"
        result = data.build_repo_runtime(settings, store, artifacts, repo)
        if result is None:
            return PlainTextResponse("Not Found", status_code=404)
        runtime, cards = result
        return templates.TemplateResponse(
            request, "repo.html", {"runtime": runtime, "cards": cards}
        )

    async def report_page(request: Request) -> Response:
        repo = f"{request.path_params['owner']}/{request.path_params['repo']}"
        filename = request.path_params["filename"]
        view = data.build_report_view(artifacts, repo, filename)
        if view is None:
            return PlainTextResponse("Not Found", status_code=404)
        return templates.TemplateResponse(request, "report.html", {"view": view})

    async def healthz(request: Request) -> Response:
        return PlainTextResponse("ok")

    _CSS_PATH = os.path.join(_STATIC_DIR, "dashboard.css")

    async def stylesheet(request: Request) -> Response:
        # Served from a fixed path (no path params) so there is no traversal
        # surface; same-origin so CSP `default-src 'self'` allows it (R-D18).
        return FileResponse(_CSS_PATH, media_type="text/css")

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
        Route("/healthz", healthz, methods=["GET"]),
        Route("/static/dashboard.css", stylesheet, methods=["GET"]),
    ]
    middleware = [
        Middleware(HostAllowlistMiddleware, allowed_hosts=allowed),
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
    is_local = host in {"127.0.0.1", "localhost", "::1", "[::1]"}
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
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    host, port = resolve_bind(args.host, args.port, allow_public=args.allow_public)
    _require_jinja()

    config_dir = os.environ.get("TRANSMUTARY_CONFIG_DIR", "config")
    # A read-only viewer needs no credentials — do not load them (avoids forcing
    # all TRANSMUTARY_* secrets into the process just to view the dashboard).
    settings = load_settings(config_dir, require_credentials=False)
    # Open the state DB strictly read-only: the dashboard never writes and must not
    # contend with a live service's writes.
    store = StateStore(settings.delivery.state_db_path, read_only=True)
    artifacts = ArtifactStore(settings.delivery.artifact_root)
    app = make_dashboard_app(
        settings, store, artifacts, allowed_hosts=_allowed_hosts_for(host)
    )

    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    main()
