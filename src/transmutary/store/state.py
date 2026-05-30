"""SQLite state store (U2, R8/R9/R13/R20/R21, KTD5).

Tables: event_fingerprint, star_snapshot, issue_baseline, seen_set,
subscriber_token, collect_cursor. DB file is enforced 0600 at startup (KTD5).
All persisted text is scrubbed of credential patterns before write (R21):
credentials must NEVER land in the DB, even inside a captured HTTP error body.

Concurrency: a single serialized write connection + WAL journal so that
high-priority (security) tasks are not blocked by trend batch writes.
"""

from __future__ import annotations

import os
import re
import sqlite3
import stat
import threading
import time
from dataclasses import dataclass

# Rolling seen-set window (R8). day-8 reappearance is treated as a NEW event
# (known residual risk, documented in plan Risks).
SEEN_SET_WINDOW_SECONDS = 7 * 24 * 60 * 60

REQUIRED_DB_MODE = 0o600

# Patterns of credential-shaped substrings that must never persist (R21). This
# is defense-in-depth: callers should not pass credentials in, but if a captured
# HTTP error body echoes one back, we scrub before it can land in the DB.
_CREDENTIAL_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),  # GitHub PAT
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),  # fine-grained PAT
    re.compile(r"gho_[A-Za-z0-9]{16,}"),  # OAuth
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),  # OpenAI-style LLM key
    # Bearer must run before the generic key=value rule so it captures the token,
    # not just the word "Bearer".
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(authorization|api[_-]?key|token|password|secret)\s*[:=]\s*\S+"),
]

_REDACTION = "[REDACTED]"


def scrub_credentials(text: str | None) -> str | None:
    """Redact credential-shaped substrings from text before persistence (R21)."""
    if text is None:
        return None
    out = text
    for pat in _CREDENTIAL_PATTERNS:
        out = pat.sub(_REDACTION, out)
    return out


class StatePermissionError(Exception):
    """Raised when the DB file has permissions wider than 0600 (KTD5)."""


@dataclass
class StarSnapshot:
    repo: str
    stargazers: int
    ts: float


@dataclass
class SubscriberToken:
    token_hash: str
    subscriber: str
    revoked: bool
    expires_at: float | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_fingerprint (
    fingerprint    TEXT PRIMARY KEY,
    repo           TEXT NOT NULL,
    kind           TEXT NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    escalated      INTEGER NOT NULL DEFAULT 0,
    first_seen     REAL NOT NULL,
    last_seen      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS star_snapshot (
    repo       TEXT NOT NULL,
    stargazers INTEGER NOT NULL,
    ts         REAL NOT NULL,
    PRIMARY KEY (repo, ts)
);

CREATE TABLE IF NOT EXISTS issue_baseline (
    repo        TEXT PRIMARY KEY,
    rate        REAL NOT NULL,
    window_secs REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_set (
    hash       TEXT PRIMARY KEY,
    first_seen REAL NOT NULL,
    source     TEXT
);

CREATE TABLE IF NOT EXISTS subscriber_token (
    token_hash TEXT PRIMARY KEY,
    subscriber TEXT NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0,
    expires_at REAL
);

CREATE TABLE IF NOT EXISTS collect_cursor (
    repo       TEXT PRIMARY KEY,
    since      TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS promoted_repo (
    repo         TEXT PRIMARY KEY,
    source       TEXT,
    promoted_at  REAL NOT NULL
);
"""


def _ensure_db_permissions(path: str, *, create: bool) -> None:
    """Enforce 0600 on the DB file. Wider perms raise (KTD5 startup check)."""
    if not os.path.exists(path):
        if create:
            # Create with restrictive perms before sqlite opens it.
            fd = os.open(path, os.O_CREAT | os.O_RDWR, REQUIRED_DB_MODE)
            os.close(fd)
        else:
            return
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        raise StatePermissionError(
            f"State DB {path!r} has permissions {oct(mode)}; require 0600. "
            "Refusing to start (KTD5)."
        )


class StateStore:
    """Serialized-write SQLite state store.

    A single connection guarded by a lock serializes writes (concurrency
    strategy from plan Risks); WAL allows concurrent reads.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        is_memory = db_path == ":memory:" or db_path.startswith("file::memory:")
        if not is_memory and parent:
            os.makedirs(parent, exist_ok=True)
        if not is_memory:
            _ensure_db_permissions(db_path, create=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            db_path, check_same_thread=False, uri=db_path.startswith("file:")
        )
        self._conn.row_factory = sqlite3.Row
        if not is_memory:
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
        self._init_schema()
        if not is_memory:
            # Re-check after sqlite may have touched the file.
            _ensure_db_permissions(db_path, create=False)

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # event_fingerprint
    # ------------------------------------------------------------------
    def upsert_fingerprint(
        self, fingerprint: str, repo: str, kind: str, *, escalate: bool = False
    ) -> int:
        """Insert a new fingerprint or bump evidence_count on an existing one.

        Returns the resulting evidence_count. Does NOT create duplicate rows.
        """
        fingerprint = scrub_credentials(fingerprint)
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "SELECT evidence_count FROM event_fingerprint WHERE fingerprint=?",
                (fingerprint,),
            )
            row = cur.fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO event_fingerprint "
                    "(fingerprint, repo, kind, evidence_count, escalated, first_seen, last_seen) "
                    "VALUES (?, ?, ?, 1, ?, ?, ?)",
                    (fingerprint, repo, kind, 1 if escalate else 0, now, now),
                )
                self._conn.commit()
                return 1
            new_count = row["evidence_count"] + 1
            self._conn.execute(
                "UPDATE event_fingerprint SET evidence_count=?, last_seen=?, "
                "escalated=MAX(escalated, ?) WHERE fingerprint=?",
                (new_count, now, 1 if escalate else 0, fingerprint),
            )
            self._conn.commit()
            return new_count

    def get_fingerprint(self, fingerprint: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM event_fingerprint WHERE fingerprint=?", (fingerprint,)
            )
            return cur.fetchone()

    # ------------------------------------------------------------------
    # star_snapshot
    # ------------------------------------------------------------------
    def add_star_snapshot(self, repo: str, stargazers: int, ts: float | None = None) -> None:
        ts = time.time() if ts is None else ts
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO star_snapshot (repo, stargazers, ts) VALUES (?, ?, ?)",
                (repo, int(stargazers), float(ts)),
            )
            self._conn.commit()

    def get_star_snapshots(self, repo: str) -> list[StarSnapshot]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT repo, stargazers, ts FROM star_snapshot WHERE repo=? ORDER BY ts ASC",
                (repo,),
            )
            return [StarSnapshot(r["repo"], r["stargazers"], r["ts"]) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # issue_baseline
    # ------------------------------------------------------------------
    def set_issue_baseline(self, repo: str, rate: float, window_secs: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO issue_baseline (repo, rate, window_secs, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (repo, float(rate), float(window_secs), time.time()),
            )
            self._conn.commit()

    def get_issue_baseline(self, repo: str) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM issue_baseline WHERE repo=?", (repo,))
            return cur.fetchone()

    # ------------------------------------------------------------------
    # collect_cursor (U3 — persisted incremental ``since`` cursor, AE4)
    # ------------------------------------------------------------------
    def get_cursor(self, repo: str) -> str | None:
        """Return the persisted ``since`` cursor for ``repo`` (None if unseen).

        The cursor survives process restarts so an issue surge already reported in
        a prior process is not re-collected and re-diagnosed after a restart (U3).
        """
        with self._lock:
            cur = self._conn.execute("SELECT since FROM collect_cursor WHERE repo=?", (repo,))
            row = cur.fetchone()
        return row["since"] if row is not None else None

    def set_cursor(self, repo: str, since: str | None) -> None:
        """Persist the ``since`` cursor for ``repo`` (advanced, never rewound by U3)."""
        since = scrub_credentials(since)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO collect_cursor (repo, since, updated_at) "
                "VALUES (?, ?, ?)",
                (repo, since, time.time()),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # seen_set (L1 dedup, rolling 7d)
    # ------------------------------------------------------------------
    def has_seen(self, hash_: str) -> bool:
        with self._lock:
            cur = self._conn.execute("SELECT 1 FROM seen_set WHERE hash=?", (hash_,))
            return cur.fetchone() is not None

    def mark_seen(self, hash_: str, source: str | None = None, ts: float | None = None) -> bool:
        """Mark hash as seen. Returns True if newly inserted, False if already present."""
        ts = time.time() if ts is None else ts
        source = scrub_credentials(source)
        with self._lock:
            if self.has_seen(hash_):
                return False
            self._conn.execute(
                "INSERT INTO seen_set (hash, first_seen, source) VALUES (?, ?, ?)",
                (hash_, float(ts), source),
            )
            self._conn.commit()
            return True

    def purge_seen_set(self, now: float | None = None) -> int:
        """Remove seen-set entries older than the rolling window. Returns count purged."""
        now = time.time() if now is None else now
        cutoff = now - SEEN_SET_WINDOW_SECONDS
        with self._lock:
            cur = self._conn.execute("DELETE FROM seen_set WHERE first_seen < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------
    # subscriber_token (R20 — used by U15 in Phase 1)
    # ------------------------------------------------------------------
    def add_subscriber_token(
        self, token_hash: str, subscriber: str, expires_at: float | None = None
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO subscriber_token "
                "(token_hash, subscriber, revoked, expires_at) VALUES (?, ?, 0, ?)",
                (token_hash, subscriber, expires_at),
            )
            self._conn.commit()

    def revoke_subscriber_token(self, token_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE subscriber_token SET revoked=1 WHERE token_hash=?", (token_hash,)
            )
            self._conn.commit()

    def get_subscriber_token(self, token_hash: str) -> SubscriberToken | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM subscriber_token WHERE token_hash=?", (token_hash,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        return SubscriberToken(
            token_hash=row["token_hash"],
            subscriber=row["subscriber"],
            revoked=bool(row["revoked"]),
            expires_at=row["expires_at"],
        )

    # ------------------------------------------------------------------
    # promoted_repo (F4 — mode-B candidate promoted into the mode-A watchlist)
    # ------------------------------------------------------------------
    def promote_repo(self, repo: str, source: str = "mode-b") -> None:
        """Persist ``repo`` as promoted (INSERT OR REPLACE → idempotent, no dup rows).

        The promoted set is the cross-process bridge (KTD-B): the CLI writes here
        and a resident service's reconcile job picks it up.
        """
        source = scrub_credentials(source)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO promoted_repo (repo, source, promoted_at) "
                "VALUES (?, ?, ?)",
                (repo, source, time.time()),
            )
            self._conn.commit()

    def demote_repo(self, repo: str) -> None:
        """Remove ``repo`` from the promoted set (no error if it is not present)."""
        with self._lock:
            self._conn.execute("DELETE FROM promoted_repo WHERE repo=?", (repo,))
            self._conn.commit()

    def list_promoted(self) -> list[str]:
        """Return all promoted repo names in deterministic (sorted) order."""
        with self._lock:
            cur = self._conn.execute("SELECT repo FROM promoted_repo ORDER BY repo ASC")
            return [r["repo"] for r in cur.fetchall()]

    def is_promoted(self, repo: str) -> bool:
        with self._lock:
            cur = self._conn.execute("SELECT 1 FROM promoted_repo WHERE repo=?", (repo,))
            return cur.fetchone() is not None

    # ------------------------------------------------------------------
    # diagnostics
    # ------------------------------------------------------------------
    def dump_all_text(self) -> list[str]:
        """Return every text-ish value stored across all tables (for tests)."""
        values: list[str] = []
        with self._lock:
            for table in (
                "event_fingerprint",
                "star_snapshot",
                "issue_baseline",
                "seen_set",
                "subscriber_token",
                "collect_cursor",
                "promoted_repo",
            ):
                cur = self._conn.execute(f"SELECT * FROM {table}")  # noqa: S608 - fixed names
                for row in cur.fetchall():
                    for val in tuple(row):
                        if isinstance(val, str):
                            values.append(val)
        return values
