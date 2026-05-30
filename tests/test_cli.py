"""U4 CLI tests — promote / demote / list-watchlist over a temp config dir.

All state lives in a per-test temp SQLite file; no network, no credentials. The
CLI loads settings with ``require_credentials=False``, so these tests deliberately
do NOT set any credential env vars (promotion must work without them)."""

from __future__ import annotations

import io

import pytest
import yaml

from transmutary.cli import main
from transmutary.store.state import StateStore


def _write_config(tmp_path):
    (tmp_path / "watchlist.yaml").write_text(
        yaml.safe_dump(
            {
                "repos": [{"repo": "acme/cli"}, {"repo": "acme/gateway"}],
                "dependency_edges": [{"from": "acme/cli", "to": "acme/gateway"}],
            }
        )
    )
    (tmp_path / "trend_scope.yaml").write_text(
        yaml.safe_dump({"topics": ["ai"], "keywords": ["llm"]})
    )
    db_path = str(tmp_path / "state.sqlite3")
    (tmp_path / "delivery.yaml").write_text(
        yaml.safe_dump(
            {
                "state_db_path": db_path,
                "artifact_root": str(tmp_path / "artifacts"),
                "token_max_age_days": 90,
                "digest_hour": 9,
            }
        )
    )
    return str(tmp_path), db_path


@pytest.fixture
def cfg(tmp_path):
    return _write_config(tmp_path)


def _run(argv):
    out = io.StringIO()
    code = main(argv, out=out)
    return code, out.getvalue()


def test_promote_writes_table_exit0(cfg, monkeypatch):
    config_dir, db_path = cfg
    # No credential env vars set — promote must still work (require_credentials=False).
    for var in (
        "TRANSMUTARY_GITHUB_TOKEN",
        "TRANSMUTARY_SMTP_USER",
        "TRANSMUTARY_SMTP_PASSWORD",
        "TRANSMUTARY_RSS_TOKEN",
        "TRANSMUTARY_LLM_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    code, out = _run(["--config-dir", config_dir, "promote", "hot/candidate"])
    assert code == 0
    assert "promoted hot/candidate" in out
    with StateStore(db_path) as store:
        assert store.is_promoted("hot/candidate")


def test_promote_records_source(cfg):
    config_dir, db_path = cfg
    code, out = _run(["--config-dir", config_dir, "promote", "hot/x", "--source", "manual"])
    assert code == 0
    assert "source=manual" in out


def test_demote_removes_row(cfg):
    config_dir, db_path = cfg
    _run(["--config-dir", config_dir, "promote", "temp/repo"])
    code, out = _run(["--config-dir", config_dir, "demote", "temp/repo"])
    assert code == 0
    assert "demoted temp/repo" in out
    with StateStore(db_path) as store:
        assert not store.is_promoted("temp/repo")


def test_list_watchlist_shows_config_and_promoted(cfg):
    config_dir, db_path = cfg
    _run(["--config-dir", config_dir, "promote", "hot/candidate"])
    code, out = _run(["--config-dir", config_dir, "list-watchlist"])
    assert code == 0
    assert "acme/cli\tconfig" in out
    assert "acme/gateway\tconfig" in out
    assert "hot/candidate\tpromoted" in out


def test_list_watchlist_marks_overlap(cfg):
    config_dir, db_path = cfg
    _run(["--config-dir", config_dir, "promote", "acme/cli"])  # overlaps config
    code, out = _run(["--config-dir", config_dir, "list-watchlist"])
    assert code == 0
    assert "acme/cli\tconfig (also promoted)" in out
    # Overlapping repo must NOT also be printed as a standalone 'promoted' line.
    assert "acme/cli\tpromoted" not in out


@pytest.mark.parametrize("bad", ["noslash", "https://github.com/a/b", "a/b/c", "/leading", ""])
def test_invalid_repo_nonzero_and_no_write(cfg, bad):
    config_dir, db_path = cfg
    out = io.StringIO()
    code = main(["--config-dir", config_dir, "promote", bad], out=out)
    assert code != 0
    # No row written for an invalid name.
    with StateStore(db_path) as store:
        assert store.list_promoted() == []


def test_works_without_any_credentials(cfg, monkeypatch):
    config_dir, db_path = cfg
    for var in (
        "TRANSMUTARY_GITHUB_TOKEN",
        "TRANSMUTARY_SMTP_USER",
        "TRANSMUTARY_SMTP_PASSWORD",
        "TRANSMUTARY_RSS_TOKEN",
        "TRANSMUTARY_LLM_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    code, _ = _run(["--config-dir", config_dir, "list-watchlist"])
    assert code == 0
