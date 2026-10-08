# Live queries

A report built with the framework is, by default, a **compiled snapshot**.
Every chart, table and KPI in it was computed once, at build time, and baked
into the artifact. Opening it — from the portal, a share link, an emailed
copy, or straight off disk — shows exactly what the build computed, with
nothing running on the other end. That is the normal case, and it should
stay the normal case: a compiled report is fast, cacheable, safe to send
around, and keeps working with nothing behind it.

A **live query** is the deliberate exception to that default, reserved for
one specific shape of question a compiled snapshot answers badly: raw,
per-entity data at a cardinality too high to bake in full. "Show me this
one user's event history" does not compile — there could be a million
users, and baking every one of their raw event logs into the artifact to
answer a question almost nobody asks of almost any given user is how a
report ends up bloated for no reader's benefit. A live query lets the
report declare that lookup at build time and fetch it on demand instead of
carrying it.

Treat live queries as the exception, not a habit. If a FilterBar over a
compiled dataset already answers the question — an aggregate, a trend, a
breakdown by category — that is almost always the right tool, and it needs
nothing running to keep working. Reach for a live query only when the data
is genuinely per-entity and too large to compile in full.

## Two modes, one report

A report is not "a compiled report" or "a live report" — it is built from
components, and each component is one or the other. The framework's own
demo, **Live Ops Monitor**, puts both side by side: a filter bar drives a
KPI row, two charts and a raw-events table, all live against one declared
query — and, further down the same page, a plain compiled line chart
showing event volume across the *entire* history, computed once at build
time and never re-queried. The live components answer "show me this one
thing in detail, right now"; the compiled chart answers "how does this
look in aggregate, over everything" — a question a live per-entity lookup
is a poor way to answer, and a compiled aggregate answers for free.

Mixing the two in one report is normal, not a special case. Put a live
per-entity lookup next to the compiled aggregates that give it context, and
let each component be whichever mode actually suits the question it
answers.

## The snapshot is one slice, never the whole table

Declaring a live query still bakes something into the artifact: one
representative slice, computed at build time exactly like everything else
in the report. If a report is built against a users table with a million
rows, the build does not compile a million users' worth of raw data — it
runs the declared query for **one** example user (or whichever single
parameter set the report author chose) and stores that one result as the
snapshot. Every other user's data is fetched live, on demand, only when
someone actually asks for it.

This is what makes the standalone case honest rather than broken. A report
with a live query still opens correctly from disk, from a share link, or
from an emailed copy — it shows that one baked example, clearly, with the
live control rendered disabled and a label saying data is current as of
the last build. Nothing is fetched and nothing looks empty; it simply
cannot change what it's showing without something to ask.

## What has to be listening

Fetching a *different* slice than the one baked in requires a host on the
other end that knows how to answer the request — the report itself never
carries the ability to query a warehouse on its own. Two things currently
provide that:

- **The portal**, in production. Every report page it serves advertises
  its live-query endpoint, so a signed-in viewer with access to the report
  can change the filters and get fresh rows back.
- **`trellum serve`**, for local development. A report author testing a
  report before publishing it can now exercise the live path without
  standing up the portal at all — the framework's own dev server answers
  the same request shape locally, against the project's own data sources,
  so what worked at `trellum serve` time is a real preview of what the
  portal will do.

Anywhere else — a file opened directly, a share link, an emailed snapshot,
or a portal build old enough to predate live queries — there is nobody to
ask, so the control stays disabled and the baked snapshot stands. This is
not a degraded error state; it is the report behaving exactly as designed
in an environment with no host behind it.

## Why the query text never reaches your browser

A live query's SQL is written by whoever builds the report, and it never
ships to the page. The build writes it to a manifest file alongside the
report's other build output — visible to the host serving the report, never
to the browser. What the page itself carries is a query *id*, the schema of
which parameters it accepts (a type — integer, date, one of a fixed list of
values, and so on — plus whether each is required), and, for hosts like the
portal that already know who is signed in, a permission check before
anything runs.

When a viewer changes a filter, the page sends only the query id and the
parameter values the filter produced — never a column name, a table name,
or a fragment of SQL. The host looks up the id, substitutes the supplied
values into the query it already has on file (rejecting anything that
doesn't match the declared type), and runs it. A visitor with access to a
report's live filters can change *which* value the declared query runs
with; they can never see or influence the query itself. The same
enforcement — parameter validation, a read-only check on the query, and a
row limit — applies whether the request lands on the portal or on
`trellum serve`, so a report author testing locally sees the same guard
rails the production endpoint holds a viewer to.

Portal live queries against DuckDB use a read-only connection with external
access, extension installation and automatic loading disabled, and configuration
locked before the query runs. DuckDB retains its internal allowances for the
configured database, its WAL files and its own database-specific temporary
directory; it does not allow access to neighboring files or other host paths.
File-reader functions such as `read_csv` and `read_text`, external-file views,
and database attachments referring to other paths are unavailable. Import those
files into database tables during the report build instead. Trusted standalone
builds retain their configured DuckDB file access.

The portal and `trellum serve` reject browser requests for `_live_queries.json`
and its compressed sibling, including filename aliases. Remote builds publish
the host manifest outside the browser-granted report prefix. Operators upgrading
an existing deployment should follow the
[private-manifest storage upgrade notes](/docs/latest/install/storage/#private-live-query-manifests)
for gateway updates and legacy object/cache cleanup.
