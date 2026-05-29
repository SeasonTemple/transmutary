"""Report artifact storage (U3, R13/R18b/R24, KTD5).

Writes a ``Report`` to ``<artifact_root>/<repo>/<ts>-<kind>.md``. The repo name
is sanitized so it can NEVER escape the artifact root (a ``foo/bar`` repo maps to
a single nested-but-contained directory; ``..`` / absolute paths are rejected).
The artifact root is enforced 0700 at startup (KTD5).
"""

from __future__ import annotations

import os
import re
import stat
import time

from ..report.schema import Report

REQUIRED_DIR_MODE = 0o700


class ArtifactPermissionError(Exception):
    """Raised when the artifact root is wider than 0700 (KTD5/R24)."""


class ArtifactPathError(Exception):
    """Raised on a repo name that would escape the artifact root (R24)."""


# Characters allowed verbatim in a path segment; everything else is replaced.
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_repo(repo: str) -> str:
    """Map a repo name to a single safe, contained path segment.

    ``owner/name`` -> ``owner__name``. Rejects ``..`` and absolute paths so the
    result can never traverse out of the artifact root (R24).
    """
    if not repo or not repo.strip():
        raise ArtifactPathError("empty repo name")
    # Reject explicit traversal / absolute markers before normalization.
    if repo.startswith("/") or repo.startswith("\\"):
        raise ArtifactPathError(f"absolute repo path rejected: {repo!r}")
    # Collapse separators into a single segment join, then sanitize.
    parts = re.split(r"[\\/]+", repo.strip())
    for part in parts:
        if part in ("..", ".") :
            raise ArtifactPathError(f"path traversal segment rejected in {repo!r}")
    joined = "__".join(p for p in parts if p)
    safe = _SAFE_SEGMENT.sub("_", joined)
    safe = safe.strip("._") or "_"
    if not safe:
        raise ArtifactPathError(f"repo name sanitizes to empty: {repo!r}")
    return safe


def _ensure_dir_permissions(path: str) -> None:
    """Create dir with 0700 if missing; raise if existing dir is wider (KTD5)."""
    if not os.path.exists(path):
        os.makedirs(path, mode=REQUIRED_DIR_MODE, exist_ok=True)
        # makedirs honors umask for intermediates; force the leaf.
        os.chmod(path, REQUIRED_DIR_MODE)
        return
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        raise ArtifactPermissionError(
            f"Artifact dir {path!r} has permissions {oct(mode)}; require 0700 (R24)."
        )


def _render_markdown(report: Report) -> str:
    lines = [f"# {report.title}", "", f"- kind: {report.kind.value}",
             f"- repo: {report.repo}", f"- severity: {report.severity.value}",
             f"- created_at: {report.created_at}", "", report.body_md, "", "## Sources"]
    if report.sources:
        for s in report.sources:
            lines.append(f"- `{s.source_id}` {s.url} (fetched {s.fetched_at})")
    else:
        # R18b / U3 edge: no sources → mark as "待核实信号".
        lines.append("- 待核实信号 (no corroborating sources)")
    lines.append("")
    return "\n".join(lines)


class ArtifactStore:
    def __init__(self, artifact_root: str) -> None:
        self.artifact_root = os.path.abspath(artifact_root)
        _ensure_dir_permissions(self.artifact_root)

    def repo_dir(self, repo: str) -> str:
        safe = sanitize_repo(repo)
        path = os.path.join(self.artifact_root, safe)
        # Containment guard: realpath must stay under the root.
        resolved = os.path.realpath(path)
        root_resolved = os.path.realpath(self.artifact_root)
        if resolved != root_resolved and not resolved.startswith(root_resolved + os.sep):
            raise ArtifactPathError(f"repo {repo!r} would escape artifact root")
        return path

    def write(self, report: Report, *, ts: float | None = None) -> str:
        """Write ``report`` to its repo dir; return the file path."""
        ts = time.time() if ts is None else ts
        directory = self.repo_dir(report.repo)
        _ensure_dir_permissions(directory)
        fname = f"{int(ts)}-{report.kind.value}.md"
        path = os.path.join(directory, fname)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_render_markdown(report))
        os.chmod(path, 0o600)
        return path
