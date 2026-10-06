# Releasing Trellum

Trellum ships from one monorepo under one version. The control plane and the
standalone reporting framework are released from the same commit, while the
framework can also be installed independently from its wheel.

## Release contract

- `trellum_portal/__init__.py` and `trellum/__init__.py` carry the same
  `__version__`.
- The release tag is `vX.Y.Z`, matching both versions exactly.
- The tag publishes the portal and runner images to GHCR and creates the
  GitHub Release through `.github/workflows/release.yml`, then dispatches the
  Pages workflow after the release assets exist.
- The wheel is built and tested by `.github/workflows/framework.yml`, then
  rebuilt from the exact tag and attached to the GitHub Release. Publishing
  it to PyPI is a separate, manually approved action.
- Published tags and releases are immutable. Fix a bad release with a new
  patch version; never move or delete a published tag.

Semantic versioning has one project rule: any change under `trellum/` that
changes report-build behavior is at least a MINOR release. Documentation and
packaging-metadata-only fixes may use a PATCH release. A breaking component,
`ctx`, `report.yaml`, validator, configuration, or migration contract is a
MAJOR release and must include concrete upgrade steps in `CHANGELOG.md`.

## Prepare the release

1. Merge every intended change to `main`. The pull requests must have their
   applicable `portal.yml` and `framework.yml` checks green.
2. Move the entries from `[Unreleased]` into one dated `[X.Y.Z]` section in
   `CHANGELOG.md`. Keep `[Unreleased]` above it for future work. Include every
   manual upgrade step under **Upgrade notes**.
3. Set the same version in both package files.
4. Run the full framework workflow on the exact `main` revision. This dispatch
   runs the test/demo, data-source integration, and standalone-wheel jobs even
   when the release preparation itself changed only documentation:

   ```bash
   gh workflow run framework.yml --ref main -f publish=false
   gh run list --workflow framework.yml --branch main --event workflow_dispatch --limit 1
   gh run watch <run-id> --exit-status
   ```

5. Confirm the latest `portal.yml` run on `main` passed. That workflow runs on
   every non-documentation push to `main` and covers version agreement, Django,
   sandbox, migration-safety, and both end-to-end topologies:

   ```bash
   gh run list --workflow portal.yml --branch main --limit 1
   gh run watch <run-id> --exit-status
   ```

6. Run the on-demand lanes when their protected surface changed. Browser
   benchmarks cover rendering performance; the Vertica lane requires its
   registry credentials and covers Vertica/data-driver changes:

   ```bash
   gh workflow run benchmarks.yml --ref main
   gh workflow run vertica.yml --ref main
   gh run list --workflow benchmarks.yml --branch main --limit 1
   gh run list --workflow vertica.yml --branch main --limit 1
   ```

   Watch each applicable run with `gh run watch <run-id> --exit-status`. Record
   why a conditional lane was not applicable rather than treating it as an
   automatic release gate.

## Tag and publish the GitHub release

Start from a clean, current `main`, then verify the versions and preview the
release notes with the same extraction used by `release.yml`:

```bash
git switch main
git pull --ff-only origin main
test -z "$(git status --porcelain)"

# Use the new, unreleased version prepared above.
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' trellum_portal/__init__.py)
PORTAL=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' trellum_portal/__init__.py)
FRAMEWORK=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' trellum/__init__.py)
test "$PORTAL" = "$VERSION"
test "$FRAMEWORK" = "$VERSION"

awk -v v="$VERSION" \
  '/^## \[/ { on = index($0, "## [" v "]") == 1; next } on' \
  CHANGELOG.md > /tmp/trellum-release-notes.md
test -s /tmp/trellum-release-notes.md
cat /tmp/trellum-release-notes.md
```

Create and push one annotated tag:

```bash
git tag -a "v$VERSION" -m "Trellum v$VERSION"
git push origin "v$VERSION"
```

The tag starts `.github/workflows/release.yml`. It checks the tag against both
versions, builds and scans the portal and runner images, publishes them to
`ghcr.io/trellumhq/trellum` and `ghcr.io/trellumhq/trellum-runner`, signs both
digests with keyless Cosign, attaches SBOM attestations, verifies the published
signatures, uploads the SBOM artifacts, and then creates the GitHub Release
from the changelog section, attaches the wheel, and dispatches the Pages
workflow on `main`. Do not create the GitHub Release by hand.

Watch the release to completion:

```bash
gh run list --workflow release.yml --branch "v$VERSION" --limit 1
gh run watch <run-id> --exit-status
gh release view "v$VERSION"
```

If the workflow fails before publishing either image, keep the tag fixed,
repair the workflow on `main`, and dispatch it against the existing tag:

```bash
gh workflow run release.yml --ref main -f tag="v$VERSION"
```

The workflow refuses to replace an existing portal or runner image tag. If
only the GitHub Release job failed after the image job completed, re-run that
failed job to finish attaching the original wheel. If the image job partially
published or the tagged product itself is wrong, release a new version; do not
delete or overwrite the existing artifacts.

## Publish the standalone wheel to PyPI

PyPI publication is deliberately separate from the tag workflow. A PyPI
project owner must configure the Trusted Publisher for this repository,
`.github/workflows/framework.yml`, and the protected `pypi` environment. A
repository environment approver then authorizes each upload; no API token is
stored in GitHub.

After the GitHub release succeeds, the release owner dispatches the framework
workflow at the immutable tag and requests publication:

```bash
gh workflow run framework.yml --ref "v$VERSION" -f publish=true
gh run list --workflow framework.yml --branch "v$VERSION" --event workflow_dispatch --limit 1
gh run watch <run-id> --exit-status
```

The workflow rebuilds and tests the wheel before the protected `publish` job
uploads it. A tag push by itself never publishes to PyPI.

The wheel's full PyPI description comes from `trellum/README.md`; its short
summary comes from the `description` field in `trellum/pyproject.toml`. Before
the first upload of a version, inspect the built wheel's `METADATA` to confirm
both fields and its rendered README are correct. PyPI retains the metadata from
the first upload for that version, so a correction requires a new patch release;
GitHub changes cannot refresh metadata already published to PyPI.

Deleted PyPI filenames also remain reserved permanently. If an upload is
rejected because that filename was previously used, prepare a new patch
release; do not retry the same version or delete existing release artifacts.

## Documentation and demo Pages

The public website and documentation ship from this repository as one Pages
site: website source lives under `website/`, canonical customer documentation
under `docs/`, and the synthetic demo gallery under `/demo/`. The combined
site is published at `https://trellum.dev/`, with the gallery at
`https://trellum.dev/demo/`. `pages.yml` owns the deployment; `release.yml`
dispatches it only after the GitHub Release and wheel exist.

`scripts/sync-docs.sh` reads `docs/customer/` from stable release tags and copies
the newest version to `/docs/latest/`. It does not publish uncommitted or
unreleased documentation changes when stable tags exist. A documentation-only
correction can ship in a patch release; existing tags remain unchanged. Website
templates and blog content, in contrast, deploy from `main` on a Pages run.

After publishing a release, `.github/workflows/release.yml` dispatches
`.github/workflows/pages.yml` on `main` with deployment enabled. The Pages
workflow selects the latest published stable release, builds every demo report
from that tag, and publishes the combined trusted-repository site. Stable tag
pushes do not deploy Pages. Check the dispatched workflow independently:

```bash
gh run list --workflow pages.yml --branch main --event workflow_dispatch --limit 1
gh run watch <run-id> --exit-status
```

Scheduled Pages runs rebuild the latest published stable release. Manual runs
are previews unless deployment is explicitly requested on `main` and a
published stable release exists. If the automatic dispatch fails after the
release is published, recover with:

```bash
gh workflow run pages.yml --ref main -f deploy=true
```

Verify both the documentation and `/demo/` paths after the deployment
completes.

## Hotfixes

Branch from the published tag, make the smallest fix, update both versions and
the changelog to the next patch, and open a pull request to `main`. After the
same checks pass and the change is merged, tag the resulting `main` commit by
following the normal process above. Do not release from a side branch or
maintain a moving `release` branch.
