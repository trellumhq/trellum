# Contributing

Trellum is one AGPL-3.0-only self-hosted product. The portal lives in
`apps/` and `trellum_portal/`; the standalone reporting framework lives in
`trellum/`. Both are covered by the root [LICENSE](LICENSE). Preserve every
third-party and contributor notice in `NOTICE` and `trellum/THIRD-PARTY.md`.

The framework remains host-independent: it never imports Django, the portal,
or an application module. The portal consumes the framework through the
compatibility contract in `trellum/docs/COMPATIBILITY.md`. Keep tenant
boundaries, roles, MFA policy, append-only audit records, encryption, resource
bounds, and the support-bundle schema intact. OIDC and LDAP/Active Directory
are supported identity sources; SAML is not implemented.

## Development

```bash
python -m venv .venv
source .venv/bin/activate  # PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-test.txt
python -m playwright install chromium webkit
```

The portal tests require PostgreSQL, including permission to create a test
database. The default connection is `trellum:trellum` on `127.0.0.1:5433`,
database `trellum_portal`. For a disposable local database:

```bash
docker run -d --name trellum-test-db -p 127.0.0.1:5433:5432 \
  -e POSTGRES_USER=trellum -e POSTGRES_PASSWORD=trellum \
  -e POSTGRES_DB=trellum_portal postgres:16-alpine
```

Wait until `docker exec trellum-test-db pg_isready -U trellum` succeeds before
running the tests. If you already have a dedicated test server, set
`DATABASE_URL` to it instead; do not use your portal's production database.
On Linux, Playwright may also require browser system dependencies; install
them with `python -m playwright install-deps chromium webkit`.

```bash
python scripts/test.py local apps/accounts/tests
python scripts/test.py local apps/core/tests/test_shared_pickers_browser.py --workers 2
python scripts/test.py portal --workers 2 --junitxml .data-test/results/portal.xml
cd trellum/demo
python -m pytest ../testing -q -m "not slow"
```

`local` requires explicit test files, directories, or pytest node IDs; `-k` can
further narrow that selection. Choose related tests yourself, including shared
behavior affected by the change. Use focused local checks during development
and before opening a PR; GitHub runs `portal` as the broader gate, covering
every ordinary portal test under `apps/`, including Chromium and WebKit browser
regressions. Running `portal` locally is optional for broad changes or CI
failure reproduction. It excludes tests marked `docker_sandbox` (real-daemon
Docker checks run separately in CI) and `heavy` (below). The standalone framework and
website suites also remain separate.

### Heavy tests

Tests marked `heavy` are opt-in: scale and performance measurements (hundreds
to 100,000 rows), the non-smoke cases of browser matrices (WebKit, mobile
viewports, larger sizes; one Chromium desktop smoke case per test stays in the
default run), and real-process lifecycle tests. They roughly double the
runtime of `portal`, so neither `scripts/test.py portal` nor the GitHub
`portal` workflow runs them by default. Run them before merging a
performance-sensitive change or a release, or when you touch the area they
cover:

```bash
python scripts/test.py heavy              # only the heavy tests
python scripts/test.py portal --heavy     # everything, heavy included
python scripts/test.py local <paths> --heavy
```

In GitHub, start the `portal` workflow manually with `heavy` set, or label a
pull request `heavy-tests`. A skipped heavy job does not block the required
checks; a requested one must pass. Marking happens in the root `conftest.py`.

The local default is four workers; `portal` defaults to two. Set `--workers`
from 1 to 16 to match available PostgreSQL connections, memory, and browser
capacity; 1 runs serially. Parallel scheduling keeps each test class or module
together with `loadscope`. Both commands print their pytest invocation, elapsed
time, and the 20 slowest tests, and write JUnit XML under `.data-test/results/`.
Use `--durations N` and `--junitxml PATH` to adjust reports. Paths are relative
to the repository root even when invoking the script from another directory.
`--collect-only` shows selected tests; `--dry-run` prints the command.
The wrapper clears inherited `PYTEST_ADDOPTS` so shell defaults cannot silently
filter coverage or change worker counts. Use plain pytest for other options.
GitHub retains sanitized JUnit outcomes, test timing JSON, and summaries for
seven days. Uploaded results omit failure bodies, captured output, and test
properties. Browser HTML, traces, and screenshots are not uploaded.

The ordinary framework suite excludes `slow` tests. Pull requests that change
rendering, components, browser code, data or report generation, themes, or
benchmark sources and shared fixtures run the full slow benchmark lane too.
Its reports include measurements at each tested row count; the `benchmarks`
workflow also remains available for manual runs. Unrelated and documentation
changes skip that expensive lane.

The aggregate check names are `portal checks` and `framework checks`. They
report on every PR, including documentation changes, and fail if an applicable
job fails, is canceled, or unexpectedly skips. Making these checks required is
a separate GitHub repository protection setting.

For repeated focused runs, optionally retain Django's test databases:

```bash
python scripts/test.py local apps/accounts/tests --reuse-db
# Rebuild after changing models or migrations, or switching to a different schema.
python scripts/test.py local apps/accounts/tests --reuse-db --create-db
```

Database reuse does **not** apply schema changes automatically. Rebuild with
`--create-db` whenever the retained schema may be stale. pytest-django gives
parallel workers separate test databases with `_gw0`, `_gw1`, etc. suffixes;
the PostgreSQL account must be able to create each one. Concurrent commands or
worktrees must use separate dedicated `DATABASE_URL` database names, or run
sequentially, to avoid sharing those worker databases. `portal` always uses
fresh databases. Test-command argument checks need no database:
`python scripts/test_test_command.py`.

When finished, `docker rm -f trellum-test-db` removes the disposable test
database. Website documentation checks have a separate setup in
[website/README.md](website/README.md).

Run the full product with `./scripts/demo.sh` or `./scripts/demo.ps1`. New
framework behavior needs a worked example under `trellum/demo/`; new behavior
needs a focused test. Do not add per-file licence headers.

When building or changing portal forms, saves, polling or navigation, follow
[Forms and navigation](docs/forms-and-navigation.md) and its browser and server
verification requirements. Use the shared settings handler and response
helpers, preserve drafts and scroll, and reject obsolete background results.

## Pull requests

Keep one concern per change and explain why it belongs in the affected layer.
Add an entry under `[Unreleased]` in `CHANGELOG.md`. There is no CLA or DCO;
submitting a change offers the files you touched under AGPL-3.0-only.

Before opening a PR, check documentation links and navigation, parse changed
YAML, and run the smallest relevant test command. Never commit credentials.
Report security issues through [SECURITY.md](SECURITY.md).
