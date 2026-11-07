# Synthetic acquisition Overview

`demo/reports/component-showcase/examples_overview.py` exports
`add_sections(ctx)`. Call it with an ordinary `ReportContext` from a report
generator to append the complete native composition. It uses no database,
external service, custom renderer, CSS, or JavaScript. The module lives beside
the showcase generator and can use its normal relative import:

```python
from . import examples_overview

# Inside generate(self, ctx):
examples_overview.add_sections(ctx)
```

The named section is **Overview — synthetic acquisition cohorts**, with the
generated anchor `#sec-overview-—-synthetic-acquisition-cohorts`. Its independent
dataset is `showcase_overview_daily`. Its section FilterBar provides cohort date,
platform and acquisition-type selection. It neither creates a global FilterBar
nor propagates state to other examples. Call it once per report; dataset and
detail anchors must be unique. The nested detail anchor is
`#overview-daily-detail`.

## Fixture and units

The 168 deterministic facts have grain **cohort date × platform × acquisition
type**: October 1–28, 2026 × iOS/Android × Organic/Paid search/Partner. Installs
are conversions. `cost` and `d7_revenue` are USD; `installs` and `d7_payers` are
counts. Every supplied D7 outcome is fully observed at the November 5, 2026 UTC
snapshot, including the October 28 cohort through November 4. There are no
forecasts, comparisons, monthly overlays or inferred maturity states. This is
synthetic sample data, with no claim of parity with another report.

`cohort_frame()` generates facts using integer cents before converting amounts
to USD. Let `d` be the zero-based day index, `p` be 0 for iOS and 1 for Android,
and `a` be 0, 1, 2 for Organic, Paid search, Partner respectively:

```text
installs      = 90 + 11d + 29p + 37a + 9(d mod 4)
cost cents    = installs × (38 + 21a + 13p) + 97(d mod 5)
D7 payers     = 4 + floor(d / 3) + 2p + 3a
revenue cents = D7 payers × (610 + 17d + 53a) + 31p
```

All counts and amounts are finite and nonnegative; every retained day's cost
and install denominators are positive. Every selected date within the fixture
has all six source combinations. Presentation never rewrites source facts.
An empty selection keeps the native runtime's existing empty behavior; this
example does not establish a new missing-day, zero-denominator or incomplete
cohort policy for real data.

## Cards and trends

The seven cards use an ordinary `KpiRow`, with its existing height, row
breakpoints, label ellipses and default `mobile_collapse=0`. No icons or
comparison deltas are added. For selected rows, let `C`, `I`, `R`, `P` be total
cost, installs, D7 revenue and D7 payers, and `N` the number of distinct retained
cohort dates:

| Card | Native aggregation | Raw value | Display |
|---|---|---|---|
| Daily avg cost | `avg_by_date`, `date_col="cohort_date"` | `C / N` | USD |
| Daily avg installs | `avg_by_date` | `I / N` | count |
| Daily avg D7 revenue | `avg_by_date` | `R / N` | USD |
| Period recovery D7 | `ratio` | `R / C` | percent |
| Cost / install | `ratio` | `C / I` | USD/install |
| Revenue / install | `ratio` | `R / I` | USD/install |
| D7 payer share | `ratio` | `P / I` | percent |

Use raw numerator/denominator columns for `ratio`, and set `format="currency"`
for per-install USD metrics. The current native KPI runtime multiplies a ratio
by 100 only for percent display. Currency ratios are never multiplied by 100.
Recovery means revenue divided by cost, without subtracting cost from revenue.

The six `LineChart` instances share `Grid(columns=3, min_width=280)` and opt into
`height="compact"` (160px) and
`header_summary={"stat": "mean", "label": "Daily avg"}`. The first three plot
daily sums of cost, installs and D7 revenue. The other three use native `ratios`
to plot daily recovery, cost/install and revenue/install. Every summary is the
arithmetic mean of its exact plotted daily values after selection.

For example, daily cost/install summary is `mean(C_day / I_day)`. The period
card is `sum(C_day) / sum(I_day)`, which weights days by installs. Daily recovery
summary weights days equally, while the period recovery card weights them by
cost. Different values are expected; do not substitute the card value for a
trend summary. The supplied varying denominators make these differences
testable. Native formatters may round some differences to the same text.

## Breakdown and detail

Three native `DoughnutChart` instances summarize selected cost, installs and
D7 revenue by the three supplied acquisition types. Native `Panel` headings
identify the metrics and units. `value_format` is currency for amounts and
number for installs. Center totals, segment labels and cross-filtering retain
their defaults (off). No categories are merged into Other.

A native `Section` starts collapsed and contains grouped daily cost/revenue
bars plus an acquisition-type installs `StackedBar` with
`normalize_toggle=True`. Its Percentage mode divides the selected daily
acquisition counts by the selected daily total, summing to 100% for this
complete positive fixture. Optional totals and percentage segment labels stay
off. Detailed charts retain ordinary responsive bar heights and native
controls, legends, tooltips and CSV. All six trend charts retain native
straight lines, adaptive dots, legends, nearest-point tooltips and CSV.

## Compatibility and verification

This is report authoring through existing components. It changes no framework
constructor, defaults, runtime, stylesheet, chart library or existing business
report. `add_sections(ctx)`, `cohort_frame()` and `kpi_specs()` are local example
helpers, not new top-level framework exports. The summary and compact options
apply only to the six authored trends. Build with the usual `compute_details`,
`validate_report`, `render_report` and vendored assets.

`testing/test_overview_composition.py` checks independent source/aggregate
formulas, grain, finite positive denominators, composition options and a full
native report with zero WARN/FAIL and no suppressions.
`testing/test_overview_composition_browser.py` exercises actual native date,
platform and acquisition filters, clear restoration, six plotted vectors and
their summaries, currency ratio cards, three doughnuts, detail disclosure,
percentage stacks, 160px canvases with wrapped headers, unchanged ordinary KPI
geometry, tooltip input, CSV downloads and native section/report captures.
Desktop is 1280px; the 390px phone case is marked `heavy`. Both use light and
dark themes. The same-content ordinary KPI row in those tests is explicitly a
**current-runtime reference**, not a historical screenshot.

Run from the repository root with the repository Python environment:

```sh
PYTHONPATH="$PWD" FW_PROJECT_ROOT="$PWD/trellum/demo" \
  python scripts/test.py local \
  trellum/testing/test_overview_composition.py \
  trellum/testing/test_overview_composition_browser.py --workers 1

PYTHONPATH="$PWD" FW_PROJECT_ROOT="$PWD/trellum/demo" \
  python scripts/test.py local \
  trellum/testing/test_overview_composition_browser.py --heavy -k 390 --workers 1
```

Browser reports build into isolated temporary directories and use a disposable
local HTTP fixture. `TRELLUM_OVERVIEW_EVIDENCE` selects a capture directory
(default `/tmp/trellum-overview-composition`); each viewport records its native
build, validation, filtered screenshots, actual CSV downloads and browser
diagnostics. Desktop additionally records the native section PNG and native
report PNG/PDF after filtering. These checks cover this complete observed
fixture and representative widths, not all embed sizes or real acquisition
data contracts.

Native image captures follow the existing export contract, which hides report
chrome, filter bars and interactive controls. The normalization control's empty
container may remain visible in a capture. Chromium can also emit canvas
`getImageData` performance advice during native capture; browser evidence records
those messages separately from errors and failed requests. This example makes
no changes to the shared capture renderer.
