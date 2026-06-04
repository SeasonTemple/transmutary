#!/usr/bin/env python3
"""Release-note helper.

Transmutary versions are still computed by python-semantic-release. This helper
adds the missing human-curated part: every published tag needs a bilingual note
file under docs/release-notes/.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
ENGLISH_HEADING_RE = re.compile(r"^##\s+(English|EN)\s*$", re.IGNORECASE | re.MULTILINE)
CHINESE_HEADING_RE = re.compile(r"^##\s+(中文|Chinese|ZH|zh-CN)\s*$", re.IGNORECASE | re.MULTILINE)
PLACEHOLDER_RE = re.compile(
    r"<(?:"
    r"one-line theme|"
    r"YYYY-MM-DD|"
    r"one-line scope:[^>]+|"
    r"Area|"
    r"change summary|"
    r"why it matters[^>]*|"
    r"CLI/API/config/UI|"
    r"previous behavior|"
    r"new behavior|"
    r"upgrade action or none|"
    r"known caveat / deferred item / none"
    r")>"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_NOTES_DIR = REPO_ROOT / "docs" / "release-notes"


def normalize_tag(value: str) -> str:
    tag = value.strip()
    if not tag:
        raise ValueError("tag/version is required")
    if not tag.startswith("v"):
        tag = f"v{tag}"
    if not TAG_RE.match(tag):
        raise ValueError(f"invalid release tag: {value!r}; expected vX.Y.Z")
    return tag


def note_path_for(tag: str) -> Path:
    return RELEASE_NOTES_DIR / f"{normalize_tag(tag)}.md"


def _rel(path: Path) -> Path | str:
    """Repo-relative display path; falls back to the absolute path when the file
    lives outside the repo (e.g. a monkeypatched dir under test)."""
    try:
        return path.relative_to(REPO_ROOT)
    except ValueError:
        return path


def validate_bilingual_release_note(text: str) -> list[str]:
    failures: list[str] = []
    if not CHINESE_HEADING_RE.search(text):
        failures.append("missing Chinese release-note section: add `## 中文`")
    if not ENGLISH_HEADING_RE.search(text):
        failures.append("missing English release-note section: add `## English`")
    return failures


def render_stub(tag: str) -> str:
    tag = normalize_tag(tag)
    return f"""# {tag} - <one-line theme>

> <YYYY-MM-DD> - <one-line scope: PRs / user-facing changes / compatibility signals>

## 中文

### Highlights

| 维度 | 改动 | 为什么重要 |
|---|---|---|
| **<Area>** | <change summary> | <why it matters in 1-2 sentences> |

### Compatibility

| Surface | Before | After | Action |
|---|---|---|---|
| <CLI/API/config/UI> | <previous behavior> | <new behavior> | <upgrade action or none> |

### Verification

- `python -m pytest -q`
- `ruff check src tests tools`

### Notes

- <known caveat / deferred item / none>

---

## English

### Highlights

| Area | Change | Why it matters |
|---|---|---|
| **<Area>** | <change summary> | <why it matters in 1-2 sentences> |

### Compatibility

| Surface | Before | After | Action |
|---|---|---|---|
| <CLI/API/config/UI> | <previous behavior> | <new behavior> | <upgrade action or none> |

### Verification

- `python -m pytest -q`
- `ruff check src tests tools`

### Notes

- <known caveat / deferred item / none>
"""


def prepare(tag: str) -> Path:
    path = note_path_for(tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(render_stub(tag), encoding="utf-8")
    return path


def check(tag: str) -> list[str]:
    path = note_path_for(tag)
    failures: list[str] = []
    if not path.exists():
        return [f"missing release-note file: {path.relative_to(REPO_ROOT)}"]
    text = path.read_text(encoding="utf-8")
    failures.extend(validate_bilingual_release_note(text))
    if PLACEHOLDER_RE.search(text):
        failures.append(f"release-note file still contains template placeholders: {path}")
    return failures


def _cmd_prepare(args: argparse.Namespace) -> int:
    path = prepare(args.tag)
    print(path.relative_to(REPO_ROOT))
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    path = note_path_for(args.tag)
    # Missing file is tolerated under --allow-missing (CI keeps semantic-release's
    # auto-generated notes); a present-but-invalid file always fails (quality gate).
    if not path.exists():
        rel = _rel(path)
        if getattr(args, "allow_missing", False):
            print(
                f"release-notes: WARNING - no curated note for {args.tag} ({rel}); "
                "keeping auto-generated release notes",
                file=sys.stderr,
            )
            return 0
        print(f"release-notes: ERROR - missing release-note file: {rel}", file=sys.stderr)
        return 1
    failures = check(args.tag)
    if failures:
        for failure in failures:
            print(f"release-notes: ERROR - {failure}", file=sys.stderr)
        return 1
    print(f"release-notes: OK - {_rel(path)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare or validate bilingual release notes.")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare_parser = sub.add_parser(
        "prepare",
        help="create docs/release-notes/vX.Y.Z.md if missing",
    )
    prepare_parser.add_argument("tag", help="release tag or version, e.g. v0.13.0")
    prepare_parser.set_defaults(func=_cmd_prepare)

    check_parser = sub.add_parser("check", help="validate docs/release-notes/vX.Y.Z.md")
    check_parser.add_argument("tag", help="release tag or version, e.g. v0.13.0")
    check_parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="treat a missing note file as a warning (exit 0), not an error; "
        "a present-but-invalid file still fails",
    )
    check_parser.set_defaults(func=_cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
