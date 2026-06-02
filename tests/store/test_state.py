"""U2 SQLite state store tests — CRUD, rolling window, perms, credential scrub."""

from __future__ import annotations

import os
import stat

import pytest

import transmutary.store.state as state_mod
from transmutary.store.state import (
    SEEN_SET_WINDOW_SECONDS,
    StatePermissionError,
    StateStore,
    scrub_credentials,
)

IS_WINDOWS = os.name == "nt"


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
        "promoted_repo",
        "admin_tracked_repo",
        "admin_dependency_edge",
        "admin_trend_topic",
        "admin_trend_keyword",
        "admin_delivery_preference",
    }
    cur = store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in cur.fetchall()}
    assert expected <= names


def test_busy_timeout_ms_sets_sqlite_pragma(tmp_path):
    db = tmp_path / "state.sqlite"
    store = StateStore(str(db), busy_timeout_ms=3000)
    try:
        cur = store._conn.execute("PRAGMA busy_timeout")
        assert cur.fetchone()[0] == 3000
    finally:
        store.close()


def test_rw_store_reopens_existing_wal_database(tmp_path):
    db = tmp_path / "state.sqlite"
    first = StateStore(str(db))
    try:
        first.promote_repo("seed/repo")
    finally:
        first.close()

    second = StateStore(str(db), busy_timeout_ms=3000)
    try:
        assert second.is_promoted("seed/repo") is True
        second.promote_repo("hot/repo")
        assert second.is_promoted("hot/repo") is True
    finally:
        second.close()


def test_rw_store_hardens_newly_created_db_parent_directory(monkeypatch, tmp_path):
    calls = []

    def fake_chmod_private(path, mode, *, inherited_ok=False):
        calls.append((path, mode, inherited_ok))

    monkeypatch.setattr(state_mod, "chmod_private", fake_chmod_private)
    db = tmp_path / "state" / "state.sqlite3"
    store = StateStore(str(db))
    try:
        assert (os.path.abspath(str(db.parent)), 0o700, False) in calls
    finally:
        store.close()


def test_rw_store_does_not_harden_existing_db_parent_directory(monkeypatch, tmp_path):
    calls = []

    def fake_chmod_private(path, mode, *, inherited_ok=False):
        calls.append((os.path.abspath(path), mode, inherited_ok))

    monkeypatch.setattr(state_mod, "chmod_private", fake_chmod_private)
    db = tmp_path / "state.sqlite3"
    store = StateStore(str(db))
    try:
        assert (os.path.abspath(str(tmp_path)), 0o700, False) not in calls
    finally:
        store.close()


def test_rw_store_hardens_existing_db_file_on_windows(monkeypatch, tmp_path):
    calls = []
    db = tmp_path / "state" / "state.sqlite3"
    db.parent.mkdir()
    db.write_bytes(b"")

    def fake_chmod_private(path, mode, *, inherited_ok=False):
        calls.append((os.path.abspath(path), mode, inherited_ok))

    monkeypatch.setattr(state_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(state_mod, "chmod_private", fake_chmod_private)

    store = StateStore(str(db))
    try:
        assert (os.path.abspath(str(db)), 0o600, False) in calls
        assert (os.path.abspath(str(db.parent)), 0o700, False) not in calls
    finally:
        store.close()


def test_read_only_store_does_not_harden_acl(monkeypatch, tmp_path):
    db = tmp_path / "state.sqlite3"
    store = StateStore(str(db))
    store.close()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("read-only open must not mutate permissions")

    monkeypatch.setattr(state_mod, "chmod_private", fail_if_called)
    monkeypatch.setattr(state_mod, "enforce_private_mode", fail_if_called)

    ro = StateStore(str(db), read_only=True)
    ro.close()


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


# --- F4: promoted_repo CRUD ---


def test_promote_lists_and_is_idempotent(store):
    store.promote_repo("acme/cli")
    assert store.list_promoted() == ["acme/cli"]
    # Repeat promote → idempotent, no duplicate row.
    store.promote_repo("acme/cli")
    assert store.list_promoted() == ["acme/cli"]
    cur = store._conn.execute("SELECT COUNT(*) c FROM promoted_repo")
    assert cur.fetchone()["c"] == 1


def test_demote_removes_and_missing_is_noop(store):
    store.promote_repo("acme/cli")
    store.demote_repo("acme/cli")
    assert store.list_promoted() == []
    # Demoting a repo that is not present must not raise.
    store.demote_repo("never/promoted")
    assert store.list_promoted() == []


def test_is_promoted_true_and_false(store):
    assert store.is_promoted("acme/cli") is False
    store.promote_repo("acme/cli")
    assert store.is_promoted("acme/cli") is True
    assert store.is_promoted("other/repo") is False


def test_list_promoted_is_sorted(store):
    for r in ("z/last", "a/first", "m/mid"):
        store.promote_repo(r)
    assert store.list_promoted() == ["a/first", "m/mid", "z/last"]


def test_promote_records_source(store):
    store.promote_repo("acme/cli", source="mode-b")
    cur = store._conn.execute("SELECT source FROM promoted_repo WHERE repo=?", ("acme/cli",))
    assert cur.fetchone()["source"] == "mode-b"


# --- admin config overrides -------------------------------------------------


def test_admin_tracked_repo_crud_is_idempotent_and_sorted(store):
    store.add_admin_repo("z/last")
    store.add_admin_repo("a/first")
    store.add_admin_repo("z/last")
    assert store.list_admin_repos() == ["a/first", "z/last"]
    cur = store._conn.execute("SELECT COUNT(*) c FROM admin_tracked_repo")
    assert cur.fetchone()["c"] == 2

    store.remove_admin_repo("z/last")
    store.remove_admin_repo("missing/repo")
    assert store.list_admin_repos() == ["a/first"]


def test_admin_dependency_edge_crud_is_idempotent_and_sorted(store):
    store.add_admin_dependency_edge("z/app", "a/lib")
    store.add_admin_dependency_edge("a/app", "b/lib")
    store.add_admin_dependency_edge("z/app", "a/lib")
    assert [(e.from_repo, e.to_repo) for e in store.list_admin_dependency_edges()] == [
        ("a/app", "b/lib"),
        ("z/app", "a/lib"),
    ]
    cur = store._conn.execute("SELECT COUNT(*) c FROM admin_dependency_edge")
    assert cur.fetchone()["c"] == 2

    store.remove_admin_dependency_edge("z/app", "a/lib")
    assert [(e.from_repo, e.to_repo) for e in store.list_admin_dependency_edges()] == [
        ("a/app", "b/lib"),
    ]


def test_admin_trend_scope_replaces_values_with_stable_order(store):
    store.set_admin_trend_scope(
        topics=["llm", "agent", "llm"],
        keywords=["rag", "evals", "rag"],
    )
    assert store.list_admin_trend_topics() == ["agent", "llm"]
    assert store.list_admin_trend_keywords() == ["evals", "rag"]

    store.set_admin_trend_scope(topics=["security"], keywords=[])
    assert store.list_admin_trend_topics() == ["security"]
    assert store.list_admin_trend_keywords() == []


def test_admin_delivery_preferences_partial_and_replace(store):
    assert store.get_admin_delivery_preferences().email_recipients is None
    assert store.get_admin_delivery_preferences().digest_hour is None

    store.set_admin_delivery_preferences(
        email_recipients=["b@example.com", "a@example.com"],
        digest_hour=17,
    )
    prefs = store.get_admin_delivery_preferences()
    assert prefs.email_recipients == ["b@example.com", "a@example.com"]
    assert prefs.digest_hour == 17

    store.set_admin_delivery_preferences(email_recipients=None, digest_hour=8)
    prefs = store.get_admin_delivery_preferences()
    assert prefs.email_recipients is None
    assert prefs.digest_hour == 8


def test_admin_config_scrubs_credential_shaped_values(store):
    store.add_admin_repo("safe/repo")
    store.add_admin_dependency_edge("safe/repo", "dep/repo")
    store.set_admin_trend_scope(
        topics=["ai", "password=hunter2supersecret"],
        keywords=["Authorization: Bearer toplevelsecret"],
    )
    store.set_admin_delivery_preferences(
        email_recipients=["ghp_abcdefghijklmnop1234567890ABCD@example.com"],
        digest_hour=9,
    )
    all_text = " ".join(store.dump_all_text())
    for cred in (
        "password=hunter2supersecret",
        "Bearer toplevelsecret",
        "ghp_abcdefghijklmnop1234567890ABCD",
    ):
        assert cred not in all_text
    assert "[REDACTED]" in all_text


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

@pytest.mark.skipif(IS_WINDOWS, reason="POSIX mode bits are not reliable on Windows")
def test_db_permission_too_wide_fails(tmp_path):
    db = tmp_path / "state.sqlite3"
    db.write_bytes(b"")
    os.chmod(db, 0o644)  # world-readable
    with pytest.raises(StatePermissionError):
        StateStore(str(db))


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX mode bits are not reliable on Windows")
def test_sidecar_permission_too_wide_fails_on_reopen(tmp_path):
    db = tmp_path / "state.sqlite3"
    s = StateStore(str(db))
    s.close()
    wal = str(db) + "-wal"
    with open(wal, "wb") as fh:
        fh.write(b"")
    os.chmod(wal, 0o644)

    with pytest.raises(StatePermissionError):
        StateStore(str(db))


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX mode bits are not reliable on Windows")
def test_sidecar_permission_too_wide_is_ignored_by_read_only_open(tmp_path):
    db = tmp_path / "state.sqlite3"
    s = StateStore(str(db))
    s.close()
    wal = str(db) + "-wal"
    with open(wal, "wb") as fh:
        fh.write(b"")
    os.chmod(wal, 0o644)

    ro = StateStore(str(db), read_only=True)
    ro.close()


def test_db_created_with_0600(tmp_path):
    db = tmp_path / "sub" / "state.sqlite3"
    s = StateStore(str(db))
    try:
        mode = stat.S_IMODE(os.stat(db).st_mode)
        if not IS_WINDOWS:
            assert mode & 0o077 == 0  # no group/other bits
    finally:
        s.close()


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows-specific POSIX mode compatibility")
def test_windows_wide_mode_db_is_accepted(tmp_path):
    db = tmp_path / "state.sqlite3"
    db.write_bytes(b"")
    os.chmod(db, 0o666)
    s = StateStore(str(db))
    try:
        s.promote_repo("owner/repo")
        assert s.is_promoted("owner/repo") is True
    finally:
        s.close()
