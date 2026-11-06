"""Author-defined table targets in original units; deterministic supplied rows."""

from decimal import Decimal

import pandas as pd

from trellum.components import ComparisonTable, DataSource, DataTable, FilterBar, RawHTML


def shading_fixture():
    """One distinct observation per campaign/platform cohort; no repeated totals."""
    return pd.DataFrame({
        "Cohort": ["Search launch", "Social launch", "Partner launch", "Search return", "Social return", "Partner return"],
        "Platform": ["iOS", "Android", "iOS", "Android", "iOS", "Android"],
        "Installs": [1200, 960, 720, 480, 240, 0],
        "CPI": [1.799, 1.801, 1.80, 0, None, 2.15],
        "Recovery D30 (%)": [104.5, 93.2, 100, 0, None, 112.3],
        "Retention D7 (%)": [24.9, 26.2, 25, 0, None, 28.1],
        "Spend": [2158.8, 1728.96, 1296, 0, None, 0],
    })


def add_sections(ctx):
    frame = shading_fixture()
    targets = {"CPI": {"target": Decimal("1.80"), "direction": "lower"},
               "Recovery D30 (%)": {"target": 100, "direction": "higher"},
               "Retention D7 (%)": {"target": 25, "direction": "higher"}}
    ctx.add_section("Table shading — snapshot comparison", [
        RawHTML(html="<p>Identical synthetic cohort rows with the default appearance and optional "
                "author targets. CPI 1.799 and 1.801 both display as 1.80 but fall on opposite "
                "sides of the exact 1.80 target. Measured zero and unavailable input stay distinct.</p>"),
        DataTable(frame, "Default · no threshold fills", static=True),
        DataTable(frame, "Opt-in · declared column targets", static=True, cell_shading=targets),
    ])
    ctx.add_section("Table shading — filterable cohort metrics", [
        DataSource("showcase_shading_cohorts", frame),
        FilterBar("showcase_shading_cohorts", frame, filters=[
            {"column": "Platform", "label": "Threshold examples: platform", "type": "toggle"},
        ]),
        RawHTML(html="<p>Each row is a distinct campaign/platform cohort. Recovery and retention "
                "are supplied on a 0–100 percentage scale; their targets are 100 and 25, "
                "not fractions. The explicit CPI text rule and spend bars coexist with fills. "
                "Search, sorting and CSV keep their original values and population.</p>"),
        DataTable(frame, "Cohort metrics · original units", dataset_id="showcase_shading_cohorts",
                  cell_shading=targets, sortable=True, searchable=True, bar_column=6,
                  conditional_formats={"CPI": [{"op": ">", "value": 0, "color": "var(--text-warning)"}]}),
    ])
    exact = pd.DataFrame({
        "Observation": ["Below boundary", "At boundary", "Above boundary"],
        "Exact integer": [9007199254740992, 9007199254740993, 9007199254740994],
        "Exact decimal": [Decimal("1.79999999999999999999"), Decimal("1.80"), Decimal("1.80000000000000000001")],
    })
    ctx.add_section("Table shading — exact snapshot boundaries", [
        RawHTML(html="<p>Integer and Decimal snapshot values retain their supplied precision. "
                "Equality meets the target; no display rounding or tolerance changes the boundary.</p>"),
        DataTable(exact, "Lossless snapshot targets", static=True, sortable=True, cell_shading={
            "Exact integer": {"target": 9007199254740993, "direction": "higher"},
            "Exact decimal": {"target": Decimal("1.80"), "direction": "lower"},
        }),
    ])
    comparison = pd.DataFrame({"Cohort": ["Mature search", "Mature social", "Mature partner"],
                               "Previous": [.23, .27, .25], "Current": [.25, .2499, .281]})
    ctx.add_section("Table shading — homogeneous retention comparison", [
        RawHTML(html="<p>Both weekly columns contain retention fractions. A target of 0.25 means "
                "25%, evaluated before percent formatting. Targets apply to the whole column; "
                "use separate tables when rows have different units.</p>"),
        ComparisonTable(comparison, "D7 retention · mature weekly cohorts", "Current", ["Previous"],
                        static=True, conditional_formats={}, cell_shading={
                            "Current": {"target": Decimal("0.25"), "direction": "higher"}},
                        comparison_context={"metric_column": "Cohort", "period_labels": {
                            "Previous": "Previous · 21–27 Oct", "Current": "Current · 28 Oct–3 Nov"},
                            "metrics": {name: {"format": "percent", "precision": 1, "change_measure": "value"}
                                        for name in comparison["Cohort"]}}),
    ])
