"""U3 artifact store tests — write, path safety (no escape), 0700 perms."""

from __future__ import annotations

import os
import stat

import pytest

import transmutary.store.artifacts as artifacts_mod
from transmutary.report.schema import Report, ReportKind, Severity, Source
from transmutary.store.artifacts import (
    ArtifactPathError,
    ArtifactPermissionError,
    ArtifactStore,
    _render_markdown,
    sanitize_repo,
)

IS_WINDOWS = os.name == "nt"


def _report(repo="owner/name", sources=None, kind=ReportKind.DIAGNOSE, **kw):
    defaults = dict(
        body_md="body",
        severity=Severity.HIGH,
        created_at="2026-05-29T10:00:00Z",
        sources=sources if sources is not None else [
            Source("s1", "https://example.com/1", "2026-05-29T09:00:00Z")
        ],
    )
    defaults.update(kw)
    return Report(kind=kind, repo=repo, title="t", **defaults)


_r = _report


def test_write_to_repo_dir_with_sources(tmp_path):
    store = ArtifactStore(str(tmp_path / "art"))
    path = store.write(_report(), ts=1700000000)
    assert os.path.exists(path)
    content = open(path, encoding="utf-8").read()
    assert "## Sources" in content
    assert "https://example.com/1" in content
    # contained under root
    assert os.path.realpath(path).startswith(os.path.realpath(str(tmp_path / "art")))


def test_repo_name_with_slash_does_not_escape(tmp_path):
    root = tmp_path / "art"
    store = ArtifactStore(str(root))
    path = store.write(_report(repo="owner/name"), ts=1)
    # single contained dir, slash collapsed
    rel = os.path.relpath(path, str(root))
    assert ".." not in rel
    assert os.path.realpath(path).startswith(os.path.realpath(str(root)) + os.sep)
    # no nested 'owner/name' traversal dir created at root level
    assert not os.path.isdir(root / "owner" / "name")


def test_traversal_and_absolute_rejected():
    with pytest.raises(ArtifactPathError):
        sanitize_repo("../../etc")
    with pytest.raises(ArtifactPathError):
        sanitize_repo("/etc/passwd")
    with pytest.raises(ArtifactPathError):
        sanitize_repo("a/../../../b")


def test_empty_sources_marked_pending(tmp_path):
    store = ArtifactStore(str(tmp_path / "art"))
    path = store.write(_report(sources=[]), ts=2)
    content = open(path, encoding="utf-8").read()
    assert "待核实信号" in content


def test_artifact_dir_created_0700(tmp_path):
    root = tmp_path / "art"
    ArtifactStore(str(root))
    mode = stat.S_IMODE(os.stat(root).st_mode)
    if not IS_WINDOWS:
        assert mode & 0o077 == 0


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX mode bits are not reliable on Windows")
def test_world_readable_root_rejected(tmp_path):
    root = tmp_path / "art"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    with pytest.raises(ArtifactPermissionError):
        ArtifactStore(str(root))


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows-specific POSIX mode compatibility")
def test_windows_wide_mode_root_is_accepted(tmp_path):
    root = tmp_path / "art"
    root.mkdir()
    os.chmod(root, 0o777)
    store = ArtifactStore(str(root))
    path = store.write(_report(), ts=1700000000)
    assert os.path.exists(path)


def test_existing_artifact_files_are_hardened_on_overwrite(monkeypatch, tmp_path):
    calls = []

    def fake_chmod_private(path, mode, *, inherited_ok=False):
        calls.append((os.path.basename(path), mode, inherited_ok))

    monkeypatch.setattr(artifacts_mod, "chmod_private", fake_chmod_private)

    store = ArtifactStore(str(tmp_path / "art"))
    store.write(_report(), ts=1700000000)
    calls.clear()
    store.write(_report(), ts=1700000000)

    assert ("1700000000-diagnose.md", 0o600, False) in calls
    assert ("1700000000-diagnose.json", 0o600, False) in calls


def test_existing_artifact_dirs_are_hardened_on_windows(monkeypatch, tmp_path):
    calls = []
    root = tmp_path / "art"
    repo_dir = root / "owner__name"
    repo_dir.mkdir(parents=True)

    def fake_chmod_private(path, mode, *, inherited_ok=False):
        calls.append((os.path.abspath(path), mode, inherited_ok))

    monkeypatch.setattr(artifacts_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(artifacts_mod, "chmod_private", fake_chmod_private)

    store = ArtifactStore(str(root))
    store.write(_report(), ts=1700000000)

    assert (os.path.abspath(root), 0o700, False) in calls
    assert (os.path.abspath(repo_dir), 0o700, False) in calls


def test_artifact_permission_hardening_failure_surfaces(monkeypatch, tmp_path):
    def fail_chmod_private(path, mode, *, inherited_ok=False):
        raise artifacts_mod.PrivatePermissionError("acl failed")

    monkeypatch.setattr(artifacts_mod, "chmod_private", fail_chmod_private)

    with pytest.raises(ArtifactPermissionError, match="acl failed"):
        ArtifactStore(str(tmp_path / "art"))


# --- read-only listing / reading (dashboard U1, R-D2/R-D11) ------------------


def _seed(root):
    store = ArtifactStore(str(root))
    store.write(_report(repo="octo/cli"), ts=1700000000)
    store.write(_report(repo="octo/cli", kind=ReportKind.EXPLAIN), ts=1700000100)
    store.write(_report(repo="acme/gw"), ts=1700000050)
    return store


def test_list_repos_maps_and_excludes_underscore_dirs(tmp_path):
    root = tmp_path / "art"
    store = _seed(root)
    os.makedirs(os.path.join(str(root), "_delivered", "immediate"), mode=0o700)
    os.makedirs(os.path.join(str(root), "_feed"), mode=0o700)
    assert store.list_repos() == ["acme/gw", "octo/cli"]


def test_list_repos_empty_when_root_empty(tmp_path):
    store = ArtifactStore(str(tmp_path / "art"))
    assert store.list_repos() == []


def test_list_repos_excludes_traversal_reverse_mapping(tmp_path):
    root = tmp_path / "art"
    store = ArtifactStore(str(root))
    # A dir name that reverse-maps to a traversal segment (foo__.. -> foo/..).
    os.makedirs(os.path.join(str(root), "foo__.."), mode=0o700)
    # R-D11: it must NOT surface as a repo (would carry `..` into a URL).
    assert "foo/.." not in store.list_repos()
    assert store.list_repos() == []


def test_list_reports_sorted_newest_first_with_kind(tmp_path):
    store = _seed(tmp_path / "art")
    refs = store.list_reports("octo/cli")
    assert [r.ts for r in refs] == [1700000100, 1700000000]
    assert refs[0].kind == "explain"
    assert refs[0].filename == "1700000100-explain.md"
    assert refs[1].kind == "diagnose"


def test_list_reports_empty_for_unknown_repo(tmp_path):
    store = _seed(tmp_path / "art")
    assert store.list_reports("nobody/here") == []


def test_read_report_returns_content(tmp_path):
    store = _seed(tmp_path / "art")
    body = store.read_report("octo/cli", "1700000000-diagnose.md")
    assert body is not None
    assert "# t" in body


def test_read_report_rejects_traversal_and_missing(tmp_path):
    store = _seed(tmp_path / "art")
    # R-D11: illegal file names / traversal → None, never read outside the dir.
    assert store.read_report("octo/cli", "../../etc/passwd") is None
    assert store.read_report("octo/cli", "..%2f..%2fetc%2fpasswd") is None
    assert store.read_report("octo/cli", "sub/dir.md") is None
    assert store.read_report("octo/cli", "1700000000-diagnose.txt") is None
    # Well-formed but absent → None (no raise).
    assert store.read_report("octo/cli", "1699999999-diagnose.md") is None


# --- Bilingual rendering (R6) ------------------------------------------------

def test_render_markdown_includes_zh_body(tmp_path):
    report = _r(body_md="EN body", body_md_zh="ZH 正文")
    rendered = _render_markdown(report)
    assert "## 中文" in rendered
    assert "ZH 正文" in rendered
    assert "EN body" in rendered


def test_render_markdown_no_zh_body_backward_compat(tmp_path):
    report = _r(body_md="EN only", body_md_zh=None)
    rendered = _render_markdown(report)
    assert "## 中文" not in rendered
    assert "EN only" in rendered


def test_json_sidecar_contains_body_md_zh(tmp_path):
    store = ArtifactStore(str(tmp_path / "art"))
    report = _r(body_md="EN", body_md_zh="ZH")
    store.write(report, ts=1700000000.0)
    meta = store.read_meta("owner/name", "1700000000-diagnose.md")
    assert meta is not None
    assert meta["body_md_zh"] == "ZH"


def test_json_sidecar_body_md_zh_none_when_absent(tmp_path):
    store = ArtifactStore(str(tmp_path / "art"))
    report = _r(body_md="EN", body_md_zh=None)
    store.write(report, ts=1700000000.0)
    meta = store.read_meta("owner/name", "1700000000-diagnose.md")
    assert meta is not None
    assert meta["body_md_zh"] is None
