"""U2 SQLite state store tests — CRUD, rolling window, perms, credential scrub."""

from __future__ import annotations

import os
import stat

import pytest

from transmutary.store.state import (
    SEEN_SET_WINDOW_SECONDS,
    StatePermissionError,
    StateStore,
    scrub_credentials,
)


@pytest.fixture
def store():
    s = StateStore(":memory:")
    yield s
    s.close()


def test_tables_created(store):
    expected = {
        "event_fingerprint",
        "star_snapshot",
        "issue_baseline",
        "seen_set",
        "subscriber_token",
    }
    cur = store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in cur.fetchall()}
    assert expected <= names


def test_fingerprint_crud_and_upsert(store):
    assert store.upsert_fingerprint("tag-v1.2.3", "a/b", "release") == 1
    # Duplicate fingerprint → evidence_count++, no new row.
    assert store.upsert_fingerprint("tag-v1.2.3", "a/b", "release") == 2
    assert store.upsert_fingerprint("tag-v1.2.3", "a/b", "release") == 3
    row = store.get_fingerprint("tag-v1.2.3")
    assert row["evidence_count"] == 3
    cur = store._conn.execute("SELECT COUNT(*) c FROM event_fingerprint")
    assert cur.fetchone()["c"] == 1


def test_fingerprint_escalation(store):
    store.upsert_fingerprint("issue-bucket-x", "a/b", "issue")
    store.upsert_fingerprint("issue-bucket-x", "a/b", "issue", escalate=True)
    assert store.get_fingerprint("issue-bucket-x")["escalated"] == 1


def test_star_snapshot_ordered_diff(store):
    store.add_star_snapshot("a/b", 100, ts=1000.0)
    store.add_star_snapshot("a/b", 150, ts=2000.0)
    store.add_star_snapshot("a/b", 130, ts=1500.0)
    snaps = store.get_star_snapshots("a/b")
    assert [s.ts for s in snaps] == [1000.0, 1500.0, 2000.0]
    assert snaps[-1].stargazers - snaps[0].stargazers == 50


def test_issue_baseline_crud(store):
    store.set_issue_baseline("a/b", rate=2.5, window_secs=3600)
    row = store.get_issue_baseline("a/b")
    assert row["rate"] == 2.5
    store.set_issue_baseline("a/b", rate=4.0, window_secs=3600)
    assert store.get_issue_baseline("a/b")["rate"] == 4.0


def test_seen_set_rolling_window(store):
    now = 1_000_000.0
    store.mark_seen("inwindow", source="x", ts=now - 100)
    store.mark_seen("old", source="x", ts=now - SEEN_SET_WINDOW_SECONDS - 100)
    purged = store.purge_seen_set(now=now)
    assert purged == 1
    assert store.has_seen("inwindow")
    assert not store.has_seen("old")


def test_seen_set_day8_reappearance_is_new(store):
    """Boundary: a hash purged after the window can be re-marked as new (residual risk)."""
    now = 1_000_000.0
    store.mark_seen("h", ts=now - SEEN_SET_WINDOW_SECONDS - 1)
    store.purge_seen_set(now=now)
    assert not store.has_seen("h")
    assert store.mark_seen("h", ts=now) is True  # treated as new


def test_seen_set_duplicate_not_reinserted(store):
    assert store.mark_seen("dup") is True
    assert store.mark_seen("dup") is False


def test_subscriber_token_crud_revoke(store):
    store.add_subscriber_token("th1", "alice", expires_at=9_999_999_999.0)
    tok = store.get_subscriber_token("th1")
    assert tok.subscriber == "alice"
    assert tok.revoked is False
    store.revoke_subscriber_token("th1")
    assert store.get_subscriber_token("th1").revoked is True


# --- security: R21 credential scrubbing ---

CRED_SAMPLES = [
    "ghp_abcdefghijklmnop1234567890ABCD",
    "sk-abcdefghijklmnop1234567890",
    "Authorization: Bearer abcdef123456789",
    "password=hunter2supersecret",
]


def test_scrub_function_redacts():
    for cred in CRED_SAMPLES:
        body = f"HTTP 500 error: upstream returned {cred} in header dump"
        scrubbed = scrub_credentials(body)
        assert cred not in scrubbed
        assert "[REDACTED]" in scrubbed


def test_credentials_never_land_in_db(store):
    """Write an HTTP-error-shaped string containing credentials; assert none persist."""
    leaky = "fetch failed: ghp_abcdefghijklmnop1234567890ABCD Authorization: Bearer toplevelsecret"
    # via seen_set source and fingerprint id (both pass through scrub)
    store.mark_seen("hash-with-leak", source=leaky)
    store.upsert_fingerprint(leaky, "a/b", "issue")
    store.set_issue_baseline("a/b", 1.0, 60)
    all_text = " ".join(store.dump_all_text())
    for cred in ("ghp_abcdefghijklmnop1234567890ABCD", "toplevelsecret", "Bearer toplevelsecret"):
        assert cred not in all_text


# --- security: KTD5 file permission enforcement ---

def test_db_permission_too_wide_fails(tmp_path):
    db = tmp_path / "state.sqlite3"
    db.write_bytes(b"")
    os.chmod(db, 0o644)  # world-readable
    with pytest.raises(StatePermissionError):
        StateStore(str(db))


def test_db_created_with_0600(tmp_path):
    db = tmp_path / "sub" / "state.sqlite3"
    s = StateStore(str(db))
    try:
        mode = stat.S_IMODE(os.stat(db).st_mode)
        assert mode & 0o077 == 0  # no group/other bits
    finally:
        s.close()
