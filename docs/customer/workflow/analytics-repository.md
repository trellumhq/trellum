# The analytics repository

One repository holds two things: the reports you ship, and everything an
assistant needs to know to write them. This page covers what {{BRAND}} actually
requires — which is very little — and then what we would recommend putting
alongside it.

## What {{BRAND}} requires

All of it:

{{figure:repository-contract}}

Directories whose names start with `_` are skipped. A report needs
`report.yaml` and `generator.py`. An analysis uses the same directory convention
with `kind: analysis` in `report.yaml` and `content.md` instead of a generator.
See [Publish an analysis](/docs/latest/workflow/published-analyses/) for the
article and evidence format. Point a studio at the repository and it works.

No other directory structure is required for report discovery. Project files
such as `data-sources/config.yaml`, `metrics.yaml`, and `config.yaml` are read
when reports use the corresponding data, metric, or extension features; the
context and assistant files below remain your own conventions.

## Everything else is yours

The rest of the repository does not need a prescribed layout. You do not need
a new repository, and adding a `reports/` directory to one you already have is
a perfectly normal way to start.

## What we recommend alongside it

```
your-analytics/
├── AGENTS.md            # how your team works
├── .mcp.json            # optional: the portal as an MCP server
├── context/
│   ├── warehouse.md     # the tables that matter
│   └── metrics.md       # how each KPI is counted
├── data-sources/
│   └── config.yaml      # which sources exist; never their secrets
├── analysis/            # ad-hoc scripts, kept or not
└── reports/
    └── weekly-revenue/
        ├── report.yaml
        ├── generator.py
        └── queries.py
```

### `AGENTS.md`

The house rules an assistant reads first: which warehouse is authoritative,
naming conventions, which tables are deprecated, that SQL belongs in
`queries.py`, and that nothing merges without review.

### `context/metrics.md`

The canonical definition of every number people argue about — what it counts,
what it excludes, which table it comes from, and who decided. This is usually
the highest-value file in the repository, and the one that pays for itself
fastest.

### `context/warehouse.md`

The grain of the important tables, which joins are safe, and the traps. What you
would tell a new analyst in their first week, written down once instead of
repeated ten times.

### `data-sources/config.yaml` — the sources, never the secrets

Where connections are declared. The same file serves both places a report
runs, which is worth being precise about.

**The file never holds credentials.** It describes a source and names an
environment prefix to fetch the secrets from:

```yaml
sources:
  warehouse:
    type: postgres
    host: warehouse.internal
    port: 5432
    database: analytics
    credentials:
      local: WAREHOUSE          # read WAREHOUSE_USER, WAREHOUSE_PASS from .env
```

**On your own machine**, you set those variables — a `.env` file beside your
project is loaded automatically — and the framework connects. The same file
serves both workflows: a report and a throwaway script reach the source
identically. See [Ad-hoc analysis](/docs/latest/workflow/ad-hoc-analysis/).

**On the portal, commit the file.** Every publish reads it and mirrors the
declarations into the studio: each source appears on the studio's Data sources
page with its type and connection details already filled in, read-only, and
asks only for the credentials. Those are stored encrypted and injected,
decrypted, into that studio's own builds — the `credentials:` block above is
for your machine and is ignored there. Before every build the worker writes the
`data-sources/config.yaml` the build reads from the portal's own settings, so
your committed copy is never the file the report opens. See
[Connecting your data sources](/docs/latest/portal/connecting-your-data-sources/).

!!! note
    Because the file holds references rather than secrets, committing it is
    safe and useful — on the portal it is how a source comes into existence at
    all, and a colleague cloning the repository sees which sources exist and
    which variables to set. What must never be committed is the `.env` holding
    the values. That indirection is the whole reason connection *secrets* are
    not part of the repository contract: **a warehouse password would otherwise
    live in every clone and every fork of your history.**

### `.mcp.json` — optional

The portal is an MCP server. With this file and a personal API key, the
assistant working in the repository can read what the portal has built —
build status, validation, published numbers — and, on a `write` key, build a
report or publish. Setup and the tool list are in
[Working with AI agents](/docs/latest/workflow/working-with-ai-agents/#connect-it-to-the-portal).
Useful, never required: an assistant reaches your data perfectly well through
the configured sources above.

### `analysis/` — optional

Somewhere for ad-hoc scripts to land. Some teams keep them as a record of what
has been asked; others delete them freely. These scripts are not discovered or
scheduled. Published analysis articles go under `reports/<slug>/` with
`kind: analysis`, so the portal can discover and publish them.

## Start with definitions, not reports

Before the first report, write `context/metrics.md`. One entry per number the
business argues about. This single file removes most of the disagreement that
otherwise surfaces *after* a dashboard ships, when it is expensive.

## Next

- [Ad-hoc analysis](/docs/latest/workflow/ad-hoc-analysis/)
- [Working with AI agents](/docs/latest/workflow/working-with-ai-agents/)
