"""Audit code-review findings against current source.

The compound-engineering code-review skill generates an HTML report from
multi-agent reviewer output. Findings can become stale once the issues are
fixed in a follow-up commit. This script cross-checks a small set of known
shape-checks against the current tree and emits a JSON verdict the user can
diff against the report before treating it as authoritative.

Each entry in :data:`CLAIMS` declares a structural check (a function the
auditor runs against the source) rather than a single regex. Multi-clause
shapes (e.g. several distinct ``except`` blocks) and constructs with nested
parens are not robust to regex matching; predicates are clearer and easier
to extend.

Run: ``uv run python tools/audit_stale_claims.py``
Output: ``{"stale": [...], "ok": [...], "errors": [...]}`` on stdout.
Exit code 0 always (informational).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "transmutary"


def _read(rel_path: str) -> str:
    return (SRC / rel_path).read_text(encoding="utf-8")


def _function_body(text: str, name: str) -> str | None:
    """Return the body of the first top-level ``def name(...):`` in ``text``.

    The slice is from the first non-space character after the signature's
    closing colon up to the next top-level ``def`` / ``class`` (or EOF).
    Indent-based slicing is fine here because all our targets are
    module-level definitions.
    """
    m = re.search(rf"^def {re.escape(name)}\([^)]*\)[^:]*:\n", text, flags=re.MULTILINE)
    if not m:
        return None
    rest = text[m.end():]
    # Walk lines; collect until a non-empty line that is not indented.
    lines: list[str] = []
    for line in rest.splitlines(keepends=True):
        if line.strip() and not line.startswith((" ", "\t")):
            break
        lines.append(line)
    return "".join(lines)


def _check_valid_repo(text: str) -> bool:
    """Stale when the regex literal is ``A-Za-z0-9`` (not the reported ``0-0`` typo)."""
    return bool(re.search(r"_REPO_RE\s*=\s*re\.compile\(r['\"]?\^\[A-Za-z0-9", text))


def _check_demotable_archived(text: str) -> bool:
    """Stale when ``demotable=`` explicitly excludes ``"archived"``."""
    return bool(
        re.search(
            r"demotable\s*=\s*watchlist\.get\([^)]*\)\s+not\s+in\s+\("
            r"\s*None\s*,\s*['\"]config['\"]\s*,\s*['\"]archived['\"]",
            text,
        )
    )


def _check_embedding_index_narrow_excepts(text: str) -> bool:
    """Stale when ``_embedding_index`` has typed excepts (no bare ``except:``).

    The original finding claimed a bare ``except``. The on-disk version
    splits narrowing across three separate ``except`` clauses (one for
    ``AttributeError``, one for ``(KeyError, TypeError)``, one for
    ``(TypeError, ValueError)``), plus a top-level ``int(raw)`` conversion
    with its own typed guard. The check: function body contains
    ``AttributeError`` AND ``KeyError`` AND ``TypeError`` AND ``ValueError``
    in ``except`` clauses AND no bare ``except:``.
    """
    body = _function_body(text, "_embedding_index")
    if body is None:
        return False
    if re.search(r"^\s*except\s*:\s*$", body, flags=re.MULTILINE):
        # Bare except present — original finding was right, NOT stale.
        return False
    # Each expected exception name must appear inside an except clause.
    required = ("AttributeError", "KeyError", "TypeError", "ValueError")
    excepts = re.findall(r"except[^\n]*", body)
    haystack = "\n".join(excepts)
    return all(name in haystack for name in required)


def _check_merge_intersecting_chain(text: str) -> bool:
    """Stale when the iterative merge has the safe shape.

    The P0 report claimed ``rest.append(current)`` runs multiple times per
    outer iteration. The on-disk version rebuilds ``merged`` from
    ``rest`` after each outer iteration, so ``current`` is appended
    exactly once. A future refactor that breaks the pairing (e.g. a
    union-find rewrite that drops a node) would NOT match this check.
    """
    body = _function_body(text, "_merge_intersecting")
    if body is None:
        return False
    if not re.search(r"rest\.append\(current\)", body):
        return False
    if not re.search(r"merged\s*=\s*rest", body):
        return False
    # Sanity: ``rest`` must be reassigned from a fresh list each outer
    # iteration. Look for ``rest: list[set[str]] = []`` inside the body.
    if not re.search(r"rest\s*:\s*list\[set\[str\]\]\s*=\s*\[\]", body):
        return False
    return True


def _check_settings_repr_redaction(_text: str) -> bool:
    """Stale when ``Settings.__repr__`` carries the endpoint but not secrets.

    The report flagged that ``Settings.__repr__`` includes
    ``llm_base_url`` and called this "may leak endpoint identity".
    Stale because:

      1. The endpoint is provider config, not a secret — LiteLLM uses it
         as the OpenAI-compatible base URL for whichever model provider
         the deployment points at; the value is not a credential.
      2. The :class:`Settings` dataclass declares
         ``credentials: Credentials | None = field(default=None, repr=False, compare=False)``
         — secrets are EXPLICITLY excluded from repr.
      3. Credentials are read from env at load time and held in a
         ``_Secret`` container whose ``__repr__`` / ``__str__`` also
         refuse to print the value.

    Stale when: building a Settings, repr(settings) does not contain any
    substring of the known credential env vars, and the credentials field
    is declared with repr=False. The endpoint MAY be in repr — that is
    intentional, not a leak.
    """
    from transmutary.config import Credentials, Settings, Watchlist, TrendScope, Delivery  # noqa: F401
    import os
    import re as _re

    src = (SRC / "config.py").read_text(encoding="utf-8")
    if not _re.search(
        r"credentials\s*:\s*Credentials[^=]*=\s*field\([^)]*repr\s*=\s*False",
        src,
    ):
        return False

    # Build a minimal Settings and assert repr excludes every known secret.
    s = Settings(
        watchlist=Watchlist(repos=("acme/cli",), dependency_edges=()),
        trend_scope=TrendScope(topics=(), keywords=()),
        delivery=Delivery(
            state_db_path="/tmp/state.db",
            artifact_root="/tmp/artifacts",
            token_max_age_days=30,
            digest_hour=8,
        ),
        llm_base_url="https://api.example.invalid/v1",
    )
    rendered = repr(s)
    secret_markers = (
        "ghs_",  # GitHub PAT prefix
        "xoxb-",  # Slack token
        "smtp-password-marker",
    )
    for marker in secret_markers:
        if marker in rendered:
            return False

    # And confirm the explicit endpoint IS present (the design intent).
    if "api.example.invalid" not in rendered:
        return False

    # Also: a Credentials instance repr must not echo a known secret value.
    creds = Credentials(
        github_token="ghs_DEADBEEFdeadbeef",
        smtp_user="user",
        smtp_password="smtp-password-marker",
        rss_token="rss-token-marker",
        llm_api_key="sk-llm-key-marker",
    )
    creds_repr = repr(creds)
    for marker in ("ghs_DEADBEEF", "smtp-password-marker", "rss-token-marker", "sk-llm-key-marker"):
        if marker in creds_repr:
            return False
    return True


# Each claim: id, title, file, predicate (str) -> bool, why.
# A predicate returns True when the report's claim is STALE (i.e. the
# code shape the report said was missing IS in fact present).
CLAIMS = [
    {
        "id": "finding-06",
        "title": "_valid_repo regex has typo A-Za-z0-0 instead of A-Za-z0-9",
        "file": "dashboard/app.py",
        "check": _check_valid_repo,
        "why": "Report claims the regex is `A-Za-z0-0`; if it is `A-Za-z0-9` the claim is stale.",
    },
    {
        "id": "finding-10",
        "title": "build_repo_runtime treats 'archived' as demotable when it should not",
        "file": "dashboard/data.py",
        "check": _check_demotable_archived,
        "why": "Report claims archived shows demotable=True; code excludes 'archived'.",
    },
    {
        "id": "finding-12",
        "title": "_embedding_index uses bare except (type-safety hole)",
        "file": "llm.py",
        "check": _check_embedding_index_narrow_excepts,
        "why": "Report claims bare except; code already narrows to the 4 expected types across 3 except clauses.",
    },
    {
        "id": "finding-01-p0",
        "title": "_merge_intersecting drops groups (P0 chain bug)",
        "file": "dedup.py",
        "check": _check_merge_intersecting_chain,
        "why": "Report claims the iterative merge appends `current` multiple times; "
        "the on-disk version rebuilds `merged` from `rest` each outer iteration "
        "and appends `current` exactly once. tests/test_dedup.py pins this.",
    },
    {
        "id": "finding-18",
        "title": "Settings.__repr__ includes llm_base_url — endpoint identity leak",
        "file": "config.py",
        "check": _check_settings_repr_redaction,
        "why": "Report flagged llm_base_url in Settings.__repr__ as 'may leak endpoint identity'. "
        "Stale: endpoint is provider config (LiteLLM OpenAI-compat base URL), not a credential. "
        "The Settings dataclass explicitly sets credentials field with repr=False, and "
        "Credentials holds every secret in a _Secret container whose repr refuses to print. "
        "The endpoint is in repr by design — useful for debugging which provider a deployment "
        "is configured against.",
    },
]


def audit() -> dict:
    stale: list[dict] = []
    ok: list[dict] = []
    errors: list[dict] = []
    for claim in CLAIMS:
        try:
            text = _read(claim["file"])
        except FileNotFoundError as exc:
            errors.append({"id": claim["id"], "error": str(exc)})
            continue
        try:
            is_stale = bool(claim["check"](text))
        except Exception as exc:  # pragma: no cover - defensive
            errors.append({"id": claim["id"], "error": f"check raised: {exc!r}"})
            continue
        verdict = {
            "id": claim["id"],
            "title": claim["title"],
            "file": claim["file"],
            "stale": is_stale,
            "why": claim["why"],
        }
        (stale if is_stale else ok).append(verdict)
    return {"stale": stale, "ok": ok, "errors": errors}


def main() -> int:
    result = audit()
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    print(
        f"\n# {len(result['stale'])} stale / {len(result['ok'])} still-valid / "
        f"{len(result['errors'])} error(s)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
