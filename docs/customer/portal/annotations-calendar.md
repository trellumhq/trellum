# Annotations calendar

Every studio that annotates its charts already has an `events.yaml` — the
file the framework reads to draw campaign, release and incident markers on
top of report data. The annotations calendar is the studio-wide reading of
that same file: one page, per studio, showing everything that happened, on
the dates it happened, instead of chart by chart.

Nothing about chart annotations changes. `events.yaml` keeps annotating
individual reports in every deployment; this page adds a second, complementary view
over the same data.

## `events.yaml`

The file lives at the root of your analytics repository, alongside
`reports/`. Its format — the `events:` list, per-event `date`, `end_date`,
`label`, `type`, `studio`, `default_visible`, and the top-level
`type_defaults` — is defined once, by the framework that reads it. The
**[framework repository]({{FRAMEWORK_REPO}})** README is the source of truth
for the exact schema; this page does not keep a second copy of it.

A minimal example:

```yaml
type_defaults:
  campaign: false

events:
  - date: 2026-08-06
    end_date: 2026-08-10
    label: Summer sale — wave 1
    type: campaign
  - date: 2026-08-11
    label: v2.15 rollout
    type: release
```

## Per-report events

A report can also declare its own events, under `annotations.events` in that
report's `report.yaml`. These are report-scoped — useful for an event that
matters to one experiment or dashboard and nowhere else — and the calendar
includes them alongside `events.yaml`, tagged with the report they came from.
Every row on the page says plainly which of the two it is: `events.yaml`, or
a link to the report.

## Scope

Inside `events.yaml`, each event may carry a `studio:` field. On this page —
and everywhere else in the portal — that field is called **scope**, never
"studio": it means *which reports' charts show this event* (`shared` reaches
every report; anything else reaches only reports in that scope). "Studio" is
reserved for the portal studio the calendar itself belongs to, and the two
are unrelated. The scope filter narrows the calendar by it; scope is never a
reason an event is hidden from its own studio's calendar — every event this
studio's `events.yaml` and reports declare appears here, badged with its
scope.

## Read-only, and how it stays current

This page renders `events.yaml` and your reports' `report.yaml` — it does not
write to either. Edit events the same way you edit reports: in the
repository. A push that changes `events.yaml` reaches the calendar on the
next sync, the same delivery path reports already use — see
[Connecting a repository](/docs/latest/portal/connecting-a-repository/).

## Full history

Chart markers only need to be recent, so the framework limits them to the
last 90 days. The calendar has no such limit: it is a record, not a live
annotation layer, so a three-year-old event is exactly as visible as
yesterday's.

## Reaching the page

Every studio member can open it at `/s/<org>/<studio>/annotations` — from the
studio dashboard's folder bar, or from the "This studio" menu next to the
studio name, beside [Experiment overview](/docs/latest/portal/experiments/).
