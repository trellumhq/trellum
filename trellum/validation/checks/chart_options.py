"""LineChart sizing and header-summary options.

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

    for comp, sec in comps:
        if _comp_name(comp) != "LineChart":
            continue
        for check_id, message in line_option_problems(comp):
            result.fail(check_id, message, component="LineChart", section=sec)
