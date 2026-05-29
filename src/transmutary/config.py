"""Configuration loading + credential handling (U1, R21, KTD4).

Parses three YAML files (watchlist / trend_scope / delivery) into a ``Settings``
object. Credentials (GitHub token, SMTP, RSS token, LLM API key + optional
base_url) are read ONLY from ``os.environ``; they are NEVER serialized into
repr / logs / SQLite / reports (KTD4).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

# ---------------------------------------------------------------------------
# Environment variable names for credentials. Values are read at load time and
# held in a write-only secret container (see Credentials below).
# ---------------------------------------------------------------------------
ENV_GITHUB_TOKEN = "TRANSMUTARY_GITHUB_TOKEN"
ENV_SMTP_USER = "TRANSMUTARY_SMTP_USER"
ENV_SMTP_PASSWORD = "TRANSMUTARY_SMTP_PASSWORD"
ENV_RSS_TOKEN = "TRANSMUTARY_RSS_TOKEN"
ENV_LLM_API_KEY = "TRANSMUTARY_LLM_API_KEY"
ENV_LLM_BASE_URL = "TRANSMUTARY_LLM_BASE_URL"  # optional; OpenAI-compat endpoint

# Required credentials — load fails clearly if any of these is missing.
_REQUIRED_ENV = (
    ENV_GITHUB_TOKEN,
    ENV_SMTP_USER,
    ENV_SMTP_PASSWORD,
    ENV_RSS_TOKEN,
    ENV_LLM_API_KEY,
)


class ConfigError(Exception):
    """Raised on invalid configuration or missing required credentials."""


class _Secret:
    """A single secret value that refuses to reveal itself in repr/str.

    The raw value is accessible only via :meth:`get`. Never stored anywhere that
    serializes (no SQLite, no reports, no logs). KTD4.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def __repr__(self) -> str:  # noqa: D105 - intentional redaction
        return "<Secret [REDACTED]>"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Secret) and other._value == self._value

    def __hash__(self) -> int:  # secrets are not used as keys; keep hashable-safe
        return hash(id(self))


class Credentials:
    """Container for all credentials. Read-only from env; redacts on serialize.

    Deliberately NOT a dataclass so that ``asdict``/``repr`` cannot leak values.
    Access secrets via the ``*_value`` accessors only.
    """

    __slots__ = ("_github_token", "_smtp_user", "_smtp_password", "_rss_token", "_llm_api_key")

    def __init__(
        self,
        github_token: str,
        smtp_user: str,
        smtp_password: str,
        rss_token: str,
        llm_api_key: str,
    ) -> None:
        self._github_token = _Secret(github_token)
        self._smtp_user = _Secret(smtp_user)
        self._smtp_password = _Secret(smtp_password)
        self._rss_token = _Secret(rss_token)
        self._llm_api_key = _Secret(llm_api_key)

    # --- accessors (raw values; callers must not log these) ---
    @property
    def github_token(self) -> str:
        return self._github_token.get()

    @property
    def smtp_user(self) -> str:
        return self._smtp_user.get()

    @property
    def smtp_password(self) -> str:
        return self._smtp_password.get()

    @property
    def rss_token(self) -> str:
        return self._rss_token.get()

    @property
    def llm_api_key(self) -> str:
        return self._llm_api_key.get()

    # --- redaction surfaces (KTD4) ---
    def __repr__(self) -> str:
        return "Credentials(<all redacted>)"

    __str__ = __repr__

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Credentials:
        env = os.environ if env is None else env
        missing = [name for name in _REQUIRED_ENV if not env.get(name)]
        if missing:
            raise ConfigError(
                "Missing required credential environment variables: " + ", ".join(missing)
            )
        return cls(
            github_token=env[ENV_GITHUB_TOKEN],
            smtp_user=env[ENV_SMTP_USER],
            smtp_password=env[ENV_SMTP_PASSWORD],
            rss_token=env[ENV_RSS_TOKEN],
            llm_api_key=env[ENV_LLM_API_KEY],
        )


@dataclass(frozen=True)
class RepoEntry:
    repo: str


@dataclass(frozen=True)
class DependencyEdge:
    from_repo: str
    to_repo: str


@dataclass(frozen=True)
class Watchlist:
    repos: list[RepoEntry]
    dependency_edges: list[DependencyEdge]

    def repo_names(self) -> list[str]:
        return [r.repo for r in self.repos]


@dataclass(frozen=True)
class TrendScope:
    topics: list[str]
    keywords: list[str]


@dataclass(frozen=True)
class Delivery:
    state_db_path: str
    artifact_root: str
    token_max_age_days: int
    digest_hour: int


@dataclass(frozen=True)
class Settings:
    """Top-level loaded configuration.

    ``credentials`` is held but never serialized; ``llm_base_url`` is non-secret
    config (an endpoint URL) and may appear in repr.
    """

    watchlist: Watchlist
    trend_scope: TrendScope
    delivery: Delivery
    llm_base_url: str | None = None
    # credentials excluded from repr to avoid any chance of leaking via str(Settings)
    credentials: Credentials | None = field(default=None, repr=False, compare=False)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def _load_yaml(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must be a mapping at top level")
    return data


def parse_watchlist(data: dict) -> Watchlist:
    repos = [RepoEntry(repo=str(r["repo"])) for r in data.get("repos", []) if r and "repo" in r]
    repo_names = {r.repo for r in repos}
    edges: list[DependencyEdge] = []
    for raw in data.get("dependency_edges", []) or []:
        from_repo = str(raw["from"])
        to_repo = str(raw["to"])
        # Edge endpoints must reference declared repos (Test: edge to unknown repo errors).
        for endpoint, label in ((from_repo, "from"), (to_repo, "to")):
            if endpoint not in repo_names:
                raise ConfigError(
                    f"Dependency edge {label!r} endpoint {endpoint!r} "
                    f"(edge {from_repo} -> {to_repo}) does not reference a declared repo"
                )
        edges.append(DependencyEdge(from_repo=from_repo, to_repo=to_repo))
    if not repos:
        raise ConfigError("watchlist must declare at least one repo")
    return Watchlist(repos=repos, dependency_edges=edges)


def parse_trend_scope(data: dict) -> TrendScope:
    topics = [str(t) for t in (data.get("topics") or [])]
    keywords = [str(k) for k in (data.get("keywords") or [])]
    if not topics and not keywords:
        raise ConfigError("trend_scope must declare at least one topic or keyword")
    return TrendScope(topics=topics, keywords=keywords)


def parse_delivery(data: dict) -> Delivery:
    try:
        return Delivery(
            state_db_path=str(data["state_db_path"]),
            artifact_root=str(data["artifact_root"]),
            token_max_age_days=int(data.get("token_max_age_days", 90)),
            digest_hour=int(data.get("digest_hour", 9)),
        )
    except KeyError as exc:
        raise ConfigError(f"delivery config missing required key: {exc}") from exc


def load_settings(
    config_dir: str,
    *,
    env: dict[str, str] | None = None,
    require_credentials: bool = True,
) -> Settings:
    """Load all three YAML files from ``config_dir`` and credentials from env.

    Looks for ``watchlist.yaml`` then ``watchlist.example.yaml`` (likewise for
    the others) so examples work out of the box in tests.
    """
    env = os.environ if env is None else env

    def _path(stem: str) -> str:
        primary = os.path.join(config_dir, f"{stem}.yaml")
        example = os.path.join(config_dir, f"{stem}.example.yaml")
        return primary if os.path.exists(primary) else example

    watchlist = parse_watchlist(_load_yaml(_path("watchlist")))
    trend_scope = parse_trend_scope(_load_yaml(_path("trend_scope")))
    delivery = parse_delivery(_load_yaml(_path("delivery")))

    credentials = Credentials.from_env(env) if require_credentials else None
    llm_base_url = env.get(ENV_LLM_BASE_URL)

    return Settings(
        watchlist=watchlist,
        trend_scope=trend_scope,
        delivery=delivery,
        llm_base_url=llm_base_url,
        credentials=credentials,
    )
