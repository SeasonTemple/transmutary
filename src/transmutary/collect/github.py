"""GitHub collector: atom releases/tags + REST issues (U6, R5/R19/R22/R23).

Deterministic API access only (KTD2). Pulls watchlist repos' release/tag feed
(``releases.atom``, parsed with feedparser) and issues via the REST API with an
incremental ``since=`` cursor. Pre-releases are not in the atom feed, so they are
backfilled via the REST releases endpoint.

Security:
  * SSRF (R23): every outbound URL is constructed from a trusted host allowlist
    (``github.com`` / ``api.github.com``). A watchlist entry whose host is not
    GitHub is rejected at construction time. Redirect-following is the one SSRF
    vector the host allowlist alone does NOT close (a 3xx ``Location`` is chosen
    by the server, after the initial host check), so the injected client MUST be
    constructed with ``follow_redirects=False``; the public entry points assert
    this (:func:`_require_no_redirects`) rather than trusting the caller.
  * Read-only (R22): only GET requests are ever issued; the token comes from env
    (passed in by the caller, never read from disk) and is sent as an
    ``Authorization`` header — never embedded in a URL or logged.

Output: a list of :class:`RawEvent` plus the advanced cursor (for incremental
polling). All HTTP is injected via an ``httpx.Client`` so tests can mock it; no
real network in tests.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import feedparser
import httpx

# GitHub owner/repo slug grammar: letters, digits, dot, underscore, dash.
_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# ---------------------------------------------------------------------------
# SSRF allowlist (R23). Only these hosts may ever be contacted.
# ---------------------------------------------------------------------------
ALLOWED_HOSTS = frozenset({"github.com", "api.github.com"})

# Rate-limit backoff (R: 429 / secondary limit). Deterministic, bounded.
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0  # seconds; multiplied by 2**attempt


class SSRFError(Exception):
    """Raised when a URL/host falls outside the trusted allowlist (R23)."""


class RateLimitError(Exception):
    """Raised when GitHub rate limiting persists past the retry budget."""


@dataclass(frozen=True)
class RawEvent:
    """A single collected change before dedup/filter.

    ``kind`` is one of ``release`` / ``tag`` / ``issue``. ``id`` is the stable
    upstream identifier (tag name or issue number). ``ts`` is an ISO-8601 string.
    """

    repo: str
    kind: str
    id: str
    url: str
    text: str
    ts: str
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# URL construction — the SSRF gate. Nothing else builds GitHub URLs.
# ---------------------------------------------------------------------------
def _validate_repo(repo: str) -> tuple[str, str]:
    """Validate ``owner/name`` and return the pair. Rejects host-bearing input.

    A watchlist entry must be a bare ``owner/repo`` slug. If it smuggles a scheme
    or a non-GitHub host (e.g. ``evil.com/foo/bar`` or
    ``https://evil.com/o/r``) we reject it here (R23) — the only place URLs are
    built.
    """
    if "://" in repo:
        # A full URL was given; its host must be on the allowlist.
        host = httpx.URL(repo).host
        if host not in ALLOWED_HOSTS:
            raise SSRFError(f"watchlist host {host!r} is not an allowed GitHub host (R23)")
        # Strip to path slug.
        path = httpx.URL(repo).path.strip("/")
        parts = path.split("/")
    else:
        parts = repo.strip("/").split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise SSRFError(f"watchlist entry {repo!r} is not a valid owner/repo slug (R23)")
    owner, name = parts[0], parts[1]
    # Defense in depth: reject traversal / backslash / host-smuggling characters.
    # GitHub owner/name allow [A-Za-z0-9._-] only; a colon, @, or "//" is a smuggled
    # authority. ".." is path traversal. (Legit names like "cli.js" are allowed.)
    for token in (owner, name):
        if token in {"..", "."}:
            raise SSRFError(f"watchlist entry {repo!r} contains a path-traversal token (R23)")
        if not _SLUG_RE.match(token):
            raise SSRFError(
                f"watchlist entry {repo!r} contains an unsafe slug token {token!r} (R23)"
            )
    return owner, name


def _atom_url(repo: str) -> str:
    owner, name = _validate_repo(repo)
    return f"https://github.com/{owner}/{name}/releases.atom"


def _issues_url(repo: str, since: str | None) -> str:
    owner, name = _validate_repo(repo)
    url = f"https://api.github.com/repos/{owner}/{name}/issues"
    params = "?state=all&sort=updated&direction=asc&per_page=100"
    if since:
        params += f"&since={since}"
    return url + params


def _releases_url(repo: str) -> str:
    owner, name = _validate_repo(repo)
    return f"https://api.github.com/repos/{owner}/{name}/releases?per_page=100"


def _assert_allowed(url: str) -> None:
    host = httpx.URL(url).host
    if host not in ALLOWED_HOSTS:
        raise SSRFError(f"refusing request to non-allowlisted host {host!r} (R23)")


def _require_no_redirects(client: httpx.Client) -> None:
    """Enforce the R23 redirect contract on an injected client.

    The host allowlist only validates URLs we construct; a server-chosen 3xx
    ``Location`` would bypass it. ``follow_redirects=False`` is therefore the
    actual redirect defense, and we refuse to issue any request through a client
    that has it enabled rather than silently trusting the caller (R23).
    """
    if getattr(client, "follow_redirects", False):
        raise SSRFError(
            "client must be constructed with follow_redirects=False (R23): "
            "redirect-following would bypass the host allowlist"
        )


# ---------------------------------------------------------------------------
# HTTP helpers — read-only, no redirects, bounded backoff.
# ---------------------------------------------------------------------------
def _headers(token: str | None) -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "transmutary"}
    if token:
        # Read-only token, sent as Authorization header (R22). Never in URL/logs.
        h["Authorization"] = f"Bearer {token}"
    return h


def _get_with_backoff(
    client: httpx.Client,
    url: str,
    *,
    token: str | None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    sleep=time.sleep,
) -> httpx.Response:
    """GET ``url`` with exponential backoff on 429 / secondary rate limits.

    Only GET is ever issued (R22). The URL's host is asserted against the
    allowlist here, and the client's ``follow_redirects=False`` contract is
    enforced (:func:`_require_no_redirects`) — note that the host assertion alone
    does NOT prevent redirect-following; only the client setting does (R23).
    """
    _assert_allowed(url)
    _require_no_redirects(client)
    attempt = 0
    while True:
        resp = client.get(url, headers=_headers(token))
        if resp.status_code == 429 or (
            resp.status_code == 403 and "rate limit" in (resp.text or "").lower()
        ):
            if attempt >= max_retries:
                raise RateLimitError(
                    f"GitHub rate limit persisted after {max_retries} retries for {url}"
                )
            delay = backoff_base * (2**attempt)
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass
            sleep(delay)
            attempt += 1
            continue
        return resp


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _parse_atom(repo: str, body: str) -> list[RawEvent]:
    parsed = feedparser.parse(body)
    events: list[RawEvent] = []
    for entry in parsed.entries:
        title = getattr(entry, "title", "") or ""
        link = getattr(entry, "link", "") or _atom_url(repo)
        updated = getattr(entry, "updated", "") or getattr(entry, "published", "")
        summary = getattr(entry, "summary", "") or ""
        # The atom feed lists published releases (and tags). pre-releases are
        # absent here and backfilled via REST.
        events.append(
            RawEvent(
                repo=repo,
                kind="release",
                id=title,
                url=link,
                text=f"{title}\n{summary}".strip(),
                ts=updated,
            )
        )
    return events


def _parse_rest_releases(repo: str, items: list[dict], *, prereleases_only: bool) -> list[RawEvent]:
    events: list[RawEvent] = []
    for rel in items:
        is_pre = bool(rel.get("prerelease"))
        if prereleases_only and not is_pre:
            continue
        tag = rel.get("tag_name") or rel.get("name") or ""
        events.append(
            RawEvent(
                repo=repo,
                kind="release",
                id=tag,
                url=rel.get("html_url", _atom_url(repo)),
                text=f"{rel.get('name') or tag}\n{rel.get('body') or ''}".strip(),
                ts=rel.get("published_at") or rel.get("created_at") or "",
                extra={"prerelease": is_pre},
            )
        )
    return events


def _parse_rest_issues(repo: str, items: list[dict]) -> tuple[list[RawEvent], str | None]:
    """Parse issues; return events and the advanced cursor (max updated_at)."""
    events: list[RawEvent] = []
    max_updated: str | None = None
    for item in items:
        # The REST issues endpoint also returns PRs (they carry pull_request).
        if "pull_request" in item:
            continue
        number = str(item.get("number", ""))
        updated = item.get("updated_at") or ""
        if updated and (max_updated is None or updated > max_updated):
            max_updated = updated
        events.append(
            RawEvent(
                repo=repo,
                kind="issue",
                id=number,
                url=item.get("html_url", ""),
                text=f"{item.get('title') or ''}\n{item.get('body') or ''}".strip(),
                ts=updated,
                extra={"state": item.get("state")},
            )
        )
    return events, max_updated


# ---------------------------------------------------------------------------
# Public collection entry point
# ---------------------------------------------------------------------------
@dataclass
class CollectResult:
    events: list[RawEvent]
    next_since: str | None  # advanced issue cursor for the next incremental poll


def collect_repo(
    client: httpx.Client,
    repo: str,
    *,
    token: str | None = None,
    since: str | None = None,
    include_prereleases: bool = True,
    sleep=time.sleep,
) -> CollectResult:
    """Collect release/tag/issue events for one repo.

    Args:
        client: an ``httpx.Client`` configured with ``follow_redirects=False``
            (R23). Injected so tests can mock transport.
        repo: ``owner/name`` slug. Non-GitHub hosts are rejected (R23).
        token: read-only GitHub token from env (R22). Sent via Authorization.
        since: incremental issue cursor (ISO-8601). Only issues updated after
            this are returned; the returned ``next_since`` advances it.
        include_prereleases: backfill pre-releases via REST (they are absent from
            the atom feed).

    Returns:
        CollectResult with parsed events and the advanced issue cursor.
    """
    # Validate host up front so a bad watchlist entry fails before any IO (R23).
    _validate_repo(repo)

    events: list[RawEvent] = []

    # 1. Releases / tags via atom (published releases).
    atom_resp = _get_with_backoff(client, _atom_url(repo), token=token, sleep=sleep)
    if atom_resp.status_code == 200:
        events.extend(_parse_atom(repo, atom_resp.text))

    # 2. Pre-release backfill via REST (atom omits pre-releases).
    if include_prereleases:
        rel_resp = _get_with_backoff(client, _releases_url(repo), token=token, sleep=sleep)
        if rel_resp.status_code == 200:
            try:
                rel_items = rel_resp.json()
            except Exception:  # noqa: BLE001 - tolerate non-JSON empty body
                rel_items = []
            events.extend(_parse_rest_releases(repo, rel_items, prereleases_only=True))

    # 3. Issues via REST with incremental cursor.
    issues_resp = _get_with_backoff(client, _issues_url(repo, since), token=token, sleep=sleep)
    next_since = since
    if issues_resp.status_code == 200:
        try:
            issue_items = issues_resp.json()
        except Exception:  # noqa: BLE001
            issue_items = []
        issue_events, max_updated = _parse_rest_issues(repo, issue_items)
        events.extend(issue_events)
        if max_updated and (next_since is None or max_updated > next_since):
            next_since = max_updated

    return CollectResult(events=events, next_since=next_since)


def make_client() -> httpx.Client:
    """Construct the SSRF-safe client: redirects OFF (R23)."""
    return httpx.Client(follow_redirects=False, timeout=30.0)
