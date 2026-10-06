# Verify what you received

Every released image is signed and published with a software bill of
materials. Verifying takes a minute and proves the image came from us and
matches the dependency tree we tested.

## Check the signature

Releases are signed with keyless [cosign](https://docs.sigstore.dev/), so
there is no public key to distribute or rotate:

```bash
cosign verify <registry>/trellum:<version> \
  --certificate-identity-regexp '^https://github\.com/trellumhq/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Check the bill of materials

Each release carries CycloneDX and SPDX attestations. Your scanner can consume
them directly:

```bash
cosign download attestation <registry>/trellum:<version>
```

The SBOM matches the image exactly: dependencies are pinned to an exact
version and the resolved tree is locked, so a rebuild of the same tag produces
the same package set.

## What the image contains

- No test tooling — packages needed only for tests are absent from the runtime
  image, because every package present is CVE surface your scanner will ask
  about
- No cloud SDKs — the portal has no cloud dependencies, and the build fails if
  one is introduced
- Processes run as an unprivileged user (uid 10001)

## Air-gapped installs

Images are verified locally, so there is no activation call and
no phone-home. An air-gapped bundle script ships the image, compose file, and
SBOM as a single archive for transfer into a disconnected network.
