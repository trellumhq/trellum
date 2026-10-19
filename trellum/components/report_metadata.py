"""Freshness and optional operator-provided metadata for a report."""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from trellum.assets import load_css, load_js
from trellum.components.base import Component, RenderContext


@dataclass
class ReportMetadata(Component):
    """The small metadata strip displayed immediately below a report header."""

    values: dict[str, Any] = field(default_factory=dict)
    _component_type: str = field(default="report_metadata", init=False, repr=False)

    def display_items(self) -> list[list[str]]:
        """``[label, display value]`` pairs, in order, for every showable value.

        Also written to data.json as ``_header_meta`` so auto-refresh can
        update the strip without a page reload.
        """
        items = []
        for label, value in self.values.items():
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if not isinstance(label, str) or not label.strip():
                continue
            if not isinstance(value, (str, int, float, bool, date, datetime)):
                continue
            if isinstance(value, float) and not math.isfinite(value):
                continue
            display_value = value.isoformat() if isinstance(value, date) else str(value)
            items.append([label, display_value])
        return items

    def render_html(self, ctx: RenderContext) -> str:
        items = [
            '<span class="fw-report-metadata-item">'
            f'<span class="fw-report-metadata-label">{html.escape(label)}</span>'
            f'<span class="fw-report-metadata-value">{html.escape(display_value)}</span>'
            '</span>'
            for label, display_value in self.display_items()
        ]
        return (
            '<div class="fw-report-metadata" id="fwReportMetadata">'
            '<span class="fw-freshness" id="fwFreshness"></span>'
            f'{"".join(items)}'
            '</div>'
        )

    @classmethod
    def css(cls) -> str:
        return load_css("components/report_metadata.css")

    @classmethod
    def client_js(cls) -> str:
        return load_js("components/report_metadata.js")
