Run the live baseline with Python 3.12, Node 22 and Playwright 1.62.0 browsers:

```sh
python scripts/portal_e2e.py --shard L0 --artifacts /absolute/new/artifact-directory
python scripts/portal_e2e.py --shard L0 --browser webkit --real-hasher --artifacts /absolute/other-directory
python scripts/portal_e2e.py --reports 200 --members 25 --studios 10 --metrics 500 --schedules 10 --rules 10 --artifacts /absolute/growth-directory
```

The runner owns a temporary PostgreSQL 16 container and DATA_DIR, applies real
migrations, starts Django, performs real CSRF/session login and profile save,
asserts PostgreSQL persistence after reload, and checks registry/status/runstats.
A safe live GET matrix also checks fixture-specific downstream role gates for
deliveries, members/groups and Metrics. Redirect-only entry is recorded separately
from its destination authentication/tenant boundary; no redirect grants access.
It refuses checkouts containing `.env`, inherits only runtime paths/language
settings and requires a new empty artifact directory. Cleanup affects only its
own process/container. Static files use test-only `runserver --insecure`.

Growth choices: reports 20/200/1000, members 3/25/100, studios 1/10/50,
metrics 20/500/2000, schedules and rules 1/10/50. Role fixtures add users to the
requested growth cohort; `seed.json` records actual persisted model counts.
Every cohort member has direct viewer membership in each studio. Two orgs have
overlapping names. Beta is deliberately public-only for the full-viewer sample;
alpha holds the private/finite-scope sample. Each budget remains keyed by its
actual tenant path. Separate users represent org/studio admins, developer/viewer,
group admin/member/selected viewer, a public-only beta full viewer and an alpha viewer with explicit
private-report assignment (still finite-scope under current permission semantics), operator, inactive, and unaffiliated revoked
identity. An additional live scenario removes an authenticated viewer's access,
checks inactive login denial, and starts/stops real operator impersonation.

Controlled artifacts include built/unbuilt/expired/failed/private reports and an
analysis plus unbuilt/failed/expired analyses, two A/B components per built fixture, metric claims/definitions,
annotations, successful/error run history, delivery recipients and disabled
schedules/rules with evaluation history, share/invite objects, assistant sessions
and proposals, paginated audit/server-log cohorts, a local Git remote and working checkouts, and declared CSV sources.
These are valid seeded artifacts, not a claim that a worker produced them.
Schedules/rules are disabled and no workers run in this runner, so no provider
calls or emails are triggered. Outbound mail uses Django's in-memory backend.

`routes.json` is a checked-in plan for all 198 first-party routes plus native
Django/admin/allauth boundaries. Each entry has explicit owner case/shard,
principal names from `identities.py`, request conditions, method/action/tab
scenarios and planned test IDs. Public entry has an explicit empty denied-role
set: invalid tokens/state/input are separate request conditions. Parameter
providers resolve in the isolated database; credentials are minted/looked up
only at runtime and never serialized into the portable manifest. Native callback
parameters are fixtures for future protocol tests, not a configured live OIDC
provider. Planned IDs deliberately refer to future suites. `manifest.py` fails
on resolver, principal, parameter, action or source/adapter drift. Handler/action
changes require reviewing this plan; never refresh hashes to hide unmapped work.
Every principal is explicitly allowed, denied, or conditional/not applicable
with concrete fixture/session setup and a linked planned scenario. These sets
are disjoint and cover the declared seeded principal universe. Normal validation
also requires every source-declared HTTP method, including forms with no named
action. These structural checks do not prove source authorization correctness;
the live matrix and independent scenario verification remain required.
Mapping completeness is separate from verification completeness. `--all` exits
nonzero while L1–L5 journeys and verified route/action evidence are pending.

Server-thread instrumentation records SQL count/time, storage API count/time,
response bytes through stream completion and total elapsed time. Storage totals
count top-level calls to avoid nested time duplication; `storage_operations`
includes nested portal storage API operations. This is API-level work timing,
not a physical filesystem/object-store operation counter. Direct filesystem reads
outside `apps.core.storage` and remote object-store integration remain unmeasured.
`PORTAL_E2E_SQL_DELAY_MS=30` delays SQL inside live HTTP request threads.

Role requests explicitly reset the server registry cache for the cold registry
sample and repeat without reset for warm. This is registry-cache cold/warm,
not OS page-cache cold, fresh-process cold or remote-object-store cold. Other
endpoints have the same labeled measurement phase but no artificial cache reset.
Budgets are keyed by method, principal label, path and phase, and include actual
fixture counts. `approved_thresholds: null` means coordinator approval pending;
observed maxima are measurements, never automatically approved acceptance caps.
Principal labels are attribution headers in test-only middleware, not auth grants.

Request logs exclude SQL text, parameters, cookies and credentials. Browser traces
contain authenticated state and must remain private. Portable result/work/role/
seed/route JSON contains no cookies or raw share/session tokens. The ordinary
Django suite uses `-m "not docker_sandbox and not portal_e2e"`; the live runner
initializes Django explicitly and disables pytest-django to retain its real
migrated/seeded database rather than create a substitute.

The separate worker gate remains the `django-e2e` job in
`.github/workflows/portal.yml`: seed with `import_project demo/demo trellum/demo`,
start `python manage.py runworker` and Django at E2E_BASE, run
`python scripts/e2e.py build`, restart the owned web process, then run
`python scripts/e2e.py verify`. The Docker gate is
`python -m pytest apps/runner/tests/test_sandbox.py -m docker_sandbox -q` after
building the runner image as specified there. This runner does not claim either.

Remaining gates: L1–L5 functional journeys, role/input conditions per mutation,
third-party fake providers and remote object-store outage paths, full-browser HTTP
fan-out/DOM/CPU/polling/concurrency, worker occupancy and background work,
independent per-case acceptance budget approval, worker/sandbox verification on
the final composed SHA. Mapping and baseline passes do not complete those gates.

After reviewing a changed handler and its permissions/actions, update only that
handler's digests in its task worktree (repeat the flag for reviewed helpers'
callers). This refuses missing actions/methods and preserves all role, case,
parameter, disposition and planned-node policy:

```sh
PORTAL_E2E_DATA_DIR=/tmp/isolated-manifest-check DJANGO_SETTINGS_MODULE=apps.core.tests.portal_e2e.settings python -m apps.core.tests.portal_e2e.manifest /tmp/reviewed-route-result.json --refresh-reviewed-view apps.reports.views.api_registry
```

This command is a deliberate reviewed ledger edit, not an automatic runner step.
# Phase 1 runtime and evidence

`scripts/portal_e2e.py --infrastructure --browser chromium --artifacts <empty-dir>`
runs the owned runtime contract and live smoke checks. Repeat with `webkit`.
The smoke checks use widths 1440/390 and 720/195 (equivalent layout widths at
200% browser zoom), native and JavaScript-enabled account POST/redirect Save,
and an actual shared AJAX group settings Save. They assert session identity,
server-observed actor identity, PostgreSQL persistence, reload, rejected CSRF
without a write, and visible feedback with nonzero geometry. This is **runtime
infrastructure proof**, not complete account/group journey coverage.

The runtime migrates and seeds an owned PostgreSQL 16 container once, collects
static assets outside the checkout, and clones a database plus DATA_DIR per
stateful journey unit. Seed-local absolute paths in database text/JSON and copied
files are relocated into the clone; symlinks and binary path references fail
closed. Each unit starts real Gunicorn with its default request/body limits and
PBKDF2 passwords. No pytest-django substitute database or rollback surrounds
external HTTP writes. Mutation-isolation checks verify the next unit and the
seed template remain unchanged. Only owned processes, databases and files are
removed. Multiple sessions within a unit can test revocation.

Default `--shard L0` retains the existing baseline checks. Empty L1–L5 journey
shards never pass. `--all` remains nonzero until every required reviewed journey
binding is collected, executes, and passes. The separate `bindings.py` catalog
starts empty: planned route IDs and successful HTTP statuses are not execution
evidence. Reviewed route policy stays in `routes.json`.

Coverage bindings identify the exact route/method/action, principal, named
condition, browser/layout/mode, pytest node ID and assertion obligations.
Portable evidence contains normalized route patterns, scoped numeric actor IDs,
salted session digests, correlation IDs, completed request observations, explicit
assertion checkpoints, pytest outcomes and source/binding digests. Role headers
are attribution only. Required skipped/xfail/error/uncollected nodes, stale or
foreign evidence, incomplete requests, wrong actors and missing persistence
checkpoints block verification. Reviewed required condition lists prevent one
happy path from certifying a whole policy tuple. No conditions are automatically
invented from policy prose. Contract mutation tests exercise these failure modes.

Browser resource measurements retain transfer/encoded/decoded byte counts,
DOM/frame/chart counts and geometry without resource URLs. SQL operation delay
remains explicitly local simulated latency. Measured baselines have no accepted
threshold until separately reviewed. Streaming measurements include completion;
aborted streams cannot certify a complete response window. Trace ZIPs and server
logs are private local artifacts and may contain authenticated snapshots; do not
publish them. Portable summaries omit cookies, resolved token URLs and bodies.
The unchanged L0 storage guard retains a literal-path compatibility log under
`baseline/work.jsonl`; that legacy log is private. The root `work.jsonl` and
budget summaries use normalized route patterns. Artifact directories are mode
0700, and seed metadata is copied into each pytest invocation directory.

Multi-engine product journey coverage is not implemented in Phase 1. Current
future journey selection filters routes, not browser variants: a single-engine
run cannot certify bindings that require both engines. Phase 2 must explicitly
namespace execution identities by engine and merge independently executed
results on the same source/binding hashes, or execute both engines within one
gate. Do not silently drop other-engine requirements or merge repeated pytest
node names as if they were distinct executed variants. The current empty
journey catalog therefore keeps the full gate blocked for both browsers.

Full suites, real worker/restart behavior and Docker sandbox checks remain
separate final gates. Phase 1 never certifies those gates or remaining journeys.
