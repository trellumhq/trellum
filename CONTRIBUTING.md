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
python -m pytest apps -q
cd trellum/demo
python -m pytest ../testing -q -m "not slow"
```

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
