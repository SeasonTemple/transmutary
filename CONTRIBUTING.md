# Contributing

## Development setup

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev,build]"
.venv/bin/python -m pytest -q      # 239 passing
.venv/bin/ruff check src tests     # clean
```

Enable the commit tooling once after cloning:

```bash
git config core.hooksPath .githooks   # enables the Conventional-Commits check
git config commit.template .gitmessage
```

## Commit convention

Commits **must** follow [Conventional Commits](https://www.conventionalcommits.org/). This is not cosmetic — [python-semantic-release](https://python-semantic-release.readthedocs.io/) derives every version number, changelog entry, tag, and GitHub Release from commit `type`s on `main`. A malformed message is rejected locally by `.githooks/commit-msg`.

```
<type>(<scope>): <summary>
```

| Type | Effect on next release |
|---|---|
| `feat` | **minor** bump · shown under *Features* |
| `fix` / `perf` | **patch** bump · shown under *Bug Fixes* / *Performance* |
| `docs` / `build` | no bump · shown in changelog |
| `chore` / `ci` / `style` / `test` / `refactor` / `revert` | no bump · hidden from release notes |
| any type + `BREAKING CHANGE:` in body | **major** bump |

- **Scope** (optional, kebab-case): `collect`, `dedup`, `filter`, `diagnose`, `deliver`, `trend`, `explain`, `pipeline`, `service`, `config`, `store`, `llm`, `release`.
- **Summary**: imperative, lowercase start, header ≤ 72 chars including the prefix.
- Template lives in [`.gitmessage`](.gitmessage); validator in [`.githooks/commit-msg`](.githooks/commit-msg).

```
feat(trend): add star-snapshot fallback when OSS Insight is down
fix(filter): use deterministic cold-start threshold on empty baseline
```

## Release process

Releases are **fully automated** — there is no manual version bump or tag step.

1. Land Conventional Commits on `main` (direct push or merged PR).
2. `.github/workflows/release.yml` runs: it re-verifies (`ruff` + `pytest`), then python-semantic-release computes the next version from the commits since the last tag.
3. If a releasable commit is present (`feat` / `fix` / `perf` / `BREAKING CHANGE`), it:
   - bumps `project.version` in `pyproject.toml`,
   - updates [`CHANGELOG.md`](CHANGELOG.md) (sectioned by type),
   - commits `chore(release): X.Y.Z`, tags `vX.Y.Z`,
   - builds sdist + wheel and publishes a GitHub Release with generated notes.
4. No releasable commit → no release (docs/chore-only pushes ship nothing).

Version policy: `0.x` while pre-1.0 (`major_on_zero = false`, so `feat` stays a minor bump). Layout of changelog/release notes is pinned in `[tool.semantic_release]` in `pyproject.toml` — do not hand-edit `CHANGELOG.md`.

### Repo prerequisite (one-time)

The release job pushes the tag and changelog commit back to `main`, so the repository must allow it:

- **Settings → Actions → General → Workflow permissions → Read and write permissions.**
- `main` must not have branch protection that blocks the `github-actions` bot, or PSR cannot push the release commit.

## Pull requests

- Target `main`.
- CI (`ruff` + `pytest` on Python 3.9 and 3.12) must be green.
- Keep changes scoped; one logical change per PR.

## License

By contributing you agree your contributions are licensed under [Apache-2.0](LICENSE).
