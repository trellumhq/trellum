"""Deterministic heatmap examples at explicitly supplied observation grains."""

import pandas as pd

from trellum.components import DataSource, FilterBar, Grid, HeatmapChart, Panel, RawHTML


def add_sections(ctx):
    """Add scales, missing-coordinate presentation, labels and live filtering."""
    complete = pd.DataFrame([
        {"band": band, "frequency": frequency, "quantity": value}
        for band, values in (("Occasional", (10, 35, 80, 140)),
                             ("Regular", (25, 100, 220, 410)),
                             ("Frequent", (50, 180, 450, 820)))
        for frequency, value in zip(("One", "Two", "Three", "Four+"), values)
    ])

    def chart(frame, title, **options):
        return HeatmapChart(frame, "frequency", "band", "quantity", title,
                            static=True, **options)

    ctx.add_section("Heatmaps — global quantity scales", [
        RawHTML(html=(
            "<p>Each synthetic cell is a supplied quantity at spend-band × "
            "purchase-frequency grain. Global colors compare positive values; "
            "zero uses the low color. Units and exact values remain in tooltips. "
            "The plot keeps its row-count height rule; the key adds context above it.</p>"
        )),
        chart(complete, "Complete nonnegative observations", log_scale=False),
        Grid(columns=2, min_width=280, children=[
            Panel(title="Explicit palette", children=[chart(
                complete, "Fixed two-stop palette", log_scale=False,
                color_scale=("#e8def8", "#53307a"))]),
            Panel(title="Optional hidden values", children=[chart(
                complete, "Values off · tooltips retained", log_scale=False, show_values=False)]),
        ]),
    ])
    sparse = pd.DataFrame({"frequency": ["One", "Two", "One"],
                           "band": ["Measured", "Measured", "Sparse"],
                           "quantity": [0, 40, 80]})
    ctx.add_section("Heatmaps — zero and absent coordinates", [
        RawHTML(html=(
            "<p>Measured/One is a supplied zero. Sparse/Two has no observation: "
            "it is hatched and labeled with a dash. Only existing axis domains "
            "are used; entirely absent categories are not inferred. Missing "
            "coordinates add no values to the dataset or CSV.</p>"
        )),
        chart(sparse, "Measured zero versus no observation", log_scale=False),
    ])
    unequal = pd.DataFrame({"frequency": ["One", "Two", "Three"] * 3,
                            "band": ["Small"] * 3 + ["Large"] * 3 + ["Uniform"] * 3,
                            "quantity": [1, 2, 4, 100, 300, 900, 64, 64, 64]})
    wide = pd.DataFrame({"frequency": ["One", "Two", "Three", "Four+"],
                         "band": ["Wide range"] * 4, "quantity": [1, 10, 1000, 100000]})
    ctx.add_section("Heatmaps — relative and logarithmic colors", [
        RawHTML(html=(
            "<p>Row normalization exposes within-row patterns across unequal "
            "magnitudes. Colors are relative, while labels retain raw units. "
            "Uniform positive rows use the midpoint. The wide-range example "
            "retains the existing automatic logarithmic selection and names it.</p>"
        )),
        chart(unequal, "Unequal rows · relative colors", normalize="row"),
        chart(wide, "Wide range · automatic logarithmic colors"),
    ])
    filtered = pd.DataFrame([
        {"frequency": row.frequency, "band": row.band, "quantity": row.quantity * weight,
         "platform": platform}
        for row in complete.itertuples(index=False)
        for platform, weight in (("iOS", 2), ("Android", 3))
    ])
    dataset = "showcase_heatmaps"
    ctx.add_section("Heatmaps — live platform quantities", [
        DataSource(dataset, filtered),
        FilterBar(dataset, filtered, filters=[
            {"column": "platform", "label": "Heatmaps: platform", "type": "toggle"},
            {"column": "band", "label": "Heatmaps: band", "type": "dropdown"},
        ]),
        RawHTML(html=(
            "<p>Every platform has distinct supplied observations. Filtering "
            "recomputes cell sums and the current-result scale. These controls "
            "do not change the independent static heatmap examples.</p>"
        )),
        HeatmapChart(filtered, "frequency", "band", "quantity", "Filtered platform quantities",
                     dataset_id=dataset, log_scale=False),
    ])
    zeros = pd.DataFrame({"frequency": ["One", "Two"], "band": ["Measured"] * 2,
                          "quantity": [0, 0]})
    signed = pd.DataFrame({"frequency": ["One", "Two", "Three"],
                           "band": ["Adjustment"] * 3, "quantity": [-20, 0, 40]})
    nulls = pd.DataFrame({"frequency": ["One", "Two", "Two", "Three"],
                          "band": ["Supplied"] * 4, "quantity": [None, 80, None, 25]})
    ctx.add_section("Heatmaps — uniform values and existing data boundaries", [
        RawHTML(html=(
            "<p>All-zero results show one value without inventing a range. Signed "
            "results retain legacy colors and are outside the nonnegative scale. "
            "Static null inputs still coerce to zero: One is 0 and the duplicate "
            "Two observations sum to 80. Nulls are not reclassified as missing. "
            "drop_empty still removes categories without a positive cell, including "
            "negative-only groups. Existing CSV and live category-key limitations "
            "are documented in the heatmap API guide.</p>"
        )),
        Grid(columns=2, min_width=280, children=[
            Panel(title="All measured zero", children=[chart(zeros, "All-zero observations")]),
            Panel(title="Signed caveat", children=[chart(signed, "Signed values · legacy semantics")]),
        ]),
        chart(nulls, "Null coercion and duplicate coordinates"),
    ])
