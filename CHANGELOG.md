# Changelog

All notable changes to Trellum are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.2] — 2026-10-09

### Added

- Operator Server logs page with retained application events, live filtering,
  tracebacks, linked build output and bounded export. Worker startup, build
  completion, stop signals and orphan cleanup include diagnostic context.

### Fixed

- Keep active report containers running when Docker status inspection temporarily
  fails; log the monitoring outage and recovery without inventing an exit code.
- Filter Operations by aggregate failures and individual build outcomes, keeping
  labels and counts current as builds move between states.
- Clear the previous failure title, color and log when retrying a report, and
  keep queued or running attempts separate from completed run history.
- Include runner memory and termination evidence in completed run logs, and
  distinguish observed memory peaks from configured allocations and caps.
- Keep local live queries available while review mode is active.
- Refresh repository completion and uploaded-file status without reloading the
  page or discarding other edits.
- Render the report catalog without waiting for favorites or subscriptions,
  reject outdated Operations responses, and restore history scroll after
  delayed catalog content is ready.
- Keep live-query parameter values literal, restrict portal DuckDB file access,
  and render ordinary table, pivot and dropdown values as text.
- Enforce API credential scope, MFA enrollment and single-use codes, verified
  SSO identity linking, and safe login redirects.
- Store personal studio themes separately from access grants, and revoke public
  report access when an organization is suspended.
- Confine datasource paths and repository staging to their owning storage,
  validate existing runner networks, and keep live-query manifests private
  across local serving, object storage and the supplied edge worker.
- Update authentication and datasource dependencies with publisher fixes.

### Changed

- Rename the report-history Server Log drawer to Build activity to distinguish
  it from the application logs.
- Save ordinary organization and studio settings in place, with shared progress,
  validation and error feedback that preserves scroll, focus and newer drafts.
- Confirm data-source settings before testing the connection, and bind test
  results to the saved configuration so obsolete checks cannot overwrite it.
- Document the shared form, refresh and navigation contract for contributors.

### Upgrade notes

Apply the additive `studios.0013_studio_preference` and
`core.0014_server_log_event` migrations. Existing access grants are preserved
and should be reviewed against approved access. Rebuild generated reports to
apply the framework and runner fixes. Deploy the updated edge worker with the
portal; custom gateways, legacy manifest objects and cached responses need the
steps in the [storage guide](docs/customer/install/storage.md#private-live-query-manifests).

After migrations complete, restart every web, worker and coordinator process so
application log capture begins. Capture starts after upgrade and does not
backfill historical logs. Trellum does not ingest host, kernel or Docker daemon
logs. Log capture and retention settings remain operator-controlled through
`SERVER_LOG_CAPTURE_ENABLED`, `SERVER_LOG_RETENTION_DAYS`,
`SERVER_LOG_MAX_ROWS` and optional `SERVER_LOG_SERVICE` settings.

## [0.4.1] — 2026-10-09

**Incomplete release:** The portal image was published, but image signing and
release publication did not complete. This release is unsigned and is not
recommended for installation. Use v0.4.2 instead.

### Added

- Operator Server logs page with retained application events, live filtering,
  tracebacks, linked build output and bounded export. Worker startup, build
  completion, stop signals and orphan cleanup include diagnostic context.

### Fixed

- Keep active report containers running when Docker status inspection temporarily
  fails; log the monitoring outage and recovery without inventing an exit code.
- Filter Operations by aggregate failures and individual build outcomes, keeping
  labels and counts current as builds move between states.
- Clear the previous failure title, color and log when retrying a report, and
  keep queued or running attempts separate from completed run history.
- Include runner memory and termination evidence in completed run logs, and
  distinguish observed memory peaks from configured allocations and caps.
- Keep local live queries available while review mode is active.
- Refresh repository completion and uploaded-file status without reloading the
  page or discarding other edits.
- Render the report catalog without waiting for favorites or subscriptions,
  reject outdated Operations responses, and restore history scroll after
  delayed catalog content is ready.
- Keep live-query parameter values literal, restrict portal DuckDB file access,
  and render ordinary table, pivot and dropdown values as text.
- Enforce API credential scope, MFA enrollment and single-use codes, verified
  SSO identity linking, and safe login redirects.
- Store personal studio themes separately from access grants, and revoke public
  report access when an organization is suspended.
- Confine datasource paths and repository staging to their owning storage,
  validate existing runner networks, and keep live-query manifests private
  across local serving, object storage and the supplied edge worker.
- Update authentication and datasource dependencies with publisher fixes.

### Changed

- Rename the report-history Server Log drawer to Build activity to distinguish
  it from the application logs.
- Save ordinary organization and studio settings in place, with shared progress,
  validation and error feedback that preserves scroll, focus and newer drafts.
- Confirm data-source settings before testing the connection, and bind test
  results to the saved configuration so obsolete checks cannot overwrite it.
- Document the shared form, refresh and navigation contract for contributors.

### Upgrade notes

Apply the additive `studios.0013_studio_preference` and
`core.0014_server_log_event` migrations. Existing access grants are preserved
and should be reviewed against approved access. Rebuild generated reports to
apply the framework and runner fixes. Deploy the updated edge worker with the
portal; custom gateways, legacy manifest objects and cached responses need the
steps in the [storage guide](docs/customer/install/storage.md#private-live-query-manifests).

After migrations complete, restart every web, worker and coordinator process so
application log capture begins. Capture starts after upgrade and does not
backfill historical logs. Trellum does not ingest host, kernel or Docker daemon
logs. Log capture and retention settings remain operator-controlled through
`SERVER_LOG_CAPTURE_ENABLED`, `SERVER_LOG_RETENTION_DAYS`,
`SERVER_LOG_MAX_ROWS` and optional `SERVER_LOG_SERVICE` settings.

## [0.4.0] — 2026-10-08

### Added

- Configure separate default audiences for new reports and analyses. A
  repository manifest can set `initial_audience` to `studio` or `private` for
  the first import; later imports preserve the item's audience.
- Save, test and select named outbound email API connections alongside SMTP.
  Built-in choices cover SendGrid, Amazon SES, Mailgun, Postmark, Brevo,
  Resend, Mailjet, MailerSend and Mailtrap Email Sending. Custom HTTPS supports
  a bounded JSON mapping editor with synthetic previews and revision-bound
  simple/report tests.

### Fixed

- Prevent runner concurrency races during report builds and data-source
  materialization, and refresh repository sync status after queued work.
- Fix the Pages export test fixture for the checkout analysis.

### Changed

- Raise the default report build timeout from 10 to 30 minutes. Explicit
  `TRELLUM_RUN_TIMEOUT` settings continue to take precedence.
- Set SSO domain verification off in supplied new-install and demo
  configuration. Existing environment files retain their configured behavior.

### Documentation

- Start website onboarding with a copyable prompt for an AI coding agent.
  Explain reports from spreadsheets, files, APIs, databases and extracted
  document data, and show browser review in the Trellum Dark theme.

### Upgrade notes

The portal upgrade applies additive migrations for email API connections,
report audiences, and studio audience defaults. Existing reports and analyses
start in the Studio audience, preserving existing access; the new defaults
apply to items discovered later.

Deploy this release to every web and worker process before enabling Private
items. Keep `TRELLUM_REPORT_SCOPED_ACCESS_READY=false` until the rollout is
complete. In `edge-signed` mode, also wait at least
`TRELLUM_CDN_COOKIE_TTL_SECONDS` after removing the last old process before
setting readiness to `true`, so legacy studio-wide grant cookies expire.
Follow the [report-content security guide](https://trellum.dev/docs/latest/operations/securing-report-content/)
for rollout and rollback restrictions.

Existing `.env` and demo environment files keep their SSO domain-verification
setting; if it is absent, verification remains enabled.

## [0.3.0] — 2026-10-08

### Added

- Publish Git-authored analysis articles with Markdown, contents navigation,
  captured evidence, author metadata, and report permissions and sharing.
- Capture chart and section views with filter and source context, then import
  them into an analysis using the framework CLI. Captures remain fixed when
  the source report changes.
- Include a complete Northwind checkout analysis in the packaged demo, portal
  demo, and public gallery, with fixed evidence and reproducibility notes.

### Fixed

- Allow Docker Engine 25 for report sandboxes with bind-backed data storage,
  while retaining the Engine 26 requirement for named-volume subpath mounts.
  Add a Compose override, an installation compatibility check, and storage
  migration instructions. Unknown Docker API versions now stop worker startup.

- Make selected report toggles, tabs, date presets, and dropdown items follow
  the active theme, with readable text across all built-in palettes.
- Align the report table's sort label, dropdown, and direction control.
- Make System health reachable from the operator sidebar and show a live
  header warning when worker or other quick health checks need attention.
- Align inline settings dropdowns and action buttons, reserve space for select
  arrows, and let crowded rows wrap controls without squeezing action labels.
- Keep account recovery codes within narrow phone layouts.
- Align audit-log date filters with their actions and keep table action menus
  inside the viewport, including their inline role controls.
- Give documentation more reading space, preserve sidebar scroll position,
  and prevent active links and long table content from shifting the layout.
- Keep narrow funnel-stage labels clear of the chart's axis labels.

### Changed

- Align documentation and feature checks with the current open-source platform.
- Add an interactive portal walkthrough with fourteen feature screenshots.

### Documentation

- Add a clearer chooser for single-server deployment, build scaling, distributed
  runners, and shared storage.
- Explain how to request changes from a report through the local browser review
  loop, and distinguish source editing from the portal's Q&A assistant.
- Clarify multi-host deployment requirements: shared PostgreSQL and a shared
  data directory for web, coordinator, and runner services, even with object storage.
- Document existing studio runner pools and distinguish memory admission
  budgets from Docker and unsandboxed process limits.
- Align feature, installation, backup, and workflow guides with the current
  implementation, and check canonical documentation routes and anchors before
  release.
- Explain documentation release snapshots and contributor test prerequisites.
- Remove unnecessary version callouts, share exact release pins within
  installation and image-verification examples, and check those pins in CI.

### Upgrade notes

The standard portal upgrade applies the additive `reports.0022_report_kind`
migration. Existing entries remain reports; analyses use the same access and
sharing controls, with their own audience. Captured evidence is republished to
that audience independently of access to the source report.

Upgrade the framework used by report builders and rebuild existing reports to
apply the corrected theme colors. Analysis articles and their evidence stay
fixed until their Git sources are changed and rebuilt.

The default named-volume sandbox still requires Docker Engine 26 or newer.
Engine 25 installations can use the documented bind-backed storage setup;
existing installations do not need to change storage mode.

## [0.2.2] — 2026-10-06

### Fixed

- Preserve sidebar scroll position across console navigation and mark Data
  retention as the active page.
- Correct documentation that referenced the previous console navigation.

### Changed

- Align the website and documentation with the portal's neutral light/dark
  surfaces and teal actions.
- Add a real portal slideshow and a three-minute captioned
  [product tour](https://trellum.dev/tour/) with a readable transcript.
- Refresh README and package imagery with Trellum Dark, explain the coding-agent
  and Git workflow, and distinguish report demos from the self-hosted portal.

No database migration or report rebuild is required by this patch.

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

[Unreleased]: https://github.com/trellumhq/trellum/compare/v0.4.2...HEAD
[0.4.2]: https://github.com/trellumhq/trellum/releases/tag/v0.4.2
[0.4.1]: https://github.com/trellumhq/trellum/releases/tag/v0.4.1
[0.3.0]: https://github.com/trellumhq/trellum/releases/tag/v0.3.0
[0.2.2]: https://github.com/trellumhq/trellum/releases/tag/v0.2.2
[0.2.1]: https://github.com/trellumhq/trellum/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/trellumhq/trellum/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/trellumhq/trellum/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/trellumhq/trellum/releases/tag/v0.1.0
