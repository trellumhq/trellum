#!/usr/bin/env bash
# Build an offline install bundle for an air-gapped customer.
#
#   scripts/airgap_bundle.sh v1.2.3 [output-dir]
#
# Produces trellum-airgap-<version>.tar.gz containing every image the stack
# needs, the compose files, and a runbook. The target site needs Docker and
# nothing else — no registry, no internet, no pip.
#
# Why images rather than sources: an air-gapped site cannot reach PyPI, so a
# build there is not merely inconvenient, it is impossible. And the customer
# must be able to verify they received what we published, which is a property
# of a signed image, not of a directory of files.
set -euo pipefail

VERSION="${1:?usage: airgap_bundle.sh <version> [output-dir]}"
OUTDIR="${2:-dist}"
REGISTRY="${TRELLUM_REGISTRY:-ghcr.io}"
REPO="${TRELLUM_REPO:-trellum}"
APP_IMAGE="${TRELLUM_IMAGE:-${REGISTRY}/${REPO}:${VERSION}}"
PG_IMAGE="${TRELLUM_PG_IMAGE:-postgres:16-alpine}"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
BUNDLE="trellum-airgap-${VERSION}"
mkdir -p "$STAGE/$BUNDLE"
mkdir -p "$OUTDIR"

ensure_image() {
    # Pull only what we do not already have: a release built moments ago is
    # present locally and may not be pushed yet.
    if docker image inspect "$1" >/dev/null 2>&1; then
        echo "    $1 (already local)"
    else
        echo "    $1 (pulling)"
        docker pull "$1"
    fi
}

echo "==> Collecting images"
ensure_image "$APP_IMAGE"
ensure_image "$PG_IMAGE"

echo "==> Saving images (this is the slow part)"
docker save "$APP_IMAGE" "$PG_IMAGE" | gzip -9 > "$STAGE/$BUNDLE/images.tar.gz"

echo "==> Copying deployment files"
cp docker-compose.yml "$STAGE/$BUNDLE/"
cp docker-compose.external-db.yml "$STAGE/$BUNDLE/"
[ -f .env.example ] && cp .env.example "$STAGE/$BUNDLE/"
# An air-gapped host has no route to trellum.dev, so the bundle carries the
# documentation itself. It lives in THIS repository now (docs/customer/), which
# means the pages shipped here are the ones written for this exact release --
# they cannot describe a version the customer is not running.
mkdir -p "$STAGE/$BUNDLE/docs"
if [ -d docs/customer ]; then
  cp -r docs/customer/. "$STAGE/$BUNDLE/docs/"
  # nav.yml orders the sidebar on the website; it is noise in a tarball.
  rm -f "$STAGE/$BUNDLE/docs/nav.yml"
else
  echo "ERROR: docs/customer not found -- refusing to ship a bundle with no docs" >&2
  exit 1
fi

echo "==> Recording provenance"
{
  echo "bundle_version: ${VERSION}"
  echo "app_image: ${APP_IMAGE}"
  echo "app_digest: $(docker inspect --format='{{index .RepoDigests 0}}' "$APP_IMAGE" 2>/dev/null || echo unknown)"
  echo "postgres_image: ${PG_IMAGE}"
  echo "built_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$STAGE/$BUNDLE/MANIFEST.txt"

cat > "$STAGE/$BUNDLE/INSTALL-AIRGAP.md" <<'RUNBOOK'
# Offline install

No internet access is required at any point.

## 1. Verify the bundle

```bash
sha256sum -c SHA256SUMS
```

## 2. Load the images

```bash
gunzip -c images.tar.gz | docker load
docker images | grep -E 'trellum|postgres'
```

## 3. Configure

```bash
cp .env.example .env
```

Set at minimum:

- `TRELLUM_IMAGE` — the app image tag printed by `docker load` (also in MANIFEST.txt)
- `SESSION_SECRET_KEY`, `SECRET_ENCRYPTION_KEY`, `POSTGRES_PASSWORD`
- `PORTAL_BASE_URL`, `ALLOWED_HOSTS`

## 4. Start

```bash
docker compose up -d
docker compose exec web python manage.py doctor
```

Then open `/setup` for the first-run wizard.

For production, put the database on a server you own and use the external-db
overlay — see `docs/install/configuration.md` in this bundle.

## 5. Upgrading later

Load the next bundle's images, update `TRELLUM_IMAGE` in `.env`, then:

```bash
docker compose up -d
```

The `migrate` service completes before web and worker start serving.

## Getting help

```bash
docker compose exec web python manage.py supportbundle
```

Inspect the archive, then send it to us. It contains no report data, no
credentials and no secrets.
RUNBOOK

echo "==> Checksums"
( cd "$STAGE/$BUNDLE" && sha256sum ./* ./docs/* 2>/dev/null > SHA256SUMS || true )

echo "==> Packing"
tar czf "${OUTDIR}/${BUNDLE}.tar.gz" -C "$STAGE" "$BUNDLE"

SIZE=$(du -h "${OUTDIR}/${BUNDLE}.tar.gz" | cut -f1)
echo
echo "Wrote ${OUTDIR}/${BUNDLE}.tar.gz (${SIZE})"
echo "Transfer it to the air-gapped host and follow INSTALL-AIRGAP.md."
