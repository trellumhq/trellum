# Report choices and views

These components are standalone report controls. Report data filtering continues
through `DataSource` and `FilterBar`; a standalone `Dropdown` does not implicitly
bind to a chart.

## Dropdown

```python
from trellum.components import Dropdown

Dropdown("metric_caption", ["Revenue", "Orders"], "Revenue", label="Displayed metric")
```

`Dropdown(id, options, default=None, *, label="Choose an option")` retains its
original positional arguments, including in dataclass subclasses. Supply a
meaningful visible `label` for each choice. Labels and option strings are text,
not HTML. The default selection is the first option when `default` is omitted.
An empty list displays a disabled "No choices available" control.

The bundled SlimSelect enhancer provides search over every option. The selected
caption and menu options wrap inside narrow panels. There are no pages or Apply
buttons. The underlying native select keeps the supplied `id`, option values and
standard bubbling `change` events. Existing listeners can continue to read
`document.getElementById("metric_caption").value`. Setting `.value` and dispatching
a native `change` also updates the enhanced caption. Refresh preservation retains
the existing `dropdown_<id>` state key. This does not add a new URL parameter or
change FilterBar dropdowns.

## TabGroup

```python
from trellum.components import TabGroup

views = [
    {"label": "Revenue", "content": [revenue_chart]},
    {"label": "Orders", "content": [orders_chart]},
]
TabGroup(views, "metric_views", label="Metric view")
TabGroup(views, "compact_views", "dropdown", label="Choose a view")
TabGroup(views, "side_views", "sidebar", label="Browse views")
```

`TabGroup(tabs, id=None, mode="tabs", *, label="Report view")` retains its three
original positional arguments. Each view still supplies `label` and a list of
real components in `content`. Use stable unique IDs for independent groups;
empty groups display "No report views available".

- `tabs`, the default, shows wrapping underlined tabs. Tab enters the selected
  tab; Left/Right, Home and End move focus and select a view.
- `dropdown` always shows a labeled searchable selector containing every view.
  Its native select values remain zero-based indices, as in previous versions.
- `sidebar` is opt-in. Its 180px vertical list becomes the same searchable
  selector when the group's panel is at most 520px wide, independently of the
  viewport. Up/Down, Home and End navigate the side list. Selection stays
  synchronized when panel width changes. The chart's plot height is preserved.

For compatibility, any unrecognized legacy `mode` still renders ordinary tabs.
Existing reports keep their requested mode; many views do not trigger an
automatic conversion. Nested and sibling groups select only their own panels.
Previously initialized charts resize when shown; hidden charts retain the normal
lazy initialization path.

The public `fw` API and state/URL ownership remain unchanged: state uses
`tab_<id>`, URLs use `tab.<id>`. Ordinary tabs and side lists store the label;
`dropdown` stores the zero-based index string. Keep labels/order stable when
retaining old bookmarks. Side-list selector changes also emit native `change`.
The accessible navigation name comes from `label`; tab buttons expose selected
state and their panel relationship.

See `demo/reports/component-showcase/examples_controls.py` for deterministic
examples, including long/empty choices, all modes, nested groups and ordinary
chart interaction.
