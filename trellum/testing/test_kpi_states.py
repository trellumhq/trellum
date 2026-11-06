"""KPI declarations validate at rendering; new arithmetic has explicit units."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from trellum.assets import load_js
from trellum.components import KpiCard, KpiRow, RenderContext
from trellum.themes import DefaultTheme


def render(component):
    ctx = RenderContext(theme=DefaultTheme)
    component.render_html(ctx)
    return ctx.component_data


def test_declarations_keyword_only_mutations_and_binding():
    import inspect
    from dataclasses import dataclass

    for key in ("availability", "maturity", "comparison"):
        assert inspect.signature(KpiCard).parameters[key].kind == inspect.Parameter.KEYWORD_ONLY

    @dataclass
    class NamedCard(KpiCard):
        extra: str = "preserved"

    assert NamedCard("Revenue", 12).label == "Revenue"
    availability = {"state": "unavailable", "reason": "No estimate <b>yet</b>"}
    card = KpiCard("Revenue", None, availability=availability)
    cfg = next(iter(render(KpiRow([card], dataset_id="bound", static=True)).values()))
    assert cfg["type"] == "kpi_row_live"
    assert cfg["kpis"][0]["value"] is None
    assert cfg["kpis"][0]["availability"] == availability
    availability["state"] = "pending"
    with pytest.raises(ValueError, match="availability"):
        render(card)
    with pytest.raises(ValueError, match="availability"):
        render(KpiRow([card], dataset_id="bound"))


def test_claims_and_explicit_override_precedence(monkeypatch):
    from trellum.metrics import Metric

    registry = {"revenue": Metric(name="revenue", label="Claimed revenue", format="currency", agg="sum", column="revenue")}
    monkeypatch.setattr("trellum.metrics.load_metrics", lambda: registry)
    states = {"state": "unavailable", "reason": "Missing observation"}
    row = KpiRow([
        {"metric": "revenue", "availability": states},
        KpiCard(value=None, metric="revenue", availability=states),
        {"metric": "revenue", "label": "Override", "column": "net", "availability": states},
    ], dataset_id="source")
    specs = next(iter(render(row).values()))["kpis"]
    assert specs[0]["label"] == "Claimed revenue"
    assert specs[0]["column"] == "revenue"
    assert specs[1]["label"] == "Claimed revenue"
    assert specs[1]["format"] == "currency"
    assert specs[1]["value"] is None
    assert "agg" not in specs[1]
    assert specs[2]["label"] == "Override"
    assert specs[2]["column"] == "net"
    assert all(k["availability"] == states for k in specs)
    row.kpis[0]["availability"]["state"] = "typo"
    with pytest.raises(ValueError):
        render(row)


@pytest.mark.parametrize("field, spec", [
    ("availability", {}),
    ("availability", {"state": []}),
    ("availability", {"required_columns": ["v"], "empty": []}),
    ("availability", {"state": "unknown"}),
    ("availability", {"state": "available", "empty": "zero"}),
    ("availability", {"required_columns": ["v"]}),
    ("availability", {"required_columns": [True], "empty": "zero"}),
    ("availability", {"required_columns": ["v"], "empty": "zero", "ratio_denominator": "nonzero"}),
    ("maturity", {"state": "immature", "age": True, "horizon": 7}),
    ("maturity", {"state": "mature", "age": 3, "horizon": 7}),
    ("maturity", {"state": "immature", "age": 3, "horizon": 7, "observed": {"value": .08, "unit": "guess"}}),
    ("comparison", {"measure": "auto"}),
    ("comparison", {"measure": []}),
    ("comparison", {"measure": "relative_percent", "current": {"value": 3, "unit": []},
                    "baseline": {"value": 2, "unit": "number"}}),
    ("comparison", {"measure": "relative_percent", "current": {"agg": [], "column": "v", "unit": "number"},
                    "baseline": {"value": 1, "unit": "number"}}),
    ("comparison", {"measure": "relative_percent", "current": {"agg": "sum", "column": "rate", "unit": "percent"},
                    "baseline": {"value": 20, "unit": "percent"}}),
    ("comparison", {"measure": "relative_percent", "value": float("nan")}),
    ("comparison", {"measure": "relative_percent", "value": True}),
    ("comparison", {"measure": "relative_percent", "value": 3, "precision": True}),
    ("comparison", {"measure": "relative_percent", "value": 3, "typo": 1}),
    ("comparison", {"measure": "relative_percent", "current": {"agg": "avg", "column": "v", "unit": "number"},
                    "baseline": {"value": 1, "unit": "number"}}),
    ("comparison", {"measure": "relative_percent", "current": {"agg": "sum", "column": "v", "unit": "number", "denominator": "n"},
                    "baseline": {"value": 1, "unit": "number"}}),
    ("comparison", {"measure": "percentage_points", "current": {"value": 22, "unit": "number"},
                    "baseline": {"value": 20, "unit": "number"}}),
    ("comparison", {"measure": "relative_percent", "current": {"agg": "ratio", "numerator": "v", "denominator": "n", "unit": "percent"},
                    "baseline": {"value": 20, "unit": "percent"}}),
])
def test_invalid_declarations_rejected(field, spec):
    with pytest.raises(ValueError):
        render(KpiRow([{"label": "Metric", field: spec}], dataset_id="inputs"))


def test_static_reducers_and_conflicting_detail_slots_rejected():
    with pytest.raises(ValueError, match="reducers"):
        render(KpiCard(comparison={"measure": "relative_percent",
                                  "current": {"agg": "sum", "column": "v", "unit": "number"},
                                  "baseline": {"value": 1, "unit": "number"}}))
    with pytest.raises(ValueError, match="detail lines"):
        render(KpiCard(sub="Other", maturity={"state": "immature", "age": 3, "horizon": 7,
                       "observed": {"value": .08, "unit": "fraction"}}))


def test_numerical_contract_without_dom():
    script = """
const assert = require('node:assert/strict');
global.window = global;
""" + load_js("runtime/aggregate.js") + load_js("components/kpi_state.js") + """
const S = window._fwKpiState;
const rate = (n,d) => ({agg:'ratio', numerator:n, denominator:d, unit:'fraction'});
const spec = {measure:'relative_percent',current:rate('n','d'),baseline:rate('bn','bd')};
// Ratio of period sums, not sum/average of row-level rates or changes.
const rows = [{n:11,d:50,bn:1,bd:10}, {n:33,d:150,bn:39,bd:190}];
assert.equal(S.comparison(spec, rows).text, '+10.0%');
assert.equal(S.comparison({...spec, measure:'percentage_points'}, rows).text, '+2.0 pp');
assert.equal(S.comparison(spec, rows.slice(0,1)).text, '+120.0%');
const literal = (v,unit='number') => ({value:v,unit});
const relative = (c,b) => S.comparison({measure:'relative_percent',current:literal(c),baseline:literal(b)},[]);
assert.equal(relative(480,0).text, '— · zero baseline');
assert.equal(relative(480,null).text, '— · baseline unavailable');
assert.equal(relative(-50,-100).text, '— · signed baseline');
assert.deepEqual(relative(900,900), {text:'0.0%', direction:''});
assert.equal(S.comparison({measure:'percentage_points',current:literal(22,'percent'),baseline:literal(.2,'fraction')},[]).text,'+2.0 pp');
assert.equal(S.comparison({...spec, precision:3}, rows).text, '+10.000%');
assert.equal(S.comparison({measure:'percentage_points',value:2},[]).text,'+2.0 pp');
for (const missing of [null, undefined, NaN, Infinity, true, '5']) {
    assert.equal(S.resolve({agg:'sum',column:'x',unit:'number'},[{x:3},{x:missing}]),null);
}
assert.equal(S.resolve({agg:'sum',column:'x',unit:'number'},[{x:0}]),0);
assert.equal(S.resolve({agg:'sum',column:'x',unit:'number'},[]),null);
assert.equal(S.resolve({agg:'count',unit:'number'},[]),0);
assert.equal(S.resolve(rate('n','d'),[{n:1,d:-1},{n:2,d:3}]),null);
assert.equal(S.resolve(rate('n','d'),[{n:1,d:0}]),null);
assert.equal(S.resolve(rate('n','d'),[{n:1,d:1e308},{n:1,d:1e308}]),null);
assert.equal(S.comparison(spec,[...rows,{n:1,d:5,bn:null,bd:4}]).text,'— · baseline unavailable');
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def module():
    path = Path(__file__).parents[1] / "demo/reports/component-showcase/examples_kpi_states.py"
    spec = importlib.util.spec_from_file_location("examples_kpi_states", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_genuine_examples_full_diagnostics(tmp_path):
    from trellum.rendering.html_builder import render_report
    from trellum.report import ReportContext
    from trellum.reporting.diagnostics import compute_details
    from trellum.validation import validate_report

    ctx = ReportContext(config={"name": "KPI states", "studio": "test", "category": "Components", "description":
        "Synthetic supplied observation revenue, cohort age and paired-period conversion comparisons by input case, cohort and region."},
        slug="kpi-states", output_dir=str(tmp_path))
    module().add_sections(ctx)
    result = validate_report(ctx, details=compute_details(ctx))
    assert not [(c.id, c.message) for c in result.checks if c.level in {"warn", "fail"}]
    render_report(ctx, str(tmp_path), auto_refresh=False)
    data = json.loads((tmp_path / "data.json").read_text())
    assert any(c.get("type") == "kpi_row_live" for c in data["components"].values())
