# Verify what you received

The portal and report-runner images are signed at release. Each has a signed
CycloneDX software bill of materials (SBOM) attestation attached to its image
digest. Change `TRELLUM_VERSION` to the exact tag when verifying another
release; the commands use it for both the image tag and workflow identity.

Set the tag once and use it for both the image and the certificate identity:

```bash
TRELLUM_VERSION=v0.10.1
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

## Recurring vulnerability checks and triage

The **published image scan** GitHub Actions workflow runs every Monday at
06:23 UTC (GitHub may delay scheduled runs), and maintainers can also start it
manually from the repository's Actions tab. It resolves GitHub's latest
published stable release once, then scans both the portal and report-runner
images for that exact version tag, using immutable registry digests. It does
not build or publish images. Release-time scanning remains in place.

Each run records the release URL and ID, version tag and image digests in its
job summaries and retains identity, registry manifest and scan reports as
Actions artifacts for 30 days. The pinned Trivy scanner uses a current
vulnerability database and the same threshold as the release workflow:
**HIGH or CRITICAL vulnerabilities with an available fix fail the scan**.
Unfixed vulnerabilities and lower severities do not fail this gate; a passing
run is not a claim that the image has no vulnerabilities. Both image jobs run
even if one fails. Missing images, registry access denial, scanner/database
download errors and timeouts fail visibly rather than count as clean scans.
Check the failed step's logs when no scan report could be produced.

The Trellum repository maintainers handling **security@trellum.dev**, the
existing reporting route in
[SECURITY.md](https://github.com/trellumhq/trellum/blob/main/SECURITY.md), own
triage of failed weekly/manual runs and dependency-update PRs. After each
weekly or manual run they review the
[published image scan runs](https://github.com/trellumhq/trellum/actions/workflows/published-image-scan.yml),
distinguish operational failures from vulnerability findings, and investigate
affected packages and available fixes. Operational failures require restoring
the scan and rerunning it; confirmed security issues follow the existing
[private vulnerability reporting route](https://github.com/trellumhq/trellum/security/advisories/new)
or **security@trellum.dev**. Do not post sensitive vulnerability details in
public issues. There is no additional messaging integration.

Dependabot checks pip manifests (portal, framework, integration tests and
website) and GitHub Actions weekly on Mondays at 07:00 UTC. Updates are grouped
by ecosystem, with separate security-update groups and a limit of two open
version-update PRs per ecosystem. GitHub applies its separate security-update
PR limit; the version-update limit does not cap security PRs. Maintainers
review and test updates before merging; updates are not automatically merged.

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
