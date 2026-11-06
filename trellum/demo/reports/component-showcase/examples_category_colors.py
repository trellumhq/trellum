"""Declared category colors on native charts with deterministic observations."""

import pandas as pd

from trellum.components import (
    DataSource,
    DoughnutChart,
    FilterBar,
    Grid,
    LineChart,
    RawHTML,
    StackedBar,
)

ACQUISITION = {"domain": "acquisition", "column": "channel", "slots": {"UA": 0, "Retargeting": 2}}


def add_sections(ctx):
    """Add independently filtered datasets and explicitly related color domains."""
    spend = pd.DataFrame([
        {"day": f"2026-09-{day:02d}", "channel": channel,
         "platform": "iOS" if (day + index) % 2 else "Android",
         "spend": base + day * (7 + index)}
        for day in range(1, 7)
        for index, (channel, base) in enumerate((("UA", 900), ("Retargeting", 180)))
    ])
    revenue = pd.DataFrame([
        {"day": f"2026-09-{day:02d}", "acquisition_channel": channel,
         "revenue": base + day * (11 + index)}
        for day in range(1, 7)
        for index, (channel, base) in enumerate((("UA", 260), ("Retargeting", 1400)))
    ])
    for frame, dataset, column, metric, declaration in (
        (spend, "category_spend", "channel", "spend", ACQUISITION),
        (revenue, "category_revenue", "acquisition_channel", "revenue",
         {**ACQUISITION, "column": "acquisition_channel"}),
    ):
        name = metric.title()
        ctx.add_section(f"Category colors — {name.lower()} observations", [
            DataSource(dataset, frame),
            FilterBar(dataset, frame, filters=[
                {"column": "day", "type": "date_range", "label": f"{name}: dates"},
                {"column": column, "type": "dropdown", "label": f"{name}: channel"},
            ]),
            RawHTML(html=(
                f"<p>{name} uses its own daily dataset and independent filters. "
                "Spend ranks UA first; revenue ranks Retargeting first. Defaults "
                "assign colors within each dataset. Declared acquisition slots "
                "keep UA and Retargeting consistent across the two sources, "
                "including when a channel is filtered away and restored.</p>"
            )),
            Grid(columns=2, min_width=280, children=[
                LineChart(frame, "day", metric, f"{name} default", dataset_id=dataset,
                          stack_by=column, y_format="currency"),
                LineChart(frame, "day", metric, f"{name} declared", dataset_id=dataset,
                          stack_by=column, y_format="currency", category_colors=declaration),
            ]),
        ])
    ctx.add_section("Category colors — selected stack breakdown", [
        DataSource("category_stacks", spend),
        FilterBar("category_stacks", spend, filters=[
            {"column": "day", "type": "date_range", "label": "Stack: dates"},
            {"column": "channel", "type": "dropdown", "label": "Stack: channel"},
        ]),
        RawHTML(html=(
            "<p>Only the Channel breakdown uses the acquisition domain. Platform "
            "keeps its existing colors. Normalization and the supplied "
            "amounts are unchanged. Category declarations apply to dataset-bound "
            "LineChart and StackedBar splits, and to DoughnutChart labels; static "
            "wide-column lines and bars are not category rows.</p>"
        )),
        StackedBar(spend, "day", ["spend"], "Spend by selected breakdown",
                   dataset_id="category_stacks", stack_by_options={
                       "Channel": "channel", "Platform": "platform"},
                   normalize_toggle=True, category_colors=ACQUISITION),
    ])
    rings = pd.DataFrame([
        {"channel": channel, "market": market, "value": base + offset}
        for channel, base in (("UA", 110), ("Retargeting", 320), ("Unmapped", 40))
        for market, offset in (("North", 3), ("South", 17))
    ])
    snapshot = pd.DataFrame({"channel": ["UA", "Retargeting", "UA", "Unmapped"],
                             "value": [90, 240, 35, 20]})
    ctx.add_section("Category colors — snapshot and filtered rings", [
        DataSource("category_rings", rings),
        FilterBar("category_rings", rings, filters=[
            {"column": "market", "type": "toggle", "label": "Ring: market"},
            {"column": "channel", "type": "dropdown", "label": "Ring: channel"},
        ]),
        RawHTML(html=(
            "<p>Static duplicate UA rows keep separate arcs with the same color. "
            "The live ring groups categories and follows its filters. Unknown "
            "categories retain the existing palette fallback, whose color can "
            "change with the result. Theme switches resolve declared palette slots "
            "again; a per-chart override keeps its supplied color.</p>"
        )),
        Grid(columns=2, min_width=280, children=[
            DoughnutChart(snapshot, "channel", "value", "Snapshot declared", static=True,
                          category_colors=ACQUISITION),
            DoughnutChart(rings, "channel", "value", "Live declared", dataset_id="category_rings",
                          category_colors=ACQUISITION),
            DoughnutChart(snapshot, "channel", "value", "Snapshot fixed override", static=True,
                          category_colors={**ACQUISITION, "overrides": {"UA": "#b1457b"}}),
        ]),
    ])
    other = pd.DataFrame({"category": ["Other", "Known"], "value": [43, 71]})
    ctx.add_section("Category colors — independent Other domains", [
        RawHTML(html=(
            "<p>These two Other labels mean different things. Their explicitly "
            "different domains and slots choose different theme colors. Domain "
            "names do not invent extra colors: finite palettes repeat when slot "
            "indices exceed their length. No other chart family opts in implicitly.</p>"
        )),
        Grid(columns=2, min_width=280, children=[
            DoughnutChart(other, "category", "value", "Other: sources", static=True,
                          category_colors={"domain": "source-other", "column": "category", "slots": {"Other": 1, "Known": 0}}),
            DoughnutChart(other, "category", "value", "Other: regions", static=True,
                          category_colors={"domain": "region-other", "column": "category", "slots": {"Other": 4, "Known": 0}}),
        ]),
    ])
