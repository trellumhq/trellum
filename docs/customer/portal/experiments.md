# Experiment overview

A per-studio page rolling up every A/B test report in the studio into one
place — running tests, concluded ones, and how they're doing — so you can
read the whole portfolio without opening each report individually.
Individual A/B test reports keep working in every deployment; this page is the
cross-report view across them.

Reach it from the studio's folder bar on the dashboard, or from the "This
studio" menu on the crumb — both list it alongside the studio's other
feature surfaces, separate from the report folders themselves.

## How a report joins the page

Nothing new to build. A report joins the overview the moment its
`report.yaml` declares an `ab_test:` block — the same block the report's own
A/B comparison component already reads for its header (test name,
description, split, start date, control/test labels). A report with no
`ab_test:` block simply never appears here.

A report that carries **several experiments on one page** — a programme
review, a portfolio page — declares a *list* of such blocks instead. Each
entry becomes its own row and its own timeline lane here, matched to its own
A/B component in the built output by `test_name` (falling back to declared
order against build order when no names are given):

```yaml
ab_test:
  - test_name: checkout_v2
    start_date: "2026-07-14"
  - test_name: starter_pack_price
    start_date: "2026-07-09"
    end_date: "2026-08-06"
```

Four additional keys are recognised by this page only — the report's own
build ignores them entirely:

- `primary_metric` — which KPI row the overview should lead with, matched
  against the row's key or its metric label. Without it, the first KPI row
  in the report's own payload order is used.
- `end_date` — when the test is (or was) meant to stop.
- `planned_end` — an alias for `end_date`, for authors who'd rather name it
  as a plan than a commitment. Checked only when `end_date` is absent.
- `closed` — the decision was made and the chapter is closed: the entry
  reads as concluded even while the report keeps rebuilding on a schedule.
  This is the honest state for a finished test sharing a page (and
  therefore a schedule) with one still running.

## Status rules

Each experiment's status is derived from `start_date` and `end_date` /
`planned_end`:

- **scheduled** — `start_date` is in the future.
- **running** — started, and no end date has passed (or none was declared
  at all — an experiment with no end runs open-ended until its report author
  adds one).
- **concluded** — an end date exists and has passed.
- **past end** — an end date exists and has passed, but the report is
  still on a live build schedule. That combination means the test outran its
  own declared horizon and nobody told the pipeline to stop rebuilding it —
  shown as a nudge, not an error. A report that's disabled, or has no
  schedule, past its end date is simply concluded — as is any entry that
  declares `closed: true`.
- **unknown** — no parseable `start_date`; nothing to place on the timeline.

Dates are read as plain calendar days in UTC, regardless of where the studio's
members sit.

## Where the numbers come from

This page computes no statistics of its own. Every number — the delta, its
confidence interval, which variance-reduction mode is showing — is read
straight out of the report's own latest built output, at that report's own
declared default mode (`default_mode` in the `ab_test` component). If a
report declares a raw view and a CUPED-adjusted view and opens on the
CUPED-adjusted one by default, that's the number shown here too: this page
always mirrors the same view you'd land on by opening the report directly.

A lift renders green or red only when its 95% confidence interval excludes
zero — and its direction is judged against the metric's own declared
"higher is better" — otherwise it renders as not-yet-significant rather than
implying a winner the data doesn't support. A "win" counts only a concluded
experiment with a significant, favourable result.

A report that has never built shows "Not built yet"; a report that built but
whose output has no A/B payload shows as much, rather than disappearing from
the list or breaking the page.
