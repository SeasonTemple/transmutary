"""Shared report data schema (KTD1).

Zero dependencies — stdlib dataclasses only. Both pipeline modes (A diagnose /
B explain) share this single ``Report`` structure. Downstream units (artifacts,
deliver, diagnose, explain) import from here.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any


class ReportKind(str, enum.Enum):
    """Which pipeline produced the report."""

    DIAGNOSE = "diagnose"  # mode A — event-driven sourcing diagnosis
    EXPLAIN = "explain"  # mode B — trend radar explanation


class Severity(str, enum.Enum):
    """Routing-relevant severity. Drives the inline two-branch delivery route."""

    CRITICAL = "critical"  # high-risk → immediate path
    HIGH = "high"  # high-risk → immediate path
    NORMAL = "normal"  # low-priority → digest path
    INFO = "info"  # low-priority → digest path

    @property
    def is_urgent(self) -> bool:
        return self in (Severity.CRITICAL, Severity.HIGH)


@dataclass(frozen=True)
class Source:
    """A single evidence source line (R18b: source_id + url + fetched_at)."""

    source_id: str
    url: str
    fetched_at: str  # ISO-8601 timestamp string

    def to_dict(self) -> dict[str, str]:
        return {"source_id": self.source_id, "url": self.url, "fetched_at": self.fetched_at}

    @classmethod
    def from_dict(cls, d: dict[str, str]) -> Source:
        return cls(source_id=d["source_id"], url=d["url"], fetched_at=d["fetched_at"])


@dataclass
class Report:
    """The shared, deliverable report structure (R12, KTD1).

    The ``sources`` section has a fixed structure across both modes.
    ``body_md_zh`` holds the Chinese translation when available (R6).
    """

    kind: ReportKind
    repo: str
    title: str
    body_md: str
    severity: Severity
    created_at: str  # ISO-8601 timestamp string
    sources: list[Source] = field(default_factory=list)
    body_md_zh: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Explicit field construction — avoids ``asdict`` leaking unintended fields."""
        return {
            "kind": self.kind.value,
            "repo": self.repo,
            "title": self.title,
            "body_md": self.body_md,
            "body_md_zh": self.body_md_zh,
            "severity": self.severity.value,
            "created_at": self.created_at,
            "sources": [s.to_dict() for s in self.sources],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Report:
        return cls(
            kind=ReportKind(d["kind"]),
            repo=d["repo"],
            title=d["title"],
            body_md=d["body_md"],
            severity=Severity(d["severity"]),
            created_at=d["created_at"],
            sources=[Source.from_dict(s) for s in d.get("sources", [])],
            body_md_zh=d.get("body_md_zh"),
        )
