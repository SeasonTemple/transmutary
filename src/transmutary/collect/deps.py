"""Dependency resolution: npm manifest + manual edges + deps.dev (U7, R3/R7).

Deterministic only (KTD2). For each watchlist repo:
  * Direct deps: fetch ``package.json`` via the GitHub Contents API (read-only)
    and parse ``dependencies`` / ``devDependencies``.
  * Manual dependency edges (CONTEXT: runtime service links not in any manifest)
    are merged in from config so the associated repo's context is observed (F1).
  * Transitive deps: for a *published* package repo, query deps.dev for the
    transitive set. Unpublished repos get DIRECT deps only (AE3 boundary).

Security:
  * SSRF (R23): deps.dev / GitHub URLs are built from a host allowlist; the
    injected client must have ``follow_redirects=False``.
  * Read-only (R22): only GET requests; token via Authorization header, env-only.

Degradation: if deps.dev is unreachable the repo is recorded with
``transitive_degraded=True`` and only direct deps — never an error (R7).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

import httpx

from .github import SSRFError, _get_with_backoff, _require_no_redirects, _validate_repo

# SSRF allowlist for this module (R23).
ALLOWED_HOSTS = frozenset({"api.github.com", "github.com", "deps.dev", "api.deps.dev"})

# deps.dev ecosystems we build URLs for. Defense in depth (the host is fixed and
# re-asserted, so this is not the SSRF gate) — a malformed coordinate fails fast
# rather than producing a surprising request path (R23 hardening).
_DEPSDEV_ECOSYSTEMS = frozenset({"npm", "pypi", "go", "maven", "cargo", "nuget"})


@dataclass(frozen=True)
class Dependency:
    """A single resolved dependency. ``transitive`` distinguishes direct vs deep."""

    name: str
    version: str | None
    ecosystem: str = "npm"
    transitive: bool = False


@dataclass
class RepoDependencies:
    """The observation dependency set for one watchlist repo."""

    repo: str
    direct: list[Dependency] = field(default_factory=list)
    transitive: list[Dependency] = field(default_factory=list)
    manual_edges: list[str] = field(default_factory=list)  # associated repo slugs
    published: bool = False
    transitive_degraded: bool = False  # deps.dev unreachable → degraded record

    def all_dependencies(self) -> list[Dependency]:
        return self.direct + self.transitive


def _assert_allowed(url: str) -> None:
    host = httpx.URL(url).host
    if host not in ALLOWED_HOSTS:
        raise SSRFError(f"refusing request to non-allowlisted host {host!r} (R23)")


def _contents_url(repo: str, path: str = "package.json") -> str:
    owner, name = _validate_repo(repo)
    return f"https://api.github.com/repos/{owner}/{name}/contents/{path}"


def _depsdev_url(name: str, version: str, ecosystem: str = "npm") -> str:
    # deps.dev v3 dependencies endpoint (transitive graph).
    # Defense-in-depth validation (R23): the host is fixed and re-asserted by
    # _assert_allowed, but we still reject a malformed ecosystem/version so a bad
    # coordinate fails fast instead of producing a surprising request path.
    eco = (ecosystem or "").lower()
    if eco not in _DEPSDEV_ECOSYSTEMS:
        raise SSRFError(f"unsupported deps.dev ecosystem {ecosystem!r} (R23)")
    if not version or any(c in version for c in ("/", "\\", "@")) or any(
        ord(ch) < 0x20 for ch in version
    ):
        raise SSRFError(f"deps.dev version {version!r} contains unsafe characters (R23)")
    safe_name = httpx.URL("https://x").copy_with(path="/" + name).path.lstrip("/")
    return (
        f"https://api.deps.dev/v3/systems/{eco}/packages/{safe_name}/"
        f"versions/{version}:dependencies"
    )


def _parse_package_json(body: str) -> list[Dependency]:
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return []
    deps: list[Dependency] = []
    for section in ("dependencies", "devDependencies"):
        for name, version in (data.get(section) or {}).items():
            deps.append(Dependency(name=name, version=str(version), transitive=False))
    return deps


def fetch_direct_deps(
    client: httpx.Client, repo: str, *, token: str | None = None, sleep=None
) -> list[Dependency]:
    """Fetch direct deps from a repo's ``package.json``. Empty if none (unpublished)."""
    url = _contents_url(repo)
    _assert_allowed(url)
    kwargs = {} if sleep is None else {"sleep": sleep}
    resp = _get_with_backoff(client, url, token=token, **kwargs)
    if resp.status_code == 404:
        return []  # no package.json — repo with no npm manifest, not an error
    if resp.status_code != 200:
        return []
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return []
    content = payload.get("content", "")
    encoding = payload.get("encoding", "")
    if encoding == "base64":
        try:
            content = base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return []
    return _parse_package_json(content)


def fetch_transitive_deps(
    client: httpx.Client,
    name: str,
    version: str,
    *,
    ecosystem: str = "npm",
    sleep=None,
) -> tuple[list[Dependency], bool]:
    """Query deps.dev for transitive deps of a published package.

    Returns ``(deps, degraded)``. ``degraded`` is True if deps.dev was
    unreachable — caller falls back to direct-only (R7).
    """
    del sleep  # deps.dev GET is a single attempt; backoff is GitHub-specific
    url = _depsdev_url(name, version, ecosystem)
    _assert_allowed(url)  # this module's allowlist (includes deps.dev) — R23
    # R23: refuse a redirect-following client — a 3xx Location from deps.dev could
    # bounce us off-allowlist after the host check. Contract violation, not a
    # transient deps.dev outage, so this raises rather than degrading.
    _require_no_redirects(client)
    try:
        resp = client.get(url, headers={"User-Agent": "transmutary"})
    except (httpx.HTTPError, OSError):
        return [], True  # unreachable → degraded
    if resp.status_code != 200:
        return [], True
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return [], True
    out: list[Dependency] = []
    for node in payload.get("nodes", []) or []:
        # node 0 is typically the root package itself; mark the rest transitive.
        relation = node.get("relation", "")
        if relation == "SELF":
            continue
        pkg = node.get("versionKey", {}) or {}
        out.append(
            Dependency(
                name=pkg.get("name", ""),
                version=pkg.get("version"),
                ecosystem=pkg.get("system", ecosystem).lower(),
                transitive=True,
            )
        )
    return out, False


def resolve_repo_dependencies(
    client: httpx.Client,
    repo: str,
    *,
    token: str | None = None,
    manual_edges: list[str] | None = None,
    published: bool = False,
    package_name: str | None = None,
    package_version: str | None = None,
    sleep=None,
) -> RepoDependencies:
    """Build the observation dependency set for one repo (U7).

    Args:
        repo: watchlist repo slug.
        manual_edges: associated repo slugs declared via dependency edges (merged
            into the observation context — F1).
        published: whether this repo publishes an npm package. Only published
            repos get transitive deps via deps.dev (AE3 boundary).
        package_name / package_version: the published package coordinates for
            the deps.dev transitive query. Required when ``published``; if a
            published repo is passed without them, transitive resolution is
            skipped and ``transitive_degraded`` is set True (not silently empty).
    """
    _validate_repo(repo)
    direct = fetch_direct_deps(client, repo, token=token, sleep=sleep)

    transitive: list[Dependency] = []
    degraded = False
    if published:
        if package_name and package_version:
            transitive, degraded = fetch_transitive_deps(
                client, package_name, package_version, sleep=sleep
            )
        else:
            # published but no coordinates → transitive cannot be resolved. Mark
            # degraded so this misconfiguration is not indistinguishable from a
            # repo that genuinely has zero transitive deps (AE3 boundary).
            degraded = True

    return RepoDependencies(
        repo=repo,
        direct=direct,
        transitive=transitive,
        manual_edges=list(manual_edges or []),
        published=published,
        transitive_degraded=degraded,
    )
