"""Independent arithmetic and native-report validation for the Overview example."""

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from trellum.components import Grid, KpiRow, LineChart, Section
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.validation import validate_report


def examples():
    path = Path(__file__).parents[1] / "demo/reports/component-showcase/examples_overview.py"
    spec = importlib.util.spec_from_file_location("overview_examples", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected_facts(start=1, end=28, platform=None, acquisition=None):
    """Compute expectations independently from the authored DataFrame/components."""
    facts = []
    for day in range(start, end + 1):
        n = day - 1
        for p, name in enumerate(("iOS", "Android")):
            for a, source in enumerate(("Organic", "Paid search", "Partner")):
                if platform and name != platform or acquisition and source != acquisition:
                    continue
                count = 90 + (day - 1) * 11 + p * 29 + a * 37 + (n % 4) * 9
                dollars = count * (0.38 + a * 0.21 + p * 0.13) + (n % 5) * 0.97
                payers = 4 + n // 3 + p * 2 + a * 3
                revenue = payers * (6.10 + n * 0.17 + a * 0.53) + p * 0.31
                facts.append({"cohort_date": (date(2026, 10, 1) + timedelta(days=n)).isoformat(),
                              "platform": name, "acquisition_type": source,
                              "installs": count, "cost": dollars,
                              "d7_revenue": revenue, "d7_payers": payers})
    return facts


def expected_values(start=1, end=28, platform=None, acquisition=None):
    facts = expected_facts(start, end, platform, acquisition)
    days = sorted({r["cohort_date"] for r in facts})
    columns = ("cost", "installs", "d7_revenue", "d7_payers")
    totals = {col: sum(row[col] for row in facts) for col in columns}
    daily = [{col: sum(r[col] for r in facts if r["cohort_date"] == day) for col in columns}
             for day in days]
    vectors = [[row[col] for row in daily] for col in columns[:3]] + [
        [row[num] / row[den] for row in daily]
        for num, den in (("d7_revenue", "cost"), ("cost", "installs"), ("d7_revenue", "installs"))
    ]
    kpis = [totals[c] / len(days) for c in columns[:3]] + [
        totals[num] / totals[den] for num, den in (
            ("d7_revenue", "cost"), ("cost", "installs"),
            ("d7_revenue", "installs"), ("d7_payers", "installs"))
    ]
    return {"facts": facts, "days": days, "series": vectors,
            "means": [sum(v) / len(v) for v in vectors], "kpis": kpis}


def build_overview(output, *, reference=False):
    module = examples()
    ctx = ReportContext(config={"name": "Synthetic acquisition Overview", "studio": "test",
                                "category": "Components", "description": (
                                    "Daily synthetic acquisition cohorts by platform and acquisition type, "
                                    "with USD cost, installs, fully observed D7 revenue and payer share "
                                    "for October 1–28, 2026 at the November 5 UTC snapshot."),
                                "theme": "trellum light"},
                        slug="overview-composition", output_dir=str(output))
    module.add_sections(ctx)
    if reference:
        ctx.add_section("Current-runtime reference — ordinary KPI row", [
            KpiRow(module.kpi_specs(), dataset_id=module.DATASET_ID),
        ])
    result = validate_report(ctx, details=compute_details(ctx))
    assert result.summary["warn"] == result.summary["fail"] == 0, result.to_dict()
    output.mkdir(parents=True, exist_ok=True)
    (output / "validation.json").write_text(json.dumps(result.to_dict(), indent=2))
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    return ctx


def test_complete_observed_fixture_and_native_report(tmp_path):
    module = examples()
    frame = module.cohort_frame()
    assert frame.equals(module.cohort_frame())
    assert len(frame) == 28 * 2 * 3
    assert not frame.duplicated(["cohort_date", "platform", "acquisition_type"]).any()
    assert (frame.groupby("cohort_date").size() == 6).all()
    assert frame.cohort_date.min() == "2026-10-01" and frame.cohort_date.max() == "2026-10-28"
    assert date.fromisoformat(frame.cohort_date.max()) + timedelta(days=7) < date(2026, 11, 5)
    assert np.isfinite(frame.select_dtypes("number")).all().all()
    assert (frame.select_dtypes("number") > 0).all().all()
    assert (frame.d7_payers <= frame.installs).all()
    for actual, expected in zip(frame.to_dict("records"), expected_facts()):
        for key, value in expected.items():
            assert actual[key] == (pytest.approx(value) if isinstance(value, float) else value)
    ctx = build_overview(tmp_path / "native")
    assert frame.equals(ctx.sections[0]["components"][0].df)
    components = ctx.sections[0]["components"]
    row = next(c for c in components if isinstance(c, KpiRow))
    assert len(row.kpis) == 7 and row.mobile_collapse == 0
    grids = [c for c in components if isinstance(c, Grid)]
    assert len(grids[0].children) == 3
    assert all(not p.children[0].center_total for p in grids[0].children)
    assert grids[1].columns == 3 and grids[1].min_width == 280
    assert len(grids[1].children) == 6
    assert all(isinstance(c, LineChart) and c.height == "compact" and
               c.header_summary == {"stat": "mean", "label": "Daily avg"} for c in grids[1].children)
    detail = next(c for c in components if isinstance(c, Section))
    assert detail.collapsible and detail.default_collapsed
    assert not detail.children[1].show_totals and not detail.children[1].show_percentages


@pytest.mark.parametrize("start,end,platform,acquisition", [
    (1, 28, None, None), (1, 28, "iOS", None),
    (15, 28, "iOS", None), (15, 28, "iOS", "Paid search"),
])
def test_daily_means_and_period_ratios_have_independent_weighting(start, end, platform, acquisition):
    values = expected_values(start, end, platform, acquisition)
    frame = examples().cohort_frame()
    frame = frame[(frame.cohort_date >= f"2026-10-{start:02d}") &
                  (frame.cohort_date <= f"2026-10-{end:02d}")]
    if platform:
        frame = frame[frame.platform == platform]
    if acquisition:
        frame = frame[frame.acquisition_type == acquisition]
    specs = examples().kpi_specs()
    for spec, expected in zip(specs, values["kpis"]):
        actual = (frame[spec["column"]].sum() / frame.cohort_date.nunique()
                  if spec["agg"] == "avg_by_date" else
                  frame[spec["numerator"]].sum() / frame[spec["denominator"]].sum())
        assert actual == pytest.approx(expected)
    # All three daily ratio means differ from their period-weighted cards.
    for mean, period in zip(values["means"][3:], values["kpis"][3:6]):
        assert mean != pytest.approx(period, abs=1e-5)
    assert specs[4]["format"] == specs[5]["format"] == "currency"
