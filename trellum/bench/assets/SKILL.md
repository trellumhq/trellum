---
name: trellum
description: Explore data and metrics, create or revise interactive reports and dashboards, and write analysis articles or presentations in a Trellum project. Use for Trellum report source, configured data sources, validation and local browser review.
---

# Work with Trellum

Trellum is a Python toolkit for answering data questions, building interactive
reports and dashboards, and writing analysis articles or presentations with
captured evidence. It can use databases, spreadsheets, files and APIs; SQL is
optional. Python prepares the data; reusable charts, KPIs, tables, filters,
layouts and themes provide the browser experience. Reports build to HTML and
JSON for local viewing or publication.

Local work and browser review do not require the optional Trellum portal.
The portal adds shared access, scheduled builds and team workflows. Ask the
installed version for commands, components and guides before choosing an API.

When acting for a portal user, **viewers can ask questions and read permitted
reports and data through available tools; they cannot create, edit, build or
publish reports**, even with a write-scoped key. Report authoring is for
developers and administrators with repository access. For a viewer's report
request, explain that a developer must author it and continue helping with
the permitted data question. Read `trellum guide portal` for tool permissions.
Standalone local work follows the repository's access rules and needs no
portal account.

Use this exact interpreter, run from the repository root:

```
{py} -m trellum                       what it is, the components, the commands
{py} -m trellum guide <topic>...      depth on demand; pass several topics at once
{py} -m trellum data [<source>]       configured sources, their tables and columns
{py} -m trellum datasource add ...    connect a database (writes config + .env)
{py} -m trellum query "<sql>"         run SQL against a configured source
{py} -m trellum checks [<id>...]      what the validator checks, and what one means
{py} -m trellum doctor                is the agent guidance layer wired up
```

Use the interpreter shown above so commands use this project's Trellum version.
Run `{py} -m trellum` at the start of each new chat. It checks for updates at
most once per local calendar day. Tell the user when an update is available,
include the upgrade command, and continue without upgrading automatically.

## Start with the question and project knowledge

Read `context/README.md`, source and metric notes, and existing findings in
`analysis/` when present. Inspect configured sources and check `trellum metrics`
before deriving a KPI. Verify columns, row grain, joins, date coverage and units
against the actual source. Save useful queries and sanitized findings in
`analysis/`; keep local exports in `analysis/results/` and verified knowledge
in `context/`.

| Requested result | Workflow |
|---|---|
| A number, explanation or investigation | `trellum guide answer`; query configured sources by name and report the source, dates, method and limitations. A report is unnecessary unless requested. |
| An interactive report or dashboard | `trellum guide queries format components`, then `guide report` as needed. Author `reports/<slug>/report.yaml`, `generator.py` and source-specific readers or `queries.py`. Reuse Trellum components. |
| An analysis article or presentation | `trellum guide analysis`; scaffold with `trellum analysis new <slug> [--format presentation]`, then author `content.md` or `slides.yaml` with evidence. Use `analysis session reports/<slug>` for capture and review. |
| An operation on a connected portal | When `.mcp.json` configures a Trellum server, read `trellum guide portal` and follow the project's publishing permissions. Local review does not require publishing. |

Commands in the table use the `{py} -m trellum` prefix shown above. Fetch only
the guide topics needed; several fit in one invocation. Use `guide upgrade`
when report source needs changes after a Trellum upgrade.

## Data sources

Discover configured sources before choosing queries or file readers. For a new
source, inspect the supported types and options; the add command writes the
configuration and tests access:

```
{py} -m trellum datasource add --help
```

Use the hidden prompt or supported secret input for credentials. If a source
fails, report the cause; do not substitute invented data for real results.

## Build, validate and open for review

Edit authored files under `reports/<slug>/`; rebuild generated `output/`
files from that source. For an interactive report:

```
{py} -m trellum.new <slug>                      scaffold a report
{py} -m trellum.run reports/<slug> --no-serve   build it
{py} -m trellum validate reports/<slug>        inspect validation
```

`--no-serve` exits when the build is done; without it the command starts a
preview server and never returns.

Read validation results, fix every FAIL and resolve or explicitly explain
WARNs before handing over the report.

A report is not finished until the validator reports no FAILs and the person
can review it. Creating or updating a report includes opening it automatically;
do not wait for another request to launch it or hand over only an output path.

After building and checking validation, run
`{py} -m trellum review start reports/<slug>`, open the printed report URL in
the user's browser, verify it loads, and share a clickable link. On a remote
or headless host, use the agent's browser integration; if opening is unavailable,
explain the limitation and provide a working link. Follow
`{py} -m trellum guide review`, keeping the report-scoped feedback poll active
and rebuilding before replying. Build changed source first with `--no-serve`
or pass `--rebuild` to review start.

For articles and presentations, use `{py} -m trellum analysis session
reports/<slug>` as the review entry point and follow `guide analysis` for its
evidence workspace. Apply the same browser handoff and report-scoped feedback loop.

Explicit build-only/source-only requests, CI and unattended publication skip
interactive review. Honor requests for a plain preview or no browser.
