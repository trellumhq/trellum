"""Datasets and ``ctx.metrics()``: the SQL the shortcut emits, the grouping
rule, the errors that fire before any query, and the per-build memo.

The worked examples pin the exact SQL because the disk cache keys on it: a
reordered column is a cache miss for every report in the project.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from trellum.datasets import DataRequest, build_sql, dim_additive
from trellum.metrics import invalidate_metrics_cache, load_metrics_result
from trellum.report import ReportContext

YAML = """\
version: 1
datasets:
  daily:
    source: demo_db
    table: fact_daily
    time: {column: event_date, grain: day}
    dimensions: [title, platform, region, spender_tier]
    columns: {total_revenue: "iap_revenue + ad_revenue"}
    lookback: 90
  ret_d1:
    source: demo_db
    table: fact_retention
    where: "day_number = 1"
    time: {column: cohort_date, grain: day}
    dimensions: [title, platform]
  snapshot:
    provider: metrics_data:snapshot
    time: none
    dimensions: [segment]
metrics:
  - name: gross_revenue
    label: "Gross Revenue"
    description: "IAP plus ad revenue, gross of platform fees, daily grain from fact_daily."
    format: currency
    agg: sum
    column: total_revenue
    dataset: daily
  - name: dau
    label: "DAU"
    description: "Daily active users, summed across dims per day and averaged over the window."
    format: number
    agg: sum
    column: dau
    time_agg: avg
    dataset: daily
  - name: rows_seen
    label: "Rows Seen"
    description: "Fact rows surviving the filters -- a data-volume sanity metric, not a business one."
    format: number
    agg: count
    dataset: daily
  - name: retention_d1
    label: "Day-1 Retention"
    description: "Share of a cohort still active one day after install, from fact_retention."
    format: percent
    agg: ratio
    numerator: retained_users
    denominator: cohort_size
    dataset: ret_d1
  - name: players
    label: "Players"
    description: "Players per segment at the time of the snapshot, from the provider."
    format: number
    agg: sum
    column: players
    dataset: snapshot
"""

PROVIDER = """\
import pandas as pd
calls = 0
def snapshot(ctx, req):
    global calls
    calls += 1
    return pd.DataFrame({"segment": ["a", "a", "b"], "players": [1, 2, 3], "noise": [9, 9, 9]})
"""


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A project root with the yaml above, a provider module, and FW_NOW pinned."""
    from trellum.project import get_project_root, set_project_root

    (tmp_path / "metrics.yaml").write_text(YAML, encoding="utf-8")
    (tmp_path / "metrics_data.py").write_text(PROVIDER, encoding="utf-8")
    monkeypatch.setenv("FW_NOW", "2026-09-05T12:00:00Z")
    original = get_project_root()
    set_project_root(str(tmp_path))
    invalidate_metrics_cache()
    yield tmp_path
    set_project_root(original)
    invalidate_metrics_cache()


def _ctx(tmp_path: Path) -> ReportContext:
    return ReportContext(config={}, slug="t", output_dir=str(tmp_path / "out"))


def _ds(name):
    return load_metrics_result().datasets[name]


# ── build_sql: the two worked examples and the grouping rule ─────────────

def test_worked_example_one_groups_and_derives(project):
    req = DataRequest(frozenset({"total_revenue", "dau"}), ("platform",),
                      "2026-06-07", "2026-09-05", "day")
    assert build_sql(_ds("daily"), req) == (
        "SELECT event_date, platform, SUM(dau) AS dau, "
        "SUM(iap_revenue + ad_revenue) AS total_revenue "
        "FROM fact_daily WHERE event_date BETWEEN :start AND :end "
        "GROUP BY event_date, platform"
    )


def test_cohort_dataset_keeps_its_where(project):
    req = DataRequest(frozenset({"retained_users", "cohort_size"}), (),
                      "2026-06-07", "2026-09-05", "day")
    assert build_sql(_ds("ret_d1"), req) == (
        "SELECT cohort_date, SUM(cohort_size) AS cohort_size, "
        "SUM(retained_users) AS retained_users "
        "FROM fact_retention WHERE cohort_date BETWEEN :start AND :end "
        "AND (day_number = 1) GROUP BY cohort_date"
    )


def test_count_forces_native_grain(project):
    reg = load_metrics_result().metrics
    assert dim_additive([reg["gross_revenue"], reg["dau"]])
    assert not dim_additive([reg["gross_revenue"], reg["rows_seen"]])
    req = DataRequest(frozenset({"total_revenue"}), ("platform",),
                      "2026-06-07", "2026-09-05", "day")
    assert build_sql(_ds("daily"), req, rollup=False) == (
        "SELECT event_date, platform, iap_revenue + ad_revenue AS total_revenue "
        "FROM fact_daily WHERE event_date BETWEEN :start AND :end"
    )


# ── ctx.metrics: errors before any query ─────────────────────────────────

def test_unknown_dimension_names_the_declared_ones(project):
    with pytest.raises(ValueError, match="has no dimension 'country'; declared: "
                                         "title, platform, region, spender_tier"):
        _ctx(project).metrics(["gross_revenue"], by=["country"])


def test_ids_across_two_datasets_spell_out_the_split(project):
    with pytest.raises(ValueError, match="gross_revenue is on 'daily', retention_d1 is "
                                         "on 'ret_d1' -- call ctx.metrics once per dataset"):
        _ctx(project).metrics(["gross_revenue", "retention_d1"])


# ── the shortcut request and the memo ────────────────────────────────────

def test_shortcut_binds_the_window_and_memoizes(project, monkeypatch):
    import trellum.data

    seen: list[tuple[str, dict]] = []

    def fake_query_df(conn, sql, params=None, **kw):
        seen.append((sql, params))
        return pd.DataFrame({"event_date": ["2026-09-01"], "platform": ["ios"],
                             "dau": [10], "total_revenue": [1.0]})

    monkeypatch.setattr(trellum.data, "query_df", fake_query_df)
    ctx = _ctx(project)
    monkeypatch.setattr(ctx, "get_connection", lambda name: name)

    a = ctx.metrics(["gross_revenue", "dau"], by=["platform"])
    b = ctx.metrics(["dau", "gross_revenue"], by=["platform"])
    assert a is b, "same request, same build: one fetch"
    assert len(seen) == 1
    sql, params = seen[0]
    assert "GROUP BY event_date, platform" in sql
    # lookback: 90 from the dataset, anchored on FW_NOW.
    assert params == {"start": "2026-06-07", "end": "2026-09-05"}

    ctx.metrics(["gross_revenue"], window=7)
    assert len(seen) == 2 and seen[1][1]["start"] == "2026-08-29"


def test_provider_frame_is_projected_and_rolled_up(project):
    import sys

    ctx = _ctx(project)
    df = ctx.metrics(["players"], by=["segment"])
    assert list(df.columns) == ["segment", "players"]
    assert df.set_index("segment")["players"].to_dict() == {"a": 3, "b": 3}
    ctx.metrics(["players"], by=["segment"])
    assert sys.modules["metrics_data"].calls == 1
