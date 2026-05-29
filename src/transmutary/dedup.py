"""Event fingerprint + L1 dedup + deterministic reference-URL merge (U8, R8/R18).

Three responsibilities, all deterministic (KTD6 — no embeddings):

  1. **Event fingerprint** — the identity of an event (CONTEXT):
       * release / advisory: ``tag`` / ``GHSA-id``
       * issue cluster: ``(repo, keyword_bucket, rolling_window)``
     The same release across polling cycles fingerprints identically → emitted
     once (AE4). New issues in the same cluster bump ``evidence_count`` only; the
     event re-fires once when it crosses the escalation threshold (AE4).

  2. **L1 hash seen-set** — content/URL hash recorded in the state store; a
     repeat within the rolling window is suppressed.

  3. **Reference-URL merge (R18 source independence)** — derived content
     (blogs / reposts) is merged onto a canonical id by the upstream PR/issue URL
     it explicitly cites. Independence is counted as **distinct domain AND not
     co-citing the same upstream URL** (KTD6): three blogs on three domains all
     citing one upstream issue count as ONE independent source, so they cannot
     fake the ">=2 independent sources" gate. Implicit reposts without an explicit
     link remain a known residual (documented in plan Risks).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

# Default escalation threshold for an issue cluster (AE4). When the accumulated
# evidence_count crosses this, the cluster re-fires once as an escalation. This
# is a shipped default, configurable later.
DEFAULT_ESCALATION_THRESHOLD = 5

# Rolling window for the issue-cluster fingerprint, in seconds (matches the
# seen-set window in the state store). Two issues in the same repo+bucket within
# the same window bucket share a fingerprint.
DEFAULT_WINDOW_SECONDS = 7 * 24 * 60 * 60

# Keyword buckets for issue clustering. An issue's bucket is the first matching
# bucket (deterministic order); unmatched issues fall in "other".
KEYWORD_BUCKETS: dict[str, tuple[str, ...]] = {
    "outage": (
        "down",
        "outage",
        "500",
        "503",
        "504",
        "timeout",
        "unavailable",
        "挂了",
        "超时",
        "宕机",
        "不可用",
    ),
    "security": ("cve", "vulnerab", "exploit", "malware", "advisory", "ghsa"),
    "crash": ("crash", "panic", "segfault", "fatal", "崩溃"),
}


def keyword_bucket(text: str) -> str:
    """Deterministically assign issue text to a keyword bucket."""
    low = (text or "").lower()
    for bucket, terms in KEYWORD_BUCKETS.items():
        for term in terms:
            if term in low:
                return bucket
    return "other"


def window_index(ts: float, window_secs: float = DEFAULT_WINDOW_SECONDS) -> int:
    """The rolling-window bucket index for a timestamp (deterministic)."""
    return int(ts // window_secs)


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------
def release_fingerprint(repo: str, tag: str) -> str:
    return f"release:{repo}:{tag}"


def advisory_fingerprint(ghsa_id: str) -> str:
    return f"advisory:{ghsa_id}"


def issue_cluster_fingerprint(
    repo: str, text: str, ts: float, *, window_secs: float = DEFAULT_WINDOW_SECONDS
) -> str:
    """``(repo, keyword_bucket, rolling_window)`` — the issue-cluster identity."""
    bucket = keyword_bucket(text)
    win = window_index(ts, window_secs)
    return f"issue:{repo}:{bucket}:{win}"


# ---------------------------------------------------------------------------
# URL canonicalization + content hashing (L1)
# ---------------------------------------------------------------------------
def canonicalize_url(url: str) -> str:
    """Normalize a URL for merge/hash: lowercase host, drop query/fragment,
    strip trailing slash, drop default ports and ``www.``.
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    # Drop default ports.
    netloc = host
    if parts.port and parts.port not in (80, 443):
        netloc = f"{host}:{parts.port}"
    path = re.sub(r"/+$", "", parts.path)  # strip trailing slashes
    return urlunsplit((scheme, netloc, path, "", ""))


def content_hash(*parts: str) -> str:
    """Stable hash of canonicalized content/URL parts for the L1 seen-set."""
    norm = "\x1f".join(canonicalize_url(p) if "://" in p else (p or "") for p in parts)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def url_domain(url: str) -> str:
    """The registrable-ish host of a URL (for source-independence counting)."""
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


# ---------------------------------------------------------------------------
# Reference-URL merge (R18)
# ---------------------------------------------------------------------------
# An explicit upstream reference embedded in derived content: a GitHub PR/issue
# or a GHSA advisory URL.
_UPSTREAM_REF_RE = re.compile(
    r"https?://github\.com/[^/\s]+/[^/\s]+/(?:issues|pull)/\d+"
    r"|https?://github\.com/advisories/GHSA-[\w-]+",
    re.IGNORECASE,
)


def extract_upstream_refs(text: str) -> list[str]:
    """Extract explicit upstream PR/issue/advisory URLs cited in derived text."""
    seen: list[str] = []
    for m in _UPSTREAM_REF_RE.findall(text or ""):
        c = canonicalize_url(m)
        if c not in seen:
            seen.append(c)
    return seen


@dataclass
class SourceItem:
    """A piece of evidence under consideration for source-independence counting."""

    url: str
    text: str = ""
    # An explicit canonical id, if known (e.g. the upstream item itself).
    canonical_id: str | None = None


@dataclass
class MergeResult:
    """Outcome of merging derived sources onto canonical upstreams (R18)."""

    # canonical_id -> list of source URLs merged under it.
    clusters: dict[str, list[str]] = field(default_factory=dict)

    def canonical_id_for(self, url: str) -> str | None:
        c = canonicalize_url(url)
        for cid, members in self.clusters.items():
            if c in members:
                return cid
        return None

    def independent_source_count(self) -> int:
        """Count independent sources per R18.

        Each canonical cluster contributes at most ONE independent source, AND
        upstream clusters whose member sets intersect are first merged together —
        so a derived item that co-cites several upstreams collapses all of them
        into one source. This blocks the multi-cite spoof: three blogs that each
        also name a distinct decoy upstream but all share one real upstream still
        count as ONE independent source, not three (R18). Standalone items (no
        shared upstream) each count once, deduped by domain.
        """
        # 1. Merge upstream clusters that share at least one member item (a single
        #    derived item co-citing multiple upstreams links those upstreams).
        upstream_groups = [
            set(members) for cid, members in self.clusters.items() if cid.startswith("upstream:")
        ]
        merged = _merge_intersecting(upstream_groups)
        count = len(merged)

        # 2. Standalone derived items: dedupe by domain so two articles on the
        #    same domain don't double-count.
        standalone_domains: set[str] = set()
        for cid, members in self.clusters.items():
            if not cid.startswith("upstream:"):
                for url in members:
                    standalone_domains.add(url_domain(url))
        return count + len(standalone_domains)


def merge_references(items: list[SourceItem]) -> MergeResult:
    """Deterministically merge derived sources onto cited upstream URLs (R18).

    Items that cite the same upstream PR/issue/advisory are clustered under
    ``upstream:<canonical_url>``. Items with an explicit ``canonical_id`` use it.
    Items with neither become standalone clusters keyed by their own URL.
    """
    result = MergeResult()
    for item in items:
        # 1. Explicit canonical id wins.
        if item.canonical_id:
            key = f"upstream:{canonicalize_url(item.canonical_id)}"
            result.clusters.setdefault(key, [])
            _append(result.clusters[key], item.url)
            continue
        # 2. If the item IS an upstream URL itself, key by itself as upstream.
        self_c = canonicalize_url(item.url)
        if _UPSTREAM_REF_RE.fullmatch(self_c) or _UPSTREAM_REF_RE.match(item.url):
            key = f"upstream:{self_c}"
            result.clusters.setdefault(key, [])
            _append(result.clusters[key], item.url)
            continue
        # 3. Derived content citing upstream(s) → cluster under EVERY upstream it
        #    cites (deterministic, sorted), not just the first. Sharing any one
        #    upstream with another item collapses them in the independence count,
        #    so co-citing a decoy upstream can't fabricate extra sources (R18).
        refs = sorted(extract_upstream_refs(item.text))
        if refs:
            for ref in refs:
                key = f"upstream:{ref}"
                result.clusters.setdefault(key, [])
                _append(result.clusters[key], item.url)
            continue
        # 4. Standalone — key by own canonical url.
        key = f"standalone:{self_c}"
        result.clusters.setdefault(key, [])
        _append(result.clusters[key], item.url)
    return result


def _append(members: list[str], url: str) -> None:
    c = canonicalize_url(url)
    if c not in members:
        members.append(c)


def _merge_intersecting(groups: list[set[str]]) -> list[set[str]]:
    """Merge sets that share at least one element (deterministic union-find).

    Used by R18 source counting: upstream clusters linked by a common derived
    item (a blog co-citing both upstreams) collapse into a single source.
    """
    merged: list[set[str]] = []
    for group in groups:
        current = set(group)
        rest: list[set[str]] = []
        for existing in merged:
            if current & existing:
                current |= existing
            else:
                rest.append(existing)
        rest.append(current)
        merged = rest
    return merged


# ---------------------------------------------------------------------------
# L1 seen-set dedup + clustering against the state store
# ---------------------------------------------------------------------------
@dataclass
class DedupDecision:
    """The outcome of running one event through dedup."""

    fingerprint: str
    is_new: bool  # first time this fingerprint is seen
    evidence_count: int
    escalated: bool  # crossed the escalation threshold on this pass


def dedup_release(store, repo: str, tag: str, *, url: str = "", text: str = "") -> DedupDecision:
    """Dedup a release/tag. Same release across cycles → not new (AE4)."""
    fp = release_fingerprint(repo, tag)
    h = content_hash(fp, url, text)
    is_new = store.mark_seen(h, source=repo)
    count = store.upsert_fingerprint(fp, repo, "release")
    return DedupDecision(fingerprint=fp, is_new=is_new, evidence_count=count, escalated=False)


def dedup_advisory(store, ghsa_id: str, repo: str = "") -> DedupDecision:
    fp = advisory_fingerprint(ghsa_id)
    h = content_hash(fp)
    is_new = store.mark_seen(h, source=repo or ghsa_id)
    count = store.upsert_fingerprint(fp, repo or ghsa_id, "advisory")
    return DedupDecision(fingerprint=fp, is_new=is_new, evidence_count=count, escalated=False)


def dedup_issue(
    store,
    repo: str,
    text: str,
    ts: float,
    *,
    url: str = "",
    window_secs: float = DEFAULT_WINDOW_SECONDS,
    escalation_threshold: int = DEFAULT_ESCALATION_THRESHOLD,
) -> DedupDecision:
    """Dedup/cluster an issue.

    New issues in the same ``(repo, bucket, window)`` bump ``evidence_count``
    only; when the count crosses ``escalation_threshold`` the cluster re-fires
    once as an escalation (AE4).
    """
    fp = issue_cluster_fingerprint(repo, text, ts, window_secs=window_secs)
    # Per-issue L1 hash so the same individual issue isn't counted twice.
    h = content_hash(fp, url, text)
    counted = store.mark_seen(h, source=repo)

    prior = store.get_fingerprint(fp)
    prior_count = prior["evidence_count"] if prior is not None else 0
    prior_escalated = bool(prior["escalated"]) if prior is not None else False

    if not counted:
        # Exact duplicate issue — no change.
        return DedupDecision(
            fingerprint=fp,
            is_new=False,
            evidence_count=prior_count,
            escalated=False,
        )

    new_count = prior_count + 1
    crosses = (new_count >= escalation_threshold) and not prior_escalated
    store.upsert_fingerprint(fp, repo, "issue", escalate=crosses)
    return DedupDecision(
        fingerprint=fp,
        is_new=(prior_count == 0),
        evidence_count=new_count,
        escalated=crosses,
    )
