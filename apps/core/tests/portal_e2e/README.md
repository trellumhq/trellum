Run the live baseline with Python 3.12, Node 22 and Playwright 1.62.0 browsers:

```sh
python scripts/portal_e2e.py --shard L0 --artifacts /absolute/new/artifact-directory
python scripts/portal_e2e.py --shard L0 --browser webkit --real-hasher --artifacts /absolute/other-directory
```

The runner owns a temporary PostgreSQL 16 container and DATA_DIR, applies real
migrations, starts Django, performs real CSRF/session login and profile save,
asserts PostgreSQL persistence after reload, and checks registry/status/runstats.
`--reports 20|200|1000`, `--members 3|25|100`, and `--studios 1|10|50` select growth
fixtures. `PORTAL_E2E_SQL_DELAY_MS=30` delays SQL inside live HTTP threads.
Static files use Django's explicit test-only `--insecure` server option.

Request instrumentation excludes query text, parameters, cookies and credentials.
Trace files contain authenticated browser state and must remain private. Portable
result/work/role JSON files contain no cookies. Screenshots and traces are test
artifacts, not proof that every page family has layout coverage.

`--all` exits nonzero while L1–L5 or route/action coverage is pending. The route
manifest inventories resolver routes (including third-party integration routes);
role/action/case mappings marked pending are not verified. The ordinary Django
suite should use `-m "not docker_sandbox and not portal_e2e"`. Browser fixtures
must never replace the ordinary unit-test database.

The separate worker gate remains the `django-e2e` job in
`.github/workflows/portal.yml`: seed using `import_project demo/demo trellum/demo`,
start `python manage.py runworker` and Django at the configured E2E_BASE, run
`python scripts/e2e.py build`, restart the owned web process, then run
`python scripts/e2e.py verify`. The Docker gate is
`python -m pytest apps/runner/tests/test_sandbox.py -m docker_sandbox -q` after
building the runner image as specified in that workflow. This baseline runner
currently does not claim either gate.

Pending: complete built/expired/failed payload fixtures; schedules/alerts/metrics
scales; object-store call timing; concurrency/worker occupancy; independent
cold/warm/role budget caps; full route/action ownership and verified node mapping;
L1–L5 journeys. Baseline budgets report observed numeric costs only. Coordinator
review is required before treating a value as an acceptance threshold.
