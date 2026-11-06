# Report keyboard controls

Ordinary sortable `DataTable` column headings can be reached with Tab.
Enter or Space invokes the same sorting operation as a pointer click. Space
does not scroll the page. A newly selected column sorts descending first;
activating it again sorts ascending. Equal values keep their supplied row
order. Focus returns to the same column heading after its DOM is replaced.
Headings retain their native table semantics and expose `aria-sort` as `none`,
`ascending`, or `descending`.

This behavior requires only the existing `sortable=True` option. With
`sortable=False`, headings acquire neither keyboard stops nor sorting
handlers. No constructor arguments, public API options, datasets, or filter
contracts change.

Search and sorting still operate on all matching rows before the display
limit. Table CSV uses that full matching population in the selected sort
order, with the existing raw values and precision. Dataset filters, comparison
tables, totals, drill-downs, row limits, pinning, cell shading, and workspace
paging keep their existing behavior. Search, filter updates, and theme redraws
do not move focus to a column heading. Ordinary pointer sorting does not
request replacement-header focus. Workspace sorting retains its existing
focus restoration and reset to the first page.

Chart CSV buttons keep their existing position, size, and hover behavior.
They also become visible when reached with keyboard focus. Tab reaches the
button; Enter or Space activates its existing download operation. This does
not add a persistent button or alter chart geometry.

`Toggle` buttons keep their native Enter/Space activation, selected styles,
spacing, and wrapping. Their focus outline is drawn inside the button so the
group's clipped boundary does not cut it off. Sortable table headings also
use an inset outline. Unfocused output remains unchanged in light and dark
themes. Image capture retains the existing policy for excluding CSV controls;
focus outlines do not become exported chrome.

## Native example

`demo/reports/component-showcase/examples_keyboard.py` exports
`add_sections(ctx)`. It adds **Keyboard — table sorting and control focus**,
with its own `showcase_keyboard_observations` dataset and platform filter.
Six deterministic daily observations include measured zero, decimal amounts,
a negative amount, and equal-valued ties. The searchable table displays four
rows; CSV includes every matching observation. A native Toggle controls real
`Visible` explanatory content, and a native LineChart supplies chart CSV.
There is no report-local JavaScript or renderer adapter.

For an existing showcase generator, import `examples_keyboard` alongside the
other example modules and call `examples_keyboard.add_sections(ctx)` once.
Its navigation anchor is
`#sec-keyboard-—-table-sorting-and-control-focus`.

## Verification and limits

`testing/test_report_keyboard.py` builds the example through `ReportContext`,
`compute_details`, `validate_report`, and the native renderer, then uses
vendored browser and capture assets. It checks actual baseline screenshots
for default and pointer-sorted output, keyboard order and stable ties, real
CSV downloads, focus preservation, search and bound-filter redraws, theme
changes, renderer re-entry, workspace keyboard/paging, and native PNG capture.
Desktop Chromium runs normally; the 390px phone case is marked `heavy`.

These are three focused keyboard fixes. They do not establish full
accessibility compliance. Broader keyboard and assistive-technology work for
legends, Section disclosures, annotations, and other report controls remains
outside this change. Existing event disclosures, modern tabs, date Escape
behavior, pointer interactions, chart libraries, and output limits are
unchanged.
