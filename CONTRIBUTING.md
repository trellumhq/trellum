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
py -3 -m venv .venv
.venv/Scripts/pip install -r requirements-django.txt -r requirements-test.txt
python -m pytest apps -q
cd trellum/demo
python -m pytest ../testing -q -m "not slow"
```

Run the full product with `./scripts/demo.sh` or `./scripts/demo.ps1`. New
framework behavior needs a worked example under `trellum/demo/`; new behavior
needs a focused test. Do not add per-file licence headers.

## Pull requests

Keep one concern per change and explain why it belongs in the affected layer.
Add an entry under `[Unreleased]` in `CHANGELOG.md`. There is no CLA or DCO;
submitting a change offers the files you touched under AGPL-3.0-only. Never
add activation, paid-tier, expiry, or growth-cap behavior.

Before opening a PR, check documentation links and navigation, parse changed
YAML, and run the smallest relevant test command. Do not include private
planning, hosted-service credentials, or historical commercial material in
public documentation. Report security issues through [SECURITY.md](SECURITY.md).
