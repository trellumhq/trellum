# Metrics workspace

The **Metrics** page in a studio shows the generated metrics report inline:
one chart after another, sorted by first tag (category) and then label. Each
chart keeps its own split controls, and the date range applies to all time
datasets. Use **Show / hide metrics** to focus on a few charts; this browser
remembers your choices for this report. **Show all** restores every chart.
Hover over a time observation to draw a guide at that same time on other
visible charts that contain it. A missing time observation has no guide, and
charts without a time axis do not participate.

Expand **Metric definitions and report usage** to read each metric's identifier,
description, aggregation or ratio specification, and the reports that claim it.
The same file is used by the standalone framework; see the
[framework metric guide](https://github.com/trellumhq/trellum/tree/main/trellum#data-layer)
and `python -m trellum guide metrics` for definition and CLI details.

When the portal syncs the connected repository, it refreshes the catalog from
the current `metrics.yaml`. For each report that claims a metric, Trellum
compares the definition hash in the report's latest build metadata with the
current definition. The catalog marks matching builds **current** and older
or unverified definitions **stale**. A changed metric definition does not
rewrite a report's existing output; rebuild the report to bring its claim up
to date.

The workspace uses one generated report, so charts share dates without loading
a separate report for each metric. Trellum generates it from `metrics.yaml`;
it is omitted from the regular report-card list. **Open metric page** retains
the current date and split URL state for a focused view. Definitions explain when a metric is
descriptive only, is not bound to a dataset, or needs its generated report
built. When the generated report is not yet built, definitions stay open.
For metrics with `time_agg: last`, the workspace shows the trend and explains
that the latest KPI total is a build-time snapshot. That static total is
omitted from the workspace because it does not follow selected dates; the
focused metric page still shows it.

Studio members with viewer access can read the catalog. It is available to
members who can see the full studio; it does not reveal definitions or report
claims from another studio. The report page also has a **Metrics in this
report** panel for viewers. It lists the metrics claimed by that build beside
their current definitions and marks whether the build matches each one.

Select **Compare** on two catalog cards, then **Compare selected metrics** to
open their full-size charts together. Uncheck a selected card to replace it,
or clear the selection. On the comparison page, either metric selector can
replace or remove a metric. Both choices belong to the current studio.

Apply an explicit shared start and end date to both time charts. Each chart's
split control affects only that card; dates and splits are retained in the URL
for reload, sharing, and Back/Forward. Smaller screens stack the same charts.
Each card shows its units, aggregation, current definition grain and build
freshness, and reports when its data does not cover the chosen dates. A chart
with no time axis is a snapshot and is unaffected by the shared date range.

Comparison charts retain separate axes and scales. This view shows trends and
omits KPI totals: a metric declared with `time_agg: last` plots observations
at each date, while its generated KPI remains a build-time snapshot. Rebuild
stale definitions before relying on a comparison. Missing builds and unavailable
splits are reported beside the affected card.
