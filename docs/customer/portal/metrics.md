# Metrics catalog

The **Metrics** page in a studio collects the business metrics declared in its
repository's `metrics.yaml`. It shows each metric's label, identifier,
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

The catalog can show a sparkline and open the generated metrics report's chart
for a metric when that report has been built and the metric has a supported
aggregation. Trellum generates this report from `metrics.yaml`; it is omitted
from the regular report-card list. The catalog explains when a metric is
descriptive only, is not bound to a dataset, or needs its generated report
built.

Studio members with viewer access can read the catalog. It is available to
members who can see the full studio; it does not reveal definitions or report
claims from another studio. The report page also has a **Metrics in this
report** panel for viewers. It lists the metrics claimed by that build beside
their current definitions and marks whether the build matches each one.
