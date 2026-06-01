# Release workflow

Transmutary uses python-semantic-release for version calculation, changelog
updates, tags, and GitHub Release creation. Human-facing release notes are not
left to the generated body: every published tag must have a curated bilingual
note at `docs/release-notes/vX.Y.Z.md`.

## Normal path

1. Let Conventional Commits drive the version:

   - `feat:` -> minor
   - `fix:` / `perf:` -> patch
   - `BREAKING CHANGE:` -> major

2. Before merging the releasable PR, prepare the expected note file:

   ```bash
   python tools/release_notes.py prepare vX.Y.Z
   ```

   The helper does not bump versions, commit, tag, or push. It only creates the
   bilingual stub if it is missing.

3. Replace every placeholder and keep both sections:

   ```md
   ## 中文

   ...

   ## English

   ...
   ```

4. Validate locally:

   ```bash
   python tools/release_notes.py check vX.Y.Z
   ruff check src tests tools
   python -m pytest -q
   ```

5. Merge to `main`. The release workflow verifies the repo, runs
   python-semantic-release, then checks `docs/release-notes/<tag>.md` for the
   tag it just published, overwrites the GitHub Release body with that file,
   and publishes the Docker image to GHCR:

   - `ghcr.io/seasontemple/transmutary:vX.Y.Z`
   - `ghcr.io/seasontemple/transmutary:X.Y.Z`
   - `ghcr.io/seasontemple/transmutary:latest`

## Guardrails

- Missing release-note file fails the release job.
- Missing `## 中文` or `## English` fails the release job.
- Template placeholders must be removed before publishing.
- Docker images are built from the released tag, not from the pre-release
  workflow checkout.
- The generated `CHANGELOG.md` remains machine-derived history. The curated
  `docs/release-notes/` files are the source of truth for GitHub Release body.

## Emergency repair

If a release was already published with generated notes:

```bash
python tools/release_notes.py check vX.Y.Z
gh release edit vX.Y.Z --notes-file docs/release-notes/vX.Y.Z.md
```

If the Docker image is missing for an already-published tag, run the manual
`Docker Image` workflow with input `tag=vX.Y.Z`.
