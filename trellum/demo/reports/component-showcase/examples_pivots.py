"""Complete, fixed examples of ordinary and optionally frozen pivots."""
import pandas as pd

from trellum.components import DataSource, FilterBar, PivotTable, RawHTML


def add_sections(ctx):
    revenue = pd.DataFrame([
        {"Channel": channel, "Title": title, "Platform": platform,
         "Revenue": amount, "Installs": installs}
        for channel, title, platform, amount, installs in [
            ("Organic", "Harbor", "Web", 1800, 120), ("Organic", "Harbor", "Mobile", 2400, 140),
            ("Paid", "Harbor", "Web", 3300, 210), ("Paid", "Cedar", "Mobile", 2800, 190),
            ("Organic", "Cedar", "Web", 1500, 90), ("Paid", "Cedar", "Web", 2100, 160),
        ]
    ])
    ctx.add_section("Pivot tables — revenue by title and platform", [
        DataSource("showcase_pivot_revenue", revenue),
        FilterBar("showcase_pivot_revenue", revenue, filters=[
            {"column": "Channel", "label": "Channel", "type": "toggle"}]),
        RawHTML(html="<p>Choose dimensions and measures with the existing selectors. The compact presentation groups platform columns; freezing is off by default.</p>"),
        PivotTable(revenue, rows=["Title"], cols="Platform", values="Revenue", value_format="currency",
                   dataset_id="showcase_pivot_revenue"),
    ])

    wide = pd.DataFrame([
        {"Region": f"Region {i % 6 + 1}", "Product": f"Product {i % 4 + 1}",
         "Month": f"2026-{i % 8 + 1:02d}", "Sales": 100 + i * 13}
        for i in range(48)
    ])
    ctx.add_section("Pivot tables — opt-in frozen wide table", [
        RawHTML(html="<p>This separate example opts into a 320px local scroller. Only the first dimension column and headers freeze; Product continues to scroll.</p>"),
        PivotTable(wide, rows=["Region", "Product"], cols="Month", values="Sales", freeze=True, static=True),
    ])

    no_columns = pd.DataFrame({"Team": ["North", "North", "South"], "Sales": [100, 900, 300]})
    ctx.add_section("Pivot tables — no column dimension and underlying average", [
        RawHTML(html="<p>With no column dimension, the table keeps one header row. The grand average is based on all observations (433.33), not the average of the two row averages.</p>"),
        PivotTable(no_columns, rows=["Team"], values="Sales", agg="avg", static=True),
    ])
