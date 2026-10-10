"""Unit tests for trellum.validation -- check IDs and severity levels."""

import pandas as pd

from trellum.report import ReportContext
from trellum.validation import validate_report


def _make_ctx(**config_overrides) -> ReportContext:
    """Build a minimal ReportContext for testing (no DB, no file I/O)."""
    config = {
        "slug": "test-report",
        "name": "Test Report",
        "studio": "gop3",
        "category": "Test",
        "description": "Unit test fixture",
        "schedule": {"cron": "*/5 * * * *"},
    }
    config.update(config_overrides)
    return ReportContext(
        config=config,
        slug=config["slug"],
        output_dir="/tmp/test-validation",
    )


def _sample_df():
    return pd.DataFrame({
        "event_date": ["2026-01-01", "2026-01-02", "2026-01-03"],
        "revenue": [100.0, 200.0, 150.0],
        "platform": ["ios", "android", "ios"],
    })


def _find_checks(result, check_id=None, level=None):
    """Filter checks by id and/or level."""
    out = result.checks
    if check_id:
        out = [c for c in out if c.id == check_id]
    if level:
        out = [c for c in out if c.level == level]
    return out


def test_invalid_report_metadata_labels_and_values_are_warned():
    ctx = _make_ctx()
    ctx.set_header(meta={"": "bad label", "Object": {"not": "plain text"}})

    result = validate_report(ctx)

    hits = _find_checks(result, check_id="report-metadata-invalid", level="warn")
    assert len(hits) == 1


# ---------------------------------------------------------------------------
# DataSource / FilterBar / dataset_id wiring
# ---------------------------------------------------------------------------


def test_missing_dataset_id_fails():
    from trellum.components import DataSource, FilterBar, LineChart

    df = _sample_df()
    ctx = _make_ctx()
    ctx.add_section("", [
        DataSource("ds1", df),
        FilterBar("ds1", df, filters=[{"column": "platform", "label": "Platform"}]),
    ])
    ctx.add_section("Charts", [
        LineChart(df, x="event_date", y="revenue", title="Rev"),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="component-missing-dataset-id", level="fail")
    assert len(hits) >= 1, f"Expected fail for missing dataset_id, got: {result.checks}"


def test_dataset_id_present_passes():
    from trellum.components import DataSource, FilterBar, LineChart

    df = _sample_df()
    ctx = _make_ctx()
    ctx.add_section("", [
        DataSource("ds1", df),
        FilterBar("ds1", df, filters=[{"column": "platform", "label": "Platform"}]),
    ])
    ctx.add_section("Charts", [
        LineChart(df, x="event_date", y="revenue", title="Rev", dataset_id="ds1"),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="component-missing-dataset-id")
    assert len(hits) == 0, f"Unexpected missing-dataset-id checks: {hits}"


def test_orphan_filterbar_fails():
    from trellum.components import FilterBar

    df = _sample_df()
    ctx = _make_ctx()
    ctx.add_section("", [
        FilterBar("nonexistent", df, filters=[{"column": "platform", "label": "Platform"}]),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="filterbar-orphan", level="fail")
    assert len(hits) >= 1, f"Expected fail for orphan FilterBar, got: {result.checks}"


def test_filterbar_with_matching_ds_passes():
    from trellum.components import DataSource, FilterBar

    df = _sample_df()
    ctx = _make_ctx()
    ctx.add_section("", [
        DataSource("ds1", df),
        FilterBar("ds1", df, filters=[{"column": "platform", "label": "Platform"}]),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="filterbar-orphan")
    assert len(hits) == 0, f"Unexpected filterbar-orphan checks: {hits}"


def test_slider_on_non_numeric_column_fails():
    from trellum.components import DataSource, FilterBar

    df = _sample_df()
    ctx = _make_ctx()
    ctx.add_section("", [
        DataSource("ds1", df),
        FilterBar("ds1", df, filters=[
            {"column": "platform", "label": "Platform", "type": "slider"},
        ]),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="filter-slider-not-numeric", level="fail")
    assert len(hits) >= 1, f"Expected fail for slider on a string column, got: {result.checks}"


def test_slider_on_numeric_column_passes():
    from trellum.components import DataSource, FilterBar

    df = _sample_df()
    ctx = _make_ctx()
    ctx.add_section("", [
        DataSource("ds1", df),
        FilterBar("ds1", df, filters=[
            {"column": "revenue", "label": "Revenue", "type": "slider"},
        ]),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="filter-slider-not-numeric")
    assert len(hits) == 0, f"Unexpected filter-slider-not-numeric checks: {hits}"


# ---------------------------------------------------------------------------
# Ordinal slider ("values" present -- sliders over ordered categories)
# ---------------------------------------------------------------------------


def test_slider_ordinal_values_on_non_numeric_column_passes():
    """The whole point of `values`: no numeric-dtype requirement."""
    from trellum.components import DataSource, FilterBar

    df = _sample_df()
    ctx = _make_ctx()
    ctx.add_section("", [
        DataSource("ds1", df),
        FilterBar("ds1", df, filters=[
            {"column": "platform", "label": "Platform", "type": "slider",
             "values": ["android", "ios"]},
        ]),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="filter-slider-not-numeric", level="fail")
    assert len(hits) == 0, f"Unexpected fail for a well-formed ordinal slider: {hits}"


def test_slider_ordinal_values_not_a_list_of_strings_fails():
    from trellum.components import DataSource, FilterBar

    df = _sample_df()
    ctx = _make_ctx()
    ctx.add_section("", [
        DataSource("ds1", df),
        FilterBar("ds1", df, filters=[
            {"column": "platform", "label": "Platform", "type": "slider",
             "values": [1, 2, 3]},
        ]),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="filter-slider-not-numeric", level="fail")
    assert len(hits) >= 1, f"Expected fail for non-string values, got: {result.checks}"


def test_slider_ordinal_values_empty_list_fails():
    from trellum.components import DataSource, FilterBar

    df = _sample_df()
    ctx = _make_ctx()
    ctx.add_section("", [
        DataSource("ds1", df),
        FilterBar("ds1", df, filters=[
            {"column": "platform", "label": "Platform", "type": "slider",
             "values": []},
        ]),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="filter-slider-not-numeric", level="fail")
    assert len(hits) >= 1, f"Expected fail for an empty values list, got: {result.checks}"


def test_slider_ordinal_default_min_outside_values_fails():
    from trellum.components import DataSource, FilterBar

    df = _sample_df()
    ctx = _make_ctx()
    ctx.add_section("", [
        DataSource("ds1", df),
        FilterBar("ds1", df, filters=[
            {"column": "platform", "label": "Platform", "type": "slider",
             "values": ["android", "ios"], "default_min": "windows"},
        ]),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="filter-slider-not-numeric", level="fail")
    assert len(hits) >= 1, f"Expected fail for default_min outside values, got: {result.checks}"


def test_slider_ordinal_single_default_outside_values_fails():
    from trellum.components import DataSource, FilterBar

    df = _sample_df()
    ctx = _make_ctx()
    ctx.add_section("", [
        DataSource("ds1", df),
        FilterBar("ds1", df, filters=[
            {"column": "platform", "label": "Platform", "type": "slider",
             "values": ["android", "ios"], "mode": "single", "default": "windows"},
        ]),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="filter-slider-not-numeric", level="fail")
    assert len(hits) >= 1, f"Expected fail for single-mode default outside values, got: {result.checks}"


def test_slider_ordinal_value_never_occurring_warns_not_fails():
    from trellum.components import DataSource, FilterBar

    df = _sample_df()  # platform column only ever has 'ios' / 'android'
    ctx = _make_ctx()
    ctx.add_section("", [
        DataSource("ds1", df),
        FilterBar("ds1", df, filters=[
            {"column": "platform", "label": "Platform", "type": "slider",
             "values": ["android", "ios", "windows"]},
        ]),
    ])

    result = validate_report(ctx)
    warns = _find_checks(result, check_id="filter-slider-value-unused", level="warn")
    assert len(warns) >= 1, f"Expected a warn for a value absent from the data, got: {result.checks}"
    fails = _find_checks(result, check_id="filter-slider-not-numeric", level="fail")
    assert len(fails) == 0, f"An unused value must warn, not fail: {fails}"


def test_slider_ordinal_all_values_present_no_warning():
    from trellum.components import DataSource, FilterBar

    df = _sample_df()
    ctx = _make_ctx()
    ctx.add_section("", [
        DataSource("ds1", df),
        FilterBar("ds1", df, filters=[
            {"column": "platform", "label": "Platform", "type": "slider",
             "values": ["android", "ios"]},
        ]),
    ])

    result = validate_report(ctx)
    warns = _find_checks(result, check_id="filter-slider-value-unused")
    assert len(warns) == 0, f"Unexpected filter-slider-value-unused checks: {warns}"


# ---------------------------------------------------------------------------
# RawHTML lifecycle checks
# ---------------------------------------------------------------------------


def test_rawhtml_missing_renderall_warns():
    from trellum.components import RawHTML

    ctx = _make_ctx()
    ctx.add_section("Custom", [
        RawHTML(
            html='<canvas id="c"></canvas>',
            js=(
                "new Chart(document.getElementById('c'), {type:'line',data:{}}); "
                "window.addEventListener('fw-theme-change', function(){});"
            ),
        ),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="rawhtml-missing-renderall", level="warn")
    assert len(hits) >= 1, f"Expected warn for missing renderAll, got: {result.checks}"


def test_rawhtml_missing_theme_handler_warns():
    from trellum.components import RawHTML

    ctx = _make_ctx()
    ctx.add_section("Custom", [
        RawHTML(
            html='<canvas id="c"></canvas>',
            js=(
                "window.renderAll = function(){}; "
                "new Chart(document.getElementById('c'), {type:'line',data:{}});"
            ),
        ),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="rawhtml-no-theme-change-handler", level="warn")
    assert len(hits) >= 1, f"Expected warn for missing theme handler, got: {result.checks}"


def test_rawhtml_missing_destroy_warns():
    from trellum.components import RawHTML

    ctx = _make_ctx()
    ctx.add_section("Custom", [
        RawHTML(
            html='<canvas id="c"></canvas>',
            js=(
                "window.renderAll = function(){ "
                "new Chart(document.getElementById('c'), {type:'line',data:{}}); "
                "}; "
                "window.addEventListener('fw-theme-change', function(){});"
            ),
        ),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="rawhtml-no-chart-destroy", level="warn")
    assert len(hits) >= 1, f"Expected warn for missing .destroy(), got: {result.checks}"


def test_rawhtml_complete_no_warnings():
    from trellum.components import RawHTML

    ctx = _make_ctx()
    ctx.add_section("Custom", [
        RawHTML(
            html='<canvas id="c"></canvas>',
            js=(
                "var _c={}; "
                "function renderAll(){ "
                "if(_c.x) _c.x.destroy(); "
                "_c.x = new Chart(document.getElementById('c'), {type:'line',data:{}}); "
                "} "
                "window.renderAll = renderAll; "
                "window.addEventListener('fw-theme-change', function(){ renderAll(); });"
            ),
        ),
    ])

    result = validate_report(ctx)
    bad_ids = {"rawhtml-missing-renderall", "rawhtml-no-theme-change-handler", "rawhtml-no-chart-destroy"}
    hits = [c for c in result.checks if c.id in bad_ids]
    assert len(hits) == 0, f"Unexpected RawHTML warnings: {hits}"


# ---------------------------------------------------------------------------
# Scope checks
# ---------------------------------------------------------------------------


def test_empty_scope_warns():

    df = _sample_df()
    ctx = _make_ctx()
    ctx.set_scope("gop3")

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="scope-empty", level="warn")
    assert len(hits) >= 1, f"Expected warn for empty scope, got: {result.checks}"


def test_scope_with_content_passes():
    from trellum.components import LineChart

    df = _sample_df()
    ctx = _make_ctx()
    ctx.set_scope("gop3")
    ctx.add_section("Data", [
        LineChart(df, x="event_date", y="revenue", title="Rev"),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="scope-empty")
    assert len(hits) == 0, f"Unexpected scope-empty checks: {hits}"


# ---------------------------------------------------------------------------
# Duplicate DataSource
# ---------------------------------------------------------------------------


def test_duplicate_datasource_fails():
    from trellum.components import DataSource

    df = _sample_df()
    ctx = _make_ctx()
    ctx.add_section("", [
        DataSource("ds1", df),
        DataSource("ds1", df),
    ])

    result = validate_report(ctx)
    hits = _find_checks(result, check_id="duplicate-datasource-id", level="fail")
    assert len(hits) >= 1, f"Expected fail for duplicate DataSource id, got: {result.checks}"


# ---------------------------------------------------------------------------
# Grain-mismatch detection
# ---------------------------------------------------------------------------


def test_unit_indicator_column_is_not_a_grain_mismatch():
    """A literal 1-per-row column is a row counter, not a coarse merge.

    The pattern: `df["orders_n"] = 1` so that `sum` means "count" and ratio
    aggs can divide by it. It is constant within every x BY CONSTRUCTION and
    re-sums from row grain like any measure -- three demo reports tripped
    the warning on it before the exemption existed.
    """
    from trellum.reporting.diagnostics import _detect_static_columns

    df = pd.DataFrame({
        "event_date": ["2026-01-01"] * 3 + ["2026-01-02"] * 3,
        "revenue": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        "orders_n": [1, 1, 1, 1, 1, 1],
        # A genuinely mis-merged daily total: constant per date, not 1.
        "daily_total": [60.0, 60.0, 60.0, 150.0, 150.0, 150.0],
    })

    static = _detect_static_columns(df, "event_date",
                                    ["revenue", "orders_n", "daily_total"])
    assert "orders_n" not in static, "unit indicator flagged as grain mismatch"
    assert "daily_total" in static, "a real coarse merge must still be caught"
    assert "revenue" not in static


# ---------------------------------------------------------------------------
# Dynamic filterable types discovery
# ---------------------------------------------------------------------------


def test_filterable_types_auto_discovered():
    """Verify dynamic discovery matches expected filterable components."""
    from trellum.validation import _get_filterable_types

    expected = {
        "LineChart", "AreaChart", "BarChart", "StackedBar", "ComboChart",
        "DoughnutChart", "HeatmapChart", "ScatterChart", "FunnelChart",
        "TreemapChart", "KpiRow", "DataTable", "ComparisonTable", "PivotTable",
    }
    discovered = _get_filterable_types()
    assert discovered == expected, (
        f"Mismatch: extra={discovered - expected}, missing={expected - discovered}"
    )


# ---------------------------------------------------------------------------
# data.json schema contract test
# ---------------------------------------------------------------------------


def test_data_json_schema():
    """Snapshot: data.json must contain expected top-level metadata keys."""
    import json
    import os
    import tempfile

    from trellum.components import DataSource, FilterBar, LineChart
    from trellum.rendering.html_builder import render_report

    df = _sample_df()
    ctx = _make_ctx()
    ctx.add_section("", [
        DataSource("ds1", df),
        FilterBar("ds1", df, filters=[{"column": "platform", "label": "Platform"}]),
    ])
    ctx.add_section("Charts", [
        LineChart(df, x="event_date", y="revenue", title="Rev", dataset_id="ds1"),
    ])

    outdir = tempfile.mkdtemp(prefix="fw_schema_test_")
    render_report(ctx, outdir, data={"_events": []}, auto_refresh=False)

    json_path = os.path.join(outdir, "data.json")
    assert os.path.isfile(json_path), "data.json not generated"
    with open(json_path) as f:
        data = json.load(f)

    required_keys = {"_freshness", "_framework_version", "components"}
    missing = required_keys - set(data.keys())
    assert not missing, f"data.json missing required keys: {missing}"

    assert isinstance(data["_freshness"], dict)
    assert "generated_at" in data["_freshness"]
    assert "refresh_seconds" in data["_freshness"]
    assert isinstance(data["_framework_version"], str)
    assert isinstance(data["components"], dict)

    meta_path = os.path.join(outdir, "_meta.json")
    assert os.path.isfile(meta_path), "_meta.json not generated"
    with open(meta_path) as f:
        meta = json.load(f)

    meta_required = {
        "slug", "name", "framework_version", "framework_license",
        "framework_source_url", "last_run", "last_status",
    }
    meta_missing = meta_required - set(meta.keys())
    assert not meta_missing, f"_meta.json missing required keys: {meta_missing}"

    with open(os.path.join(outdir, "index.html"), encoding="utf-8") as f:
        html = f.read()
    assert "Trellum runtime" in html
    assert meta["framework_source_url"] in html
    assert 'id="fwReportMetadata"' in html
    assert 'id="fwFreshness"' in html
    assert os.path.isfile(os.path.join(outdir, "TRELLUM-LICENSE.txt"))


def test_metadata_strip_follows_custom_header(tmp_path):
    from trellum.components import ReportHeader
    from trellum.rendering.html_builder import render_report

    ctx = _make_ctx()
    ctx.set_header(meta={"Last event": "2026-10-10"})
    ctx.set_header_component(ReportHeader(name="Custom header"))
    render_report(ctx, str(tmp_path), auto_refresh=False)

    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    header_at = html.index("Custom header")
    metadata_at = html.index('id="fwReportMetadata"')
    freshness_at = html.index('id="fwFreshness"')
    container_at = html.index('class="fw-container')
    assert header_at < metadata_at < freshness_at < container_at
    assert "Last event" in html and "2026-10-10" in html
