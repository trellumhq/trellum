# Ad-hoc analysis

Not every question deserves a report. Once a data source is configured, you can
query it directly — from a script, a notebook, or an assistant writing one for
you — with no report, no build, and no portal involved.

## Query a configured source

```bash
trellum query --source warehouse "
    select channel, sum(revenue) as revenue
    from orders
    where week = date_trunc('week', current_date - interval '7 days')
    group by 1
    order by revenue desc
"
```

`warehouse` is a source name from `data-sources/config.yaml` — omit
`--source` when only one source is configured. Results print as an aligned
table; `--csv` emits CSV instead, for piping into a notebook or a
spreadsheet.

!!! note
    This uses the same connection a built report uses. That is the point: an
    answer produced on Tuesday and a report shipped on Friday cannot disagree
    about where the data came from or how the source is configured.

## From Python

The same sources, by the same names, from a script or a notebook:

```python
from trellum import query, sources

sources()                                  # what data-sources/config.yaml configures
df = query("warehouse", "select channel, sum(revenue) as revenue from orders group by 1")
```

You get a pandas `DataFrame` back. It works from any subdirectory of the
project — the project root is found by walking up to
`data-sources/config.yaml` — and local file databases are opened read-only.
None of it requires the report machinery.

!!! note
    The full data API — listing configured sources, getting a raw connection
    for `pandas.read_sql`, file and API sources — is documented in the
    [framework repository]({{FRAMEWORK_REPO}}), alongside the code. This page
    only covers *when* to reach for it.

## When to promote it to a report

Move an ad-hoc script into `reports/` when any of these is true:

- Someone wants it on a schedule
- More than one person needs to read the result
- The number is going somewhere consequential and should be reviewed
- You have written it more than twice

Until then, a script is the cheaper answer. See
[Two workflows](/docs/latest/workflow/two-workflows/) for the promotion path.

## What this is not

Ad-hoc querying is for the people writing the analysis, not for people reading
a dashboard. Built reports compile their data at build time by default, so
viewers do not need a warehouse connection. A report can opt into on-demand
lookups with the framework's `LiveDataSource`; those queries run only when the
report is served by a host that supports live queries (the portal or the local
`trellum serve` server). Standalone files, share links, and email snapshots
show the build-time snapshot and do not run live queries.
