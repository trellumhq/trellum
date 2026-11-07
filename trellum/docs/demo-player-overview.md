# Player Overview: compact daily trends

The business demo at `demo/reports/player-overview/generator.py` adds a
**Daily Trends** section directly after its eight headline Overview cards.
It uses the existing `main` DataSource for six native `LineChart` components
in a `Grid(columns=3, min_width=280, color_cycle="theme")`: Daily Active Users, New Users,
Sessions, Transactions, IAP Revenue and Ad Revenue. The grid fits fewer
columns in narrower containers and stacks on a phone. Native chart titles,
legends and CSV controls remain available.

The optional Grid setting gives each metric a stable active-theme palette slot.
Filtering does not shift colors; changing theme repaints them. This replaces the
demo's fixed six-color list. Existing reports keep their Grid colors unless they
opt in; see [Grid theme colors](report-grid-colors.md).

Each trend opts into `height="compact"` (a 160px plot) and
`header_summary={"stat": "mean", "series": 0, "label": "Daily avg", ...}`.
Number formats apply to activity counts; currency formats apply to revenue.
These options are local to the six new charts. Existing detailed chart
heights, libraries, data, breakdowns and normalization options are preserved.

For a metric `m`, the plotted daily value is
`sum(m for selected fact_daily rows on that event_date)`. Its summary is
`sum(displayed daily values) / count(displayed days)`. It averages the final
plotted dates, rather than raw warehouse rows. No new missing-day filling,
calendar denominator or lookback query is introduced. The active-user series
uses the existing additive daily fact grain, like the detailed platform
chart. A sum across dates is not a count of unique players over the period.

The existing **Engagement** and **Revenue** sections use
`ctx.add_section(..., collapsible=True, default_collapsed=True)`. Readers
expand their headings to reach the original charts and controls. Efficiency,
geography, behaviour overlap, cohort retention and Yesterday remain visible
in their original relative order. The eight headline cards keep their
existing sizes, row breakpoints, metric claims and defaults.

## Filter and calculation scope

The six trends and headline cards use the existing daily DataSource and
its date, region, country, title, platform and promo controls. Their filters
do not change the source query or join the other two grains into daily rows.
Membership-grain `segments` and cohort-grain `cohorts` retain their independent
section filters. Retention keeps its monthly chunks and three-chunk inline
default, loading older chunks as the selected cohort range requires.

Some existing values deliberately retain their authored scope:

- **Efficiency** cards are computed once over the entire fetched daily
  window, before browser filters. ARPDAU is
  `sum(iap_revenue) / sum(dau)`; Avg Transaction Value is
  `sum(iap_revenue) / sum(transactions)`. Their existing zero-denominator
  fallback is zero. The current currency-ratio runtime could support live
  cards, but these descriptive metric claims remain static.
- **Day-1 Retention** and **Day-7 Retention** are computed once over all
  fetched cohort rows at the matching day offset:
  `100 * sum(retained_users) / sum(cohort_size)`, with zero for an empty
  denominator. They remain static under cohort filters. The neighbouring
  Cohort Size and Retention Rate cards retain their existing live behaviour,
  including their aggregation across selected day-offset rows.
- The custom revenue map retains its full-window snapshot; the neighbouring
  country bar and doughnut retain their live daily filters. Behaviour overlap
  retains its native membership selection. Yesterday remains the fixed-day
  snapshot stated in its title.

No query, metric registry, population, default filter, static calculation or
framework default changes with this composition. This demo uses synthetic
business data and does not claim parity with another report's source data.

## Verification

`testing/test_player_overview_compact.py` imports the actual baseline
generator from Git commit `99e5c9420c796843ff477566710156ae11049f5e`
into a temporary module. Both actual generators receive identical pinned
synthetic query results, build through `ReportContext`, run `compute_details`
and `validate_report`, and render with vendored assets. Git objects for that
baseline must be present to run this comparison.

`testing/test_player_overview_compact_browser.py` checks native platform and
date controls, narrower dates, reset, daily arrays and summaries, KPI geometry
and values, detail disclosure, independent retention/membership filters,
themes, CSV, native section PNG capture and embed PNG/PDF downloads. The phone and export cases are
marked `heavy`. Set `TRELLUM_PLAYER_OVERVIEW_ARTIFACTS` to an isolated output
directory to retain before/after builds, desktop/phone screenshots, comparison
JSON and downloads. The checks never start a persistent server or call a
warehouse or external provider.

From the repository root, using a Python environment with the test dependencies:

```sh
PYTHONPATH="$PWD" FW_PROJECT_ROOT="$PWD/trellum/demo" \
  python scripts/test.py local trellum/testing/test_player_overview_compact.py \
  trellum/testing/test_player_overview_compact_browser.py --workers 1
PYTHONPATH="$PWD" FW_PROJECT_ROOT="$PWD/trellum/demo" \
  python scripts/test.py local trellum/testing/test_player_overview_compact_browser.py \
  --heavy --workers 1
```

The native `fw.captureElementSnapshotForAnalysis(section)` capture retains the
Daily Trends section's titles, summaries and three-column layout. The existing
`?only=sec-daily-trends` embed is a different presentation: its shared embed
styles hide chart headings/summaries and restack chart grids. Its PNG/PDF
downloads work, but do not preserve this multi-chart report composition.
There is no dedicated selected-section PDF API. A faithful section PDF would
need a separate framework export change; this demo adds no export override.

These are native generated demo checks with deterministic fixtures. They do
not verify production warehouse values, unique-player deduplication across
fact dimensions or dates, or every possible container width and data state.
