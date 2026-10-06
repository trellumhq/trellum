# Changelog

All notable changes to Trellum are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/trellumhq/trellum/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/trellumhq/trellum/releases/tag/v0.1.0
