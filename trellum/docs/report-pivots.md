# Pivot tables

`PivotTable` keeps its existing row, column, value and aggregation selectors. Its table uses compact spacing, alternating row shading, grouped column-category headers, a divider before Total and a quiet grand-total footer. Categories and totals are unchanged; group labels are presentation only.

`freeze=True` opts into a local vertical and horizontal scroller (320px by default). `freeze_height` accepts a positive integer pixel height and is used only when freezing is enabled. The headers and first row-dimension column remain visible; additional dimension columns scroll. Both options are keyword-only, and freezing is off by default. Rebuild generated reports to receive the updated component CSS and JavaScript.

Calculations and CSV behavior are unchanged: grouping and category order follow the existing string keys and sort order; sum, count and average use underlying observations; missing values contribute zero; `value_format` is reused for each aggregation; CSV includes selected dimensions/category/value columns and raw body values, and excludes the grand-total footer. Existing limitations include separator and prototype-name group identity collisions, currency-style formatting of counts when selected, missing values treated as zero, an empty grand total displayed as zero, and CSV footer exclusion. These behaviors are not corrected by the presentation options.
