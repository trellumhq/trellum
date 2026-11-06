# Report layouts

`Grid` and `SplitPane` use their available panel width to arrange children.
They work inside nested Panels and tabs, including when a hidden tab first
becomes visible. Neither layout changes chart plotting heights, data sources,
filters, or URL state.

## Grid

```python
from trellum.components import Grid, Panel

Grid(children=[revenue_chart, orders_chart, channel_chart],
     columns=3, min_width=280, card=True)
```

| Argument | Default | Behavior |
| --- | --- | --- |
| `children` | Required | Each child's entire rendered output occupies one cell. |
| `columns` | `2` | Maximum number of equal-width columns. |
| `card` | `False` | Add the existing card background, border and padding to each cell. |
| `min_width` | `0` | Preferred minimum cell width in CSS pixels; `0` selects 320px. Includes card framing. |
| `show_titles` | `True` | Keyword-only. Set `False` to omit chart titles in this Grid. |
| `show_controls` | `True` | Keyword-only. Set `False` to omit local chart breakdown and normalization controls in this Grid. |

The grid fits as many tracks as its panel allows, up to `columns`. Empty
tracks remain available when there are fewer children. Below the preferred
minimum it uses one track that fits the panel, without horizontal overflow.
Nested Grids use their own visibility options: an outer omission stops at
the nested Grid. Section/Panel headings, filters, legends and CSV download
buttons are unaffected by either omission option. Separately declared shared
Toggle components also retain their visibility and behavior.

```python
Panel(title="Daily revenue", children=[
    Grid([revenue_chart], columns=1, show_titles=False),
])
```

Existing positional `children`, `columns`, `card` and `min_width` arguments
remain accepted. Two intentional differences apply: `columns` is now a
maximum, including with `min_width`, and automatic tile grids keep titles
and controls visible by default. They no longer shorten other chart families
to a tile height. LineChart retains its 220/160/320px presets, and other chart
families retain their own sizes. KPI row rules and the metrics workspace's
intentional sizing/title treatment remain in place.

## SplitPane

```python
from trellum.components import SplitPane

SplitPane(revenue_chart, supporting_panel, "2:1",
          left_min_width=480, right_min_width=240)
```

`left`, `right` and `ratio` keep their positional arguments. `ratio` defaults
to `"1:1"` and accepts two finite positive numbers separated by a colon,
including decimal ratios. The keyword-only `left_min_width` and
`right_min_width` each default to 320 CSS pixels and require finite positive
values. Minimums include any child Panel's framing.

The layout keeps the declared ratio exactly when both minimums fit. For a
2:1 ratio with 340px/240px minimums, the side-by-side panes need at least
480px/240px plus the existing gap. Below that width they stack, left above
right; each pane then fits the available width even below its minimum.
Increasing the width automatically restores the ratio. Each pane wraps its
child's full output, keeping a bare chart's title, controls, plot and runtime
legend together. Panel styling and the existing gap remain unchanged.
