"""A common home for interactive, deterministic component examples."""

import pandas as pd

from trellum import BaseReport
from trellum.components import DataSource, FilterBar, Grid, LineChart, RawHTML

from . import (
    examples_areas, examples_bars, examples_chart_context, examples_combos, examples_comparisons, examples_controls, examples_dates, examples_doughnuts, examples_heatmaps, examples_kpis,
    examples_kpi_states, examples_layouts, examples_line_styles, examples_pivots, examples_status_text, examples_tables, examples_treemaps,
)


class ComponentShowcaseReport(BaseReport):
    def generate(self, ctx):
        ctx.set_header(subtitle="Reusable report components and optional settings")
        # RawHTML supplies explanatory prose; interactive examples below use
        # the framework components and their normal filtering runtime.
        ctx.add_section("About these examples", [RawHTML(html=(
            "<p>These examples use deterministic synthetic data. Change their "
            "controls to explore the component behavior. Each group of examples "
            "has its own filters. Optional features are "
            "identified beside each example.</p>"
            '<p>Jump to <a href="#sec-line-example-filters">line charts</a>, '
            '<a href="#sec-line-styles:-density-follows-the-plot">line styles</a>, '
            '<a href="#sec-chart-header-context-—-filtered-revenue-and-summary">chart context</a>, '
            '<a href="#sec-area-example-filters">area charts</a>, '
            '<a href="#sec-bar-values-—-category-and-platform-observations">bar values and stack labels</a>, '
            '<a href="#sec-combo-charts-—-daily-channel-observations">combo charts</a>, '
            '<a href="#sec-layout-example-filters">layouts</a>, '
            '<a href="#sec-kpi-cards-and-compact-metrics">KPI metrics</a>, '
            '<a href="#sec-kpi-states-—-supplied-observation-completeness">KPI states and comparisons</a>, '
            '<a href="#sec-readable-small-status-text">status text</a>, '
            '<a href="#sec-standalone-choices">report controls</a>, '
            '<a href="#sec-datatable-—-static-population">data tables</a>, '
            '<a href="#sec-explicit-weekly-comparisons">comparison tables</a>, '
            '<a href="#sec-pivot-tables-—-revenue-by-title-and-platform">pivot tables</a>, '
            '<a href="#sec-heatmaps-—-global-quantity-scales">heatmaps</a>, '
            '<a href="#sec-treemaps-—-hierarchy-and-optional-shares">treemaps</a>, '
            '<a href="#sec-doughnuts-—-compact-rings-and-optional-total">doughnuts</a> or '
            '<a href="#sec-date-ranges-—-daily-readings">date ranges</a>.</p>'
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
        ctx.add_section("Line example filters", [
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

        examples_kpis.add_sections(ctx)

        examples_kpi_states.add_sections(ctx)

        examples_areas.add_sections(ctx)

        examples_bars.add_sections(ctx)

        examples_combos.add_sections(ctx)

        examples_line_styles.add_sections(ctx)

        examples_chart_context.add_sections(ctx)

        examples_controls.add_sections(ctx)

        examples_tables.add_sections(ctx)

        examples_comparisons.add_sections(ctx)

        examples_dates.add_sections(ctx)

        examples_doughnuts.add_sections(ctx)

        examples_pivots.add_sections(ctx)

        examples_heatmaps.add_sections(ctx)

        examples_treemaps.add_sections(ctx)

        examples_status_text.add_sections(ctx)
