# Business report examples

The bundled reports combine native components around business questions. They
read the offline demo warehouse; they do not contact paid providers.

## Player Overview

The existing eight headline cards keep their calculations and layout. Six
compact daily charts follow: active users, new users, sessions, transactions,
IAP revenue and ad revenue. Each has its own colour and a **Daily avg** summary
calculated from the plotted daily totals after filters. Active-user totals are
not unique players over the period. Engagement and revenue detail remain
collapsible sections.

## Monetization + UA

Revenue and paid acquisition retain their independent daily sources. The
**Acquisition Quality by Cohort** section adds a separate monthly retention
snapshot from `fact_retention`; this source has no campaign attribution and
cannot establish paid ROAS or cohort revenue recovery.

The title and platform controls select whole monthly cohort identities. The
initial selection shows one title and platform across six calendar months.
The observation table uses native `CohortTable` cells at D1, D3, D7, D14 and
D30. A horizon becomes observed only when the last cohort date in that month
has reached it and every daily record for that horizon is available. Recent
months stay explicitly **Not mature**; missing daily inputs or an absent
denominator produce **Unavailable**. No forecast is generated or supplied.

The **Retention heat** tab uses native `HeatmapChart` with one rate per unique
cohort and horizon coordinate and a shared absolute colour scale. Its source
contains observed cells only. Missing or immature cells cannot turn into a
zero-valued observation, and selecting multiple titles/platforms cannot sum
their rates into one coordinate.

The native comparison table includes complete D30-mature cohorts only. D1,
D7 and D30 footer totals each divide retained users by eligible users at that
same horizon. They never sum or average displayed percentages. The table
declares illustrative D7 15% and D30 7% reference targets for cell shading;
these are demo targets rather than measured business requirements. Search
narrows this table and its weighted totals. Its pinned header/footer and row
selector remain local to the table.

## Conversion

The title scope switcher retains the existing funnel and device drill-down.
**Where They Go** uses native `SankeyChart` in adjacent-edge mode, sharing
the funnel's date and device filters. Its **Stage-count reconciliation**
contains illustrative links derived from aggregate stage counts, rather
than observed transitions. Links reconcile net differences separately for each
date/title/device observation. If
independently supplied stage counts increase, an explicit **Additional
arrivals** node explains the difference.

These are stage observations. The diagram does not reconstruct individual
complete journeys, sum boundary counts into unique users, or infer an absent
stage as zero. Native CSV, accessibility, theme and capture behaviour apply;
the old custom JavaScript diagram and its per-report CDN declaration are
removed.

## Verification

`trellum/testing/test_curated_business_demos.py` checks independent weighted
retention arithmetic, maturity and missingness, adjacent edge reconciliation,
and generated desktop Chromium reports with native filter propagation.
`test_player_overview_compact.py` retains its existing source, query, KPI,
chunking and compact-layout comparisons.
