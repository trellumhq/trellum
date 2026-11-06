# ComboChart axes and legends

`ComboChart(df, x, bar_cols, line_cols, title="", ...)` plots bars against the
left `y` axis and lines against the right `y1` axis. Multiple series are supported
on each side. The framework keeps the existing plot height (320px at ordinary
desktop width, 180px at 390px phone width), responsive height rules, and explicit
numeric `height`. Wrapping context outside the canvas can increase panel height.

```python
ComboChart(
    df, "day", ["spend", "fees"], ["orders"], "Daily activity",
    dataset_id="daily_activity", bar_labels=["Spend", "Fees"],
    line_labels=["Orders"], bar_format="currency", line_format="number",
    left_axis_label="Spend and fees · USD", right_axis_label="Orders · count",
)
```

The new `left_axis_label` and `right_axis_label` arguments are keyword-only.
Each accepts `None` (default) or a nonempty string. Invalid values raise
`ValueError` at construction and rendering, including after mutation. Existing
positional arguments and dataclass subclass arguments keep their positions.
An explicit `dataset_id` remains binding even when `static=True` is also set.
For an independent fixed chart, omit `dataset_id` and set `static=True`.

Each populated side has a heading identifying Left/bars or Right/lines, followed
by its description and legend controls. By default the description lists actual
series captions; no unit or formatter is inferred. Explicit descriptions let
authors declare metric units. A side without configured series has no legend
group; an empty result with configured series keeps their context. The renderer's
existing scales, including the base scale of a line-only chart, are preserved.

Legend buttons use rectangular bar or line-with-point symbols and actual current
series colors. Click, Enter or Space toggles a caption on its axis. Shift-click,
Meta-click or double-click isolates that axis/caption pair; repeating isolation
restores all series. Matching captions on opposite axes have separate buttons.
Matching captions on the same axis remain intentionally paired; their source
datasets and values remain separate. Keyboard focus survives ordinary chart
updates and matching-control redraws. Labels are inserted as text.

Tooltips retain nearest-series inspection and current callbacks. There is no
shared-date inspection or ratio API. CSV retains every current-result series,
including legend-hidden series, with raw values and display captions as headers;
duplicate captions therefore remain duplicate CSV headers.

## Current live and static behavior

| Option/data | Bound (`dataset_id`) | Independent static |
|---|---|---|
| Rows | Sums bar and line columns per x over filtered rows | Supplied row order and values |
| Tick formats | `bar_format` / `line_format` | Shared compact-number left / currency right defaults |
| `y_max` / `y1_max` | Applied | Not forwarded |
| `show_labels` | Applied to bars and lines | Not forwarded to the chart factory |
| `height`, `line_colors` | Applied | Applied |
| Missing values | Existing aggregation converts to zero | Nulls remain gaps |

These pre-existing differences are unchanged. Explicit unit descriptions do not
activate formats, bounds or labels. Avoid describing a requested static percent
format as active. Live sums are appropriate only for additive quantities at the
supplied grain; precomputed fractions are summed, not recomputed. Supplying the
same source column on both sides still has the existing live repeated-column
aggregation behavior. Use distinct columns and meaningful source grain.

## Runtime compatibility

Rebuild reports to include `combo_axes` metadata and the matching runtime/CSS.
Both bound and static payloads carry the dedicated metadata, forwarded to the
chart factory as `comboAxes`. Untagged legacy Combo payloads retain their legacy
legend, as do other families using `y1`. Older runtimes safely ignore the added
metadata and retain legacy presentation. Ordinary LineChart legend chips,
adaptive marker styles and tooltips are unchanged. No host or artifact schema
migration is needed.
