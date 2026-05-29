"""U15 server.py tests — Authorization-header auth, no token in URL, revoke/expiry
(R20), token redaction in logs (R21).

Uses an in-memory U2 StateStore + starlette TestClient. No real network/uvicorn.
"""

from __future__ import annotations

import logging

from starlette.testclient import TestClient

from transmutary.deliver.server import hash_token, make_app
from transmutary.store.state import StateStore


def _store_with_token(token, subscriber="alice", expires_at=None):
    store = StateStore(":memory:")
    store.add_subscriber_token(hash_token(token), subscriber, expires_at=expires_at)
    return store


def _app(store, now=None):
    return make_app(store, feed_source=lambda name: f"<feed>{name}</feed>", now=now)


def test_valid_token_via_authorization_header_serves_feed():
    store = _store_with_token("good-token")
    client = TestClient(_app(store))
    resp = client.get("/feed/immediate", headers={"Authorization": "Bearer good-token"})
    assert resp.status_code == 200
    assert "immediate" in resp.text
    assert "application/atom+xml" in resp.headers["content-type"]


def test_missing_token_rejected():
    store = _store_with_token("good-token")
    client = TestClient(_app(store))
    resp = client.get("/feed/immediate")
    assert resp.status_code == 401


def test_invalid_token_rejected():
    store = _store_with_token("good-token")
    client = TestClient(_app(store))
    resp = client.get("/feed/immediate", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_token_in_query_string_does_not_authenticate():
    # R20: a tokenized URL must NOT authenticate — auth is header-only.
    store = _store_with_token("good-token")
    client = TestClient(_app(store))
    resp = client.get("/feed/immediate?token=good-token")
    assert resp.status_code == 401


def test_revoked_token_rejected_others_unaffected():
    store = StateStore(":memory:")
    store.add_subscriber_token(hash_token("alice-tok"), "alice")
    store.add_subscriber_token(hash_token("bob-tok"), "bob")
    store.revoke_subscriber_token(hash_token("alice-tok"))
    client = TestClient(_app(store))
    # Alice revoked → 401; Bob unaffected → 200.
    assert client.get("/feed/immediate",
                      headers={"Authorization": "Bearer alice-tok"}).status_code == 401
    assert client.get("/feed/immediate",
                      headers={"Authorization": "Bearer bob-tok"}).status_code == 200


def test_expired_token_rejected():
    # expires_at in the past relative to the frozen clock.
    store = _store_with_token("exp-tok", expires_at=1000.0)
    client = TestClient(_app(store, now=lambda: 2000.0))
    resp = client.get("/feed/immediate", headers={"Authorization": "Bearer exp-tok"})
    assert resp.status_code == 401


def test_token_not_in_logs(caplog):
    store = _store_with_token("good-token")
    client = TestClient(_app(store))
    with caplog.at_level(logging.INFO, logger="transmutary.deliver.server"):
        client.get("/feed/immediate", headers={"Authorization": "Bearer good-token"})
    # R21: raw token never appears in any log record.
    for rec in caplog.records:
        assert "good-token" not in rec.getMessage()


def test_basic_auth_password_component_used():
    import base64

    store = _store_with_token("good-token")
    client = TestClient(_app(store))
    cred = base64.b64encode(b"alice:good-token").decode()
    resp = client.get("/feed/immediate", headers={"Authorization": f"Basic {cred}"})
    assert resp.status_code == 200
