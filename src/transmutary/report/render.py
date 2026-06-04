"""Markdown → HTML rendering for report bodies (R2/R3/R4).

Shared by the dashboard (WebUI report view) and the email/digest delivery layer.
Lives under ``report/`` (common upstream of ``dashboard/`` and ``deliver/``) to
avoid a cross-layer import. LLM report bodies are UNTRUSTED markdown; this renders
them to HTML safely without a heavyweight sanitizer:

  * ``MarkdownIt("commonmark", {"html": False})`` escapes ALL raw HTML — a
    ``<script>``/``<img onerror>``/``<div onclick>`` in the source becomes inert
    text, never a live tag. So no untrusted tag can reach the DOM.
  * markdown-it-py's default ``validateLink`` rejects ``javascript:`` /
    ``vbscript:`` / ``data:text/html`` (and case / tab / leading-space variants),
    so a crafted link cannot smuggle script. Verified against markdown-it-py 3.0.

The rendered output therefore contains only markdown-it's own generated tags
(``<h1>``/``<ul>``/``<a href="http…">``/``<code>`` …) and is safe to emit through
Jinja ``| safe``. This holds for the dashboard (CSP ``style-src 'self'`` forbids
inline style/script anyway) and for email HTML (which renders the same output).
"""

from __future__ import annotations

from markdown_it import MarkdownIt

# A single shared parser instance: stateless across renders, cheap to reuse.
# html=False is the load-bearing security switch (raw HTML → escaped text).
_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})


def render_markdown(text: str | None) -> str:
    """Render untrusted markdown to safe HTML. Empty/None → empty string."""
    if not text:
        return ""
    return _MD.render(text)
