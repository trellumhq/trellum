"""Portable visual comparisons using genuine historical framework captures."""

import base64
import html
from pathlib import Path

import pandas as pd

import trellum
from trellum import BaseReport
from trellum.components import DataTable, Grid, Panel, RawHTML, TabGroup

_IMAGES = Path(trellum.__file__).resolve().parent / "docs/images/report-upgrade"


def _capture(version, component, width=900):
    filename = f"{version}-{component}-{width}.png"
    encoded = base64.b64encode((_IMAGES / filename).read_bytes()).decode("ascii")
    alt = html.escape(f"{version}: {component}, captured at {width}px", quote=True)
    return RawHTML(
        f'<img src="data:image/png;base64,{encoded}" alt="{alt}" '
        'style="display:block;width:100%;height:auto;border-radius:8px">'
    )


def _code(source):
    return RawHTML('<pre style="white-space:pre-wrap;overflow-wrap:anywhere;'
                   'padding:12px;border:1px solid var(--border-color);border-radius:8px;'
                   'font-size:var(--font-size-label)"><code>'
                   + html.escape(source) + '</code></pre>')


def _comparison(component, label, source):
    return TabGroup([
        {"label": "Before and after rebuild", "content": [Grid([
            Panel([_capture("v0.9.5", component)], title="Real v0.9.5"),
            Panel([_capture("current", component)], title="Current defaults after rebuild"),
        ], columns=2, min_width=320)]},
        {"label": "Explicit additions", "content": [
            Panel([_capture("opt-in", component)], title="Current with authored options"),
            _code(source),
        ]},
    ], id=f"upgrade-{component}", label=f"{label} comparison")


class ReportUpgradeReport(BaseReport):
    def generate(self, ctx):
        ctx.set_header(subtitle="Real v0.9.5 → current implementation · same synthetic observations")
        ctx.add_section("Start here", [RawHTML(
            '<p>Rebuild an existing report to receive the current component defaults. '
            'Previously generated HTML keeps its old appearance until you rebuild '
            'and deploy the complete output. Explicit additions below need report '
            'code changes; upgrading never enables those options automatically.</p>'
            '<p>These are genuine native captures from the v0.9.5 tag and the current '
            'implementation at the time this guide was captured, using identical '
            'synthetic values. They are fixed screenshots, not live charts. Wide '
            'captures use a 900px viewport in Trellum Dark; phone captures use '
            '390px in Trellum Light. The screenshots keep their captured theme.</p>'
            '<p>Jump to <a style="color:var(--accent-blue)" href="#sec-automatic-and-authored">the upgrade matrix</a>, '
            '<a style="color:var(--accent-blue)" href="#sec-tables">tables</a>, <a style="color:var(--accent-blue)" href="#sec-doughnuts">doughnuts</a>, '
            '<a style="color:var(--accent-blue)" href="#sec-line-charts">line charts</a>, '
            '<a style="color:var(--accent-blue)" href="#sec-dated-events">dated events</a> or '
            '<a style="color:var(--accent-blue)" href="#sec-layouts-and-views">layouts and views</a>. '
            'Return to the <a style="color:var(--accent-blue)" href="../component-showcase/">component showcase</a> '
            'for live examples, or the <a style="color:var(--accent-blue)" href="../">demo gallery</a>.</p>'
        ), _code("python -m trellum.run reports/<slug> --no-serve\n"
                 "python -m trellum validate reports/<slug>")])

        matrix = pd.DataFrame([
            ("DataTable", "Compact stripes, numeric alignment, matching/CSV counts",
             "Column formats, targets, totals, row limit, pinning"),
            ("DoughnutChart", "170px ring; readable category values and shares",
             "Center total, explicit units and precision"),
            ("LineChart", "220px plot; adaptive dots and dated event labels; explicit unsplit dataset colors honored",
             "160px compact / 320px roomy, summaries, curves, forecasts"),
            ("Grid / SplitPane", "Panel-width layout; chart titles/controls stay visible",
             "Theme color cycle, column maximum, pane minimums, intentional title/control omission"),
            ("Dropdown / TabGroup", "Searchable choices and wrapping underlined tabs",
             "Meaningful labels and sidebar mode"),
        ], columns=["Component", "Automatic after rebuild", "Explicit report options"])
        ctx.add_section("Automatic and authored", [
            DataTable(matrix, "Upgrade matrix", static=True),
            RawHTML('<p>Source values and metric definitions remain yours. Existing '
                    'line observations and null gaps keep their meaning. New totals '
                    'and forecasts are never inferred from column names. This guide '
                    'covers a representative set of visible changes.</p>'),
        ])

        ctx.add_section("Tables", [RawHTML(
            '<p>Compact striped rows and right-aligned numbers arrive on rebuild. '
            'Matching and CSV counts explain the population. Search and sorting '
            'now run before the display limit: max_rows limits visible rows, while '
            'search and CSV cover all matching supplied rows. Static CSV keeps raw '
            'numeric precision; numeric sorting can differ from rounded text order.</p>'
            '<p>The explicit example declares currency units, a target of 14,000 and '
            'an additive total. Targets and totals do not appear by default. Totals '
            'cover matching delivered rows before the display limit. The code uses a '
            'channels DataFrame with Channel and numeric Revenue columns; import '
            'the named classes from trellum.components.</p>'
        ), _comparison("table", "Table", '''DataTable(channels, "Revenue by channel", static=True,
    sortable=True, searchable=True,
    column_formats={"Revenue": {"format": "currency", "precision": 0}},
    totals={"Revenue": {"agg": "sum", "format": "currency", "precision": 0}},
    cell_shading={"Revenue": {"target": 14000, "direction": "higher"}})''')])

        ctx.add_section("Doughnuts", [RawHTML(
            '<p>The rebuilt default uses a compact ring beside a wrapping HTML '
            'legend with all category values and shares. The center stays empty '
            'until center_total=True is authored. Totals require additive quantities '
            'and complete finite nonnegative values. Shares include legend-hidden '
            'categories; hiding an arc changes visibility, not filters or CSV.</p>'
            '<p>On phones the legend stacks under the ring, so the complete component '
            'can grow taller. Existing signed/incomplete arc calculations remain '
            'unsupported for part-to-whole interpretation.</p>'
        ), TabGroup([
            {"label": "Wide comparison", "content": [_comparison("doughnut", "Doughnut", '''DoughnutChart(channels, "Channel", "Revenue", "Revenue by channel",
    static=True, center_total=True, center_label="Revenue",
    value_format="currency", precision=0)''')]},
            {"label": "Phone comparison", "content": [Grid([
                Panel([_capture("v0.9.5", "doughnut", 390)], title="Real v0.9.5 · phone"),
                Panel([_capture("current", "doughnut", 390)], title="Current default · phone"),
                Panel([_capture("opt-in", "doughnut", 390)], title="Explicit total · phone"),
            ], columns=3, min_width=280)]},
        ], id="upgrade-doughnut-width", label="Doughnut capture size")])

        ctx.add_section("Line charts", [RawHTML(
            '<p>The default plot is now 220px at every viewport width. In the '
            '900px comparison the old plot is 260px; at 390px it was 180px. '
            'Automatic dots adapt to point spacing without dropping observations. '
            'Straight lines remain the default.</p>'
            '<p>Existing explicit LineChart.colors lists now apply to unsplit '
            'dataset-bound value/ratio series, where they previously were ignored. '
            'Omitted or empty lists and missing entries keep theme fallback; active '
            'category splits keep category mapping precedence. This correction is '
            'not illustrated in the frozen screenshots.</p>'
            '<p>The explicit example chooses a 160px plot, a daily average header '
            'and a curve. Its daily DataFrame has ISO day and numeric revenue columns. '
            'Curves interpolate and can overshoot; they are not '
            'averages or forecasts. The eight underlying daily values stay identical.</p>'
        ), _comparison("line", "Line", '''LineChart(daily, "day", "revenue", "Daily revenue", static=True,
    height="compact", tension=0.35,
    header_summary={"stat": "mean", "series": 0,
                    "label": "Daily avg", "format": "number"})''')])

        ctx.add_section("Dated events", [RawHTML(
            '<p>On rebuild, ordinary LineCharts put dated event labels above the '
            'plot. Tap a label for its exact name, original dates and type. '
            'Overlapping labels become an event count with local details. Phones '
            'also show event names beneath the plot. This adds context around '
            'the current 220px plot; measured values and event dates stay the same.</p>'
            '<p>The comparison uses two identical supplied events. The old phone '
            'labels overlap; the current name key keeps both readable. '
            'Use event_labels="full" for in-plot labels or '
            'event_labels="grouped" for the earlier numbered-marker presentation. '
            'Other chart families keep their existing labels.</p>'
        ), TabGroup([
            {"label": label, "content": [Grid([
                Panel([_capture("v0.9.5", "events", width)], title="Real v0.9.5 · events"),
                Panel([_capture("current", "events", width)], title="Current default · readable events"),
            ], columns=2, min_width=280)]}
            for label, width in (("Wide comparison", 900), ("Phone comparison", 390))
        ], id="upgrade-events", label="Dated event capture size")])

        ctx.add_section("Layouts and views", [RawHTML(
            '<p>Automatic grids retain chart titles and each chart family’s height. '
            'Grid columns is now a maximum even when min_width is supplied; '
            'explicitly set columns=4 if an old automatic grid relied on fitting '
            'four tiles. SplitPane stacks according to its own panel width.</p>'
            '<p>Ordinary tabs become wrapping underlined navigation. Sidebar mode '
            'is an author choice and becomes a searchable selector in narrow '
            'panels. Existing control state and URL keys are preserved.</p>'
        ), TabGroup([
            {"label": "Grid defaults", "content": [Grid([
                Panel([_capture("v0.9.5", "layout")], title="Real v0.9.5 grid"),
                Panel([_capture("current", "layout")], title="Current grid after rebuild"),
            ], columns=2, min_width=320), _code("Grid(children, columns=2, min_width=320)")]},
            {"label": "Optional tile colors", "content": [RawHTML(
                '<p>Color cycling is off by default. Enable it on a Grid to give '
                'direct single-metric charts successive active-theme colors. '
                'Filtering and wrapping keep each tile’s slot. Explicit chart '
                'colors win; category splits and multi-series charts keep their '
                'own colors. The same six metric series and plot sizes appear below.</p>'
            ), Grid([
                Panel([_capture("current", "grid-colors")], title="Current · cycle omitted"),
                Panel([_capture("opt-in", "grid-colors")], title="Current · theme cycle enabled"),
            ], columns=2, min_width=320),
                _code('Grid(charts, columns=3, min_width=260, color_cycle="theme")')]},
            {"label": "Tabs and sidebar", "content": [Grid([
                Panel([_capture("v0.9.5", "views")], title="Real v0.9.5 tabs"),
                Panel([_capture("current", "views")], title="Current tabs after rebuild"),
            ], columns=2, min_width=320),
                Panel([_capture("opt-in", "views")], title="Explicit sidebar mode"),
                _code('TabGroup(views, "report_views", mode="sidebar", label="Report view")')]},
        ], id="upgrade-layout", label="Layout comparison"), RawHTML(
            '<p>After rebuilding your own reports, check desktop and phone widths, '
            'long legends, automatic grids and custom CSS. Keep source/query bounds '
            'for large table populations; max_rows is not a payload-size limit.</p>'
        )])
