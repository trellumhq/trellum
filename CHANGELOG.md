# Changelog

All notable changes to Trellum are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Review controls and conversations use consistent typography, softer theme surfaces and compact desktop controls while retaining touch targets on phones.

### Fixed

- Require an explicit Build or Rebuild action before generating a selected
  source report in the review workspace.

- Keep empty datasource placeholders from blocking queries against other
  configured sources; selecting the empty source still explains how to configure it.
- Disable date presets when no date bounds are available and keep obsolete
  live lookup responses from replacing a newer filter selection.

### Added

- Show feedback pickup, factual agent activity, elapsed time and stale presence
  beside each request in report review and analysis workspace conversations.
  Agents can report bounded activity updates with `trellum review activity`
  without completing work or consuming queued follow-ups.

## [0.9.3] — 2026-10-20

### Fixed

- Recognize tenant-bound Microsoft Entra ID-token email domain verification
  when linking an organization member or provisioning an allowed new account.
- Keep Test sign-in from updating stored identity claims for an already-linked
  account.

### Upgrade notes

- For Entra accounts whose email lacks `email_verified: true`, add the optional
  **ID-token** claims `email` and `xms_edov` to the app registration. Trellum
  requires Boolean `xms_edov: true` with matching tenant, issuer, subject and
  email. Trust only tenants authorized to manage the organization's identities;
  domain confirmation is not individual mailbox verification. Keep existing
  optional claims and the registered callback URI unchanged, then run a fresh
  **Test sign-in**. Application consent alone does not provide these claims.

## [0.9.2] — 2026-10-20

### Fixed

- Use the configured public URL for OpenID Connect authorization callbacks and
  token exchange, including deployments behind HTTPS proxies.

### Upgrade notes

- Check that the instance Public URL (or `PORTAL_BASE_URL` when empty) is the
  externally reachable HTTPS address. Keep the exact callback shown in SSO
  settings registered at the identity provider, including its trailing slash.

## [0.9.1] — 2026-10-20

The v0.9.0 tag was never published: its images failed the release
vulnerability scan. 0.9.1 is the first release with the 0.9.0 changes, so
when upgrading from 0.8.0, also read the 0.9.0 section of `CHANGELOG.md`,
including its upgrade notes.

### Security

- Update aiohttp to 3.14.4 (and yarl to 1.25.1) for CVE-2026-111064 and
  GHSA-2g87-pp6c-29x5: the aiohttp server had no initial request or header
  timeout, and several limits were missing or unbounded.

## [0.9.0] — 2026-10-19 (not published)

### Added

- Bounded report-stage failure evidence and durable automatic recovery for
  explicitly replay-safe reports, with recovery disposition in run history. A
  report opts in with `recovery: replay_safe: true` in `report.yaml`; see
  *Troubleshooting build failures* for what qualifies.
- Preserve successful report artifacts while replacement builds run or fail.

### Changed

- Keep query execution, diagnostics and Decimal conversion together within
  the process-wide query limit.
- Split a chunked DataSource (`chunk_by="month"` or `"week"`) into its period
  files without copying the whole frame, both while rendering and while the
  build measures chunk sizes for its details. A two-million-row report needs
  about 460 MB less peak memory and builds about 30% faster. The chunk files,
  their sizes and row order are unchanged.
- Serialize a DataSource's text columns without checking every value for a
  date or NaN when the column holds only text and empty values. A build that
  sends two million rows to the browser finishes about 15% sooner; data.json
  and chunk files are byte-for-byte unchanged.
- Start the portal's web workers and runworker without loading pandas, numpy
  and pyarrow. They load on the first request or build that uses them, so an
  idle process holds about 75 MB less (163 MB to 87 MB per process; about
  230 MB across the default two web workers and one runworker). Everything
  `trellum.data` and `trellum.rendering` export is unchanged.
- Require pandas 3.0 (images pin 3.0.6). Text built from Python strings is now
  pandas' `str` dtype instead of `object`; PivotTable still treats it as a
  dimension, and `df_to_json_records` still writes a missing text value as
  null. The Snowflake connector is installed without its `[pandas]` extra,
  which caps pandas below 3; pandas and pyarrow are required directly. Report
  code that relies on pandas 2 behaviour may need changes; see
  `trellum/docs/MIGRATIONS.md`.
- Return repeated text from `query_df` as pandas `category` columns: a text
  column whose values are at most half distinct (titles, platforms, dates as
  text) holds one small code per row instead of one string. For DB-API
  sources the codes are built while the rows are read, so the two-million-row
  Economy Firehose demo builds with a 640 MB peak in 15 s, where 0.8.0 needed
  2.1 GB and 47 s and could not build under the default 1.5 GB cap at all.
  DataSource serialization and chunk splitting use the codes directly. Output
  is unchanged, but report code that uses `.map()` then arithmetic,
  `pd.to_datetime()` then `<`/`>`, `.min()`/`.max()`, `fillna()` with a new
  value or `+` with a string on such a column must be updated:
  `python -m trellum guide upgrade` lists each fix, and a build that fails on
  one prints a hint pointing there. `query_df(..., compact_text=False)`
  returns plain text.
- Send the ordinary Broken alert for a report's first failed build, with
  bounded automatic-recovery status and a link to its build history.
- Allow 60 minutes for report builds by default, retaining explicit
  `TRELLUM_RUN_TIMEOUT` overrides and the five-second termination grace.
- Contain direct Windows report builds in a Job Object so successful runs can
  publish and stopped process trees can be confirmed before recovery.

### Fixed

- A monitor's metadata strip (`ctx.set_header(meta=...)`) updates on each
  auto-refresh, not only after a full page reload.
- The portal no longer reads an empty or half-written `_meta.json` while a
  build's attribution step rewrites it; the file is now replaced atomically.

### Upgrade notes

**Report code may need changes for pandas 3 and category text columns.** Run
`python -m trellum guide upgrade` against your reports before upgrading the
report-runner image; it lists each pattern to change (`.map()` then
arithmetic, `pd.to_datetime()` then `<`/`>`, `.min()`/`.max()`, `fillna()`
with a new value, `+` with a string). A build that fails on one prints a hint
pointing there. Pass `compact_text=False` to `query_df` to keep plain text
columns while you migrate. See `trellum/docs/MIGRATIONS.md`.

Apply the migrations `runner.0008`, `runner.0009` and `runner.0010`: a new
`RunRecovery` table and nullable `Run` columns. `runner.0009` builds two
indexes concurrently, outside a transaction, so queue writes continue while
it runs. Update the portal, the coordinator, every worker and the matched
report-runner image to v0.9.0 together.

Builds now get 60 minutes by default instead of 30. An explicit
`TRELLUM_RUN_TIMEOUT` keeps its value.

Automatic recovery is off until a report sets `recovery: replay_safe: true`.

Rebuild reports with a metadata strip to pick up the auto-refresh fix.


## [0.8.0] — 2026-10-17

### Added

- Add **Test sign-in** to the organization's single sign-on settings. An org
  admin can run a real OpenID Connect round trip, or check a directory login,
  and see what would happen -- identity provider reached, email claim, whether
  the domain routes, whether the email is verified, whether the person would be
  signed in, created or refused, and which permission groups would be granted
  or removed -- without anyone being logged in, linked, created or synced, and
  without leaving the admin's own session. The last result, who ran it and when
  are kept on the page and audited as `sso.test_signin`. Turning on **Enforce
  SSO** or turning off password login before a successful test now shows a
  non-blocking warning.
- Send invitations by email from the invitations page, with **Send invitation
  by email** (disabled while mail is not configured), explicit delivery
  feedback instead of silent failure, and **Resend email** for pending
  invitations (once a minute per invitation), audited as `member.invite_resend`.
- Add a **Manage studio access** page for each organization member. An
  organization home with more than 200 studios pages its studio list.
- Show "Loading <page>…" with **Cancel loading** (or Esc) while the console
  opens a page. After 15 seconds it says the page is still loading and keeps
  waiting; only Cancel or Esc stops it.
- Add an **Operator console** entry to the shell sidebar for operators.
- Add `EMAIL_TIMEOUT` (default 10 seconds, must be positive) to bound every SMTP
  connect and read, including SMTP configured in instance settings.
- Add the audit actions `registry.refresh_requested`,
  `registry.refresh_finished`, `member.invite_resend` and `run.auto_enqueue`.
- `scripts/demo.sh --dev-login` prints one-click admin, member and viewer
  login links for local review, and seeds fixtures as the invoking user on
  Linux. It runs development settings with unauthenticated login links: use
  it only on a machine nobody else can reach.

### Changed

- Sign in in two steps. The login page asks for the email first and sends
  single sign-on users straight to their identity provider; everyone else
  continues to a password page (**Directory password** for directory logins)
  at `/login/password` that shows the email read-only with a **Use a
  different email** link. Where single sign-on is configured, **Sign in with
  a password instead** skips the redirect. SSO enforcement is re-checked on
  the password step, and a POST to `/login` that already carries an email and
  password still signs in in one request.
- Show a clearer page when single sign-on fails after the identity provider
  answers. "We couldn't sign you in" gives a plain-language cause for each
  refusal (and for cancelled, expired or provider-reported errors), a Try again
  button, and a short reference. The reference is recorded on the
  `auth.sso_denied` audit row, so an administrator can search for it in the
  audit log.
- Make single sign-on routing failures visible. The SSO settings page warns
  about listed email domains that are not yet verified (and when no domain
  routes at all), and refuses to turn off password login while no domain
  routes. The portal logs a warning when a listed domain is unverified.
- Search now lives in the page it searches. The top bar no longer carries a
  "Search reports" box on every page, and the phone search button is gone.
  Reports, Analyses and Operations have their own search field in the folder
  bar before Sort (the first row on phones), with an "N of M" count and a
  "Clear search" button when nothing matches. A new search icon in the top
  bar, with a Ctrl+K badge, opens "Jump to a report": your studio's reports
  and analyses, filtered as you type, with the arrow keys, Enter to open and
  Esc to close. Ctrl+K focuses the page's own search field where there is one
  and opens the jump list everywhere else. Metrics, Report Analytics and
  category filters now read "Search ...".
- Keep a summary of each report's build, runtime, experiments and metric
  usage in the database, refreshed in the background by the workers. An
  unchanged report is confirmed from a file stamp (or the object ETag on
  remote storage) instead of re-reading its sources. The catalog,
  `/api/registry`, Operations, Experiments and Metrics read these summaries and
  the build history instead of each report's files, so page and API requests
  no longer read object storage.
- The Reports and Analyses catalog loads in slices of 50 reports with the same
  category tabs, instant search, sorting, cards and list; beyond 500 reports it
  offers "Show more" and searches on the server. Cards show every tag and the
  full description, and the catalog and Operations search the same fields:
  name, description, tags, category, studio and slug.
- `/api/registry` returns the same report row for the paged and unpaged call,
  Operations and `api/system/status`, built from the report summaries and
  build history. Without `?page=` it still returns the whole registry, now of
  both kinds unless `kind` is given; `has_output` is `null` until a worker has
  observed the report.
- Operations reads up to 500 reports at once and asks larger studios to search
  to narrow the list, and offers **Download** only when a stored file exists.
  **Reload Registry** runs on a worker: the page shows Scheduled and then the
  result. If no worker has taken it after a minute, the page says it is still
  waiting for a worker and the button works again; a worker that starts
  within 10 minutes still runs it, and after that it ends with a clear
  message.
  `POST api/system/registry/refresh` answers `202` with an `operation_id` and
  `status_url` instead of the report count.
- Experiments and Report Analytics keep every row on one page with instant
  filtering and sorting; a studio with more than 200 experiments or reports
  pages them and searches on the server.
- Pickers keep instant search for up to 500 choices; longer lists search on
  the server as you type, with a "keep typing" hint. Delivery, metric and
  alert pickers apply as soon as you choose.
- List search boxes (members, groups, studios, invitations, data sources,
  member studio access) and the Server logs filters apply by themselves:
  selects at once, typing after a short pause. The Search and Apply filters
  buttons are gone.
- Data sources list 50 per page in the portal. `GET api/datasources` still
  returns every source; pass `?page=` or `?limit=` (up to 200) for one page
  with `total`, `page` and `has_next`. Invalid values return `400`, and a
  page past the end is empty.
- Run data-source connection tests in a separate process that can be stopped,
  at most 2 at a time per source and 4 per studio or organization in each web
  process; a refused test says "Another connection test for this source is
  still running." After a passing saved check, a worker queues the reports it
  unblocked, and the queueing survives a restart.
- System health and an organization's uploaded-file usage are measured by the
  coordinator while the page is open -- about every minute, every five for
  uploads -- in a separate process with a time limit. The page shows "Checked
  2 minutes ago" and has no refresh buttons; without a usable coordinator it
  measures in the request as before. An organization with no uploaded files
  shows 0 MB.
- The Metrics page fills in usage and charts by itself from the report
  summaries; while reports are still being indexed, usage reads "measuring
  usage…" rather than unused. Each metric lists every report that uses it, up
  to 500, with a searchable list beyond that.
- Build the generated Metrics report's comparison workspace as pages of 25
  monitored metrics, with search across all metrics, page links and deep links
  to the right page; metric definitions are listed once. After upgrading, the
  coordinator rebuilds the Metrics report once by itself.
- Report pages load the PNG and PDF export libraries on first export instead
  of on every page, and say so if they cannot load; the Inter font ships as
  WOFF2.
- Serve report widget scripts and vendored assets without a session or
  database work, cached for five minutes. They set no cookie, no longer send
  `Vary: Cookie`, and no longer count as session activity; idle and revoked
  sessions are still enforced on the next request.
- Cache the AI assistant's availability for 30 seconds per process. Saving the
  assistant configuration clears the cache on that process; others follow
  within 30 seconds.
- The annotations calendar reads a large `events.yaml` several times faster.
- Phone layout of console-hosted reports: the report header is one compact row
  (freshness left, labelled Options and Monitor right), annotation chips scroll
  on one row, and the shell header buttons share one neutral bordered style.
- Document required portal performance patterns in
  `docs/performance-patterns.md` with a new-feature checklist, and track a
  tool-neutral `AGENTS.md` agent guide.

### Fixed

- Run the nightly retention cleanup and the daily release check. Since at
  least v0.4.6 the coordinator removed both jobs right after it started, so
  cleanup never ran and `manage.py doctor` failed its retention check.
- The console top bar no longer jumps while a page loads. The AI assistant has
  one slot, in the same place on every page, and its space is reserved from
  the first paint, so the health warning and Docs link stop shifting when the
  assistant finishes loading. The assistant button, its unavailable entry and
  the organization picker now share one look.
- Opening the live log of a build that has just been requested shows an empty
  "waiting" state instead of a 404.
- Ending an impersonation that is no longer active returns 404 instead of a
  server error.
- Use white text in the Classic Dark report header.
- A report left open in a background tab refreshes as soon as it is shown
  again when an update is due, instead of waiting for its next interval, and
  its "Updated … ago" label follows the new data. A hidden tab no longer runs
  the refresh timer, and a resumed tab no longer reloads twice.

### Security

- The report catalog and `/api/registry` list only reports the viewer can
  open. While selected-report access is not enabled
  (`TRELLUM_REPORT_SCOPED_ACCESS_READY`), earlier releases listed private and
  selected-scope reports, with their names and metadata, to viewers who could
  not open them.
- Throttle password reset: one mail per address every two minutes and ten
  requests per client address every 15 minutes
  (`PASSWORD_RESET_EMAIL_COOLDOWN_SECONDS`, `PASSWORD_RESET_IP_MAX_REQUESTS`,
  `PASSWORD_RESET_IP_WINDOW_SECONDS`). The page is always the neutral "if an
  account exists" page, and the mail is sent outside the request, so neither
  a delivery failure nor the response time reveals whether an account exists.

### Upgrade notes

**Before upgrading, run retention cleanup once.** Earlier releases never ran
the scheduled cleanup, so `manage.py doctor` reports `[FAIL] retention:
cleanup has never run` and `scripts/upgrade.sh` stops at its preflight. On the
running instance, preview with `docker compose exec web python manage.py
cleanup --dry-run`, then run `docker compose exec web python manage.py cleanup`.
This first run deletes everything past its retention window that accumulated
since installation; review the dry-run counts and the `RETENTION_*` settings
first. From this release the coordinator runs cleanup nightly. `doctor` must
pass as a whole: an install whose backup service has not completed a run yet
also fails its backups check, and `scripts/upgrade.sh` stops on that too.

Apply the additive migrations `datasources.0007`, `orgs.0025`, `reports.0027`
and `runner.0007`: new tables and nullable columns only. Update the portal,
the coordinator, every worker and the matched report-runner image to v0.8.0
together. Older workers do not run **Reload Registry** (after a minute the
page says it is still waiting for a worker; after 10 minutes the action ends
with a message), do not queue reports unblocked by a data-source check, do not
build report summaries and do not build the paged Metrics workspace. Existing
sessions stay signed in.

Report summaries fill in the background after the upgrade: a few seconds for a
dozen reports, longer for studios with large report output. Until then the
catalog shows each report's latest finished build.
`manage.py reconcile_source_summaries` runs one batch by hand.

When the upgraded coordinator first starts, it queues one rebuild of each
studio's generated Metrics report that is enabled, present and not
idle-paused; sparklines and charts return when that build finishes. This
happens once per installation and is not retried. If the runner image is
still on an earlier release, or the build fails, run the Metrics report from
Operations once the image is updated; a paused Metrics report is rebuilt
when it is resumed.

Rebuild other reports to pick up lazy-loaded export libraries, WOFF2 fonts,
the Classic Dark header and the background-tab refresh fix.

Scripts calling `POST api/system/registry/refresh` must accept `202` and poll
`status_url`. `GET api/datasources` rejects invalid `page` or `limit` values
with `400`. `EMAIL_TIMEOUT` must be a positive number of seconds; the portal
refuses to start otherwise. Edge caches that allow-list vendored assets must
also allow `/_vendor/fonts/*.woff2`.

## [0.7.0] — 2026-10-15

### Added

- Pause the scheduled refresh of reports nobody has viewed for an
  organization-configurable number of days, set under **Org settings →
  Auto-refresh** (60 by default, up to 3650; `0` never pauses and resumes
  paused reports). Portal and share-link views count as activity; reports
  with an enabled email delivery or schedule-triggered alert are exempt.
  Paused reports show an **Auto-refresh paused** badge in card and list views
  and restart through **Resume** in Operations, which also queues a rebuild.
  Pauses and resumes are audited.

### Changed

- Show the analysis and its source reports on separate full-width **Analysis**
  and **Reports** workspace pages, with the shared queue in an overlay drawer
  and **Report only** and **Analysis only** views without workspace controls.
  Switching pages or source reports keeps filters, selection drafts and scroll
  positions.
- Add focused local and parallel portal test commands, CI timing reports,
  and a portal check that also reports for documentation-only changes.

### Fixed

- Build analyses in the Docker report sandbox, where staging beside the
  writable output mount failed on its read-only parent. A failed build keeps
  the previous artifacts; a successful rebuild removes obsolete ones.
- Use the report theme font for chart labels, matching report headers and
  metadata, including after theme changes and font loading.
- Reject live-query rate limits too large to store instead of failing with a
  server error.
- Make `scripts/demo.sh`, `scripts/upgrade.sh`, `scripts/rollback.sh` and
  `scripts/check-docker.sh` executable, so the documented `./scripts/demo.sh`
  and `scripts/upgrade.sh` commands run on macOS and Linux.

### Upgrade notes

Apply the additive `reports.0026_report_idle_refresh` migration before starting
the new portal. Update the portal, every worker and the matched report-runner
image to v0.7.0 together; older workers do not pause idle reports, and analysis
builds need the new runner image. The migration starts every existing report's
idle clock at upgrade time, so no report is paused until the organization's
threshold (60 days by default) has passed after the upgrade. Organizations that
want every schedule to keep running regardless of views should set
**Auto-refresh** to `0`. Rebuild reports to apply the chart label font.

## [0.6.0] — 2026-10-13

### Added

- Add local analysis sessions that build and preview Git-authored articles or
  presentations, with a private capture inbox and review-poll delivery for
  selected report evidence. A shared workspace includes a report picker and one
  queue; source selections add frozen evidence, while analysis selections request edits.
- Add versioned presentation slides with four layouts, stable annotation
  targets, a readable no-JavaScript view, and a slide player with keyboard,
  mobile, deep-link and fullscreen controls.
- Add a release-only Compose overlay that pins matched portal and runner images
  to one required version and removes source image build definitions.
- Offer organization Assistant access through an accessible-studio picker, with named conversation scope and visible availability reasons and retry.
- Add permission-scoped assistant alert discovery with stable identities and
  settings revision tokens that reject stale approved or MCP updates.
- Compare two studio metrics with shared dates, independent chart splits,
  mobile stacking and URL history. Show chart availability, units, definition
  grain and build freshness without displaying snapshot KPI totals.
- Add in-place alert draft tests with isolated preview results billed to the
  requesting developer.
- Add shared delivery management for organization admins and instance
  superusers, with owner repair, scoped pause, future schedule defaults and
  transport outcome reporting.
- Add alert condition results, interpreted evidence windows, repeat/episode
  notification policies and audited reconciliation of uncertain deliveries.
- Add dedicated data source editors with draft credential testing, separate
  saved and draft results, and protection against obsolete test responses.
- Add a Capture for analysis icon, image preview and persistent instructions
  for importing frozen evidence into an analysis.

### Security

- Bound local capture submissions by loopback, Host, Origin, session token,
  image validation and request size; keep pending evidence outside the Git
  repository and generated output.

### Changed

- Refresh the website product tour and report-review screenshots at 2× resolution.
- Remove the incomplete offline bundle generator and clarify that offline
  installation and upgrade bundles are not currently provided.

- Preserve cents in shared currency formatting below the compact-total boundary.
- Reserve search shortcut space beside the input, show the platform shortcut, and remove duplicate placeholder hints.
- Evaluate alert conditions during cooldown, keeping condition truth separate
  from notification suppression; deduplicate build and schedule triggers.
- Separate System Health diagnostics from Instance Settings. Keep detailed
  workers and build execution in Fleet, preserve legacy settings submissions,
  and save instance, security and email connection edits in place.
- Group instance settings into independently saved Identity, Networking, Email
  and Limits sections while keeping security and email delivery actions separate.

### Fixed

- Exclude report-accepted inactive filters from the live health badge while
  keeping their status and rationale in its filter detail panel.
- Keep Operations polling confined to summary statistics, preserving expanded
  history memory values. Calculate average and percentile durations from
  successful runs while retaining failed-run counts, memory and latest details.
- Recompute bound last-value metric KPIs from the final retained date under
  date and dimension filters, including restored filter state.
- Fetch the report registry without the browser cache so catalog changes appear
  after Back navigation.
- Preserve scheduled delivery settings when their creator is deleted, and skip
  ownerless schedules during delivery.
- Keep metric detail controls visible with one modal scroll area, and preserve
  the selected metric, date range and chart breakdown when opening its page.
  Keep a separate Metrics overview link. Rebuild reports to include automatic
  chart breakdowns in saved URLs.
- Isolate alert tests from live notification history and all email paths.
  Record transport acceptance, partial delivery and failures explicitly; start
  cooldown only when at least one recipient is accepted for delivery.

### Upgrade notes

Apply the additive alert and delivery migrations: `alerts.0003`/`0004`,
`core.0015`, `orgs.0024` and `reports.0024`/`0025`. Update the portal and all
workers together; older workers do not enforce delivery pause or alert claims.
Existing cooldown timestamps are preserved. Alert rules default to repeating
while their condition remains met after cooldown, replacing the previous LLM
novelty suppression; evaluations now run and consume budget during cooldown.
Existing alert runs retain unknown legacy outcomes and are excluded from new
live notification history. Transport acceptance does not confirm inbox receipt.
Rebuild reports with the matched framework to include the capture preview and
guidance; the portal supplies the updated capture menu icon.

## [0.5.0] — 2026-10-10

### Added

- Add native AI provider connections for Anthropic, OpenAI, LiteLLM, OpenRouter,
  DeepSeek, Azure OpenAI, Amazon Bedrock, Vertex AI and custom compatible
  endpoints. Organization AI settings support model discovery, encrypted
  credentials, gateway-managed authentication and operator-authorized cloud
  workload identities for assistant chats and alert evaluations.
- Add a persistent report status strip with the report build time and optional
  labeled metadata such as the last event time. Authors can set metadata with
  `ctx.set_header(meta=...)`; the strip remains visible in Monitor view, and
  report titles use a compact 12px size.

### Changed

- Save AI settings in place and bind connection tests and capability checks to
  the saved configuration revision. Cloud providers use native streaming and
  authentication flows with bounded requests and shared tool handling.

### Fixed

- Preserve v0.4.6 compatibility while the new AI configuration schema is
  installed by adding a follow-up compatibility migration for existing portal
  releases.
- Stop incomplete AI turns when inference or tool continuation is interrupted,
  and avoid nested credential retries in cloud identity requests. Close Azure
  identity transports with their OpenAI client.

### Security

- Require explicit operator authorization before organizations can use cloud
  workload identities and keep private provider continuation data out of
  session exports.

### Upgrade notes

Apply the additive `orgs.0022_orgassistantconfig_auth_mode_and_more` migration
and `orgs.0023_assistant_config_previous_release_compat` before starting the
new portal. The follow-up migration keeps inserts from the previous portal
release compatible during an application rollback. Existing keyless gateways
with a configured base URL are migrated to gateway-managed authentication.
Review saved AI settings after upgrading.

Workload identity is disabled by default. To enable it, configure
`ASSISTANT_WORKLOAD_IDENTITY_ORGS` on web and alert-evaluating worker processes
with provider-to-organization-ID lists, configure the corresponding cloud
identity on the host, then restart those processes. Deploy the matched portal,
worker and report-runner images. Rebuild reports to include the persistent
status strip and metadata in their generated output. No database change is
needed for the report metadata feature.

## [0.4.6] — 2026-10-09

### Added

- Add a 56px mobile report toolbar with native Options and scope pickers. Move
  theme selection into Options and show a Monitor link that preserves filters
  and fragments without exposing internal frame parameters.

### Changed

- Open top-level authenticated report apps installed on Android or iOS in
  Monitor by default when their launch URL does not specify a display mode.
  Explicit modes and browser and hosted navigation keep their existing behavior.

### Fixed

- Keep report drawers focused and correctly layered when opening or closing
  native mobile pickers.

### Upgrade notes

No database migrations are required from v0.4.5. Update the portal and worker
images and the matched report-runner image to v0.4.6, then restart the services.
The shared menu bundle updates previously built portal reports without a
rebuild. Reload installed apps to receive the updated runtime.

## [0.4.5] — 2026-10-09

### Added

- Add the per-data-source `new_connection_per_query` opt-in. It opens a fresh
  session for each uncached framework query and cleans it up when the query
  finishes; existing connection reuse remains the default. Eligible remote
  disconnects receive bounded retries. This mode supports independent queries
  through framework helpers and does not support shared temporary tables,
  transactions or direct driver session access. Configure it in the repository
  data-source definition, the portal's data-source setting, or the matching
  `<CREDENTIAL_PREFIX>_NEW_CONNECTION_PER_QUERY` environment variable.

### Upgrade notes

Update the portal and worker images and every configured report-runner image to
v0.4.5, then restart those processes so newly started runs use the new code.
After the upgrade, enable `new_connection_per_query` for the data source that
needs it. The option is off by default, and no database migration is required.

## [0.4.4] — 2026-10-09

### Changed

- Allow models without listed prices when both budgets are blank. Enforcing
  either budget for an unlisted model requires input and output prices;
  unpriced usage remains without a cost estimate rather than being counted as
  zero.
- Name the shared organization configuration **AI settings** for report chats
  and alert evaluations. Show saved availability and pricing state on the page
  and update it after an in-place save; connection tests continue to report
  readiness from the saved configuration.

### Upgrade notes

Apply the additive `alerts.0002_alert_run_nullable_cost` and
`orgs.0021_assistant_model_pricing_help_text` migrations. Update the configured
portal/worker and report-runner images to v0.4.4, then restart web and worker
processes so they run the upgraded code.

## [0.4.3] — 2026-10-09

### Fixed

- Retry recognized remote database disconnects during report queries using a
  fresh connection and, when configured, a fresh SSH tunnel. Recovery is bounded,
  logs each retry and keeps completed query results; failed or partial results
  are never cached. Permanent errors and manually managed session state are not
  automatically replayed.

### Upgrade notes

Update the portal/worker image and the configured runner image to v0.4.3,
then restart the worker processes so newly started builds use the new runner.
Builds already running
continue with the runner image they started with. Rerun reports whose previous
builds failed because of a remote database disconnect. This release does not
include a customer database fix.

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

[Unreleased]: https://github.com/trellumhq/trellum/compare/v0.9.1...HEAD
[0.9.1]: https://github.com/trellumhq/trellum/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/trellumhq/trellum/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/trellumhq/trellum/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/trellumhq/trellum/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/trellumhq/trellum/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/trellumhq/trellum/releases/tag/v0.5.0
[0.4.3]: https://github.com/trellumhq/trellum/releases/tag/v0.4.3
[0.4.2]: https://github.com/trellumhq/trellum/releases/tag/v0.4.2
[0.4.1]: https://github.com/trellumhq/trellum/releases/tag/v0.4.1
[0.3.0]: https://github.com/trellumhq/trellum/releases/tag/v0.3.0
[0.2.2]: https://github.com/trellumhq/trellum/releases/tag/v0.2.2
[0.2.1]: https://github.com/trellumhq/trellum/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/trellumhq/trellum/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/trellumhq/trellum/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/trellumhq/trellum/releases/tag/v0.1.0
