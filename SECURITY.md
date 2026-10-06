# Security

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private vulnerability reporting
on this repository, or email **security@trellum.dev**.

Please include what you did, what happened, and what you expected. A working
proof of concept helps but is not required — a clear description of the flaw
is worth more than a half-finished exploit.

We will acknowledge within three working days and tell you what we think the
severity is and roughly when a fix will land. If we disagree that something is
a vulnerability we will say so, and why.

There is no bug bounty. Fixes land on the latest release; older releases are
not backported.

## What is in scope

This repository contains two things with very different threat models.

**The control plane** (`apps/`, `trellum_portal/`) is a
multi-tenant web application. Anything you would expect: authentication and
session handling, the organization/studio/report permission model, tenant
isolation, the encrypted-credential storage for data sources, SSRF, injection,
and the sandbox the report runner executes customer code in.

Two areas deserve particular attention because they are where the interesting
mistakes live:

- **Cross-tenant access.** Reaching an organization, studio, report or built
  artifact you are not a member of. `apps/core/permissions.py` resolves every
  answer; a path that renders content without going through it is a bug even
  if you cannot yet demonstrate a leak.
- **The report sandbox.** `generator.py` is code *customers* write, executed
  by us. Escaping the sandbox, reading another tenant's data, reaching the
  host network or the cloud metadata endpoint, or persisting across runs are
  all in scope. See `apps/runner/`.

**The framework** (`trellum/`) is a library, and the statements below scope
*the package*, not the product. Used standalone it has no authentication, no
accounts, no access control and no multi-tenancy, by design — a built report
is a self-contained HTML file and access control belongs to whatever hosts the
output. (In this repository the control plane is that host, and it has all
four; see above.) So these are not framework vulnerabilities: "there is no
login", "the preview server is not hardened" (it is a local authoring tool),
or anything requiring the attacker to already control your report code, your
config, or the machine running the build.

These are, and we want to hear about them:

- A data value escaping into HTML or JavaScript in a built report and
  executing.
- Credentials reaching build output, logs, or the generated `data.json`.
- Path traversal or arbitrary file write during a build, driven by config or
  data.
- A report reaching the network when it should not.
- Dependency vulnerabilities actually reachable from the framework's own code.

If you are not sure which side of the line something is on, report it anyway.

## What is not in scope

- Findings from an automated scanner with no demonstrated impact.
- Missing hardening headers on a page that carries no session or data.
- The `docker-compose.*.yml` development overlays. They ship well-known
  credentials on purpose and say so in a comment; they are not a deployment.
- Denial of service through resource exhaustion by a user who is *already*
  authenticated and authorized to run builds. Quotas exist for this and are a
  product feature, not a security boundary.
- Social engineering, physical access, or anything requiring a compromised
  administrator account.

## Licence

Owned Trellum code is released under AGPL-3.0-only. Third-party components
retain their own terms; see `NOTICE` and `trellum/THIRD-PARTY.md`.
