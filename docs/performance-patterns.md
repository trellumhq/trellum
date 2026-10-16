# Performance and responsiveness patterns

Every portal page and action must stay fast as an organization grows. These
are the patterns the portal already uses. A new feature follows them by
default, and a PR that departs from one says why.


## 0. Invisible to users

**User experience comes first.** Performance work is invisible: it must not
change layouts, controls or interactions that users already know. Any visible
change needs explicit product approval. Before review, compare the changed pages
with `main` on desktop and mobile. Specifically:

- Changing a dropdown, select, tab or toggle applies immediately; there is no
  Apply or Go button. Typing in a search box applies automatically after a short
  pause (about 400 ms); there is no Search button.
- Dropdowns and pickers never paginate. Show every choice, with instant search.
  For a genuinely huge set, switch to search-as-you-type; never show
  Previous/Next pages.
- Pages for realistic studio sizes (tens to low hundreds of items) look and
  behave exactly as on `main`. Server-side bounds stay out of sight until well
  above that.
- Never make the user press Retry, Refresh or Apply for something the system can
  resolve or apply by itself. Poll and retry quietly.
- Keep navigation reachable. A new page needs a visible entry for the roles that
  use it.

## 1. Cost does not grow with data size

- A page or API response does a fixed amount of work whether a studio has 20
  reports or 1,000. Measure query count, response bytes and DOM size at two
  sizes. If they grow, the page is not done.
- Paginate in SQL, never in Python after loading every row. Default page size
  is 25. Use the existing helpers: `apps.operator.bounded.paginate`,
  `apps.orgs.views.paginate`, and `bounded_payload` in `apps/reports/catalog.py`.
- No per-row queries (N+1). Resolve permissions, counts and related rows with
  `Exists`/`Subquery`, annotations, or one bulk query per page.
- Byte-bound anything user-sized: log tails, JSON payloads, previews and
  uploads. See `apps/reports/operations_logs.py` (64 KiB tails, bytes not
  characters).
- Load secondary sections, like favorites, indicators and statistics, after
  the primary content and independently of it. A slow or failed secondary read
  must not delay or break the primary list.

## 2. Requests never wait on slow or unbounded work

- If an action can take more than about 3 seconds at realistic scale, do not
  run it on the request thread. Use a durable action instead: see
  `StudioMaintenanceAction` and `apps/runner/maintenance_actions.py`.
  - Admission is transactional. Allow one active action per scope, honour
    idempotency keys, and set pending limits that return 429.
  - Return `202` with `operation_id` and `status_url`. The UI polls that URL
    and never says "done" on 202.
  - The worker runs the action under a lease. A crashed or expired lease
    becomes `unconfirmed` or `failed`. Never silently re-run it.
- Persist "work to do later" in the same transaction as the data that caused
  it, not only in `on_commit`. See `UnblockRequest` in `apps/datasources`.
- Every external call has an explicit timeout: SMTP (`EMAIL_TIMEOUT`), HTTP
  mail providers, LDAP, data source drivers and assistant providers.
- A timeout must actually stop the work. Thread `join(timeout)` does not stop
  anything. Run killable probes in a subprocess, kill the process group at the
  deadline, and cap concurrency. See `apps/datasources/probe.py`.
- Never automatically retry a write whose outcome is unknown. Show it as
  unconfirmed and let the user decide.

## 3. Browser behavior

- The portal is JavaScript-first. Don't build or test a separate no-JavaScript
  mode for pages, filters, search, pickers or switchers. Settings saves keep
  their real HTML forms enhanced by the shared save handler.
- Use `boundedFetch` (in `static/portal.js`) with an `AbortController`. Keep
  one request in flight per region and use a generation or currency guard so a
  stale response never overwrites newer state.
- Show navigation feedback immediately, and support Cancel and Back
  restoration. Mark links to console pages with `data-console-nav` (see
  [forms-and-navigation.md](forms-and-navigation.md#console-navigation)); a slow
  page keeps loading and is never stopped for the person. Stop polling when the tab is hidden or the view unmounts.
- Use the lifecycle events instead of resetting other controllers:
  `trellum:catalog-hide`, `trellum:catalog-return`, `trellum:catalog-loading`,
  `trellum:catalog-ready`.
- Load feature-only scripts and styles, such as exports and editors, only on
  pages that use them.
- Forms follow [forms-and-navigation.md](forms-and-navigation.md).

## 4. Permissions

- Resolve access once per request with `request_org_access` /
  `actor_studio_access` in `apps/core/request_access.py`. Filter report lists
  with `visible_reports(user, qs, request=request)` in SQL.
- New routes keep or narrow the existing role gates, never widen them, and are
  added to `apps/core/tests/portal_e2e/routes.json`.

## 5. Tests: lean by default

- For each change, write logic and PostgreSQL tests, plus one Chromium desktop
  browser check per new UI. These run on every PR.
- Mark scale, timing, multi-engine, mobile and real-process tests
  `@pytest.mark.heavy`, or add them to the heavy rules in `conftest.py`. They
  run only on demand (`scripts/test.py heavy`, the `heavy-tests` PR label, or a
  manual workflow run). Run them before merging a performance-sensitive change
  or a release.
- A performance fix includes one heavy test that shows the bound, such as
  query count or bytes at two sizes.

## 6. Delivery

- Keep PRs small, with one concern per PR, and merge to `main` early.
  Long-lived parallel branches hide integration failures until the end.
- Run `makemigrations --check` and the default `scripts/test.py portal` on the
  combined branch before asking for review.

## Checklist for a new feature

1. Page and API cost stays flat as data grows. Paginate in SQL, with no
   per-row queries and byte-bounded payloads.
2. Anything slow, or anything that calls an external service, has a timeout
   or runs as a durable background action. Nothing auto-retries an unknown
   write.
3. It feels instant: filters apply on change, search after a short pause.
   Fetches are abortable and guarded against stale responses.
4. Permissions come from the request access facts. New routes are added to
   `routes.json`.
5. Default tests cover the logic plus one Chromium desktop browser check.
   Scale, timing and multi-engine tests are marked `heavy`.
6. It ships as a small PR, merged early.
