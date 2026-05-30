<div align="center">

<!-- hero banner: assets/hero-banner.* (TODO) -->

# Transmutary

**Proactive open-source ecosystem intelligence — continuously watch repositories and their dependencies, turn changes into diagnostic reports, and get pushed what matters before it becomes an incident.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/SeasonTemple/transmutary/actions/workflows/ci.yml/badge.svg)](https://github.com/SeasonTemple/transmutary/actions/workflows/ci.yml)
[![Tests: 315 passing](https://img.shields.io/badge/tests-315_passing-brightgreen.svg)](#tests)

[English](README.md) · [简体中文](README.zh-CN.md) · [Why](#why-transmutary) · [Getting started](#getting-started) · [How it works](#how-it-works) · [Releases](#releases--versioning)

</div>

---

A repository-observation system for external open-source **ecosystem intelligence**. It continuously watches a set of repositories and their dependencies, turns changes into readable diagnostic / explanatory reports, and pushes them to its subscribers — converting *reactive post-incident investigation* into *proactive awareness*.

## Why Transmutary

Subscribers are structurally late to changes in the ecosystem they depend on:

- **Dependency breakage is found after the fact** — an upstream CLI tool changes, your internal gateway starts returning 504s, and someone manually asks an LLM to cross-check the two repos.
- **No discovery channel for AI trends** — fast-rising tools surface on social media, not in any feed you own.
- **Slow reaction to supply-chain attacks** — a malicious npm release lands before anyone notices.

Transmutary closes these gaps with a pure-pull, all-free-data-source pipeline that needs no webhooks and no paid APIs.

## Observation modes

The system is **two collection pipelines + one shared delivery layer** (two pipelines, one delivery layer — not a unified engine):

- **Mode A · event-driven (watchlist)** — watches specific repos a subscriber maintains or depends on. On any change (release, issue surge, supply-chain advisory) it detects, diagnoses the source, and pushes by severity.
- **Mode B · scheduled batch (trend radar)** — periodically scans a defined scope (MVP: AI domain), finds repos with rapidly rising stars, and emits explanatory summaries.

The two modes diverge only at the collection stage, then share `LLM report → channel delivery (private RSS / email)`. A repo discovered by Mode B can be **promoted** into Mode A's watchlist.

## How it works

```
collect → clean → dedup → filter → report → deliver
```

- **Pure-pull architecture** — no webhooks (you can't create webhooks on third-party repos); Atom feeds + incremental REST polling instead.
- **Clean before LLM** — structured checks (URL/content fingerprint, staleness, reachability) run first; only passing content reaches the LLM for chunk-level relevance filtering.
- **L1 → L2 → L3 funnel** — cheap keyword/rule gating (L1), then embedding-cosine *semantic grouping* of survivors (L2, representative-linkage, zero-miss), then the expensive LLM-as-judge once per group (L3). Authoritative supply-chain / release signals bypass L2; if embedding is unavailable the funnel degrades to full L3 so nothing is dropped.
- **Deterministic API, LLM only for semantics** — external APIs run through deterministic code; the LLM only does diagnosis / relevance / summarization. Security verdicts are cross-validated against deterministic OSV/GHSA hits.
- **Tiered scheduling** — a single resident service with internal cadences: supply-chain (minutes), releases/issues (~10 min), trends (daily).
- **Security baseline** — untrusted external content is structurally isolated from instructions (prompt-injection defense); credentials live only in env, never persisted; SSRF allowlist with no redirects; private access-controlled artifacts.

## Getting started

### Install

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

### Configure

Copy the example configs and fill them in. Credentials are read from environment variables (`TRANSMUTARY_*`) and are never persisted.

```bash
cp config/watchlist.example.yaml   config/watchlist.yaml
cp config/trend_scope.example.yaml config/trend_scope.yaml
cp config/delivery.example.yaml    config/delivery.yaml
export TRANSMUTARY_GITHUB_TOKEN=...      # read-only
export TRANSMUTARY_LLM_API_KEY=...       # any LiteLLM-supported provider
export TRANSMUTARY_LLM_BASE_URL=...      # optional: OpenAI/Anthropic-compatible endpoint
```

### Verify

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
```

## Configuration

| File | Purpose |
|---|---|
| `config/watchlist.yaml` | Mode A repos + manual dependency edges |
| `config/trend_scope.yaml` | Mode B scope filter (topics + keywords) |
| `config/delivery.yaml` | DB/artifact paths, digest hour, optional RSS feed dir + SMTP recipients |

## Output & storage

Two roots, both configured in `delivery.yaml`. All reports are private (files `0600`, dirs `0700`) and gitignored — nothing is committed.

```
<artifact_root>/
├── <owner>__<repo>/                       # per-repo analysis archive (canonical, R24)
│   └── <ts>-<kind>.md                     #   citation-bearing record of each report
├── _delivered/<route>/                    # channel-rendered reports
│   └── <owner>__<repo>-<kind>.md          #   route = immediate (urgent) | digest
└── _feed/<route>.atom.xml                 # private RSS feed, one per route

<state_db_path>  state.sqlite3  (SQLite, WAL)
  event_fingerprint   event dedup (release / advisory / issue-cluster)
  seen_set            rolling 7-day seen set (artifact diff)
  issue_baseline      per-repo issue-rate baseline
  collect_cursor      per-repo incremental since-cursor (survives restart)
  star_snapshot       Mode B star snapshots (growth rate)
  subscriber_token    per-subscriber RSS tokens (revoke / expiry)
  promoted_repo       mode-B candidates promoted into the watchlist (F4)
```

One pipeline pass (tick):

```
collect (atom + incremental REST)
  → dedup (event_fingerprint / seen_set)
  → release → diagnose   |   issues → filter funnel (L1 rules → L2 semantic group → L3 judge) → diagnose
  → diagnose (LLM + R18 quality gate + OSV/GHSA cross-validation)
  → archive per-repo artifact  +  deliver (route → _delivered/<route>/ + RSS; email on immediate)
  → persist state (advance cursor, update baseline, record fingerprints)
```

Routing is severity-driven: urgent (malware/critical) → `immediate` + email; everything else (or R18-downgraded) → `digest`.

## Promotion (mode-B → mode-A)

Promotion adds a mode-B trend candidate to the effective watchlist so it is observed by mode A. Use the `transmutary` CLI:

```bash
transmutary promote owner/repo            # add to the watchlist (persisted)
transmutary promote owner/repo --source manual
transmutary demote owner/repo             # remove
transmutary list-watchlist                # config repos + promoted repos, with source
```

The effective watchlist is `config watchlist ∪ promoted_repo`. The CLI runs in a separate process and only writes the shared `promoted_repo` table; a running service's periodic **reconcile** job (every 60s) full-syncs its per-repo jobs to the effective watchlist, so a promote/demote takes effect **without restarting** the service. Promotion never touches credentials.

## Deployment

Run the resident service (embedded tiered scheduler) via Docker:

```bash
cp .env.example .env            # fill credentials (gitignored, never baked into the image)
# prepare ./config/{watchlist,trend_scope,delivery}.yaml
#   delivery.yaml: point state_db_path & artifact_root under /var/lib/transmutary
docker compose up -d
```

The image runs as a non-root user; credentials come from `.env` at runtime; the state DB and private artifacts persist in the `transmutary-state` volume. Without Docker, run the entrypoint directly: `transmutary-serve` (reads `TRANSMUTARY_CONFIG_DIR`, default `config`).

## Architecture & docs

- Domain glossary: [`CONTEXT.md`](CONTEXT.md)
- Requirements (brainstorm): [`docs/brainstorms/`](docs/brainstorms/)
- Implementation plans: [`docs/plans/`](docs/plans/)

## Releases & versioning

Releases are automated with [python-semantic-release](https://python-semantic-release.readthedocs.io/). Version numbers, the changelog, tags, and GitHub Releases are derived from [Conventional Commits](https://www.conventionalcommits.org/) on `main`:

- `feat:` → minor · `fix:` / `perf:` → patch · `BREAKING CHANGE:` → major.

Enable the local commit-message hook once after cloning:

```bash
git config core.hooksPath .githooks
git config commit.template .gitmessage
```

See [`CHANGELOG.md`](CHANGELOG.md) for release history.

## Project

### Status

| Stage | Status |
|---|---|
| Requirements + plan | ✅ done (multi-round review) |
| Phase 0 — shared skeleton (U1-U5, U14) | ✅ done |
| Phase 1 — Mode A (collect / diagnose / deliver / supply-chain) | ✅ done · F1 real-repo milestone verified |
| Phase 2 — Mode B (trend radar) | ✅ done |
| Phase 3 — scheduling wiring (pipeline + service) | ✅ done |
| Tests | ✅ 315 passing · ruff clean |

### Roadmap

Deferred by design: critique→refine report pass, channel interface abstraction, dashboard one-click promotion button, subscription config, web dashboard, live resident run. (L2 semantic grouping is implemented.)

### Tests

```bash
.venv/bin/python -m pytest -q      # 315 passing
.venv/bin/ruff check src tests     # clean
```

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, the Conventional-Commits convention (enforced by `.githooks/commit-msg`), and the automated release process. PRs target `main`.

### License

[Apache-2.0](LICENSE) © SeasonTemple
