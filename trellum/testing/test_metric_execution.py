"""Synthetic, deterministic contract checks; no provider or customer access."""
from copy import deepcopy
from dataclasses import replace

import pytest

from trellum.metric_execution import (
    MAX_ROWS,
    MetricUnavailable,
    execute_metric,
    make_binding,
    publish_bindings,
)
from trellum.metrics import Metric, compute_definition_hash, metrics_artifact


def freeze_clock(monkeypatch):
    from datetime import datetime, timezone

    from trellum import metric_execution
    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 3, 5, tzinfo=timezone.utc).astimezone(tz)
    from trellum.rendering import html_builder
    monkeypatch.setattr(metric_execution, "datetime", FrozenClock)
    monkeypatch.setattr(html_builder, "datetime", FrozenClock)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    freeze_clock(monkeypatch)


def fixture(agg="sum", time="sum", additive=True):
    semantics = dict(timezone="Europe/Amsterdam", unit="currency", currency="EUR", grain="day",
                     dimensions=["platform"], additive_dimensions=["platform"] if additive else [],
                     filters=["platform"], time_aggregation=time, null_policy="propagate", zero_denominator="null", max_age_seconds=31_536_000)
    metric = Metric(name="revenue", agg=agg, column="amount" if agg != "ratio" else None,
                    numerator="amount" if agg == "ratio" else None, denominator="users" if agg == "ratio" else None,
                    date_col="day", dataset="warehouse_daily", execution=semantics,
                    time_agg={"average": "avg", "last": "last"}.get(time))
    metric = replace(metric, definition_hash=compute_definition_hash(
        metric.name, metric.format, metric.agg, metric.column, metric.numerator, metric.denominator, metric.sql, metric.version))
    columns = {"day": "date", "platform": "os", "amount": "value"}
    if agg == "ratio":
        columns["users"] = "count"
    binding = dict(output_dataset="report_output", columns=columns,
                   coverage_start="2026-10-01", coverage_end="2026-10-03", source_revision="fixture-v1")
    entry = make_binding(metric, **binding, build_id="build-1", generated_at="2026-10-03T04:00:00Z")
    payload = {"_metrics": metrics_artifact({metric.name: ["kpi"]}, {metric.name: metric}),
               "components": {"kpi": {"dataset_id": "report_output", "kpis": [{"metric": metric.name, **metric.agg_spec()}]}},
               "_ds_report_output": {"_cols": ["date", "os", "value", "count"], "_data": [
                   ["2026-10-01", "ios", 10, 1], ["2026-10-01", "android", 20, 9],
                   ["2026-10-02", "ios", 30, 3], ["2026-10-02", "android", 40, 17]], "_dict": {}}}
    publish_bindings(payload, {metric.name: binding}, registry={metric.name: metric},
                     build_id="build-1", generated_at=entry["generated_at"])
    assert payload["_metric_execution"][metric.name] == entry
    return metric, entry, payload


def run(payload, entry, **kwargs):
    return execute_metric(payload, entry, **dict(start="2026-10-01", end="2026-10-03", **kwargs))


@pytest.mark.parametrize("agg,time,expected", [("sum", "sum", 100), ("sum", "last", 70),
    ("sum", "average", 50), ("ratio", "sum", 100 / 30), ("abssum", "sum", 100)])
def test_declared_calculations(agg, time, expected):
    _, entry, payload = fixture(agg, time)
    if agg == "abssum":
        payload["_ds_report_output"]["_data"][0][2] = -10
    assert run(payload, entry)["rows"] == [[expected]]


def test_ratio_zero_and_null_are_unavailable_not_zero():
    _, entry, payload = fixture("ratio")
    for row in payload["_ds_report_output"]["_data"]:
        row[-1] = 0
    assert run(payload, entry)["rows"] == [[None]]
    payload["_ds_report_output"]["_data"][0][-1] = None
    assert run(payload, entry)["rows"] == [[None]]


def test_filters_breakdown_and_truncation():
    _, entry, payload = fixture()
    assert run(payload, entry, filters={"platform": ["ios"]})["rows"] == [[40]]
    result = run(payload, entry, breakdown=["platform"], limit=1)
    assert result["truncated"] and result["rows"] == [["android", 60]]
    with pytest.raises(MetricUnavailable, match="Unknown filter"):
        run(payload, entry, filters={"platform": ["unknown"]})


@pytest.mark.parametrize("change,reason", [
    (lambda p: p["_ds_report_output"]["_data"].pop(), "missing periods"),
    (lambda p: p.update(_chunk_manifests={"report_output": {}}), "Chunked"),
    (lambda p: p["components"]["kpi"]["kpis"][0].update(column="count"), "overrides"),
    (lambda p: p["components"]["kpi"].update(dataset_id="warehouse_daily"), "conflicts"),
    (lambda p: p["_ds_report_output"]["_data"].append(p["_ds_report_output"]["_data"][0]), "Duplicate"),
])
def test_incomplete_or_conflicting_artifacts(change, reason):
    _, entry, payload = fixture()
    change(payload)
    with pytest.raises(MetricUnavailable, match=reason):
        run(payload, entry, breakdown=["platform"])


def test_no_inference_of_additivity_or_distinct_rollups():
    _, entry, payload = fixture(time="none", additive=False)
    with pytest.raises(MetricUnavailable, match="non-additive"):
        run(payload, entry)
    with pytest.raises(MetricUnavailable, match="roll up periods"):
        run(payload, entry, breakdown=["platform"])
    result = execute_metric(payload, entry, start="2026-10-01", end="2026-10-02", breakdown=["platform"])
    assert result["rows"] == [["android", 20], ["ios", 10]]


def test_semantic_fingerprint_supplements_legacy_hash():
    metric, entry, _ = fixture()
    other = replace(metric, execution={**metric.execution, "timezone": "UTC"})
    assert other.definition_hash == metric.definition_hash
    changed = make_binding(other, **{k: entry[k] for k in ("output_dataset", "columns", "coverage_start", "coverage_end", "source_revision", "build_id", "generated_at")})
    assert changed["fingerprint"] != entry["fingerprint"]


def test_input_bounds_and_missing_semantics():
    metric, entry, payload = fixture()
    payload["_ds_report_output"]["_data"] *= (MAX_ROWS // 4 + 1)
    with pytest.raises(MetricUnavailable, match="Input rows"):
        run(payload, entry)
    with pytest.raises(MetricUnavailable, match="semantics"):
        make_binding(replace(metric, execution=None), **{k: entry[k] for k in ("output_dataset", "columns", "coverage_start", "coverage_end", "source_revision", "build_id", "generated_at")})


def test_dictionary_encoded_dimensions_use_canonical_mappings():
    _, entry, payload = fixture()
    data = payload["_ds_report_output"]
    data["_dict"] = {"os": ["ios", "android"]}
    for row in data["_data"]:
        row[1] = data["_dict"]["os"].index(row[1])
    assert run(payload, entry, breakdown=["platform"])["rows"] == [
        ["android", 60], ["ios", 40],
    ]
    assert run(payload, entry, filters={"platform": ["ios"]})["rows"] == [[40]]
    data["_data"].pop()
    with pytest.raises(MetricUnavailable, match="missing periods") as exc:
        run(payload, entry)
    assert exc.value.status == "incomplete"


@pytest.mark.parametrize("code", [True, -1, 2, "0", None])
def test_invalid_dictionary_codes_are_rejected_before_calculation(code):
    _, entry, payload = fixture()
    data = payload["_ds_report_output"]
    data["_dict"] = {"os": ["ios", "android"]}
    for row in data["_data"]:
        row[1] = 0
    data["_data"][0][1] = code
    with pytest.raises(MetricUnavailable, match="Invalid dictionary encoding"):
        run(payload, entry)


def test_invalid_binding_does_not_break_ordinary_report():
    metric, _, payload = fixture()
    payload.pop("_metric_execution")
    publish_bindings(payload, {metric.name: {"output_dataset": "warehouse_daily"}}, registry={metric.name: metric}, build_id="new", generated_at="now")
    assert "_metric_execution" not in payload and "_metrics" in payload


def test_real_report_build_publishes_explicit_binding(tmp_path, monkeypatch):
    import json

    import pandas as pd
    import yaml

    from trellum.components import DataSource, KpiRow
    from trellum.metrics import invalidate_metrics_cache
    from trellum.rendering.html_builder import render_report
    from trellum.report import ReportContext

    metric, entry, _ = fixture()
    monkeypatch.setattr("trellum.project._project_root_override", str(tmp_path))
    (tmp_path / "metrics.yaml").write_text(yaml.safe_dump(dict(version=1, datasets={
        "warehouse_daily": dict(provider="fixture:daily", time=dict(column="day", grain="day"), dimensions=["platform"])
    }, metrics=[dict(name="revenue", agg="sum", column="amount", dataset="warehouse_daily", execution=metric.execution)])))
    invalidate_metrics_cache()
    binding = {k: entry[k] for k in ("output_dataset", "columns", "coverage_start", "coverage_end", "source_revision")}
    binding["columns"] = {"day": "day", "platform": "platform", "amount": "amount"}
    out = tmp_path / "output"
    ctx = ReportContext(dict(name="Synthetic", metric_bindings={"revenue": binding}), "synthetic", str(out))
    frame = pd.DataFrame({"day": ["2026-10-01", "2026-10-02"], "platform": ["ios", "ios"], "amount": [10, 20]})
    ctx.add_section("", [DataSource(id="report_output", df=frame), KpiRow(kpis=[{"metric": "revenue"}], dataset_id="report_output")])
    try:
        render_report(ctx, auto_refresh=False)
        data = json.loads((out / "data.json").read_text())
        meta = json.loads((out / "_meta.json").read_text())
        bound = data["_metric_execution"]["revenue"]
        assert bound["build_id"] == meta["metric_execution_build"]
        assert bound["registry_dataset"] != bound["output_dataset"]
        assert run(data, bound)["rows"] == [[30]]
    finally:
        invalidate_metrics_cache()


def test_freshness_is_explicit_and_enforced():
    from datetime import datetime, timezone
    _, entry, payload = fixture()
    with pytest.raises(MetricUnavailable, match="freshness") as exc:
        run(payload, entry, now=datetime(2028, 1, 1, tzinfo=timezone.utc))
    assert exc.value.status == "stale"


def test_input_and_output_byte_budgets():
    from trellum.metric_execution import MAX_INPUT_BYTES
    _, entry, payload = fixture()
    oversized = deepcopy(payload)
    oversized["extra"] = "x" * MAX_INPUT_BYTES
    with pytest.raises(MetricUnavailable, match="byte budget"):
        run(oversized, entry)
    for row in payload["_ds_report_output"]["_data"]:
        row[1] += "x" * 20_000
    with pytest.raises(MetricUnavailable, match="byte budget"):
        run(payload, entry, breakdown=["platform"])


@pytest.mark.parametrize("agg,time", [("sum", "sum"), ("ratio", "sum"),
                                      ("sum", "last"), ("sum", "average")])
@pytest.mark.parametrize("breakdown", [[], ["platform"]])
def test_missing_dimension_day_never_disappears_into_rollup(agg, time, breakdown):
    _, entry, payload = fixture(agg, time)
    payload["_ds_report_output"]["_data"].pop()
    with pytest.raises(MetricUnavailable, match="missing periods") as exc:
        run(payload, entry, breakdown=breakdown)
    assert exc.value.status == "incomplete"
    # A complete explicitly selected series remains usable.
    assert run(payload, entry, filters={"platform": ["ios"]})["completeness"] == "complete"


def test_tuple_missing_entire_requested_period_is_not_zero():
    _, entry, payload = fixture()
    payload["_ds_report_output"]["_data"].pop()
    with pytest.raises(MetricUnavailable, match="missing periods"):
        execute_metric(payload, entry, start="2026-10-02", end="2026-10-03")


def test_typed_filters_and_breakdowns_keep_bool_int_string_separate():
    _, entry, payload = fixture()
    payload["_ds_report_output"]["_data"] = [
        [day, dim, value, 1] for day in ("2026-10-01", "2026-10-02")
        for dim, value in [(True, 1), (1, 10), ("1", 100)]]
    for dim, expected in [(True, 2), (1, 20), ("1", 200)]:
        assert run(payload, entry, filters={"platform": [dim]})["rows"] == [[expected]]
    rows = run(payload, entry, breakdown=["platform"])["rows"]
    assert {(type(row[0]), row[1]) for row in rows} == {(bool, 2), (int, 20), (str, 200)}
    payload["_ds_report_output"]["_data"] = [
        row for row in payload["_ds_report_output"]["_data"] if type(row[1]) is bool]
    with pytest.raises(MetricUnavailable, match="Unknown filter"):
        run(payload, entry, filters={"platform": [1]})


def test_null_and_zero_dimension_cells_are_explicit_not_missing():
    _, entry, payload = fixture()
    payload["_ds_report_output"]["_data"][-1][2] = 0
    assert run(payload, entry)["rows"] == [[60]]
    payload["_ds_report_output"]["_data"][-1][2] = None
    assert run(payload, entry)["rows"] == [[None]]


def test_full_dimension_tuple_completeness_precedes_partial_breakdown():
    metric, entry, payload = fixture("ratio")
    metric = replace(metric, execution={**metric.execution,
        "dimensions": ["platform", "country"], "additive_dimensions": ["platform", "country"]})
    entry = make_binding(metric, **{**{k: entry[k] for k in (
        "output_dataset", "columns", "coverage_start", "coverage_end", "source_revision", "build_id", "generated_at")},
        "columns": {**entry["columns"], "country": "country"}})
    data = payload["_ds_report_output"]
    data["_cols"].append("country")
    # Both countries exist for each platform, with deliberately unequal weights.
    data["_data"] = [row + [country] for row in data["_data"] for country in ("NL", "US")]
    payload["_metric_execution"][metric.name] = entry
    assert run(payload, entry, breakdown=["platform"])["rows"] == [["android", 60 / 26], ["ios", 10]]
    data["_data"].pop()
    with pytest.raises(MetricUnavailable, match="missing periods"):
        run(payload, entry, breakdown=["platform"])


def test_correlated_dimensions_do_not_require_fabricated_cartesian_cells():
    metric, entry, payload = fixture()
    metric = replace(metric, execution={**metric.execution,
        "dimensions": ["platform", "country"], "additive_dimensions": ["platform", "country"]})
    entry = make_binding(metric, **{**{k: entry[k] for k in (
        "output_dataset", "columns", "coverage_start", "coverage_end", "source_revision", "build_id", "generated_at")},
        "columns": {**entry["columns"], "country": "country"}})
    payload["_ds_report_output"]["_cols"].append("country")
    for row in payload["_ds_report_output"]["_data"]:
        row.append("NL" if row[1] == "ios" else "US")
    payload["_metric_execution"][metric.name] = entry
    assert run(payload, entry)["rows"] == [[100]]


def test_period_and_byte_budgets_reject_work_before_calculation():
    from trellum.metric_execution import MAX_INPUT_BYTES
    _, entry, payload = fixture()
    with pytest.raises(MetricUnavailable, match="at most 366"):
        execute_metric(payload, entry, start="2026-10-01", end="2027-11-01")
    payload["unused"] = "x" * MAX_INPUT_BYTES
    with pytest.raises(MetricUnavailable, match="byte budget"):
        run(payload, entry)
