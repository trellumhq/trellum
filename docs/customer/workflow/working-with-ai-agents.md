# Working with AI agents

Reports are plain code in a git repository, which is exactly the shape a coding
assistant works well in: it can read what exists, write something new, and open
a pull request you review. This page covers how to set that up so the first
draft is usually right.

## Write AGENTS.md first

Most bad output comes from missing context rather than a weak model. `AGENTS.md`
is where you put what a new colleague would need on day one:

```markdown
# Working in this repository

## Data
- The warehouse is authoritative. Never query the replica.
- Revenue is defined in context/metrics.md. Do not derive it another way.
- `orders_legacy` is deprecated — use `orders`.

## Reports
- Keep database SQL in `queries.py`; use source-specific Python readers where
  needed, and keep transformations and report composition in the generator.
- One report per directory under reports/.
- Every report declares its data sources in report.yaml.

## Review
- Open a pull request. Nothing is merged unstested.
- If a metric definition is unclear, ask rather than guess.
```

Be specific and be blunt. "Do not guess a definition — ask" prevents more bad
reports than any amount of prompting.

## Refine a local preview

After the first build, keep your coding agent listening while you inspect the
report in a browser. In review mode you can select a chart or table, or send a
general change request. The agent edits the report definition or source files
in your repository, rebuilds, and the preview reloads. See
[Refine reports in your browser](refine-reports.md) for the prompt and command loop.

This local source-editing loop is separate from the portal assistant. The
assistant answers questions about portal reports and never edits your Git
repository.

## Give it real data access

For database-backed reports, an assistant that can check a column writes SQL
that runs. One that cannot will invent column names that look plausible and
fail at build time — or worse, silently return the wrong grain. Other sources
can be read with their built-in reader or a project-specific Python adapter.

Two ways to provide access, and you do not need both:

{{figure:data-routes}}

Configured sources are usually enough — see
[Ad-hoc analysis](/docs/latest/workflow/ad-hoc-analysis/).

## Connect it to the portal

The portal is also an MCP server, so the agent that writes reports can read
what the portal has built and, with the right key, operate it: check a
report's last build and validation, query its published numbers, build it,
test a data source, publish pending repository changes. The agent brings its
own model — the portal holds no model key for this, and it works on a
network with no route out.

Two things to have at hand:

- **The server URL** — `https://<portal>/s/<org>/<studio>/mcp`: the studio's
  own URL plus `/mcp`, one server per studio.
- **A personal [API key](/docs/latest/portal/api-keys/)**, sent as a bearer
  token. The agent acts as you, in that organization, under your studio role.

### One command

With the key in hand, run this in the repository root — the API-keys page
shows it ready-made, with the URL filled in, for each studio you can reach:

```bash
trellum setup portal --url https://<portal>/s/<org>/<studio> --key trellum_pk_…
```

It writes the two files below, puts the key in `.env` as `TRELLUM_API_KEY`,
and adds a short "Portal" section to `CLAUDE.md` and `AGENTS.md` so the
agent knows the server exists and what it is for. It refuses to finish while
`.env` is not ignored by git, and re-running it with a new URL or key
updates the entry in place without touching any other server in either
file. `trellum doctor` warns about an entry whose URL is malformed or whose
key is missing from `.env`. What it writes, if you would rather write it
yourself:

### Claude Code

`.mcp.json` in the repository root:

```json
{
  "mcpServers": {
    "trellum": {
      "type": "http",
      "url": "https://<portal>/s/<org>/<studio>/mcp",
      "headers": { "Authorization": "Bearer ${TRELLUM_API_KEY}" }
    }
  }
}
```

### Cursor

`.cursor/mcp.json`, the same shape:

```json
{
  "mcpServers": {
    "trellum": {
      "url": "https://<portal>/s/<org>/<studio>/mcp",
      "headers": { "Authorization": "Bearer ${env:TRELLUM_API_KEY}" }
    }
  }
}
```

Both files reference the key through an environment variable and are safe to
commit. The key itself lives in your shell environment or the `.env` the
repository already keeps out of git — never in the file.

### `read` or `write`

The key's scope decides what the agent is offered:

| Scope | The agent gets |
|---|---|
| `read` | The four read tools. It can look at anything you can look at and change nothing. |
| `read,write` | The read tools plus the actions — and they run immediately. There is no approval card here: your coding agent asks you before every tool call, and that is the approval. |

Actions are offered only when an organization admin has turned on **Let the
assistant propose actions** (the same switch the in-portal assistant uses)
and your studio role allows them; a viewer gets the read tools whatever the
key's scope. Every action is recorded in the audit log against you, with the
key's id.

### The tools

| Tool | What it does | Needs |
|---|---|---|
| `list_reports` | The studio's reports: slug, name, category, last build, link | `read` |
| `get_report_details` | One report's charts, datasets and columns, validation state, last run | `read` |
| `query_report_data` | Filter and aggregate a report's already-built data — published numbers, no warehouse query | `read` |
| `read_doc` | A report's `report.yaml` or `queries.py`, `assistant.md`, project docs (allowlisted paths) | `read` |
| `check_repo_changes` | What the portal would publish: the remote head it last saw and when, reports and data-source declarations added, changed or removed since the last publish, the last error | `read`, developer |
| `test_data_source` | Re-run a data source's connection test | `write`, developer |
| `run_report` | Queue a build now | `write`, developer |
| `publish_repo_changes` | Publish fetched-but-unapplied repository changes (manual publish mode) | `write`, developer |
| `configure_data_source` | Point you at the page where a declared source's credentials are entered — see below | `write`, studio admin |

### Secrets never travel over MCP

`configure_data_source` takes no password, key or token. It answers with the
link to the studio's Configure page for that source and the fields it still
needs; you type them there, in the browser, and they go straight to the
encrypted store. A secret passed as a tool argument is refused and nothing is
stored — so a credential never lands in your agent's context or transcript.

## Publish and configure from your agent

Once wired, the agent that edits reports can also take them to the portal
and back. `trellum guide portal` prints this loop to the agent; here it is
for you.

1. **Edit and build locally.** `python -m trellum.run reports/<slug> --no-serve`.
2. **Validate.** `python -m trellum validate reports/<slug>` — a report is
   finished when there are no FAILs.
3. **Commit and push.** The portal reads the branch it is configured for,
   never a working tree.
4. **Check.** `check_repo_changes` says what the portal saw at its last
   fetch: the remote head and when, the reports added, modified and removed
   since the last publish, data-source declarations that changed, and the
   last error. In [manual publish mode](/docs/latest/portal/publishing-from-git/)
   this is the review step; in auto mode the fetch already published.
5. **Publish.** `publish_repo_changes` publishes the pending changes at the
   head the agent just reviewed.
6. **Build and read the result.** `run_report` queues a build; a moment
   later `get_report_details` shows the last run, its status and the
   validator summary the portal recorded. A FAIL here is the same FAIL the
   local validator shows: the fix is in the repository.
7. **Fix a data source.** A build that fails in the driver usually means a
   credential: `test_data_source` re-runs the connection test and reports
   the detail line; `configure_data_source` returns the studio's Configure
   link, where you enter the secret in the browser. The agent never sees it.
8. **Add an alert.** `create_alert` turns "tell me if APAC DAU drops sharply"
   into a rule on a report — evaluated after each build or on a schedule by
   the portal's own agent, see [Alerts](/docs/latest/portal/alerts/);
   `update_alert` changes it.

Steps 4 and 6 read, and work on a `read` key. Steps 5, 7 and 8 need
`read,write` and the organization's actions switch; your harness asks before
each call, and every call is in the audit log under your key.

## A good first prompt

Point at the context explicitly rather than assuming it will be found:

> Read `AGENTS.md` and `context/metrics.md`. Add a report under
> `reports/churn-cohorts/` showing monthly churn by signup cohort for the last
> twelve months, using the churn definition in the metrics file. Follow the
> structure of `reports/weekly-revenue/`. Run
> `python -m trellum.run reports/churn-cohorts --no-serve` and fix anything
> that fails before you finish.

Naming an existing report as the pattern to follow is the single highest-value
part of that prompt.

## What to check before merging

The diff is the review surface, so review the diff:

- **Does the calculation match the definition?** Code can run perfectly and
  compute the wrong thing, whatever source it reads.
- **Is the grain right?** A join that duplicates rows inflates a total without
  any error appearing.
- **Are filters where they belong?** A filter applied at the wrong step quietly
  changes what a number means.
- **Did it invent a column?** If the build succeeded, it did not — which is why
  it should build before you review.
- **Is SQL in `queries.py`?** Inline SQL is harder to review and harder to reuse.

!!! warning
    An assistant is fast at producing plausible analysis. The reviewer supplies
    the judgement about whether it is *correct* — that responsibility does not
    move. The workflow is built so review is possible: a diff you can read
    rather than a chart you have to trust.

## Why this beats asking a chatbot for a number

A chat answer is produced once and cannot be re-derived — you cannot audit it,
re-run it next quarter, or explain in a meeting how the number was reached. Here
the assistant's output is a program: reviewed, committed, and re-run on a
schedule. Everyone who opens the dashboard next quarter sees a number produced
by that same approved code.
