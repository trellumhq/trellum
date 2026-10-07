# Framework capabilities

The standalone framework is documented in its
[canonical README](https://github.com/trellumhq/trellum/tree/main/trellum),
which ships with each release. This page is an index: it points to the relevant
sections and installed CLI guides instead of duplicating their API reference.

| If you want to… | Start here |
|---|---|
| Install the Python package, create a project, or build a report | [Install and Quick Start](https://github.com/trellumhq/trellum/tree/main/trellum#install) · `python -m trellum guide report` |
| Query a source, inspect tables, or reuse a defined metric and track its version | [Data Layer](https://github.com/trellumhq/trellum/tree/main/trellum#data-layer) · `python -m trellum guide answer queries metrics` |
| Choose charts, tables, KPI cards, controls, or layout components | [Components](https://github.com/trellumhq/trellum/tree/main/trellum#components) · `python -m trellum guide components` |
| Use cascading filters, cross-data-source propagation, section-local scopes, or chunked datasets | [DataSource and FilterBar](https://github.com/trellumhq/trellum/tree/main/trellum#datasource-and-filterbar) · [Chunked DataSource Loading](https://github.com/trellumhq/trellum/tree/main/trellum#chunked-datasource-loading) · `python -m trellum guide filters` |
| Run parameterized live queries or use long-format data, ratios, and stacked chart breakdowns | [Live Queries](https://github.com/trellumhq/trellum/tree/main/trellum#live-queries) · [DataSource and FilterBar](https://github.com/trellumhq/trellum/tree/main/trellum#datasource-and-filterbar) · `python -m trellum guide live-queries format` |
| Compare experiments or render confidence intervals and variance reduction | [A/B Testing](https://github.com/trellumhq/trellum/tree/main/trellum#ab-testing) · `python -m trellum guide components generator` |
| Add themes, events, annotations, or browser exports (PNG, PDF, CSV) | [Themes](https://github.com/trellumhq/trellum/tree/main/trellum#themes) · [Events and Annotations](https://github.com/trellumhq/trellum/tree/main/trellum#events-and-annotations) · [JavaScript Runtime API](https://github.com/trellumhq/trellum/tree/main/trellum#javascript-runtime-api) |
| Validate a report and understand its check results | [Validation](https://github.com/trellumhq/trellum/tree/main/trellum#validation) · `python -m trellum checks` · `python -m trellum guide validation` |
| Build a multi-scope report or custom dashboard | [Multi-Scope Reports](https://github.com/trellumhq/trellum/tree/main/trellum#multi-scope-reports) · [Custom Dashboards](https://github.com/trellumhq/trellum/tree/main/trellum#custom-dashboards) |
| Refine an existing report from its local browser preview | [Refine reports in your browser](/docs/latest/workflow/refine-reports/) · [CLI Reference: review](https://github.com/trellumhq/trellum/tree/main/trellum#cli-reference) · `python -m trellum guide review` |
| Add custom components, themes, data drivers, or output backends | [Extensibility](https://github.com/trellumhq/trellum/tree/main/trellum#extensibility) |

The installed CLI describes the version in your environment. Run
`python -m trellum` for its command list, or request several guides together,
for example `python -m trellum guide components filters validation`.

The [portal guide](/docs/latest/portal/connecting-a-repository/) covers team
workflows. Its pages document [Git publishing](/docs/latest/portal/publishing-from-git/),
[organizations and studios](/docs/latest/portal/organizations-and-studios/),
[access control](/docs/latest/portal/access-control/), [SSO](/docs/latest/portal/single-sign-on/),
[data-source connections](/docs/latest/portal/connecting-your-data-sources/),
[sharing](/docs/latest/portal/share-links/), [embedding](/docs/latest/portal/embedding/),
[email delivery](/docs/latest/portal/email-delivery/),
[alerts](/docs/latest/portal/alerts/), [live queries](/docs/latest/portal/live-queries/),
[experiments](/docs/latest/portal/experiments/), [annotations](/docs/latest/portal/annotations-calendar/),
and [the AI assistant](/docs/latest/portal/ai-assistant/). The
[metrics catalog](/docs/latest/portal/metrics/) explains shared metric
definitions and whether a report build uses the current definition;
[Report Analytics](/docs/latest/portal/report-analytics/) documents readership
counts and their limits. The
[framework overview](/docs/latest/framework/the-framework/) explains where
the standalone package ends and portal operations begin.
