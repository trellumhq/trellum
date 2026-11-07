"""A curated feature tour; exhaustive fixtures remain in examples_*.py."""

from html import escape

import pandas as pd

from trellum.components import (
    AreaChart,
    BarChart,
    CohortTable,
    ComboChart,
    ComparisonTable,
    DataSource,
    DataTable,
    DoughnutChart,
    Dropdown,
    FilterBar,
    FunnelChart,
    Grid,
    HeatmapChart,
    HierarchyTable,
    KpiCard,
    KpiRow,
    LineChart,
    MiniKpi,
    Panel,
    PivotTable,
    RawHTML,
    SankeyChart,
    ScatterChart,
    Section,
    SplitPane,
    StackedBar,
    TabGroup,
    Toggle,
    TreemapChart,
)

from .examples_comparisons import comparison_fixture
from .examples_hierarchies import spend_facts
from .examples_sankeys import STAGES, paths_frame

CATEGORIES = (
    ("metrics", "Metrics", "Headlines, precision and compact context"),
    ("trends", "Time series", "Lines, forecasts, areas and dual axes"),
    ("categories", "Category charts", "Rankings, shares and consistent colors"),
    ("tables", "Tables", "One table for each kind of question"),
    ("patterns", "Patterns and journeys", "Heatmaps, flows, funnels and relationships"),
    ("layouts", "Layouts and controls", "Compact grids and responsive views"),
)
COLORS = {"domain": "tour-acquisition", "column": "Channel",
          "slots": {"Search": 0, "Social": 2, "Partners": 4}}


def note(purpose, attempt, options=""):
    """Short, consistent reading instructions beside the owning example."""
    return RawHTML(html=(f'<div class="tour-note"><p>{escape(purpose)}</p>'
                         f'<p><strong>Try it</strong> · {escape(attempt)}</p>'
                         + (f'<p class="tour-options"><strong>Enabled here</strong> · {escape(options)}</p>' if options else "")
                         + '</div>'))


def tab(label, *content):
    return {"label": label, "content": list(content)}


def platform_filter(dataset, frame, label):
    return FilterBar(dataset, frame, filters=[
        {"column": "Platform", "label": label, "type": "toggle"}])


def daily_frame():
    return pd.DataFrame([
        {"Day": day, "Platform": platform, "Channel": channel,
         "Spend": (90 + i * 3 + j * 28) * scale,
         "Revenue": (130 + i * 5 + j * 17 + (i % 7) * 9) * scale,
         "Installs": (70 + i * 2 + j * 10) * scale,
         "Orders": (8 + i % 8 + j * 2) * scale}
        for i, day in enumerate(pd.date_range("2026-08-20", periods=31).strftime("%Y-%m-%d"))
        for platform, scale in (("iOS", 2), ("Android", 1))
        for j, channel in enumerate(("Search", "Social", "Partners"))
    ])


def _navigation():
    # TabGroup switches component views below. No native component represents
    # editorial anchor links to separate report sections; RawHTML owns only
    # this navigation and explanatory prose, never charts or filter state.
    links = ''.join(
        f'<a href="#sec-{label.lower().replace(" ", "-")}"><span class="tour-index">{i:02}</span>'
        f'<span><strong>{label}</strong><small>{description}</small></span>'
        '<span aria-hidden="true">↗</span></a>'
        for i, (key, label, description) in enumerate(CATEGORIES, 1)
    )
    return RawHTML(html='''<style>
.tour-nav{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:18px 0}
.tour-nav a{display:flex;align-items:center;gap:12px;padding:16px;border:1px solid var(--border-color);border-radius:10px;background:var(--bg-card-hover);color:var(--text-main);text-decoration:none}
.tour-nav a:hover{border-color:var(--accent-blue)}
.tour-nav a:focus-visible{outline:2px solid var(--accent-blue);outline-offset:3px}
.tour-nav a>span:nth-child(2){flex:1;min-width:0}
.tour-nav strong{display:block;font-size:15px}.tour-nav small{display:block;margin-top:5px;line-height:1.45;color:var(--text-muted)}
.tour-index{color:var(--text-muted);font-size:12px;font-variant-numeric:tabular-nums}
@media(max-width:960px){.tour-nav{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:640px){.tour-nav{grid-template-columns:1fr}}
.tour-note{margin:12px 0 18px;max-width:900px;color:var(--text-main)}
.tour-note p{margin:6px 0;line-height:1.55}.tour-options{color:var(--text-muted);font-size:12px}
.tour-story-links{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0;line-height:1.7}
.tour-story-links a{display:inline-block;padding:7px 12px;border:1px solid var(--border-color);border-radius:7px;color:var(--text-main);text-decoration:none;font-size:13px;background:var(--bg-card-hover)}
.tour-story-links a:hover{text-decoration:underline}
.tour-story-links a:focus-visible{outline:2px solid var(--accent-blue);outline-offset:2px}
.tour-footer a{color:var(--text-main);text-underline-offset:3px}
</style><p>A guided tour of native report components. Pick a family, then switch views to see
what each component does. Every example uses synthetic data; filters affect only their named family.</p>
<nav class="tour-nav" aria-label="Component families">''' + links + '''</nav>
<p>Want to see a complete report?</p><div class="tour-story-links">
<a href="../player-overview/">Player Overview · compact dashboard</a>
<a href="../monetization/">Monetization · acquisition and cohorts</a>
<a href="../conversion/">Conversion · player journeys</a>
<a href="../report-upgrade/">Visual upgrade guide · defaults and opt-ins</a>
</div>''')


def _metrics():
    frame = pd.DataFrame({"Platform": ["iOS", "Android"], "Revenue": [18420, 12750],
                          "Players": [6200, 4800], "Sessions": [19400, 15300]})
    return [
        note("KPI cards answer the headline question; MiniKpi adds quiet context.",
             "Choose a platform. All six cards update; on a phone, expand the last two.",
             "Four-card mobile preview. Card heights and row arrangement stay standard."),
        DataSource("tour_metrics", frame), platform_filter("tour_metrics", frame, "Metrics platform"),
        KpiRow([
            {"label": "Revenue", "column": "Revenue", "format": "currency"},
            {"label": "Players", "column": "Players"},
            {"label": "Sessions", "column": "Sessions"},
            {"label": "Revenue / player", "agg": "ratio", "numerator": "Revenue", "denominator": "Players", "format": "currency", "precision": 2},
            {"label": "Revenue · full", "column": "Revenue", "format": "currency", "full_number": True},
            {"label": "Sessions / player", "agg": "ratio", "numerator": "Sessions", "denominator": "Players", "format": "ratio"},
        ], dataset_id="tour_metrics", mobile_collapse=4),
        Section("Card presentation options", [
            note("These fixed values isolate formatting from filtering.",
                 "Compare the same revenue left-aligned and centered; compare relative and percentage-point changes."),
            KpiRow([
                KpiCard("Revenue · left", 18420, "currency"),
                KpiCard("Revenue · centered", 18420, "currency", alignment="center"),
                KpiCard("Conversion · relative", .22, "percent", comparison={"current": {"value": .22, "unit": "fraction"}, "baseline": {"value": .20, "unit": "fraction"}, "measure": "relative_percent"}),
                KpiCard("Conversion · points", .22, "percent", comparison={"current": {"value": .22, "unit": "fraction"}, "baseline": {"value": .20, "unit": "fraction"}, "measure": "percentage_points"}),
            ], static=True),
            MiniKpi("Conversion", .22, "percent"),
            MiniKpi("Revenue / player", 2.97, "currency", stacked=True),
        ], collapsible=True, default_collapsed=True),
    ]


def _trends():
    frame = daily_frame()
    dataset = "tour_trends"
    horizon = pd.DataFrame({"Day": pd.date_range("2026-10-01", periods=10).strftime("%Y-%m-%d"),
                            "Observed": [42, 45, 43, 48, 51, 49, 54, None, None, None],
                            "Forecast": [None] * 7 + [52, 55, 57]})
    return [
        note("Choose how to explain change over time.",
             "Platform and dates affect Line, Area and Dual axis. Forecast is a separate fixed illustration."),
        DataSource(dataset, frame), FilterBar(dataset, frame, filters=[
            {"column": "Platform", "label": "Trend platform", "type": "toggle"},
            {"column": "Day", "label": "Trend dates", "type": "date_range"},
        ]),
        TabGroup([
            tab("Line", note("A daily trend with a summary that follows the selection.",
                "Select iOS, narrow the dates, then tap a dated event label for its exact details.",
                "Header summary, units, month labels and dated events; 220px plot."),
                LineChart(frame, "Day", "Revenue", "Daily revenue", dataset_id=dataset, y_format="currency",
                          header_context="USD · selected platform and dates",
                          header_summary={"stat": "mean", "label": "Daily avg", "format": "currency"}, month_context=True),
                Section("Optional monthly guides", [
                    note("Monthly guides summarize observed daily values; they are not fitted trend lines.",
                         "Change the date range and compare observed-day counts and averages."),
                    LineChart(frame, "Day", "Revenue", "Revenue with monthly averages", dataset_id=dataset,
                              y_format="currency", month_context=True,
                              monthly_average={"metric": "daily_additive", "aggregation": "sum_complete",
                                               "start": "2026-08-20", "end": "2026-09-19", "unit": "USD"}),
                ], collapsible=True, default_collapsed=True)),
            tab("Area", note("Area fill emphasizes volume over time.", "Change platform; the light fill and daily values update together."),
                AreaChart(frame, "Day", ["Revenue", "Spend"], "Revenue and spend", dataset_id=dataset, y_format="currency")),
            tab("Dual axis", note("A ComboChart compares measures with different units.",
                "Read dollars on the left and install counts on the right; the legend groups their series."),
                ComboChart(frame, "Day", ["Spend"], ["Installs"], "Spend and acquisition", dataset_id=dataset,
                           left_axis_label="Spend · USD", right_axis_label="Installs · count")),
            tab("Forecast", note("Separate supplied observations from an author's forecast.",
                "Hover the dashed future series. The platform/date controls above do not change this fixed example.",
                "Dashed forecast with hollow dots. No forecast is calculated by the chart."),
                LineChart(horizon, "Day", ["Observed", "Forecast"], "Observed and supplied forecast", static=True,
                          forecast_series=["Forecast"], header_context="Illustration supplied as of 7 October")),
        ], id="tour_trend_views", label="Time-series example"),
    ]


def _categories():
    frame = daily_frame()
    ds = "tour_categories"
    return [
        note("Compare categories, their share of a total, or a hierarchy.",
             "Change platform, then switch views. Search, Social and Partners keep their declared colors in the stack and ring."),
        DataSource(ds, frame), platform_filter(ds, frame, "Category platform"),
        TabGroup([
            tab("Ranked bars", BarChart(frame, "Channel", "Revenue", "Revenue by channel", dataset_id=ds,
                horizontal=True, sort="desc", y_format="currency",
                header_summary={"stat": "sum", "scope": "plotted_series", "label": "Total", "format": "currency"})),
            tab("Stacked mix", note("Absolute bars compare volume; percentages compare mix.",
                "Switch Absolute / Percentage. Totals belong to absolute bars; segment percentages appear where they fit.",
                "Optional totals, segment percentages and 220px plot."),
                StackedBar(frame, "Day", ["Revenue"], "Daily channel mix", dataset_id=ds, stack_by="Channel",
                           normalize_toggle=True, show_totals=True, show_percentages=True, height=220, category_colors=COLORS)),
            tab("Doughnut", note("Use a ring for a small number of categories.",
                "Hide a category in the legend; the labeled total continues to cover all selected categories.",
                "Optional center total."),
                DoughnutChart(frame, "Channel", "Revenue", "Revenue mix", dataset_id=ds,
                              value_format="currency", center_total=True, center_label="Selected revenue", category_colors=COLORS)),
            tab("Treemap", note("Rectangle area shows revenue; colors identify the top-level channel.",
                "Open the details for exact channel/platform paths and values.", "Optional tile percentages."),
                TreemapChart(frame, ["Channel", "Platform"], "Revenue", "Where revenue comes from", dataset_id=ds,
                             value_format="currency", show_percentages=True)),
        ], id="tour_category_views", label="Category chart example"),
    ]


def campaign_frame():
    frame = pd.DataFrame([
        {"Campaign": f"{channel} {i+1:02}", "Channel": channel,
         "Platform": platform, "Spend": 500 + i * 71 + j * 180,
         "Installs": 270 + i * 23 + j * 80,
         "Revenue": 580 + i * 88 + j * 91}
        for i in range(8) for j, channel in enumerate(("Search", "Social", "Partners"))
        for platform in ("iOS", "Android")
    ])
    frame["CPI"] = frame.Spend / frame.Installs
    return frame.sort_values("Spend", ascending=False, kind="stable").reset_index(drop=True)


def _cohorts():
    frame = pd.DataFrame([
        {"Cohort": month, "Month": month, "Day": day,
         "Recovery": round(.24 + i * .018 + j * .23, 3), "State": "observed"}
        for i, month in enumerate(pd.date_range("2025-01-01", periods=6, freq="MS").strftime("%Y-%m"))
        for j, day in enumerate([7, 30, 60, 90, 120])
    ])
    return [note("Compare acquisition cohorts at the same age, with months grouped by year.",
                 "Read down a month to follow its supplied recovery; open cell details for exact values.",
                 "Optional sampled 100% recovery milestone. These mature synthetic ratios are supplied, not estimated."),
            CohortTable(frame, "Recovery by acquisition cohort", cohort="Cohort", cohort_month="Month", horizon="Day",
                        observed="Recovery", observed_state="State", static=True,
                        population={"label": "Six independent synthetic acquisition cohorts", "as_of": "2026-01-01T00:00:00Z", "timezone": "UTC"},
                        milestone={"threshold": 1.0, "label": "Recovery reached"})]


def _tables():
    frame = campaign_frame()
    ds = "tour_campaigns"
    comparisons, context = comparison_fixture()
    comparisons = comparisons[comparisons.Focus == "Performance"].copy()
    leaves = spend_facts()
    totals = {"Spend": {"agg": "sum", "format": "currency"}, "Revenue": {"agg": "sum", "format": "currency"},
              "Installs": {"agg": "sum", "precision": 0},
              "CPI": {"agg": "ratio", "numerator": "Spend", "denominator": "Installs", "format": "currency"}}
    return [
        note("Choose the table by the question, rather than comparing dozens of near-identical tables.",
             "Campaigns and Pivot share the platform/channel filters. Other tabs are independent fixed examples."),
        DataSource(ds, frame), FilterBar(ds, frame, filters=[
            {"column": "Platform", "type": "toggle", "label": "Table platform"},
            {"column": "Channel", "type": "dropdown", "label": "Table channel"},
        ]),
        TabGroup([
            tab("Campaigns", note("Search and rank individual campaigns, keeping a trustworthy total.",
                "Search for Search, sort Spend, change the row limit and download CSV. Click a channel name to drill into it.",
                "10-row selector; pinned header/total; CPI ≤ $2 shading; channel drill-down. Totals and CSV cover all matching rows."),
                DataTable(frame, "Campaign performance", dataset_id=ds, columns=["Campaign", "Channel", "Platform", "Spend", "Installs", "Revenue", "CPI"],
                          sortable=True, searchable=True, row_limit=10, totals=totals, pin_header=True, pin_footer=True, pin_height=360,
                          column_formats={name: {"format": "currency", "precision": 2} for name in ("Spend", "Revenue", "CPI")},
                          cell_shading={"CPI": {"target": 2, "direction": "lower"}},
                          drill_down={"dataset_id": ds, "column": "Channel", "value_column": "Channel", "label_column": "Channel"})),
            tab("Comparison", note("A comparison table explains periods, units and favorable direction.",
                "Compare cost per install and revenue: lower cost is favorable; higher revenue is favorable.",
                "Explicit periods and percentage-point changes for rates."),
                ComparisonTable(comparisons, "This week versus last week", "Current", ["Change"],
                                columns=["Metric", "Previous", "Current", "Change"], comparison_context=context, static=True)),
            tab("Pivot", note("Summarize a measure across row and column dimensions.",
                "Change the native dimension or measure selectors; platform/channel filters still apply.",
                "Frozen headers and first column; original selectors."),
                PivotTable(frame, rows=["Channel"], cols="Platform", values="Revenue", title="Revenue by channel and platform",
                           dataset_id=ds, value_format="currency", freeze=True)),
            tab("Hierarchy", note("Expand parent groups into their underlying platform contributions.",
                "Expand Paid search. The grand total stays the same because collapsed descendants are included."),
                HierarchyTable(leaves, "Spend by source and platform", rows=["Source", "Platform"], value="Spend",
                               record_id="Fact", column="Month", value_format="currency", static=True)),
            tab("Cohorts", *_cohorts()),
        ], id="tour_table_views", label="Table example"),
    ]


def _patterns():
    heat = pd.DataFrame([
        {"Day": day, "Hour": hour, "Sessions": 40 + (i + 1) * (j + 2) * 17 + (110 if j in (3, 4) else 0), "Platform": platform}
        for i, day in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        for j, hour in enumerate(["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"])
        for platform in ["iOS", "Android"]
    ])
    paths = paths_frame()
    funnel = pd.DataFrame({"Stage": ["Visit", "Install", "Register", "Purchase"], "Players": [10000, 6200, 3900, 1240]})
    scatter = campaign_frame()
    return [
        note("Explore patterns within a platform or compare fixed conversion and campaign examples.",
             "Pattern platform changes Heatmap and Sankey. Funnel and Scatter are fixed illustrations."),
        DataSource("tour_heat", heat), DataSource("tour_paths", paths),
        FilterBar("tour_heat", heat, filters=[{"column": "Platform", "label": "Pattern platform", "type": "toggle"}],
                  propagate_to={"tour_paths": {"Platform": "platform"}}),
        TabGroup([
        tab("Heatmap", note("Find repeating day/hour patterns in a matrix of quantities.",
            "Filter platform and hover a cell for its exact count. The single-hue legend explains the scale."),
            HeatmapChart(heat, "Hour", "Day", "Sessions", "When players return", dataset_id="tour_heat", log_scale=False)),
        tab("Sankey", note("Follow a fixed cohort between weekly activity bands.",
            "Choose iOS: 1,000 players becomes 500. Open path details to see exact complete journeys."),
            SankeyChart(paths, "Weekly player journeys", mode="paths", stages=STAGES, weight="players", unit="players", dataset_id="tour_paths")),
        tab("Funnel", note("Track a complete sequential population through conversion stages.",
            "Compare stage counts and the previous-stage conversion percentages."),
            FunnelChart(funnel, "Stage", "Players", "From visit to purchase", static=True)),
        tab("Scatter", note("Explore the relationship between campaign spend and return.",
            "Hover the points. Spend is x, revenue is y, and bubble size represents installs."),
            ScatterChart(scatter, "Spend", "Revenue", size="Installs", color_by="Channel", title="Campaign spend and revenue",
                         x_format="currency", y_format="currency", static=True)),
    ], id="tour_pattern_views", label="Pattern example")]


def _layouts():
    frame = daily_frame().groupby("Day", as_index=False)[["Revenue", "Spend", "Installs", "Orders"]].sum()
    frame["Margin"] = frame.Revenue - frame.Spend
    frame["Sessions"] = frame.Installs * 3
    # Explicit per-chart palette overrides distinguish different metrics. Shared
    # category mappings above serve the separate purpose of consistent identity.
    colors = ["#4e79a7", "#f28e2b", "#59a14f", "#b07aa1", "#e15759", "#76b7b2"]
    charts = [LineChart(frame, "Day", name, name, static=True, height="compact", colors=[color],
                        header_summary={"stat": "mean", "label": "Daily avg", "format": fmt}, y_format=fmt)
              for name, color, fmt in zip(["Revenue", "Spend", "Installs", "Orders", "Margin", "Sessions"], colors,
                                         ["currency", "currency", "number", "number", "currency", "number"])]
    return [
        note("Fit related small multiples to the available panel width.",
             "Resize the page: six 160px charts move from three columns to one while keeping titles and summaries.",
             "Distinct metric colors, compact height and optional summaries."),
        Grid(charts, columns=3, min_width=260, card=True),
        Section("Split panes and alternate navigation", [
            note("Keep unequal panels together until they need to stack.",
                 "Switch the searchable view selector, then compare the side list on desktop and phone."),
            SplitPane(
                Panel([TabGroup([tab("Revenue", LineChart(frame, "Day", "Revenue", "Revenue", static=True, height="compact")),
                                 tab("Spend", LineChart(frame, "Day", "Spend", "Spend", static=True, height="compact"))],
                                id="tour_selector", mode="dropdown", label="Metric view")]),
                Panel([TabGroup([tab("Compact context", MiniKpi("Period", "31 days", "plain")),
                                 tab("Formatting", MiniKpi("Full total", 1250345, full_number=True, stacked=True))],
                                id="tour_sidebar", mode="sidebar", label="Context view")]), ratio="2:1"),
            note("Standalone controls are building blocks for author-supplied interactions.",
                 "Search the dropdown or choose a button. These two controls demonstrate selection only; they do not filter the charts."),
            Dropdown("tour_standalone", ["Revenue", "Installs", "Sessions"], label="Standalone metric choice"),
            Toggle("tour_toggle", ["Absolute", "Percentage"]),
        ], collapsible=True, default_collapsed=True),
    ]


def add_sections(ctx):
    ctx.set_header(subtitle="Six component families · practical examples · native controls")
    ctx.add_section("Explore the framework", [_navigation()])
    for (key, title, _), content in zip(CATEGORIES, [_metrics(), _trends(), _categories(), _tables(), _patterns(), _layouts()]):
        owners = [c for c in content if isinstance(c, (DataSource, FilterBar))]
        views = [c for c in content if not isinstance(c, (DataSource, FilterBar))]
        ctx.add_section(title, owners + views)
    ctx.add_section("Go from components to a report", [RawHTML(html='''
<div class="tour-footer"><p>The <a href="../player-overview/">Player Overview</a>, <a href="../monetization/">Monetization</a>
and <a href="../conversion/">Conversion</a> demos combine these features into complete reports.</p>
<p>For authoring recipes and defaults, use the <a href="../report-upgrade/">visual upgrade guide</a>.
Detailed parameter, lifecycle and loading-state examples remain in the framework source and API guides.</p></div>''')])
