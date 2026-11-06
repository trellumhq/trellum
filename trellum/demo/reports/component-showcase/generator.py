"""A common home for interactive, deterministic component examples."""

import pandas as pd

from trellum import BaseReport
from trellum.components import DataSource, FilterBar, Grid, LineChart, RawHTML

from . import (
    examples_annotations, examples_areas, examples_bars, examples_category_colors, examples_chart_context, examples_chart_summaries, examples_combos, examples_comparisons, examples_controls, examples_dates, examples_doughnuts, examples_heatmaps, examples_hierarchies, examples_kpis,
    examples_kpi_states, examples_layouts, examples_line_availability, examples_line_forecasts, examples_line_styles, examples_month_context, examples_monthly_averages, examples_pivots, examples_status_text, examples_table_drill_down, examples_table_pinning, examples_table_row_limits, examples_table_shading, examples_table_totals, examples_tables, examples_treemaps,
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
            '<a href="#sec-line-forecasts-—-supplied-horizon-and-opt-in-styling">line forecasts</a>, '
            '<a href="#sec-line-availability-—-selected-supplied-observations">line availability</a>, '
            '<a href="#sec-annotations-—-synthetic-daily-comparison">dated events</a>, '
            '<a href="#sec-month-context-—-off-and-on">month context</a>, '
            '<a href="#sec-monthly-averages-—-partial-selection-and-observed-days">monthly averages</a>, '
            '<a href="#sec-chart-header-context-—-filtered-revenue-and-summary">chart context</a>, '
            '<a href="#sec-chart-summaries-—-static-off-and-on">chart summaries</a>, '
            '<a href="#sec-category-colors-—-spend-observations">category colors</a>, '
            '<a href="#sec-area-example-filters">area charts</a>, '
            '<a href="#sec-bar-values-—-category-and-platform-observations">bar values and stack labels</a>, '
            '<a href="#sec-combo-charts-—-daily-channel-observations">combo charts</a>, '
            '<a href="#sec-layout-example-filters">layouts</a>, '
            '<a href="#sec-kpi-cards-and-compact-metrics">KPI metrics</a>, '
            '<a href="#sec-kpi-states-—-supplied-observation-completeness">KPI states and comparisons</a>, '
            '<a href="#sec-readable-small-status-text">status text</a>, '
            '<a href="#sec-standalone-choices">report controls</a>, '
            '<a href="#sec-datatable-—-static-population">data tables</a>, '
            '<a href="#sec-datatable-row-limits-—-legacy">table row limits</a>, '
            '<a href="#sec-table-totals-—-exact-campaign-snapshot-off-and-on">table totals</a>, '
            '<a href="#sec-datatable-pinning-—-static-snapshots">table pinning</a>, '
            '<a href="#sec-table-drill-down-—-report-selection">row drill-down</a>, '
            '<a href="#sec-table-shading-—-snapshot-comparison">table shading</a>, '
            '<a href="#sec-explicit-weekly-comparisons">comparison tables</a>, '
            '<a href="#sec-pivot-tables-—-revenue-by-title-and-platform">pivot tables</a>, '
            '<a href="#sec-hierarchy-—-complete,-collapsed">hierarchical tables</a>, '
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
        examples_line_forecasts.add_sections(ctx)
        examples_line_availability.add_sections(ctx)
        examples_annotations.add_sections(ctx)
        examples_month_context.add_sections(ctx)
        examples_monthly_averages.add_sections(ctx)

        examples_chart_context.add_sections(ctx)
        examples_chart_summaries.add_sections(ctx)

        examples_category_colors.add_sections(ctx)

        examples_controls.add_sections(ctx)

        examples_tables.add_sections(ctx)
        examples_table_row_limits.add_sections(ctx)
        examples_table_totals.add_sections(ctx)
        examples_table_pinning.add_sections(ctx)
        examples_table_drill_down.add_sections(ctx)

        examples_table_shading.add_sections(ctx)

        examples_comparisons.add_sections(ctx)

        examples_dates.add_sections(ctx)

        examples_doughnuts.add_sections(ctx)

        examples_pivots.add_sections(ctx)
        examples_hierarchies.add_sections(ctx)

        examples_heatmaps.add_sections(ctx)

        examples_treemaps.add_sections(ctx)

        examples_status_text.add_sections(ctx)
