"""Audit code-review findings against current source.

The compound-engineering code-review skill generates an HTML report from
multi-agent reviewer output. Findings can become stale once the issues are
fixed in a follow-up commit. This script cross-checks a small set of known
shape-checks against the current tree and emits a JSON verdict the user can
diff against the report before treating it as authoritative.

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


# Each claim: id, title, file, regex (must match for "stale"), why.
# The regex searches the file text; presence = the code has the shape the
# report's finding said was missing, so the report is stale.
CLAIMS = [
    {
        "id": "finding-06",
        "title": "_valid_repo regex has typo A-Za-z0-0 instead of A-Za-z0-9",
        "file": "dashboard/app.py",
        "stale_when": re.compile(r"_REPO_RE\s*=\s*re\.compile\(r['\"]?\^\[A-Za-z0-9"),
        "why": "Report claims the regex is `A-Za-z0-0`; if it is `A-Za-z0-9` the claim is stale.",
    },
    {
        "id": "finding-10",
        "title": "build_repo_runtime treats 'archived' as demotable when it should not",
        "file": "dashboard/data.py",
        "stale_when": re.compile(
            r"demotable=watchlist\.get\([^)]*\)\s+not\s+in\s+\(\s*None\s*,\s*['\"]config['\"]\s*,\s*['\"]archived['\"]"
        ),
        "why": "Report claims archived shows demotable=True; code excludes 'archived'.",
    },
    {
        "id": "finding-12",
        "title": "_embedding_index uses bare except (type-safety hole)",
        "file": "llm.py",
        "stale_when": re.compile(
            r"def _embedding_index\([^)]*\):.*?except\s*\(\s*AttributeError\s*,\s*KeyError\s*,\s*TypeError\s*,\s*ValueError\s*\)",
            re.DOTALL,
        ),
        "why": "Report claims bare except; code already narrows to the 4 expected types.",
    },
    {
        "id": "finding-01-p0",
        "title": "_merge_intersecting drops groups (P0 chain bug)",
        "file": "dedup.py",
        "stale_when": re.compile(
            r"def _merge_intersecting\([^)]*\):[\s\S]{0,400}?rest\.append\(current\)[\s\S]{0,80}?merged\s*=\s*rest"
        ),
        "why": "Report claims the iterative merge appends `current` multiple times; "
        "the on-disk version rebuilds `merged` from `rest` each outer iteration "
        "and appends `current` exactly once. The regression test in "
        "tests/test_dedup.py pins this; if the file still has the safe shape, "
        "the P0 is a false positive.",
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
        is_stale = bool(claim["stale_when"].search(text))
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
