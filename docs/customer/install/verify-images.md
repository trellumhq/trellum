# Verify what you received

The portal and report-runner images are signed at release. Each has a signed
CycloneDX software bill of materials (SBOM) attestation attached to its image
digest. Change `TRELLUM_VERSION` to the exact tag when verifying another
release; the commands use it for both the image tag and workflow identity.

Set the tag once and use it for both the image and the certificate identity:

```bash
TRELLUM_VERSION=v0.5.0
```

## Check the signature

Releases use keyless [cosign](https://docs.sigstore.dev/). The certificate
identity is the release workflow running for the exact Git tag:

```bash
cosign verify \
  --certificate-identity "https://github.com/trellumhq/trellum/.github/workflows/release.yml@refs/tags/${TRELLUM_VERSION}" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "ghcr.io/trellumhq/trellum:${TRELLUM_VERSION}"

cosign verify \
  --certificate-identity "https://github.com/trellumhq/trellum/.github/workflows/release.yml@refs/tags/${TRELLUM_VERSION}" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "ghcr.io/trellumhq/trellum-runner:${TRELLUM_VERSION}"
```

## Check the bill of materials

Verify the CycloneDX attestation for each image before handing it to a scanner:

```bash
cosign verify-attestation --type cyclonedx \
  --certificate-identity "https://github.com/trellumhq/trellum/.github/workflows/release.yml@refs/tags/${TRELLUM_VERSION}" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "ghcr.io/trellumhq/trellum:${TRELLUM_VERSION}"

cosign verify-attestation --type cyclonedx \
  --certificate-identity "https://github.com/trellumhq/trellum/.github/workflows/release.yml@refs/tags/${TRELLUM_VERSION}" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  "ghcr.io/trellumhq/trellum-runner:${TRELLUM_VERSION}"
```

The verified attestation identifies the SBOM for that exact image digest. The
release workflow also generates an SPDX SBOM for the portal image as a
90-day GitHub Actions artifact; it is not an attestation on the published
image. Check the release workflow's artifacts if you specifically need SPDX.

## What the image contains

- No test tooling — packages needed only for tests are absent from the runtime
  image, because every package present is CVE surface your scanner will ask
  about
- Optional data-source drivers — both images include the framework's supported
  database, cloud-file, and object-storage clients. Their presence enables
  configured integrations; it does not mean the portal makes an outbound call
  by default.
- Processes run as an unprivileged user (uid 10001)

## Air-gapped installs

Trellum does not currently provide an offline installation bundle or bundle
generator. The previous incomplete helper has been removed. The documented
installation procedure requires access to the published images.

A manually prepared air-gapped deployment needs both portal and report-runner
images, Postgres when bundled, all required Compose-mounted scripts, and
offline verification material. A complete disconnected installation and
backup/restore procedure has not yet been verified.
