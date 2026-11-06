"""KPI component examples wired into the shared showcase by its generator."""

import pandas as pd

from trellum.components import DataSource, FilterBar, KpiCard, KpiRow, MiniKpi, RawHTML


def add_sections(ctx):
    """Add deterministic KPI examples without reading a configured source."""
    ctx.add_section("KPI cards and compact metrics", [
        RawHTML(html=(
            "<p>Cards keep their standard height and spacing. Centered content, "
            "full values, explicit precision and stacked MiniKpi labels are "
            "optional settings.</p>"
        )),
        KpiRow([
            KpiCard(label="Revenue · left", value=184230.5, format="currency",
                    delta="+4.8%", delta_direction="up"),
            KpiCard(label="Revenue · centered", value=184230.5, format="currency",
                    delta="+4.8%", delta_direction="up", alignment="center"),
            KpiCard(label="Compact total", value=1250345, format="number"),
            KpiCard(label="Full total", value=1250345, format="number",
                    full_number=True),
            KpiCard(label="Compact precision", value=1250345, format="number",
                    precision=2),
            KpiCard(label="Precise ratio", value=1.23456, format="ratio",
                    precision=3),
        ], static=True),
        MiniKpi(label="Conversion", value=0.0842, format="percent"),
        MiniKpi(label="Revenue / player", value=6.475, format="currency",
                stacked=True),
    ])

    frame = pd.DataFrame([
        {"platform": platform, "revenue": revenue, "players": players,
         "sessions": sessions}
        for platform, revenue, players, sessions in (
            ("iOS", 18420, 6200, 19400),
            ("Android", 12750, 4800, 15300),
            ("Web", 6380, 2100, 7450),
            ("Console", 4920, 1650, 5300),
        )
    ])
    ctx.add_section("Filterable KPI row", [
        DataSource("showcase_kpi", frame),
        FilterBar("showcase_kpi", frame, filters=[
            {"column": "platform", "label": "KPI examples: platform",
             "type": "toggle"},
        ]),
        RawHTML(html=(
            "<p>Optional mobile_collapse=4 reveals the remaining cards on "
            "narrow screens. All values update when platform changes, including "
            "cards that are currently collapsed.</p>"
        )),
        KpiRow([
            {"label": "Revenue", "format": "currency", "agg": "sum",
             "column": "revenue"},
            {"label": "Players", "format": "number", "agg": "sum",
             "column": "players"},
            {"label": "Sessions", "format": "number", "agg": "sum",
             "column": "sessions"},
            {"label": "Revenue / player", "format": "currency",
             "agg": "ratio", "numerator": "revenue", "denominator": "players",
             "precision": 2},
            {"label": "Revenue (full)", "format": "currency", "agg": "sum",
             "column": "revenue", "full_number": True},
            {"label": "Selected platforms", "format": "number", "agg": "count",
             "full_number": True},
        ], dataset_id="showcase_kpi", mobile_collapse=4),
    ])
