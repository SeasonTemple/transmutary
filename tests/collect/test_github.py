"""U6 github.py tests — atom+REST parsing, cursor, 429 backoff, SSRF, read-only.

All HTTP is mocked via httpx.MockTransport. No real network.
"""

from __future__ import annotations

import httpx
import pytest

from transmutary.collect import github
from transmutary.collect.github import (
    ALLOWED_HOSTS,
    RateLimitError,
    SSRFError,
    collect_repo,
)

ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>v1.2.0</title>
    <link href="https://github.com/acme/cli/releases/tag/v1.2.0"/>
    <updated>2026-05-20T10:00:00Z</updated>
    <summary>Stable release</summary>
  </entry>
</feed>
"""


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_parses_release_and_issue_events():
    requested: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append((request.method, str(request.url)))
        url = str(request.url)
        if url.endswith("releases.atom"):
            return httpx.Response(200, text=ATOM_FEED)
        if "/releases?" in url:
            return httpx.Response(200, json=[])
        if "/issues" in url:
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 42,
                        "title": "API down",
                        "body": "500 errors everywhere",
                        "html_url": "https://github.com/acme/cli/issues/42",
                        "updated_at": "2026-05-21T08:00:00Z",
                        "state": "open",
                    },
                    # A pull request must be filtered out (issues endpoint returns PRs too).
                    {
                        "number": 43,
                        "title": "PR",
                        "pull_request": {"url": "x"},
                        "updated_at": "2026-05-21T09:00:00Z",
                    },
                ],
            )
        return httpx.Response(404)

    result = collect_repo(_client(handler), "acme/cli", token="ghp_x")
    kinds = {e.kind for e in result.events}
    assert "release" in kinds and "issue" in kinds
    releases = [e for e in result.events if e.kind == "release"]
    issues = [e for e in result.events if e.kind == "issue"]
    assert releases[0].id == "v1.2.0"
    assert len(issues) == 1  # PR filtered out
    assert issues[0].id == "42"
    # Only GET requests issued (R22).
    assert {m for m, _ in requested} == {"GET"}


def test_prerelease_backfilled_via_rest():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("releases.atom"):
            return httpx.Response(200, text=ATOM_FEED)
        if "/releases?" in url:
            return httpx.Response(
                200,
                json=[
                    {
                        "tag_name": "v2.0.0-rc1",
                        "name": "RC1",
                        "body": "candidate",
                        "prerelease": True,
                        "html_url": "https://github.com/acme/cli/releases/tag/v2.0.0-rc1",
                        "published_at": "2026-05-22T00:00:00Z",
                    },
                    {"tag_name": "v1.2.0", "prerelease": False},  # not a pre-release
                ],
            )
        if "/issues" in url:
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    result = collect_repo(_client(handler), "acme/cli", token="ghp_x")
    pre = [e for e in result.events if e.extra.get("prerelease")]
    assert len(pre) == 1
    assert pre[0].id == "v2.0.0-rc1"


def test_since_cursor_advances():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/issues" in url:
            captured["issues_url"] = url
            return httpx.Response(
                200,
                json=[
                    {"number": 1, "title": "a", "updated_at": "2026-05-21T08:00:00Z"},
                    {"number": 2, "title": "b", "updated_at": "2026-05-23T12:00:00Z"},
                ],
            )
        if url.endswith("releases.atom"):
            return httpx.Response(200, text="<feed></feed>")
        if "/releases?" in url:
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    result = collect_repo(
        _client(handler), "acme/cli", token="ghp_x", since="2026-05-20T00:00:00Z"
    )
    # since= was sent on the issues request.
    assert "since=2026-05-20T00%3A00%3A00Z" in captured["issues_url"] or (
        "since=2026-05-20T00:00:00Z" in captured["issues_url"]
    )
    # Cursor advanced to the max updated_at seen.
    assert result.next_since == "2026-05-23T12:00:00Z"


def test_since_cursor_does_not_regress_on_older_issues():
    # Non-regression: when every returned issue is OLDER than the incoming since,
    # the cursor must stay at the incoming value (never rewind and re-emit forever).
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/issues" in url:
            return httpx.Response(
                200,
                json=[{"number": 1, "title": "old", "updated_at": "2026-01-01T00:00:00Z"}],
            )
        if url.endswith("releases.atom"):
            return httpx.Response(200, text="<feed></feed>")
        if "/releases?" in url:
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    result = collect_repo(
        _client(handler), "acme/cli", token="ghp_x", since="2026-05-20T00:00:00Z"
    )
    assert result.next_since == "2026-05-20T00:00:00Z"  # cursor did not regress


def test_403_secondary_rate_limit_then_success():
    # The 403 + 'rate limit' secondary-limit branch must retry like a 429.
    state = {"calls": 0}
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/issues" in url:
            state["calls"] += 1
            if state["calls"] == 1:
                return httpx.Response(403, text="You have exceeded a secondary rate limit")
            return httpx.Response(200, json=[])
        if url.endswith("releases.atom"):
            return httpx.Response(200, text="<feed></feed>")
        if "/releases?" in url:
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    result = collect_repo(
        _client(handler), "acme/cli", token="ghp_x", sleep=lambda d: slept.append(d)
    )
    assert state["calls"] == 2  # retried the 403 secondary limit
    assert slept  # backed off before retrying
    assert result.events == []


def test_backoff_is_exponential_without_retry_after():
    # With no Retry-After header, the delay grows base, base*2, ... (exponential).
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/issues" in url:
            return httpx.Response(429, text="rate limited")  # always 429 → exhaust retries
        if url.endswith("releases.atom"):
            return httpx.Response(200, text="<feed></feed>")
        if "/releases?" in url:
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    with pytest.raises(RateLimitError):
        collect_repo(
            _client(handler), "acme/cli", token="ghp_x", sleep=lambda d: slept.append(d)
        )
    # DEFAULT_MAX_RETRIES sleeps before raising; deltas follow base * 2**attempt.
    assert len(slept) == github.DEFAULT_MAX_RETRIES
    base = github.DEFAULT_BACKOFF_BASE
    assert slept == [base * (2**i) for i in range(github.DEFAULT_MAX_RETRIES)]


def test_429_backoff_then_success():
    state = {"calls": 0}
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/issues" in url:
            state["calls"] += 1
            if state["calls"] == 1:
                return httpx.Response(429, headers={"Retry-After": "2"}, text="rate limited")
            return httpx.Response(200, json=[])
        if url.endswith("releases.atom"):
            return httpx.Response(200, text="<feed></feed>")
        if "/releases?" in url:
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    result = collect_repo(
        _client(handler), "acme/cli", token="ghp_x", sleep=lambda d: slept.append(d)
    )
    assert state["calls"] == 2  # retried once
    assert slept and slept[0] >= 2.0  # honored Retry-After
    assert result.events == []


def test_429_persists_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("releases.atom"):
            return httpx.Response(429, text="rate limited")
        return httpx.Response(404)

    with pytest.raises(RateLimitError):
        collect_repo(_client(handler), "acme/cli", token="ghp_x", sleep=lambda d: None)


def test_non_github_host_rejected():
    # A watchlist entry that is a full URL on a non-GitHub host is rejected (R23).
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never hit
        raise AssertionError("must not perform IO for a rejected host")

    with pytest.raises(SSRFError):
        collect_repo(_client(handler), "https://evil.com/acme/cli", token="ghp_x")


def test_slug_form_host_smuggle_rejected():
    # owner token carrying a colon authority is rejected without IO.
    with pytest.raises(SSRFError):
        github._validate_repo("evil.com:8080/acme/cli")


def test_traversal_token_rejected():
    with pytest.raises(SSRFError):
        github._validate_repo("../../etc/passwd")


def test_legit_dotted_repo_name_allowed():
    owner, name = github._validate_repo("acme/cli.js")
    assert (owner, name) == ("acme", "cli.js")


def test_assert_allowed_blocks_redirect_host():
    # Even if a URL somehow points off-allowlist, _assert_allowed rejects it.
    with pytest.raises(SSRFError):
        github._assert_allowed("https://evil.com/repos/x/y/issues")


def test_allowed_hosts_are_github_only():
    assert ALLOWED_HOSTS == frozenset({"github.com", "api.github.com"})


def test_token_sent_as_header_not_in_url():
    seen_urls: list[str] = []
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        seen_auth.append(request.headers.get("Authorization", ""))
        url = str(request.url)
        if url.endswith("releases.atom"):
            return httpx.Response(200, text="<feed></feed>")
        if "/releases?" in url:
            return httpx.Response(200, json=[])
        if "/issues" in url:
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    collect_repo(_client(handler), "acme/cli", token="ghp_supersecret")
    assert all("ghp_supersecret" not in u for u in seen_urls)
    assert any("Bearer ghp_supersecret" == a for a in seen_auth)


def test_empty_repo_no_release_no_error():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("releases.atom"):
            return httpx.Response(200, text="<feed></feed>")
        if "/releases?" in url:
            return httpx.Response(200, json=[])
        if "/issues" in url:
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    result = collect_repo(_client(handler), "acme/empty", token="ghp_x")
    assert result.events == []
    assert result.next_since is None


def test_make_client_disables_redirects():
    client = github.make_client()
    try:
        assert client.follow_redirects is False
    finally:
        client.close()


def test_redirect_to_evil_host_not_followed():
    # R23 behavior: a GitHub endpoint replying 302 -> evil.com must NOT cause an
    # outbound request to evil.com. With follow_redirects=False the 302 is a
    # terminal non-200 response; the collector surfaces it as-is and never hits
    # the off-allowlist host.
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if "evil.com" in url:  # pragma: no cover - must never be reached
            raise AssertionError("redirect to evil.com was followed (SSRF, R23)")
        if url.endswith("releases.atom"):
            return httpx.Response(302, headers={"Location": "https://evil.com/x"})
        if "/releases?" in url:
            return httpx.Response(200, json=[])
        if "/issues" in url:
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    result = collect_repo(_client(handler), "acme/cli", token="ghp_x")
    # The 302 was treated as a terminal non-200 (no release events parsed from it)
    # and no request to evil.com was ever recorded.
    assert all("evil.com" not in u for u in requested)
    assert [e for e in result.events if e.kind == "release"] == []


def test_collect_repo_rejects_redirect_following_client():
    # R23 contract is enforced, not merely documented: a client built with
    # follow_redirects=True is refused before any IO.
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never hit
        raise AssertionError("must not perform IO with a redirect-following client")

    bad_client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        with pytest.raises(SSRFError):
            collect_repo(bad_client, "acme/cli", token="ghp_x")
    finally:
        bad_client.close()
