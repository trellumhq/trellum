"""Framework components — use these instead of writing custom HTML/JS.

MANDATORY: Check this list BEFORE writing any RawHTML with Chart.js code.
If a component below can do it, you MUST use it.

Charts:   LineChart, AreaChart, BarChart, StackedBar, ComboChart,
          DoughnutChart, HeatmapChart, FunnelChart, TreemapChart, ScatterChart
KPIs:     KpiRow, KpiCard, MiniKpi
Tables:   DataTable, ComparisonTable, PivotTable
Controls: Toggle, Dropdown, TabGroup, DateSelector
Layout:   Section, Panel, Grid, SplitPane
Filter:   DataSource + FilterBar (every component needs dataset_id=)
          ScopedDataSource for section-local filters (inherits parent filters)
          LiveDataSource + FilterBar (param bindings) for a declared live
          query -- see ctx.declare_live_query and LiveDataSource's docstring
Advanced: RawHTML (ONLY when no component above can express the visualization)
"""

from trellum.components.ab_compare import ABCompare
from trellum.components.ab_methodology import ABMethodologyNote
from trellum.components.base import Component, RenderContext
from trellum.components.charts import (
    AreaChart,
    BarChart,
    ComboChart,
    DoughnutChart,
    FunnelChart,
    HeatmapChart,
    LineChart,
    ScatterChart,
    StackedBar,
    TreemapChart,
)
from trellum.components.controls import DateSelector, Dropdown, TabGroup, Toggle
from trellum.components.filterable import (
    DataSource,
    FilterBar,
    LiveDataSource,
    ScopedDataSource,
)
from trellum.components.header import ReportHeader
from trellum.components.kpis import KpiCard, KpiRow, MiniKpi
from trellum.components.layout import Grid, Panel, RawHTML, Section, SplitPane, Visible
from trellum.components.tables import ComparisonTable, DataTable, PivotTable

__all__ = [
    "Component", "RenderContext",
    "LineChart", "AreaChart", "BarChart", "StackedBar", "ComboChart", "DoughnutChart", "HeatmapChart", "FunnelChart", "TreemapChart", "ScatterChart",
    "DataTable", "ComparisonTable", "PivotTable",
    "KpiCard", "KpiRow", "MiniKpi",
    "Toggle", "Dropdown", "TabGroup", "DateSelector",
    "Section", "Panel", "Grid", "SplitPane",
    "RawHTML", "Visible",
    "ABCompare", "ABMethodologyNote",
    "ReportHeader",
    "DataSource", "FilterBar", "ScopedDataSource", "LiveDataSource",
]
