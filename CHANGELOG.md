# Changelog

All notable changes to Trellum are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] — 2026-10-06

### Fixed

- Publish the 0.2.0 improvements under a fresh package version because PyPI
  cannot reuse a filename from a deleted release. No runtime behavior changed.
- Update installation and signature-verification examples to v0.2.1.

## [0.2.0] — 2026-10-06

### Fixed

- Keep the landing-page preview at a stable height while switching between
  report cards and the report view.
- Keep ordinal slider endpoint labels inside their filter control so they do
  not overlap neighboring filters.
- Replace placeholder installation commands and outdated image-content claims
  with instructions for the published portal and runner images.
- Load the demo configuration when removing its Docker stack from a new
  terminal, and report teardown failures accurately.

### Changed

- Lead the README with Store Health and bring charts earlier in the Store
  Health and Player Overview demos.
- Add a first-user guide for the Python package and local Docker demo, and a
  feature index linking to the framework and portal documentation.
- Document the portal's Metrics catalog and Report Analytics.
- Correct demo descriptions to work with both small and full synthetic data.

### Upgrade notes

- Rebuild existing reports to receive the slider layout fix. No database or
  report-authoring contract changed.
- Existing copied demo projects retain their files. Copy the revised examples
  into a new directory to compare layouts before replacing your own edits.

## [0.1.1] — 2026-10-06

### Changed

- Corrected the standalone PyPI package description and documentation links,
  and clarified named `pip install trellum` instructions in the README, site,
  and agent guidance.
- Clarified standalone onboarding and restored the original illustrated
  personal blog.
- No runtime behavior changed.

## [0.1.0] — 2026-10-06

Trellum brings its reporting framework, self-hosted platform, documentation,
and demos together in one AGPL-licensed project.

### Added

- A self-hosted control plane for organizations, studios, Git-backed report
  publishing, scheduled builds, data-source management, sharing, alerts,
  annotations, experiments, and operational health.
- A standalone Python framework that builds themed, interactive reports as
  portable HTML and JSON, with filters, governed metrics, validation, live-query
  support, and source-linked runtime metadata.
- Isolated report execution with a dedicated non-root runner image, read-only
  containers, bounded resources, and network egress denied by default.
- Versioned customer documentation, a synthetic ten-report demo gallery, and a
  single Pages site at `https://trellum.dev/`.
- Signed portal and runner images, software bills of materials, a standalone
  wheel, verified backup and restore tooling, audit logs, and retention controls.

### Licensing

- Owned Trellum code is released under AGPL-3.0-only. Third-party and
  contributor notices retain their own terms.

[Unreleased]: https://github.com/trellumhq/trellum/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/trellumhq/trellum/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/trellumhq/trellum/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/trellumhq/trellum/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/trellumhq/trellum/releases/tag/v0.1.0
