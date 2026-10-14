# Idle refresh suspension

Scheduled reports keep rebuilding forever, whether or not anyone still opens
them. That costs warehouse time and worker capacity on reports nobody reads.
This feature uses the view analytics we already capture (`ReportViewEvent` /
`ReportViewDaily`, fed by `record_view()`) to stop auto-refreshing a report
once nobody has looked at it for a configurable number of days.

## Behaviour

- **Org setting**, "Auto-refresh" tab under org settings (org admins):
  *Suspend scheduled refresh after N days without a view.*
  Blank = default **60 days**. `0` = never suspend (feature off for the org).
- **Activity clock** for a report is the latest of:
  `last_viewed_at` (any portal or share-link view), `refresh_resumed_at`
  (last manual resume), and `first_seen_at` (so a brand-new report gets a
  full window before it can be suspended).
- When a report's schedule fires and the activity clock is older than the
  org's threshold, the run is **skipped** and the report is marked
  `refresh_suspended_at = now`. From then on the scheduler does not register
  a job for it at all. The `report.yaml` schedule is untouched: the cron
  is still shown, but it doesn't fire.
- **Exempt**: a report with an enabled `EmailSchedule` or an enabled
  schedule-triggered `AlertRule`. The people who get those emails and alerts
  never open the portal, so they never record a view, and those consumers
  need fresh data.
- **Resume is manual only.** Viewing a suspended report still records the
  view, but does not resume it. Someone with the developer role has to press
  **Resume auto-refresh**, which clears the suspension, sets
  `refresh_resumed_at = now` (which restarts the clock), queues an immediate
  rebuild, and writes an audit entry (`report.refresh_resume`).
- A plain "Run now" builds once and leaves the report suspended.

## Data model (`apps.reports`)

- `Report.last_viewed_at` (nullable). A denormalised copy of the latest view,
  bumped by `record_view()` only when it creates a new hourly event, so the
  cost is at most one extra UPDATE per viewer per hour. A migration backfills
  it from `ReportViewDaily` and `ReportViewEvent`.
- `Report.refresh_suspended_at` (nullable). NULL means active.
- `Report.refresh_resumed_at` (nullable). The migration sets it to the upgrade time for every existing report, so nothing is suspended before a full window has passed after rollout.
- `OrgRefreshPolicy` (one-to-one with the org, same shape as
  `OrgLiveQueryPolicy`): `suspend_after_days` (nullable, where NULL means
  `DEFAULT_REFRESH_SUSPEND_DAYS = 60`), `updated_by`, `updated_at`.
  Helper: `refresh_suspend_days(org) -> int`.

## Touch points

| Where | Change |
|---|---|
| `apps/reports/models.py` | fields, `OrgRefreshPolicy`, `refresh_suspend_days()`, `idle_since()`/`should_suspend()` helpers, `record_view` bumps `last_viewed_at` |
| `apps/runner/management/commands/runworker.py` | `refresh()` excludes `refresh_suspended_at__isnull=False`; `_enqueue_scheduled` checks `should_suspend` and marks plus skips instead of enqueueing |
| `apps/reports/views.py` | `api_refresh_policy` + `org_refresh_settings` (copy of the live-query pair); `api_report_resume_refresh` (DEVELOPER, POST) |
| `trellum_portal/urls.py`, `templates/_org_nav.html`, `apps/core/context_processors.py` | new settings tab `org-refresh` at `/orgs/<org>/settings/auto-refresh` |
| `apps/core/audit_actions.py` | `refresh_policy.update`, `report.refresh_suspend`, `report.refresh_resume` |
| `apps/reports/scan.py` | payload gains `refresh_suspended_at` (ISO or null) |
| `static/portal.js` | "Auto-refresh paused" badge and a **Resume** button on report cards (developer role) |
| `docs/customer/portal/report-analytics.md` | customer-facing explanation |

## Phases

1. **Backend (this branch):** model, migration with backfill, policy plus
   settings tab, scheduler gating, resume API, scan payload field, tests.
2. **Portal UI (this branch):** badge and resume button, customer docs.
3. **Later / open:**
   - A banner inside the built report page ("auto-refresh paused, data as of X").
   - A notification to the report owner a few days before suspension.
   - Studio- or report-level overrides of the org default.
   - Whether embed views should count (they go through the share-link path today, so they do).
