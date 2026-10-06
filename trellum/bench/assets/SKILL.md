---
name: trellum
description: Build, fix and validate reports in a repository that uses the Trellum reporting framework, and add or troubleshoot the data sources they read from. Use whenever a task involves a report, a chart, a dashboard, a metric, `report.yaml`, a generator, `data-sources/config.yaml`, or connecting a database for reporting.
---

# Trellum reports

The framework installed in this repository describes itself. Ask it rather
than reading its source or guessing at its API — the answers below are
generated from the installed version, so they cannot drift the way a copied
example does.

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

`trellum` alone is not on PATH — name the interpreter every time.

## Where to start

Before writing a generator, ask for the authoring contract. These are not
guessable from the API, and getting them wrong builds a clean report that
shows nothing useful:

```
{py} -m trellum guide queries format
```

`{py} -m trellum` lists every available guide topic. Ask for the ones your
task touches in a single command — each invocation re-reads your whole
context, so four separate calls cost four times the context, not four times
the content.

## Data sources

To connect a database, hand the details to the framework rather than writing
`data-sources/config.yaml` and `.env` yourself. It puts each part where it
belongs and connects to check, so a wrong password surfaces immediately:

```
{py} -m trellum datasource add <name> --type postgres --host <h> --port <p> --database <db> --user <u> --password <pw>
```

Column names come from the warehouse, never from an example. Check them
before writing SQL rather than after the build fails:

```
{py} -m trellum data
```

When a connection fails, read the error rather than working around it. A
build that falls back to mock data is worse than a build that fails: it
produces a report full of numbers that look real and are not. Report the
failure and its cause.

## Building

```
{py} -m trellum.new <slug>                      scaffold a report
{py} -m trellum.run reports/<slug> --no-serve   build it
```

`--no-serve` exits when the build is done; without it the command starts a
preview server and never returns.

A report is not finished until the validator reports no FAILs.
