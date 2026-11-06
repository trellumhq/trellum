"""Deterministic horizontal value columns and optional additive stack labels."""
import pandas as pd

from trellum.components import BarChart, DataSource, FilterBar, RawHTML, StackedBar


def add_sections(ctx):
    categories = ["Direct storefront", "Partner distribution with a long category name", "Community", "Archive", "New releases", "Other regions"]
    frame = pd.DataFrame([
        {"category": category, "platform": platform,
         "revenue": (8200 - i * 1100) * scale, "cost": (1300 + i * 170) * scale,
         "adjustment": [140, -120, 0, 80, -20, 0][i] * scale,
         "orders": (430 - i * 60) * (1 if platform == "Web" else 3) if i != 5 else 0}
        for i, category in enumerate(categories)
        for platform, scale in (("Web", 1), ("Mobile", 2))
    ])
    ds = "showcase_bar_categories"
    ctx.add_section("Bar values — category and platform observations", [
        DataSource(ds, frame),
        FilterBar(ds, frame, filters=[{"column": "platform", "type": "toggle", "label": "Bar examples: platform"}]),
        RawHTML(html="<p>Horizontal category values are automatic (show_values=None); False releases the value column. "
                "The current numeric axis formatter supplies compact text. Values reflect the plotted result, including grouped "
                "series and weighted ratios. Narrow panels may omit labels to preserve category context. Tooltips and CSV retain values.</p>"),
        BarChart(frame, "category", "revenue", "Horizontal defaults · values automatic", horizontal=True, dataset_id=ds, y_format="currency"),
        BarChart(frame, "category", "revenue", "Horizontal show_values=False · full plot width", horizontal=True, dataset_id=ds, show_values=False),
        BarChart(frame, "category", "revenue", "Grouped values · signed and measured zero", horizontal=True, dataset_id=ds,
                 y_cols=["revenue", "adjustment"], y_labels=["Revenue", "Adjustment"], show_values=True),
        BarChart(frame, "category", "revenue", "Weighted revenue per order · undefined ratio is a dash", horizontal=True, dataset_id=ds,
                 ratios=[{"numerator": "revenue", "denominator": "orders", "label": "Revenue per order"}], y_format="currency"),
        BarChart(frame, "category", "revenue", "Top three plus Other · current matching categories", horizontal=True,
                 dataset_id=ds, top_n=3, y_format="currency"),
        BarChart(frame, "category", "revenue", "Vertical presentation unchanged · no new values", dataset_id=ds, show_values=True),
    ])
    stacks = pd.DataFrame([
        {"period": f"2026-10-{i + 1:02d}", "source": source, "platform": platform,
         "revenue": (base + i * 137) * scale if i != 5 else 0,
         "orders": (11 + i * 3 + j) * scale if i != 5 else 0}
        for i in range(6) for j, (source, base) in enumerate((("Direct", 4800), ("Paid", 2200), ("Partner", 800), ("Small", 7)))
        for platform, scale in (("Web", 1), ("Mobile", 2))
    ])
    ds = "showcase_bar_stacks"
    ctx.add_section("Stack labels — daily source and platform observations", [
        DataSource(ds, stacks),
        FilterBar(ds, stacks, filters=[{"column": "period", "type": "date_range"},
                  {"column": "platform", "type": "toggle", "label": "Stack examples: platform"}]),
        RawHTML(html="<p>Both new options default to False. These are additive synthetic revenue observations at "
                "day × source × platform grain. Totals describe visible bar segments in absolute currency units, excluding lines. "
                "Percentage labels appear only in the live pivot Percentage view and retain all-series denominators when a legend series is hidden. "
                "Tiny segments keep their tooltips; measured-zero buckets have a zero absolute total and no percentage share.</p>"),
        StackedBar(stacks, "period", ["revenue"], "Both labels off · current defaults", dataset_id=ds,
                   stack_by="source", normalize_toggle=True),
        StackedBar(stacks, "period", ["revenue"], "Visible totals · Source or Platform", dataset_id=ds,
                   stack_by="source", stack_by_options={"Source": "source", "Platform": "platform"}, normalize_toggle=True, show_totals=True),
        StackedBar(stacks, "period", ["revenue"], "Totals and fitting percentages · switch to Percentage", dataset_id=ds,
                   stack_by="source", stack_by_options={"Source": "source", "Platform": "platform"}, normalize_toggle=True,
                   show_totals=True, show_percentages=True),
        StackedBar(stacks, "period", ["revenue"], "Top two stacks plus Other · complete matching population", dataset_id=ds,
                   stack_by="source", max_stacks=2, show_totals=True),
        StackedBar(stacks, "period", ["revenue"], "Revenue totals exclude order line overlay", dataset_id=ds,
                   stack_by="source", line_cols=["orders"], line_labels=["Orders"], normalize_toggle=True, show_totals=True),
        StackedBar(stacks, "period", ["revenue"], "No pivot · percentage labels unavailable", dataset_id=ds,
                   normalize_toggle=True, show_totals=True, show_percentages=True),
    ])
    fixed = pd.DataFrame({"period": ["Snapshot A", "Snapshot B", "Zero"], "direct": [600, 350, 0], "partner": [200, 75, 0]})
    signed = pd.DataFrame({"period": ["Adjustment A", "Adjustment B"], "inflow": [180, 120], "outflow": [-60, -120]})
    incomplete = pd.DataFrame({"period": ["Partial A", "Partial B"], "measured": [90, 120], "unavailable": [None, 30]})
    ctx.add_section("Stack labels — independent fixed snapshots and eligibility", [
        RawHTML(html="<p>These independent static snapshots retain supplied rows. Static StackedBar omits its title and always emits "
                "its legend; it does not emit breakdown or normalization controls. Currency ticks differ from some live paths. "
                "The headings below identify each fixed example. Labels are withheld for incomplete or signed inputs without changing the legacy bars. "
                "Authors must declare additive quantities; the options do not establish that ratios can be summed.</p><h4>Static labels off</h4>"),
        StackedBar(fixed, "period", ["direct", "partner"], "Static labels off", static=True),
        RawHTML(html="<h4>Static visible totals on; percentage labels unavailable</h4>"),
        StackedBar(fixed, "period", ["direct", "partner"], "Static totals on", static=True, show_totals=True, show_percentages=True, normalize_toggle=True, show_legend=False),
        RawHTML(html="<h4>Signed quantities — labels withheld</h4>"),
        StackedBar(signed, "period", ["inflow", "outflow"], "Signed eligibility", static=True, show_totals=True),
        RawHTML(html="<h4>Incomplete quantities — labels withheld</h4>"),
        StackedBar(incomplete, "period", ["measured", "unavailable"], "Incomplete eligibility", static=True, show_totals=True),
        BarChart(pd.DataFrame({"day": ["2026-10-01", "2026-10-02"], "value": [31, 47]}),
                 "day", "value", "Horizontal dates · automatic labels remain off", horizontal=True, static=True),
    ])
