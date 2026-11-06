"""Strict supplied-data contracts, aggregation and effective snapshot checks."""

import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trellum.components import DataSource, FilterBar, LiveDataSource, ScopedDataSource
from trellum.components.base import RenderContext
from trellum.components import SankeyChart
from trellum.components.charts.sankey import SAFE_INTEGER
from trellum.themes import DEFAULT_THEME
from trellum.validation.checks.sankey import check_sankey_charts
from trellum.validation.result import ValidationResult


def examples():
    path = Path(__file__).resolve().parents[1] / "demo/reports/component-showcase/examples_sankeys.py"
    spec = importlib.util.spec_from_file_location("sankey_examples", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def chart(df=None, **options):
    e = examples()
    return SankeyChart(e.paths_frame() if df is None else df, **{
        "mode": "paths", "stages": e.STAGES, "weight": "players", "unit": "players", **options})


def edge_chart(df=None, **options):
    e = examples()
    return chart(e.edges_frame() if df is None else df, **{
        "mode": "edges", "source": "from_category", "target": "to_category",
        "source_stage": "from_week", "target_stage": "to_week", **options})


@pytest.mark.parametrize("value", [True, np.bool_(False), "12", None, pd.NA, float("nan"),
                                        float("inf"), -1, .5, SAFE_INTEGER + 1, 10**400])
def test_reject_bad_count_weights(value):
    df = examples().paths_frame().astype(object)
    df.loc[0, "players"] = value
    with pytest.raises(ValueError):
        chart(df)


@pytest.mark.parametrize("options", [
    {"mode": "guessed"}, {"weight_kind": "money"}, {"unit": " "}, {"weight": "missing"},
    {"height": True}, {"height": 0}, {"height": 1201}, {"min_width": 639.5}, {"min_width": 2401},
    {"source": "w1"}, {"stages": []}, {"stages": [{"key": "a", "label": "A"}]},
    {"stages": [{"key": "w1", "label": "A"}, {"key": "w1", "label": "B"}]},
    {"stages": [{"key": [], "label": "A"}, {"key": "b", "label": "B"}]},
    {"weight": "w1"}, {"header_context": []}, {"dataset_id": " "}, {"static": 1},
])
def test_reject_bad_schema(options):
    with pytest.raises(ValueError):
        chart(**options)


def test_quantity_counts_aggregation_and_ambiguity():
    c = chart()
    full = c.snapshot(c.df)
    assert full["total"] == 1000 and full["boundaries"] == [1000] * 3
    ios = c.snapshot(c.df[c.df.platform == "iOS"])
    assert ios["total"] == 500 and ios["boundaries"] == [500] * 3
    assert len(full["paths"]) == 8 and len(full["edges"]) == 20
    duplicate = pd.concat([c.df, c.df.iloc[:1]])
    assert chart(duplicate).snapshot(duplicate)["paths"][("Low",) * 4] == 520
    df = c.df.copy()
    df["players"] = df.players / 10
    assert chart(df, weight_kind="quantity").snapshot(df)["total"] == 100
    for values in ([1e15] * 8, [math.inf] * 8):
        df["players"] = values
        with pytest.raises(ValueError):
            chart(df, weight_kind="quantity")
    stages = [{"key": f"w{i}", "label": "Repeated label"} for i in range(1,5)]
    a = pd.DataFrame([["Low","Mid","Mid","Low",1], ["High","Mid","Mid","High",1]], columns=["w1","w2","w3","w4","players"])
    b = a.copy(); b["w4"] = ["High","Low"]
    am, bm = chart(a, stages=stages).snapshot(a), chart(b, stages=stages).snapshot(b)
    assert am["edges"] == bm["edges"] and am["paths"] != bm["paths"]


@pytest.mark.parametrize("from_stage,to_stage", [("w2","w1"),("w1","w3"),("w2","w2"),("bad","w2"),(None,"w2"),(1,"w2")])
def test_edges_require_adjacent_declared_stages(from_stage, to_stage):
    df = examples().edges_frame().astype(object)
    df.loc[0, ["from_week","to_week"]] = [from_stage,to_stage]
    with pytest.raises(ValueError): edge_chart(df)


def test_edges_unbalanced_zeros_and_null_are_exact():
    c = edge_chart(); graph = c.snapshot(c.df)
    assert graph["total"] is None and graph["paths"] == {}
    assert graph["boundaries"] == [100,50,15]
    assert graph["edges"][(2,"Purchase","No purchase")] == 0
    assert graph["edges"][(1,"Consider",None)] == 5
    with pytest.raises(ValueError): edge_chart(source=None)
    with pytest.raises(ValueError): edge_chart(target="from_category")


@pytest.mark.parametrize("category", [True, 0, [], {"label":"A"}, "", " ", "a"*513])
def test_reject_non_scalar_or_blank_categories(category):
    df = examples().paths_frame().astype(object)
    df.at[0,"w1"] = category
    with pytest.raises(ValueError): chart(df)


def test_typed_identity_unavailable_literal_and_escaping():
    df = pd.DataFrame([[None,"Unavailable",1], ["Unavailable",None,2],
                       ["__proto__","constructor",3], ["a|b","c",0], ["a","b|c",1]], columns=["a","b","players"])
    c = chart(df, stages=[{"key":"a","label":"Same"},{"key":"b","label":"Same"}],
              title='<img src=x onerror="bad()">', header_context="<script>bad()</script>")
    m = c.snapshot(df)
    assert len(m["paths"]) == 5 and len(m["edges"]) == 5 and m["total"] == 7
    markup = c.render_html(RenderContext(DEFAULT_THEME))
    assert "<script>bad()" not in markup and "<img src=x" not in markup
    assert "&lt;img" in markup and "&lt;script&gt;" in markup
    df.loc[0,"players"] = None
    with pytest.raises(ValueError): c.render_html(RenderContext(DEFAULT_THEME))


@pytest.mark.heavy
def test_resource_and_numeric_bounds():
    frame = examples().paths_frame()
    with pytest.raises(ValueError, match="10000"):
        chart(pd.concat([frame]*1251))
    df = pd.DataFrame({"a":[str(i) for i in range(101)], "b":[str(i) for i in range(101)], "players":1})
    stages = [{"key":"a","label":"A"},{"key":"b","label":"B"}]
    with pytest.raises(ValueError,match="200 nodes"):
        chart(df, stages=stages)
    # >1000 edges with only 64 distinct nodes.
    df = pd.DataFrame([[str(i),str(j),1] for i in range(32) for j in range(32)],columns=["a","b","players"])
    with pytest.raises(ValueError,match="1000 edges"):
        chart(df, stages=stages)
    # >1000 paths, only 40 nodes and 300 edges.
    df = pd.DataFrame([[str(i),str(j),str(k),str(i),1] for i in range(11) for j in range(10) for k in range(10)],columns=["w1","w2","w3","w4","players"])
    with pytest.raises(ValueError,match="1000 complete paths"):
        chart(df)
    df = pd.concat([frame.iloc[:1]]*9000,ignore_index=True)
    df["w1"] = "é"*512
    with pytest.raises(ValueError,match="4 MiB"):
        chart(df)
    frame["players"] = SAFE_INTEGER
    with pytest.raises(ValueError,match="grouped total"):
        chart(frame)


def test_effective_source_scopes_mutation_and_validator():
    df = examples().paths_frame()
    ctx = RenderContext(DEFAULT_THEME)
    DataSource("base",df).render_html(ctx)
    ScopedDataSource("child",parent="base").render_html(ctx)
    c = chart(dataset_id="child")
    c.render_html(ctx)
    cfg = list(ctx.component_data.values())[-1]
    assert cfg["domain"] == ["High","Low","Mid","VIP"] and "rows" not in cfg
    result = ValidationResult("test")
    ds = DataSource("base",df.copy())
    scope = ScopedDataSource("child",parent="base")
    ds_map = {"base":ds,"child":scope}
    check_sankey_charts([(c,"Scoped")],ds_map,result)
    assert result.summary["pass"] == 1
    ds.df.loc[0,"players"] = -1
    check_sankey_charts([(c,"Scoped")],ds_map,result)
    assert result.summary["fail"] == 1
    invalid = df.copy(); invalid["players"] = "10"
    DataSource("base",invalid).render_html(ctx)
    with pytest.raises(ValueError): c.render_html(ctx)
    with pytest.raises(ValueError): chart(dataset_id="absent").render_html(ctx)
    c.height = False
    with pytest.raises(ValueError): c.render_html(ctx)


def test_live_chunked_and_live_connected_sources_rejected():
    df = examples().paths_frame()
    c = chart(dataset_id="snapshot")
    live = LiveDataSource("live",query="lookup",df=df)
    ds = DataSource("snapshot",df)
    fb = FilterBar("live",df,filters=[{"column":"platform"}],propagate_to={"snapshot":{"platform":"platform"}})
    result = ValidationResult("test")
    check_sankey_charts([(c,"x"),(fb,"x")],{"live":live,"snapshot":ds},result)
    assert result.summary["fail"] == 1 and "live-connected" in result.checks[0].message
    for base in (live, DataSource("snapshot",df,chunk_by="month")):
        result = ValidationResult("test")
        check_sankey_charts([(chart(dataset_id=base.id),"x")],{base.id:base},result)
        assert result.summary["fail"] == 1
    ctx = RenderContext(DEFAULT_THEME)
    ds.render_html(ctx)
    ctx.register("live",{"type":"live_data_source","dataset_id":"live"})
    fb.render_html(ctx)
    with pytest.raises(ValueError,match="live-connected"): c.render_html(ctx)
