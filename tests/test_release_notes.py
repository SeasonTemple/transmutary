from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_notes", ROOT / "tools" / "release_notes.py")
assert SPEC is not None and SPEC.loader is not None
release_notes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_notes)


def test_validate_requires_both_language_sections():
    failures = release_notes.validate_bilingual_release_note("# v1\n\n## English\n\n...")
    assert "missing Chinese release-note section: add `## 中文`" in failures
    assert "missing English release-note section: add `## English`" not in failures


def test_stub_is_bilingual_and_placeholder_marked():
    text = release_notes.render_stub("0.13.0")
    assert not release_notes.validate_bilingual_release_note(text)
    assert "# v0.13.0" in text
    assert "<one-line theme>" in text


def test_current_release_note_is_publishable():
    assert release_notes.check("v0.12.0") == []


# --- check --allow-missing: three-state exit for CI graceful degradation -----

def _run(argv):
    return release_notes.main(argv)


def test_check_missing_strict_fails():
    # default (no flag): a missing note is an error — preserves existing semantics.
    assert _run(["check", "v99.99.99"]) == 1


def test_check_missing_allow_missing_warns_and_passes(capsys):
    rc = _run(["check", "v99.99.99", "--allow-missing"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "WARNING" in err and "v99.99.99" in err
    assert "keeping auto-generated" in err


def test_check_present_invalid_still_fails_with_allow_missing(tmp_path, monkeypatch):
    # present but not bilingual → fail even under --allow-missing (quality gate).
    monkeypatch.setattr(release_notes, "RELEASE_NOTES_DIR", tmp_path)
    (tmp_path / "v9.9.9.md").write_text(
        "# v9.9.9\n\n## English\n\nonly english\n", encoding="utf-8"
    )
    assert _run(["check", "v9.9.9", "--allow-missing"]) == 1


def test_check_present_placeholder_still_fails_with_allow_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(release_notes, "RELEASE_NOTES_DIR", tmp_path)
    (tmp_path / "v9.9.9.md").write_text(release_notes.render_stub("v9.9.9"), encoding="utf-8")
    assert _run(["check", "v9.9.9", "--allow-missing"]) == 1


def test_check_present_valid_passes_with_allow_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(release_notes, "RELEASE_NOTES_DIR", tmp_path)
    (tmp_path / "v9.9.9.md").write_text(
        "# v9.9.9\n\n## 中文\n\n摘要\n\n## English\n\nsummary\n", encoding="utf-8"
    )
    assert _run(["check", "v9.9.9", "--allow-missing"]) == 0
