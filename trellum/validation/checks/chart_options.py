"""Chart sizing and header-summary options.

The component refuses a bad ``height`` or ``header_summary`` when it is built.
These checks cover a chart whose options were changed afterwards, so a report
that reaches the validator with an unusable summary says so instead of
rendering a summary of the wrong series."""

from __future__ import annotations

from typing import Any

from trellum.validation.result import ValidationResult
from trellum.validation.walk import _comp_name


def _check_chart_options(comps: list[tuple[Any, str]], result: ValidationResult) -> None:
    from trellum.components.charts.line_area import line_option_problems
    from trellum.components.charts._funnel_summary import funnel_option_problems
    from trellum.components.charts.bar import stack_option_problems

    from trellum.components.charts._header_summary import header_summary_config, doughnut_summary_config

    for comp, sec in comps:
        if _comp_name(comp) == "StackedBar":
            for check_id, message in stack_option_problems(comp):
                if check_id == "stacked-bar-height-invalid":
                    result.fail(check_id, message, component="StackedBar", section=sec)
        if _comp_name(comp) == "FunnelChart":
            for check_id, message in funnel_option_problems(comp):
                result.fail(check_id, message, component="FunnelChart", section=sec)
        if _comp_name(comp) == "DoughnutChart":
            try:
                doughnut_summary_config(comp)
            except ValueError as exc:
                result.fail("chart-summary-invalid", str(exc),
                            component="DoughnutChart", section=sec)
        if _comp_name(comp) in ("BarChart", "AreaChart"):
            try:
                header_summary_config(comp)
            except ValueError as exc:
                result.fail("chart-summary-invalid", str(exc),
                            component=_comp_name(comp), section=sec)
        if _comp_name(comp) != "LineChart":
            continue
        for check_id, message in line_option_problems(comp):
            result.fail(check_id, message, component="LineChart", section=sec)
