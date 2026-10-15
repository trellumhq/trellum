# Agent guide

This is the main instruction file for AI coding agents working in this
repository, whichever model or tool you use. Read it before changing code.
People should start with [CONTRIBUTING.md](CONTRIBUTING.md).

## Repository

- This is a monorepo with two layers. The AGPL-3.0-only portal is in `apps/`
  and `trellum_portal/`. The standalone reporting framework is in `trellum/`;
  report-building agents also follow [trellum/AGENTS.md](trellum/AGENTS.md).
- The framework never imports Django, the portal or an application module, and
  CI enforces this. The portal consumes it through
  `trellum/docs/COMPATIBILITY.md`.
- Both layers share one version: `trellum_portal/__init__.py` and
  `trellum/__init__.py` must agree.
- New core code goes in `apps/` or `trellum/`. Do not add paid editions,
  licence gates or capability caps.
- Keep tenant boundaries, roles, MFA policy, append-only audit records,
  encryption, resource bounds and the support-bundle schema intact.

## Required reading by area

| Changing | Read first |
|---|---|
| Any portal page, API, list, action or background job | [docs/performance-patterns.md](docs/performance-patterns.md) |
| Pages, forms, selectors, dialogs, shared controls | [docs/design-system.md](docs/design-system.md) |
| Forms, saves, polling, navigation | [docs/forms-and-navigation.md](docs/forms-and-navigation.md) |
| Models or migrations | [docs/MIGRATIONS.md](docs/MIGRATIONS.md) |
| Reports built with the framework | [trellum/AGENTS.md](trellum/AGENTS.md) |

## Rules for every feature

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

Then:

1. **Bounded cost.** Page and API work stays flat as data grows. Paginate in
   SQL with 25 rows by default, never make per-row queries, and byte-bound
   payloads. Load primary content first and secondary sections independently.
2. **No unbounded waits.** Every external call has a timeout, and the timeout
   really stops the work. Anything slow at realistic scale becomes a durable
   background action: transactional admission, a `202` response with a status
   URL that the UI polls, a worker lease, and no silent re-run. Never
   automatically retry a write with an unknown outcome; show it as
   unconfirmed.
3. **JavaScript-first.** The portal is a JavaScript application; don't build
   or test a separate no-JavaScript mode for pages, filters, search, pickers or
   switchers. Settings saves keep their existing real HTML forms enhanced by the
   shared save handler. Fetches are abortable, keep one request in flight per region, and reject stale
   responses. Polling stops when the tab is hidden or the view unmounts.
4. **Reuse shared UI.** Use the shared controls and the settings save
   contract. Don't write another per-page submit pattern or reload the
   document after a routine save. Saving preserves scroll, focus and
   neighbouring drafts, and shows saving, saved, error and unconfirmed states.
5. **Permissions.** Resolve access with `apps/core/request_access.py` and
   filter reports with `visible_reports(..., request=request)`. Never widen an
   existing role gate. Add new routes to
   `apps/core/tests/portal_e2e/routes.json`.
6. **Lean tests.**
   - Write logic and PostgreSQL tests, plus one Chromium desktop browser check
     for each new UI.
   - Mark scale, timing, other-engine, mobile and real-process tests
     `@pytest.mark.heavy`, or add them to the heavy rules in `conftest.py`.
     Run heavy tests only before merging a performance-sensitive change or a
     release.
   - A performance fix includes one heavy test showing the bound.
   - For UI changes, also look at the rendered page on desktop and mobile.
     Passing tests do not prove the layout.

## Workflow

- Do product work in a task branch and git worktree, never directly on `main`.
- Keep PRs small, with one concern each, and merge to `main` early. Don't
  build up many long-lived parallel branches. If you run parallel agents,
  integrate each one as soon as it passes.
- Before review, run `python scripts/test.py local <paths>` for the touched
  areas, `python manage.py makemigrations --check`, and, for broad changes,
  `python scripts/test.py portal` on the combined branch.
- Delegate long test runs and mechanical execution to cheaper or faster
  models where your tool supports subagents. Give them exact scopes and ask
  for short reports: commit SHA, what passed, what is still open.
- Add an `[Unreleased]` entry to `CHANGELOG.md`.
- Commit messages, code comments and docs state product facts. Product
  strategy, roadmap reasoning and research belong in the maintainers' private
  planning notes, not in this repository or its history. Never reference
  other products as inspiration.
- Never commit credentials, send real email, or call paid providers in tests.
  Pushing, releasing and deploying need explicit maintainer approval.

## Launching the portal for someone to review

Use `./scripts/demo.sh --dev-login`. Set `TRELLUM_DEMO_PROJECT` and
`TRELLUM_PORT` to run beside an existing instance. Always hand the person the
three instant-login links the script prints, for admin, member (studio
developer) and viewer:

    <base>/dev-login/?as=superuser
    <base>/dev-login/?as=developer:demo/demo
    <base>/dev-login/?as=viewer:demo/demo

They work only because the overlay runs the web process with DEBUG settings.
Use them on local or LAN review instances only, never on one reachable from
the internet.

## Local report previews

To show someone a local report, run
`python -m trellum review start reports/<slug>`, then follow
`python -m trellum guide review`. Poll with `--report <slug>` and rebuild
before replying. Build changed source first with `--no-serve`, or pass
`--rebuild`.
