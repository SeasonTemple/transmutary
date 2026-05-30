"""Command-line entry point for repository promotion (F4, KTD-C).

``transmutary`` console_script exposing the agent-native promotion surface:

  * ``transmutary promote <owner/repo> [--source SRC]`` — add a mode-B candidate
    to the effective watchlist (persisted to ``promoted_repo``).
  * ``transmutary demote <owner/repo>`` — remove a promoted repo.
  * ``transmutary list-watchlist`` — print config repos + promoted repos, each
    annotated with its source.

The CLI is a SEPARATE process from the resident service: it only writes the
shared ``promoted_repo`` table. A running service's periodic reconcile job
(KTD-B) picks the change up without a restart. Promotion never touches
credentials, so settings are loaded with ``require_credentials=False`` — a
promote works with no credential env vars set.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence

from .config import ConfigError, Settings, load_settings
from .store.state import StateStore

# A repo identifier is ``owner/repo`` — exactly one slash, each side a
# conservative GitHub-ish slug. Rejecting scheme/URL forms (``https://...``) and
# bare names keeps a typo or a pasted URL from landing in the table.
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

DEFAULT_CONFIG_DIR = "config"
ENV_CONFIG_DIR = "TRANSMUTARY_CONFIG_DIR"


def _valid_repo(repo: str) -> bool:
    """True if ``repo`` is a well-formed ``owner/repo`` (no scheme, single slash)."""
    if "://" in repo:
        return False
    return bool(_REPO_RE.match(repo))


def _load(config_dir: str) -> Settings:
    """Load settings WITHOUT requiring credentials (promotion never needs them)."""
    return load_settings(config_dir, require_credentials=False)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transmutary",
        description="Transmutary repository promotion (mode-B candidate → mode-A watchlist).",
    )
    parser.add_argument(
        "--config-dir",
        default=os.environ.get(ENV_CONFIG_DIR, DEFAULT_CONFIG_DIR),
        help=f"config directory (default: ${ENV_CONFIG_DIR} or {DEFAULT_CONFIG_DIR!r})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_promote = sub.add_parser("promote", help="promote a repo into the watchlist")
    p_promote.add_argument("repo", help="repository as owner/repo")
    p_promote.add_argument("--source", default="mode-b", help="promotion source tag")

    p_demote = sub.add_parser("demote", help="remove a promoted repo")
    p_demote.add_argument("repo", help="repository as owner/repo")

    sub.add_parser("list-watchlist", help="list config + promoted repos")
    return parser


def _cmd_promote(settings: Settings, repo: str, source: str, out) -> int:
    if not _valid_repo(repo):
        print(f"error: invalid repo {repo!r}; expected owner/repo", file=sys.stderr)
        return 2
    with StateStore(settings.delivery.state_db_path) as store:
        store.promote_repo(repo, source=source)
    print(f"promoted {repo} (source={source})", file=out)
    return 0


def _cmd_demote(settings: Settings, repo: str, out) -> int:
    if not _valid_repo(repo):
        print(f"error: invalid repo {repo!r}; expected owner/repo", file=sys.stderr)
        return 2
    with StateStore(settings.delivery.state_db_path) as store:
        store.demote_repo(repo)
    print(f"demoted {repo}", file=out)
    return 0


def _cmd_list(settings: Settings, out) -> int:
    config_repos = settings.watchlist.repo_names()
    with StateStore(settings.delivery.state_db_path) as store:
        promoted = store.list_promoted()
    promoted_set = set(promoted)
    for repo in config_repos:
        suffix = " (also promoted)" if repo in promoted_set else ""
        print(f"{repo}\tconfig{suffix}", file=out)
    for repo in promoted:
        if repo not in set(config_repos):
            print(f"{repo}\tpromoted", file=out)
    return 0


def main(argv: Sequence[str] | None = None, *, out=None) -> int:
    """Parse args, dispatch, and return a process exit code.

    ``out`` is an injectable stdout stream (tests capture it); defaults to
    ``sys.stdout``. Invalid repo names / config errors return non-zero WITHOUT
    writing the table.
    """
    out = sys.stdout if out is None else out
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Validate the repo BEFORE loading config / opening the DB so a bad name never
    # touches the store (constraint: invalid repo → non-zero exit, no write).
    if args.command in ("promote", "demote") and not _valid_repo(args.repo):
        print(f"error: invalid repo {args.repo!r}; expected owner/repo", file=sys.stderr)
        return 2

    try:
        settings = _load(args.config_dir)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.command == "promote":
        return _cmd_promote(settings, args.repo, args.source, out)
    if args.command == "demote":
        return _cmd_demote(settings, args.repo, out)
    if args.command == "list-watchlist":
        return _cmd_list(settings, out)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover - argparse guards
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
