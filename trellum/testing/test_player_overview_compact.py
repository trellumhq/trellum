"""Pinned native Player Overview builds; no second report implementation.

The comparison imports the original generator from the recorded Git commit,
then supplies identical synthetic rows to each generator's query_df boundary.
Set TRELLUM_PLAYER_OVERVIEW_ARTIFACTS to retain native builds and browser captures.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from trellum.components import DataSource, Grid, LineChart
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.runner.discovery import discover_report
from trellum.themes import THEME_REGISTRY
from trellum.validation import validate_report

BASELINE = "99e5c9420c796843ff477566710156ae11049f5e"
ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "trellum/demo/reports/player-overview"
TRENDS = [
    ("dau", "Daily Active Users", "number"),
    ("new_users", "New Users", "number"),
    ("sessions", "Sessions", "number"),
    ("transactions", "Transactions", "number"),
    ("iap_revenue", "IAP Revenue", "currency"),
    ("ad_revenue", "Ad Revenue", "currency"),
]


def pinned_frames():
    """Independent daily facts, memberships and cohorts, including old chunks."""
    daily = []
    for day, date in enumerate(pd.date_range("2026-07-09", "2026-11-06")):
        for p, platform in enumerate(("ios", "android")):
            for t, title in enumerate(("Orbit", "Nova")):
                for c, country in enumerate(("United States", "Germany")):
                    for s, tier in enumerate(("Minnow", "Whale")):
                        n = 1 + day + p * 7 + t * 11 + c * 13 + s * 17
                        daily.append(dict(
                            event_date=date.strftime("%Y-%m-%d"), platform=platform,
                            title=title, country=country,
                            region="Americas" if c == 0 else "Europe", spender_tier=tier,
                            dau=100 + n, new_users=3 + n % 17, sessions=200 + 3 * n,
                            payers=5 + n % 11, transactions=9 + n % 23,
                            iap_revenue=round(10 + n * 1.23, 2),
                            ad_revenue=round(2 + n * .17, 2), is_promo=int(day % 7 == 0)))
    segments = []
    for p, platform in enumerate(("ios", "android")):
        for t, title in enumerate(("Orbit", "Nova")):
            for mask in range(1, 8):
                for bit, segment in enumerate(("Payer", "Social", "Competitive")):
                    if mask & (1 << bit):
                        segments.append(dict(user_id=f"{p}-{t}-{mask}",
                                             platform=platform, title=title, segment=segment))
    cohorts = []
    for month, date in enumerate(pd.date_range("2025-12-01", "2026-11-01", freq="MS")):
        for p, platform in enumerate(("ios", "android")):
            for t, title in enumerate(("Orbit", "Nova")):
                for offset, fraction in ((1, .7), (7, .4), (30, .1)):
                    size = 300 + month * 9 + p * 13 + t * 17
                    cohorts.append(dict(cohort_date=date.strftime("%Y-%m-%d"),
                                        title=title, platform=platform, day_number=offset,
                                        cohort_size=size, retained_users=int(size * fraction)))
    return {"main": pd.DataFrame(daily), "segments": pd.DataFrame(segments),
            "cohorts": pd.DataFrame(cohorts)}


def build_pair(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_NOW", "2026-11-06T12:00:00Z")
    monkeypatch.setenv("FW_PROJECT_ROOT", str(ROOT / "trellum/demo"))
    report = discover_report(str(REPORT))
    current = sys.modules[report.__module__]
    # Read Git objects only: never creates or changes a checkout.
    source = subprocess.run(
        ["git", "show", f"{BASELINE}:trellum/demo/reports/player-overview/generator.py"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout
    baseline_file = tmp_path / "baseline_generator.py"
    baseline_file.write_text(source)
    spec = importlib.util.spec_from_file_location(
        current.__package__ + ".baseline_generator", baseline_file)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    frames = pinned_frames()
    builds = {}
    artifact_root = Path(os.environ.get("TRELLUM_PLAYER_OVERVIEW_ARTIFACTS", tmp_path))
    for name, module in (("before", baseline), ("after", current)):
        calls = []

        def query(connection, sql, params=None):
            assert connection == "isolated-fixture"
            calls.append((sql, params))
            key = {module.queries.PLAYER_DAILY: "main",
                   module.queries.PLAYER_SEGMENTS: "segments",
                   module.queries.RETENTION: "cohorts"}[sql]
            return frames[key].copy(deep=True)

        monkeypatch.setattr(module, "query_df", query)
        output = artifact_root / name
        config = report.load_config(str(REPORT))
        ctx = ReportContext(config=config, slug="player-overview", output_dir=str(output),
                            theme=THEME_REGISTRY[config["theme"]])
        monkeypatch.setattr(ctx, "get_connection", lambda name: "isolated-fixture")
        module.PlayerOverviewReport().generate(ctx)
        details = compute_details(ctx, mock_data=True)
        result = validate_report(ctx, details=details)
        render_report(ctx, str(output), auto_refresh=False, validation=result, details=details)
        result.write(str(output))
        (output / "_details.json").write_text(json.dumps(details, indent=2, default=str))
        copy_vendor_tree(output)
        builds[name] = dict(ctx=ctx, calls=calls, result=result, output=output,
                            data=json.loads((output / "data.json").read_text()))
    return builds, frames


@pytest.fixture
def player_builds(tmp_path, monkeypatch):
    return build_pair(tmp_path, monkeypatch)


def _same_component(before, after):
    assert type(before) is type(after)
    for field in dataclasses.fields(before):
        left, right = getattr(before, field.name), getattr(after, field.name)
        if (field.name == "trend_line" and isinstance(after, LineChart)
                and after.title.startswith("ARPDAU by Platform")):
            assert left is None
            assert right == {"method": "linear", "series": "each"}
            continue
        if isinstance(left, pd.DataFrame):
            pd.testing.assert_frame_equal(left, right)
        else:
            assert left == right, field.name


def test_native_generator_preserves_sources_queries_kpis_and_existing_components(player_builds):
    builds, frames = player_builds
    before, after = builds["before"], builds["after"]
    assert before["calls"] == after["calls"]
    assert [params for _, params in after["calls"]] == [
        {"start_date": "2026-07-09", "end_date": "2026-11-06"}, None,
        {"start_date": "2025-11-06", "end_date": "2026-11-06"}]
    sections = {s["title"]: s for s in after["ctx"].sections}
    assert list(sections) == ["", "Overview", "Daily Trends", "Engagement", "Efficiency",
                              "Revenue", "Where the Money Comes From",
                              "Which Behaviours Overlap", "Do They Come Back", "Yesterday"]
    assert len(sections["Overview"]["components"][0].kpis) == 8
    for original in before["ctx"].sections:
        revised = sections[original["title"]]
        assert len(original["components"]) == len(revised["components"])
        for left, right in zip(original["components"], revised["components"]):
            _same_component(left, right)
        collapsed = original["title"] in ("Engagement", "Revenue")
        assert revised["collapsible"] is collapsed
        assert revised["default_collapsed"] is collapsed
    grid = sections["Daily Trends"]["components"][1]
    assert isinstance(grid, Grid) and grid.columns == 3
    assert grid.show_titles and grid.show_controls and len(grid.children) == 6
    for chart, (column, title, fmt) in zip(grid.children, TRENDS):
        assert isinstance(chart, LineChart)
        assert (chart.x, chart.y, chart.title, chart.dataset_id, chart.height) == (
            "event_date", column, title, "main", "compact")
        assert chart.header_summary == dict(stat="mean", series=0, label="Daily avg", format=fmt)
        assert chart.stack_by is None and chart.y_format == fmt
    # Source populations/grains and the chunked retention contract remain independent.
    sources = {c.id: c for s in after["ctx"].sections for c in s["components"]
               if isinstance(c, DataSource)}
    assert set(sources) == {"main", "segments", "cohorts"}
    assert sources["cohorts"].chunk_by == "month"
    assert sources["cohorts"].default_chunks == 3
    for key, frame in frames.items():
        assert len(sources[key].df) == len(frame)
    def metric_definitions(data):
        return {key: {k: v for k, v in value.items() if k != "component_ids"}
                for key, value in data["_metrics"].items()}
    assert metric_definitions(before["data"]) == metric_definitions(after["data"])
    for key in ("main", "segments", "cohorts"):
        assert before["data"][f"_ds_{key}"] == after["data"][f"_ds_{key}"]
    assert before["data"]["_revenue_map"] == after["data"]["_revenue_map"]
    assert before["data"]["_player_segments"] == after["data"]["_player_segments"]
    chunks = sorted(after["output"].glob("data_chunk_cohorts_*.json"))
    assert len(chunks) == 9
    for path in chunks:
        assert path.read_bytes() == (before["output"] / path.name).read_bytes()
    daily = frames["main"]
    efficiency = sections["Efficiency"]["components"][0].kpis
    assert [k["value"] for k in efficiency] == [
        round(float(daily.iap_revenue.sum() / daily.dau.sum()), 4),
        round(float(daily.iap_revenue.sum() / daily.transactions.sum()), 2)]
    retention = sections["Do They Come Back"]["components"][2].kpis
    for card, offset in zip(retention[2:], (1, 7)):
        cohort = frames["cohorts"].query("day_number == @offset")
        assert card["value"] == round(float(100 * cohort.retained_users.sum()
                                            / cohort.cohort_size.sum()), 1)


def test_native_validation_adds_no_diagnostics(player_builds):
    builds, _ = player_builds
    before, after = (builds[name]["result"] for name in ("before", "after"))
    # Compare full diagnostics, including retained baseline warnings and suppressions.
    assert [dataclasses.asdict(c) for c in before.checks] == [
        dataclasses.asdict(c) for c in after.checks]
    assert after.summary["fail"] == 0
    assert after.summary["warn"] == 0
    assert before.groups_run == after.groups_run
