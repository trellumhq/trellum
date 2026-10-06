# Verify what you received

The portal and report-runner images are signed at release. Each has a signed
CycloneDX software bill of materials (SBOM) attestation attached to its image
digest. The commands below verify the `v0.1.1` release; change both the image
tag and workflow identity when verifying another release.

## Check the signature

Releases use keyless [cosign](https://docs.sigstore.dev/). The certificate
identity is the release workflow running for the exact Git tag:

```bash
cosign verify \
  --certificate-identity 'https://github.com/trellumhq/trellum/.github/workflows/release.yml@refs/tags/v0.1.1' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/trellumhq/trellum:v0.1.1

cosign verify \
  --certificate-identity 'https://github.com/trellumhq/trellum/.github/workflows/release.yml@refs/tags/v0.1.1' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/trellumhq/trellum-runner:v0.1.1
```

## Check the bill of materials

Verify the CycloneDX attestation for each image before handing it to a scanner:

```bash
cosign verify-attestation --type cyclonedx \
  --certificate-identity 'https://github.com/trellumhq/trellum/.github/workflows/release.yml@refs/tags/v0.1.1' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/trellumhq/trellum:v0.1.1

cosign verify-attestation --type cyclonedx \
  --certificate-identity 'https://github.com/trellumhq/trellum/.github/workflows/release.yml@refs/tags/v0.1.1' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/trellumhq/trellum-runner:v0.1.1
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

The repository includes `scripts/airgap_bundle.sh` to assemble a transfer
archive with image files, Compose files, documentation, a manifest, and
checksums. It does not include the report-runner image, backup and restore
scripts, or cosign verification material, so it is not a complete offline
installer. The self-hosted portal has no activation step or required
phone-home.
