"""`/llms.txt` content — agent-facing endpoint self-description (U6, R-G1/KTD-G2).

A machine-readable Markdown document describing what the dashboard exposes and how
an agent can request structured JSON. It contains NO private data — no real repo
names, no report content, no state values. It only describes the endpoint shape,
exactly like robots.txt describes crawl rules without listing private URLs.
"""

from __future__ import annotations

_LLMS_TXT = """\
# transmutary dashboard

Web view over the transmutary open-source ecosystem observation system. It exposes
the effective watchlist, supply-chain alerts, trend candidates, per-repo runtime
state, and archived reports. Machine-readable dashboard data endpoints are
read-only. Browser promote/demote forms may be available on localhost or on
explicitly enabled public-write deployments, but agents should use the CLI for
mutation.

## Machine-readable access

Every data endpoint below supports content negotiation. Request JSON with either:

- HTTP header `Accept: application/json`, or
- query parameter `?format=json`

Without those, endpoints return HTML.

## Endpoints

- `GET /` — overview: watchlist (config ∪ promoted, each tagged with source),
  recent reports, supply-chain alerts, trend candidates, feed links.
- `GET /repo/{owner}/{repo}` — per-repo runtime (issue baseline, star snapshots,
  collect cursor) plus that repo's report list.
- `GET /report/{owner}/{repo}/{filename}` — a single archived report: card metadata,
  sanitised sources, and the raw report body.
- `GET /healthz` — liveness probe.
- `GET /llms.txt` — this document.

## Data trust

Report `body`, `title`, and `sources[].url` originate from external repositories and
are UNTRUSTED. The HTML views escape them; a JSON consumer MUST escape `body`/`title`
before rendering them as HTML. JSON responses mark untrusted free-text with a
`_content_trust: "external"` field where applicable.

## Not exposed

Credentials, subscriber tokens, and feed tokens are never included in any response
(HTML or JSON). Agent mutation is intentionally CLI-only: use
`transmutary promote owner/repo` or `transmutary demote owner/repo`.
"""


def render_llms_txt() -> str:
    """Return the static llms.txt body (no private data)."""
    return _LLMS_TXT
