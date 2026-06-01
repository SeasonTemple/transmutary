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
