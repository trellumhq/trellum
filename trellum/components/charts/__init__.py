"""Chart components.

Split out of a single 2,552-line charts.py (1,192 once its JavaScript moved to
static/js/): eleven chart types and their shared base class in one namespace,
where finding the ~100 lines that define a funnel meant scrolling past a
674-line client_js method. One module per chart family now; the names below are
what report authors import, and they are unchanged.
"""

from trellum.components.charts.bar import BarChart, ComboChart, StackedBar
from trellum.components.charts.base import _ChartBase
from trellum.components.charts.doughnut import DoughnutChart
from trellum.components.charts.funnel import FunnelChart
from trellum.components.charts.heatmap import HeatmapChart
from trellum.components.charts.line_area import AreaChart, LineChart
from trellum.components.charts.scatter import ScatterChart
from trellum.components.charts.treemap import TreemapChart

__all__ = [
    "LineChart", "AreaChart", "BarChart", "StackedBar", "ComboChart",
    "DoughnutChart", "HeatmapChart", "FunnelChart", "TreemapChart",
    "ScatterChart",
    # Not a component: the shared base, imported by validation and by any
    # project defining its own chart type.
    "_ChartBase",
]
