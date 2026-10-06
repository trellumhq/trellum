# API keys

A personal API key lets a script, a CI job or your own coding agent call
the portal as you — under your own roles, in one organization — without a
browser session. The studio JSON endpoints the portal's own pages already
use accept it; there is no separate API to learn.

## Creating a key

Open your account menu (top right) and choose **API keys**. A key has:

- **Name** — what it is for ("laptop agent", "nightly CI"), so you
  recognise it in the list later.
- **Organization** — the one organization the key works in. A key never
  reaches another organization, even one you are also a member of; make one
  per organization.
- **Scope** — `read` or `read,write`, see below.
- **Expires** — optional. Blank means the key lasts until it is revoked.

The secret is shown **once**, on the page right after you create it, with a
copy button and a ready-made `curl` line. It is stored only as a hash: if
you lose it, revoke the key and create a new one. The list shows each key's
prefix (`trellum_pk_ab12cd34…`), scope, when it was created and last used,
and whether it is active, expired or revoked.

## `read` vs `write`

| Scope | Unlocks |
|---|---|
| `read` | Every GET endpoint: the report registry, build status and logs, validation results, git status and history, the data-source list. |
| `read,write` | Everything above, plus every POST: run or stop a report, request a fetch or publish from git, test a data source, clear the query cache. |

Scope is a ceiling on top of your studio role, never a way around it. A
`read,write` key held by a studio *viewer* still cannot start a build — the
same refusal the portal gives in the browser. A `read` key used on a POST
is refused with `403 {"error": "write_scope_required"}`.

The AI assistant's chat endpoints are session-only and refuse every key
with `403 {"error": "session_required"}`; the door for agents is MCP, below.

## Using a key

Send it as a bearer token:

```bash
curl -H "Authorization: Bearer trellum_pk_…" \
  https://<portal>/s/<org>/<studio>/api/system/git/status
```

That call needs the *developer* role on the studio; `api/registry` works
for any member. The endpoints that matter most, each relative to
`https://<portal>/s/<org>/<studio>/`:

| Endpoint | Method | Role | Scope |
|---|---|---|---|
| `api/registry` | GET | viewer | read |
| `api/reports/<slug>/status` | GET | viewer | read |
| `api/reports/<slug>/validation` | GET | viewer | read |
| `api/reports/<slug>/log` | GET | developer | read |
| `api/reports/<slug>/run` | POST | developer | write |
| `api/reports/<slug>/stop` | POST | developer | write |
| `api/system/git/status` | GET | developer | read |
| `api/system/git/check` | POST | developer | write |
| `api/system/git/publish` | POST | developer | write |
| `api/datasources` | GET | viewer | read |
| `api/datasources/<name>/test` | POST | developer | write |

A key that is revoked, expired, switched off for its organization, or
belongs to someone who has left the organization or been deactivated gets
`401 {"error": "invalid_api_key"}` — and never falls back to a browser
session that happens to be present in the same request.

## Use it from your coding agent

Every studio is also an MCP server, at
`https://<portal>/s/<org>/<studio>/mcp`, authenticated with this same key
and nothing else. Point Claude Code or Cursor at it and the agent reads the
portal — and, on a `read,write` key, operates it — under your role. The
configuration snippets, the tool list and what each scope unlocks there are
in [Working with AI agents](/docs/latest/workflow/working-with-ai-agents/#connect-it-to-the-portal).

The quickest way in: right after you create a key, this page shows a
ready-made `trellum setup portal --url https://<portal>/s/<org>/<studio>
--key …` line for each studio you can reach. Run it in the repository root
and the agent is wired — the files, the key in `.env`, and a note in the
agent's own instructions that the portal is there.

## Revoking

**Revoke** on the key's row stops it immediately; there is no undo.
Rotating a key is creating a new one and revoking the old. A key also dies
the moment its owner is removed from the organization.

## The organization view

An org admin sees every key issued in the organization under
**Organization settings → API keys** — owner, name, prefix, scope, last
used — and can revoke any of them.

The same page carries one switch, **Allow API keys in this organization**,
on by default. Turning it off stops every key in the organization on its
next request without deleting any; turning it back on restores the ones
that were not revoked in the meantime.

## What is recorded

Creating and revoking a key, and flipping the organization switch, are all
in the audit log. Every action taken with a key is recorded against its
owner with the key's id in the entry's details, so "the CI key did this" is
one filter away.
