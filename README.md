<div align="center">

# Transmutary

**Proactive open-source ecosystem intelligence — continuously watch repositories and their dependencies, turn changes into diagnostic reports, and get pushed what matters before it becomes an incident.**

<img src="assets/dashboard.png" alt="Transmutary dashboard: watchlist, supply-chain alerts, trend candidates, and recent reports" width="920" />

<sub>Dashboard + offline demo: observe repos, triage supply-chain alerts, promote trend candidates, and preview the full pipeline with zero credentials. <a href="#dashboard">Open the dashboard →</a></sub>

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/SeasonTemple/transmutary/actions/workflows/ci.yml/badge.svg)](https://github.com/SeasonTemple/transmutary/actions/workflows/ci.yml)
[![Tests: 440 passing](https://img.shields.io/badge/tests-440_passing-brightgreen.svg)](#tests)

[English](README.md) · [简体中文](README.zh-CN.md) · [Why](#why-transmutary) · [Dashboard](#dashboard) · [Try the demo](#try-the-demo) · [Getting started](#getting-started) · [How it works](#how-it-works) · [Releases](#releases--versioning)

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

## Try the demo

See the whole pipeline run in one command — **zero credentials, zero network, zero LLM**:

<img src="assets/demo.gif" alt="transmutary-demo: one offline command runs the full pipeline" width="720" />

```bash
pip install -e .
transmutary-demo
```

It feeds the *real* pipeline (`build_runtime` + the three ticks) built-in mock data through an `httpx.MockTransport` and a stub LLM, so nothing leaves the process: no GitHub token, no API key, no outbound HTTP, no model call. One pass produces a release diagnosis, an issue-surge diagnosis (with dependency-edge related context), a supply-chain alert from a deterministic OSV hit, and three trend explanations.

Artifacts land in a fresh temp directory (printed at the top of the run; pass `--out DIR` to choose one) with the exact same private layout the real service writes — `0700` dirs, `0600` files:

```
<artifact_root>/
├── octocat__hexbridge-cli/        # per-repo analysis archive (canonical, R24)
│   └── <ts>-diagnose.md
├── _delivered/
│   ├── immediate/                 # urgent route: diagnosis + supply-chain alert
│   └── digest/                    # digest route: trend explanations
└── _feed/
    ├── immediate.atom.xml         # private RSS feeds, one per route
    └── digest.atom.xml
```

The run prints the artifact tree plus a couple of rendered-report excerpts so you can read the output it would deliver. Reproduce by copy-pasting the two commands above — no setup.

To refresh the README terminal GIF after CLI/demo copy changes:

```bash
vhs assets/demo.tape
```

`assets/demo.tape` records only the offline `transmutary-demo` command. The Web dashboard image (`assets/dashboard.png`) should be refreshed from a browser screenshot so the admin UI is captured as users see it.

## How it works

```
collect → clean → dedup → filter → report → deliver
```

- **Pure-pull architecture** — no webhooks (you can't create webhooks on third-party repos); Atom feeds + incremental REST polling instead.
- **Clean before LLM** — structured checks (URL/content fingerprint, staleness, reachability) run first; only passing content reaches the LLM for chunk-level relevance filtering.
- **L1 → L2 → L3 funnel** — cheap keyword/rule gating (L1), then embedding-cosine *semantic grouping* of survivors (L2, representative-linkage, zero-miss), then the expensive LLM-as-judge once per group (L3). Authoritative supply-chain / release signals bypass L2; if embedding is unavailable the funnel degrades to full L3 so nothing is dropped.
- **Deterministic API, LLM only for semantics** — external APIs run through deterministic code; the LLM only does diagnosis / relevance / summarization. Security verdicts are cross-validated against deterministic OSV/GHSA hits.
- **Tiered scheduling** — a single resident service with internal cadences: supply-chain (minutes), releases/issues (~10 min), trends (daily).
- **Security baseline** — untrusted external content is structurally isolated from instructions (prompt-injection defense); non-LLM credentials live only in env, the LLM key is env-or-`0600`-file (see [Credential security model](#credential-security-model)); SSRF allowlist with no redirects; private access-controlled artifacts.

## Getting started

### Install

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

### Configure

Copy the example configs and fill them in. Non-LLM credentials (GitHub token, SMTP, RSS) are read from environment variables only. LLM credentials may come from env vars **or** `config/llm.yaml` (0600 permissions).

```bash
cp config/watchlist.example.yaml   config/watchlist.yaml
cp config/trend_scope.example.yaml config/trend_scope.yaml
cp config/delivery.example.yaml    config/delivery.yaml
export TRANSMUTARY_GITHUB_TOKEN=...      # read-only
```

**LLM configuration** — three options (pick one):

```bash
# Option 1: environment variables
export TRANSMUTARY_LLM_API_KEY=...       # any LiteLLM-supported provider
export TRANSMUTARY_LLM_BASE_URL=...      # optional: OpenAI/Anthropic-compatible endpoint

# Option 2: interactive CLI wizard
.venv/bin/transmutary config

# Option 3: Dashboard Settings panel → LLM Configuration
#           (available after starting the dashboard)
```

### Credential security model

API keys are stored the same way every comparable tool stores them (opencode, `llm`, aider, and the headless fallback of `gh` / Claude Code all do this): a **`0600` file plus an environment-variable override**, not an OS keychain.

- **Non-LLM credentials** (GitHub token, SMTP, RSS) — **environment variables only**, never written to disk by transmutary.
- **LLM key** — env var (`TRANSMUTARY_LLM_API_KEY`) takes precedence; otherwise `config/llm.yaml`, created `0600` (owner read/write only) and **gitignored**. The dashboard masks it (shows last-4) and never echoes it back.
- **env = locked layer, dashboard/yaml = the mutable runtime layer.** By default the LLM config is managed through the dashboard / `transmutary config` (writes `config/llm.yaml`). Setting a `TRANSMUTARY_LLM_*` env var **locks** that specific field for ops injection: env always wins, and the dashboard renders the field **read-only** with a badge naming the variable — so a UI edit is never silently ignored. Leave the env vars unset for a self-hosted deployment where the dashboard is your config surface.
- **Why not an OS keychain?** transmutary is a long-lived headless service. Keychains assume an unlocked interactive session; an unattended daemon would have to store an unlock secret to open the keychain — moving the plaintext secret down a layer, not removing it, while adding D-Bus/keyring babysitting and known keychain footguns. `0600` already meets the realistic single-tenant-host threat model (it stops other users/processes; nothing in user space stops an attacker who already has code-exec as the service user — a keychain wouldn't either).
- **Real secret management for a daemon belongs in the environment** — inject the key via systemd `EnvironmentFile=` (itself `0600`), Docker/Kubernetes secrets, or a vault sidecar, and leave `config/llm.yaml` empty. Run transmutary as a dedicated unprivileged user.
- **Defense-in-depth in this repo** — `.env` and `config/llm.yaml` are gitignored; a `pre-commit` hook scans staged content for key patterns (`sk-…`, `ghp_…`, `github_pat_…`, PEM private keys) and blocks the commit before a secret can ever enter git history.

### Per-tier models

The pipeline uses three model tiers, each independently configurable:

| Tier | Used for | Default |
|------|----------|---------|
| **strong** | diagnosis, issue-surge L3 judge, critique/refine | `gpt-4o` |
| **cheap** | supply-chain advice, trend summaries | `gpt-4o-mini` |
| **embed** | L2 semantic grouping (clusters issue-surge / trend candidates to collapse L3 calls) | `text-embedding-3-small` |

By default all three share one provider (the shared `api_key`/`base_url`/`transport` above). A tier can run on a **different provider** by setting a per-tier override — each of `api_key`/`base_url`/`transport`/`model` falls back to the shared value when left unset. Configure overrides via the dashboard's "Per-tier overrides (advanced)" disclosure, `transmutary config`, or env vars `TRANSMUTARY_LLM_<TIER>_<FIELD>` (e.g. `TRANSMUTARY_LLM_EMBED_BASE_URL`).

**Common case** — chat on one provider, embeddings on another (the chat provider has no embedding API). Set the embed tier's `base_url`/`transport`/`model` (and `api_key` if different); the rest inherits the shared chat config.

**Embed is optional.** If no working embedding endpoint is configured, the L2 grouping step degrades to full L3 (every candidate judged individually) — correct results, just more LLM calls. Leaving embed unset is a valid deployment.

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
| `config/llm.yaml` | LLM API key + optional base URL (0600, env vars take precedence) |

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

The effective watchlist is `config watchlist ∪ promoted_repo`. The CLI runs in a separate process and only writes the shared `promoted_repo` table; a running service's periodic **reconcile** job (every 10 min) full-syncs its per-repo jobs to the effective watchlist, so a promote/demote takes effect **without restarting** the service. Promotion never touches credentials.

## Deployment

Run the resident service (embedded tiered scheduler) via Docker:

```bash
docker pull ghcr.io/seasontemple/transmutary:latest
cp .env.example .env            # fill credentials (gitignored, never baked into the image)
# prepare ./config/{watchlist,trend_scope,delivery}.yaml
#   delivery.yaml: point state_db_path & artifact_root under /var/lib/transmutary
docker compose up -d
# optional local dashboard/admin UI
docker compose --profile dashboard up -d dashboard
```

The image runs as a non-root user; credentials come from `.env` at runtime; the state DB and private artifacts persist in the `transmutary-state` volume. The dashboard profile shares the same config mount and state volume, binds `127.0.0.1:8787` by default, and expects `TRANSMUTARY_ADMIN_TOKEN` for the settings UI. For public access, keep it behind HTTPS/auth/rate limiting. Without Docker, run the entrypoints directly: `transmutary-serve` and `transmutary-dashboard` (both read `TRANSMUTARY_CONFIG_DIR`, default `config`).

Release images are published to GHCR as
`ghcr.io/seasontemple/transmutary:vX.Y.Z`, `ghcr.io/seasontemple/transmutary:X.Y.Z`,
and `ghcr.io/seasontemple/transmutary:latest`. For a local source build, use
`docker build -t transmutary:local .` and run compose with
`TRANSMUTARY_IMAGE=transmutary:local`.

## Dashboard

A local web view over the system's runtime state and archived reports — open it in a browser to see the effective watchlist, recent diagnostic/explanatory reports, supply-chain alerts, trend candidates, per-repo runtime (issue baseline / star snapshots / cursor), and feed links.

```bash
pip install -e ".[dashboard]"     # adds jinja2 (Starlette/uvicorn are already core)
transmutary-dashboard             # serves on http://127.0.0.1:8787
```

It reuses the existing Starlette stack and store interfaces. On localhost it can promote/demote Mode B candidates through a server-side confirmation flow; the POST only writes the shared `promoted_repo` table, so the resident service picks it up on the next reconcile pass without restart.

The Settings area is an authenticated admin control plane for non-secret config: add/remove tracked repos, add manual dependency edges, edit trend topics/keywords, and adjust email recipients / digest hour. It does **not** edit YAML files directly. Effective runtime config is `YAML base ∪ SQLite admin overrides ∪ promoted_repo`; the service reconcile job picks up repo-scope changes without restart. Provider secrets stay env-only: GitHub/SMTP/RSS/LLM credentials are never accepted through the UI, never rendered, and never persisted. The UI only shows configured/missing status.

Security posture: binds `127.0.0.1` by default; a non-localhost bind is **refused** unless you pass `--allow-public`; public binds stay **read-only** unless you also pass `--allow-public-writes`. Settings writes require `TRANSMUTARY_ADMIN_TOKEN` login, a signed HttpOnly session cookie, double-submit CSRF, `SameSite=Strict`, Origin/Referer host check, same-origin form action, and server-side validation. Public admin use should still sit behind HTTPS, rate limits, and repo allow-lists.

External repository content is HTML-escaped (XSS), dangerous source URLs are blanked, and credentials/tokens are never rendered. Filesystem paths (`state_db_path`, `artifact_root`, `feed_dir`) and provider credentials remain YAML/env-owned.

The UI is a modern sidebar dashboard (stat tiles, Sentry-style issue stream, severity encoded by colour + icon + text for accessibility), with a light/dark theme toggle and an EN/中文 language toggle (both remembered, both rendered server-side on first paint so there is no flash). A per-request CSP nonce keeps the inline theme bootstrap script precisely allow-listed without weakening the policy.

**Agent-native access** — every data endpoint supports content negotiation: request `Accept: application/json` (or `?format=json`) to get the view-model as JSON instead of HTML, with the same credential/token exclusion as the HTML path. `GET /llms.txt` describes the endpoint surface for agents (no private data).

### LAN / intranet access

To expose the dashboard on your local network (e.g. for team viewing or a reverse proxy on another machine), use the `dashboard-lan` profile:

```bash
docker compose --profile dashboard-lan up -d dashboard-lan
```

This binds `0.0.0.0:8787` on the host while reusing the same `.env`, config, and `transmutary-state` volume. The `--allow-public` flag is already set (it skips Host-header validation and sets `csrf_secure=False` so cookies work over plain HTTP). All hard gates remain: explicit opt-in, CSRF double-submit, and `TRANSMUTARY_ADMIN_TOKEN` authentication.

**Important:** this mode accepts plaintext HTTP on your LAN. Only run it on a trusted network segment. For public internet access, place it behind an HTTPS reverse proxy with authentication and rate limiting (see security posture above).

## Architecture & docs

- Domain glossary: [`CONTEXT.md`](CONTEXT.md)
- Requirements (brainstorm): [`docs/brainstorms/`](docs/brainstorms/)
- Implementation plans: [`docs/plans/`](docs/plans/)

## Releases & versioning

Releases are version-automated with [python-semantic-release](https://python-semantic-release.readthedocs.io/). Version numbers, the changelog, tags, and GitHub Releases are derived from [Conventional Commits](https://www.conventionalcommits.org/) on `main`:

- `feat:` → minor · `fix:` / `perf:` → patch · `BREAKING CHANGE:` → major.

GitHub Release body is curated, not left to generated notes. Each published tag
must have a bilingual note at `docs/release-notes/vX.Y.Z.md` with both `## 中文`
and `## English`; the release workflow applies that file after publishing.
Release assets include the Python wheel/sdist and a GHCR Docker image.

Enable the local commit-message hook once after cloning:

```bash
git config core.hooksPath .githooks
git config commit.template .gitmessage
```

See [`CHANGELOG.md`](CHANGELOG.md) for machine-derived history and
[`docs/release-workflow.md`](docs/release-workflow.md) for the release-note
policy.

## Project

### Status

| Stage | Status |
|---|---|
| Requirements + plan | ✅ done (multi-round review) |
| Phase 0 — shared skeleton (U1-U5, U14) | ✅ done |
| Phase 1 — Mode A (collect / diagnose / deliver / supply-chain) | ✅ done · F1 real-repo milestone verified |
| Phase 2 — Mode B (trend radar) | ✅ done |
| Phase 3 — scheduling wiring (pipeline + service) | ✅ done |
| Phase B — F4 promotion · deployment · L2 semantic grouping · critique→refine | ✅ done |
| Offline demo (`transmutary-demo`) | ✅ done |
| Web dashboard (`transmutary-dashboard`) | ✅ done |
| Tests | ✅ 440 passing · ruff clean |

### Roadmap

Deferred by design: channel interface abstraction, Web secret storage/rotation, multi-user RBAC/OAuth, subscription config, live resident run controls. (The dashboard, admin non-secret settings UI, one-click promote/demote UI, L2 semantic grouping, and the optional critique→refine report pass are implemented — see [Dashboard](#dashboard) and [How it works: optional critique→refine](#how-it-works-optional-critiquerefine-r11).)

### Tests

```bash
.venv/bin/python -m pytest -q      # 440 passing
.venv/bin/ruff check src tests     # clean
```

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, the Conventional-Commits convention (enforced by `.githooks/commit-msg`), and the automated release process. PRs target `main`.

### License

[Apache-2.0](LICENSE) © SeasonTemple


## How it works: optional critique→refine (R11)

Both diagnostic reports (mode A) and explanatory reports (mode B) support an
**optional** three-stage quality pass: `synthesize (draft) → critique → refine`,
**off by default**. It is enabled per-tick via `refine_reports=True` on
`run_release_issue_tick` / `run_trend_tick`.

- **Synthesize** — produce a single-pass draft (today's behavior).
- **Critique** — have the model critique its own draft against the evidence
  (unsupported claims / omissions / logic gaps).
- **Refine** — rewrite the draft per the critique, without going beyond the
  supplied evidence.

The critique/refine **instructions** go in the trusted system slot; the draft,
the critique, and the evidence go **only** in the untrusted data slot (injection
isolation, KTD3). The key guarantee: **the revised text is NOT exempt from any
security control** — a diagnostic revised draft passes the exact same OSV/GHSA
cross-validation + verdict sanitization + R18 source gate as the single-pass
draft; critique→refine runs only *before* draft generation and never bypasses the
downstream security pipeline (KTD-C). Any LLM failure in either stage degrades to
the draft, so a report is never lost to it (KTD-D). With `refine_reports=False`
(the default), behavior is identical to before.
