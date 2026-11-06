# Traffic composition with native StackedBar

`StackedBar(..., *, show_controls=True, height=None)` preserves the existing
constructor positional arguments, markup, chart libraries, CSV units, plot
breakpoints and selected filters. Both additions are keyword-only. `show_controls`
is strictly boolean. `height` is `None` or a Python integer from 128 through 1200;
booleans, floats and strings are rejected, including after mutation. `None` keeps
responsive heights (320px at 1280px, 180px at 390px). `height=220` sets the canvas
plot box to 220px at both widths; title, context, controls and legend retain their
normal layout outside that box. It can make a phone chart taller.

For one shared breakdown and normalization group above a native two-column Grid:

```python
choices = {"Channel": "channel", "New/Returning": "user_type", "Inactivity": "inactivity"}
components = [
    Grid(children=[
        Toggle("traffic_breakdown", list(choices)),
        Toggle("traffic_mode", ["Absolute", "Percentage"]),
    ]),
    Grid(children=[
        StackedBar(frame, "day", [metric], title, dataset_id="traffic",
                   stack_by="channel", stack_by_options=choices,
                   normalize_toggle=True, show_controls=False,
                   stack_toggle_id="traffic_breakdown", normalize_toggle_id="traffic_mode",
                   height=220, value_format=units)
        for metric, title, units in [("events", "Events", "number"),
                                      ("d7_revenue", "D7 revenue", "currency")]
    ]),
]
```

Place the dataset and FilterBar before these components. The first native Grid
keeps shared controls together and gives the normal PNG/PDF capture path a
block layout for their buttons. Each requested active
control needs its explicit external ID when `show_controls=False`. The owning
native Toggle must exist exactly once, with the exact ordered breakdown labels
or `Absolute`, `Percentage` options and a valid default. No local controls are
emitted in this mode. Defaults remain Absolute unless the owning Toggle selects
Percentage; current selected values also apply on restoration. Static wide
StackedBar still emits no dynamic controls, regardless of `show_controls`.

The visibility validator currently flattens report scopes. This opt-in shared
composition therefore requires IDs unique across the whole report, including
inactive scopes. Use distinct native owner IDs for independent scopes/pairs.
This explicit restriction does not add duplicate-owner failures for legacy
reports. Valid shared bindings count as Toggle usage. Missing/duplicate owners,
chart-local copies, mismatched/empty options and invalid selected defaults fail
validation. `stack_option_problems(chart)` supplies post-mutation option diagnostics;
shared ownership checks live in the existing visibility check.

Only the new shared composition opts into scoped subscription ownership. Repeated
render, config replacement, canvas/control replacement and scope exit retire its
filter, theme and control listeners. Workspace and older untagged payloads retain
their existing lifecycle. Normalization, `_absData`, Top-N/Other, independent
legends, axes, tooltips and CSV calculations use the current native paths. CSV
contains plotted percentages in Percentage mode and absolute metric values in
Absolute mode. Legend hiding does not change a metric/date denominator.

The deterministic `examples_traffic.add_sections(ctx)` module supplies three
independently filtered teaching sections: local/responsive, shared/responsive,
and shared/220px. All use current native KPI rows, doughnuts, Grid, Toggle and
StackedBar components. It contains 48 complete date × platform × channel × persona
facts for August 1–2, 2026: 408 disjoint synthetic events / $480 event-linked D7
revenue, mature as of November 4. Android has twice iOS volume. A single iOS day
contains 68 events / $80. This is synthetic event attribution, not distinct people.
Channel counts are 102/90/216 (UA/RT/Organic), revenue is $102/$270/$108. Event
shares (25/22.0588235/52.9411765%) differ from revenue shares (21.25/56.25/22.5%).
Each date and metric divides by its own matching population.

Paid (UA+RT) and Organic are an explicit disjoint exhaustive source partition:
192 events/$372 and 216/$108. Collapsible details show source facts with those
predicates and the parent date/platform filters. Collapse controls visibility;
it does not select a partition. Aggregated reactive group details require a
separate shared metric-view binding; this example adds no local aggregator.
Missing, zero, signed and incomplete normalization populations keep their existing
native policy. This complete positive fixture establishes no new policy for
them and makes no performance or production-parity claim.

Section PNG capture uses the normal native readiness guard. Because source-detail
tables initialize lazily, reveal/scroll each detail before capturing its containing
section, then collapse it again if that is the intended captured state. This
example retains that existing capture limitation. Native report PNG/PDF and CSV
actions use their normal paths; no alternate renderer or export units are added.

The chart-options dispatcher can consume the sizing diagnostic without repeating
the visibility check's control errors:

```python
from trellum.components.charts.bar import stack_option_problems

# Inside the existing component/section loop in checks/chart_options.py:
if _comp_name(comp) == "StackedBar":
    for check_id, message in stack_option_problems(comp):
        if check_id == "stacked-bar-height-invalid":
            result.fail(check_id, message, component="StackedBar", section=sec)
```
