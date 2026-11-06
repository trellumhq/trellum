"""A common home for interactive, deterministic component examples."""

import pandas as pd

from trellum import BaseReport
from trellum.components import DataSource, FilterBar, Grid, LineChart, RawHTML

from . import examples_layouts


class ComponentShowcaseReport(BaseReport):
    def generate(self, ctx):
        ctx.set_header(subtitle="Reusable report components and optional settings")
        # RawHTML supplies explanatory prose; interactive examples below use
        # the framework components and their normal filtering runtime.
        ctx.add_section("About these examples", [RawHTML(html=(
            "<p>These examples use deterministic synthetic data. Change their "
            "controls to explore the component behavior. Optional features are "
            "identified beside each example.</p>"
            '<p>For business reports, return to the <a href="../">demo gallery</a>: '
            "Player Overview, Monetization, Store Health and Live Ops show these "
            "components in context.</p>"
        ))])

        days = pd.date_range("2026-09-01", periods=30).strftime("%Y-%m-%d")
        frame = pd.DataFrame([
            {"day": day, "platform": platform,
             "revenue": (180 + index * 7 + (index % 7) * 19) * multiplier}
            for index, day in enumerate(days)
            for platform, multiplier in (("iOS", 2), ("Android", 1))
        ])
        ctx.add_section("", [
            DataSource("showcase_lines", frame),
            FilterBar("showcase_lines", frame, filters=[
                {"column": "platform", "label": "Line examples: platform",
                 "type": "toggle"},
            ]),
        ])
        common = {"df": frame, "x": "day", "y": "revenue",
                  "dataset_id": "showcase_lines", "y_format": "currency"}
        ctx.add_section("Line charts — sizes and optional summaries", [
            RawHTML(html=(
                "<p>Ordinary line plots are 220px high. The compact option uses "
                "160px, and roomy uses 320px. The optional daily average follows "
                "the selected platform and includes every displayed date.</p>"
            )),
            Grid(children=[
                LineChart(title="Default · 220px", **common),
                LineChart(title="Compact · 160px", height="compact",
                          header_summary={"stat": "mean", "label": "Daily avg",
                                          "format": "currency"}, **common),
            ]),
            LineChart(title="Roomy · 320px", height="roomy", **common),
        ])

        examples_layouts.add_sections(ctx)
