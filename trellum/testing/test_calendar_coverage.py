"""UTC declaration admission and independent acquisition-calendar oracles."""

from __future__ import annotations

import dataclasses
import importlib.util
import inspect
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from trellum.components import BarChart, DataSource, RenderContext, ScopedDataSource
from trellum.components.calendar_coverage import CalendarCoverage, calendar_bounds
from trellum.themes import THEME_REGISTRY
from trellum.validation.checks.calendar_coverage import _check_calendar_coverage
from trellum.validation.result import ValidationResult

ROOT = Path(__file__).resolve().parents[2]


def example():
    spec = importlib.util.spec_from_file_location(
        "kpi_buckets_examples",
        ROOT / "trellum/demo/reports/component-showcase/examples_kpi_buckets.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_fixture_and_independent_calendar_oracle():
    m = example()
    frame = m.facts()
    assert len(frame) == 208 and frame.day.nunique() == 104
    assert frame.cost.sum() == 5200 and frame.installs.sum() == 4160
    assert frame.revenue_d7.sum() == 3120 and frame.depositors_d7.sum() == 520
    assert frame.cost.sum() / frame.installs.sum() == 1.25
    assert frame.revenue_d30.sum() / frame.cost.sum() == 1.2
    # Enumeration uses Python's independent weekday/month/quarter identities,
    # not the implementation's bucket helper or emitted metadata as an oracle.
    dates = [date(2026, 6, 12) + timedelta(days=i) for i in range(104)]
    for grain, count, partial, first_days, last_days, first_full, last_full in (
        ("daily", 104, 0, 1, 1, 1, 1),
        ("weekly", 16, 2, 3, 3, 7, 7),
        ("monthly", 4, 2, 19, 23, 30, 30),
        ("quarterly", 2, 2, 19, 85, 91, 92),
    ):
        groups = {}
        for day in dates:
            key = (
                (day.year, day.month, day.day)
                if grain == "daily"
                else (day - timedelta(days=day.weekday())).isoformat()
                if grain == "weekly"
                else (day.year, day.month)
                if grain == "monthly"
                else (day.year, (day.month - 1) // 3)
            )
            groups.setdefault(key, []).append(day)
        assert len(groups) == count
        declaration = m.declaration(frame, grain, 7)
        bounds = list(declaration.config()["buckets"].values())
        observed = list(groups.values())
        assert [len(g) for g in observed][0 :: len(observed) - 1] == [first_days, last_days]
        full = [(date.fromisoformat(b) - date.fromisoformat(a)).days + 1 for a, b in bounds]
        assert full[0] == first_full and full[-1] == last_full
        assert sum(len(g) != f for g, f in zip(observed, full)) == partial
        assert sum(len(g) * 50 for g in observed) == 5200
        assert sum(len(g) * 30 for g in observed) == 3120
        assert all((len(g) * 50) / (len(g) * 40) == 1.25 for g in observed)
    first = frame[frame.weekly_bucket == "2026-06-08"]
    assert (first.cost.sum(), first.revenue_d7.sum(), first.installs.sum()) == (150, 90, 120)


def test_keyword_only_default_off_and_explicit_binding():
    m = example()
    frame = m.facts()
    coverage = m.declaration(frame, "weekly", 7)
    assert (
        inspect.signature(BarChart).parameters["bucket_context"].kind
        == inspect.Parameter.KEYWORD_ONLY
    )

    @dataclasses.dataclass
    class Extended(BarChart):
        extra: str = "unchanged"

    chart = Extended(frame, "weekly_bucket", "cost", "Cost", dataset_id=m.SOURCE, static=True)
    args = [getattr(chart, f.name) for f in dataclasses.fields(chart) if f.init and not f.kw_only]
    assert Extended(*args).extra == "unchanged"
    ctx = RenderContext(THEME_REGISTRY["trellum light"])
    chart.render_html(ctx)
    assert "bucket_context" not in ctx.component_data["fw_c1"]
    chart.bucket_context = coverage
    ctx.reset()
    chart.render_html(ctx)
    assert ctx.component_data["fw_c1"]["dataset_id"] == m.SOURCE
    assert ctx.component_data["fw_c1"]["bucket_context"] == coverage.config()
    default_ctx = RenderContext(THEME_REGISTRY["trellum light"])
    default_html = default_ctx.render_child(
        BarChart(frame, "weekly_bucket", "cost", "Cost", dataset_id=m.SOURCE)
    )
    assert "Authored UTC calendar context" not in default_ctx.collect_assets()[1]
    hover_ctx = RenderContext(THEME_REGISTRY["trellum light"])
    hover_html = hover_ctx.render_child(chart)
    assert "fw-calendar-coverage" not in hover_html
    assert "Authored UTC calendar context" in hover_ctx.collect_assets()[1]
    note_ctx = RenderContext(THEME_REGISTRY["trellum light"])
    assert "<aside" in note_ctx.render_child(coverage)
    assert all(c["type"] != "chart" for c in note_ctx.component_data.values())


@pytest.mark.parametrize(
    "change",
    [
        dict(timezone="Europe/Amsterdam"),
        dict(week_start="Sunday"),
        dict(grain="fiscal"),
        dict(start="2026-06-31"),
        dict(end="2026-01-01"),
        dict(end="2037-01-01"),
        dict(caption="x" * 161),
        dict(buckets={"x": ("2026-06-09", "2026-06-15")}),
        dict(horizon={"name": "D7", "days": True, "observed_through": "2026-11-04"}),
        dict(input_columns=("cost", "cost")),
    ],
)
def test_strict_declarations_and_mutation(change):
    m = example()
    good = m.declaration(m.facts(), "weekly", 7)
    values = {f.name: getattr(good, f.name) for f in dataclasses.fields(good) if f.init}
    values.update(change)
    with pytest.raises((ValueError, TypeError)):
        CalendarCoverage(**values)
    for k, v in change.items():
        setattr(good, k, v)
    with pytest.raises((ValueError, TypeError)):
        good.config()


@pytest.mark.parametrize(
    "options",
    [
        dict(horizontal=True),
        dict(stacked=True),
        dict(sort="asc"),
        dict(top_n=0),
        dict(net_color=True),
        dict(month_context=True),
        dict(dataset_id="other"),
        dict(x="day"),
    ],
)
def test_incompatible_chart_options_only_rejected_when_opted_in(options):
    m = example()
    f = m.facts()
    args = dict(df=f, x="weekly_bucket", y="cost", title="Cost", dataset_id=m.SOURCE)
    args.update(options)
    BarChart(**args)
    with pytest.raises(ValueError, match="bucket_context"):
        BarChart(**args, bucket_context=m.declaration(f, "weekly", 7))


def test_native_admission_bounds_keys_and_scope():
    m = example()
    f = m.facts()
    d = m.declaration(f, "weekly", 7)

    def check(source, decl=d, extra=()):
        r = ValidationResult("probe")
        _check_calendar_coverage([(source, "Snapshot"), *extra, (decl, "Calendar")], r)
        return r

    assert check(DataSource(m.SOURCE, f)).summary["fail"] == 0
    for invalid in (
        f.assign(weekly_bucket="fabricated"),
        f.assign(day="2026-02-30"),
        pd.concat([f] * 49, ignore_index=True),
    ):
        assert check(DataSource(m.SOURCE, invalid)).summary["fail"] == 1
    assert check(DataSource(m.SOURCE, f, chunk_by="month")).summary["fail"] == 1
    scoped = dataclasses.replace(d, dataset_id="child")
    assert (
        check(
            DataSource(m.SOURCE, f),
            scoped,
            [(ScopedDataSource("child", parent=m.SOURCE), "Snapshot")],
        ).summary["fail"]
        == 0
    )
    assert (
        check(
            DataSource(m.SOURCE, f),
            dataclasses.replace(d, dataset_id="child2"),
            [
                (ScopedDataSource("child", parent=m.SOURCE), "Snapshot"),
                (ScopedDataSource("child2", parent="child"), "Snapshot"),
            ],
        ).summary["fail"]
        == 1
    )


def test_leap_year_and_year_boundary_bounds():
    assert calendar_bounds(date(2024, 2, 29), "monthly") == (date(2024, 2, 1), date(2024, 2, 29))
    assert calendar_bounds(date(2026, 1, 1), "weekly") == (date(2025, 12, 29), date(2026, 1, 4))
    assert calendar_bounds(date(2024, 2, 29), "quarterly") == (date(2024, 1, 1), date(2024, 3, 31))


def test_runtime_scope_calendar_and_explicit_unavailable_probes(tmp_path):
    """Actual engine/metadata modules under Node; these are not full reports."""
    import json
    import subprocess

    from trellum.assets import load_js

    m = example()
    frame = m.facts()
    d = m.declaration(frame, "weekly", 7).config()
    script = (
        "global.window=global;window._fwRenderers={};window._reportData={components:{source:{type:'data_source',dataset_id:'"
        + m.SOURCE
        + "'}}};\n"
    )
    script += load_js("runtime/filter_engine.js") + "\n" + load_js("components/calendar_coverage.js")
    script += "\nconst raw=" + frame.to_json(orient="records") + ";const d=" + json.dumps(d) + ";"
    script += r'''
const e=window._fwFilterEngine,c=window._fwCalendarCoverage,source=d.dataset_id;
e.init(source,raw);
function assert(ok, message){if(!ok)throw Error(message);}
function state(declaration=d){return c.compute(declaration);}
e.setFilter(source,'date','day','range',{min:'2026-07-09',max:'2026-07-09'});
let b=state().buckets[0];assert(b.start==='2026-07-06'&&b.end==='2026-07-12'&&b.selected===1&&b.observed===1,'one day');
e.addScopedChild('child',source);
let child={...d,dataset_id:'child'};
e.setFilter('child','date','day','range',{min:'2026-07-10',max:'2026-07-10'});
assert(state(child).empty===true,'disjoint parent/child intersection');
e.clearAllFilters('child');
e.setFilter('child','platform','platform','equals','iOS');
assert(state(child).buckets[0].observed===1,'parent date plus child platform');
e.setFilter(source,'date','day','range',{min:'2026-07-10',max:'2026-07-09'});
assert(state().reason.includes('reversed'),'reversed');
e.setFilter(source,'date','day','range',{min:'2026-02-30'});
assert(state().status==='unavailable','invalid Gregorian');
e.setFilter(source,'date','day','equals','2026-07-09');
assert(state().reason.includes('range'),'unsupported selection');
e.clearAllFilters(source);
assert(state({...d,input_columns:[]}).inputsKnown===false,'unknown numeric completeness');
assert(state({...d,horizon:{name:'D7',days:7,observed_through:'2026-06-12'}}).buckets[0].mature===0,'separate maturity');
e.addScopedChild('grandchild','child');assert(state({...d,dataset_id:'grandchild'}).reason.includes('Chained'),'chained');
e._liveDatasets[source]={};assert(state().reason.includes('Live'),'live');delete e._liveDatasets[source];
window._reportData._chunk_manifests={[source]:{chunks:{}}};assert(state().reason.includes('unchunked'),'chunked');delete window._reportData._chunk_manifests;
let invalid=raw.map(r=>({...r,weekly_bucket:'unknown'}));e.init(source,invalid);assert(state().reason.includes('keys'),'mapping');
e.initColumnar(source,{...e._rowsToColumnar(raw),_data:[]});assert(state().buckets.reduce((n,b)=>n+b.missing,0)===104,'empty population is unknown dates');
e.init(source,raw);let completed={labels:['2026-06-15','2026-06-08'],datasets:[],tooltip:{callbacks:{label:()=> 'original'}}};
c.spec({bucket_context:d,dataset_id:source,x:d.bucket_column},raw,completed);assert(completed.calendarCoverage.status==='unavailable','reordered categories');
let leap={...d,start:'2024-02-28',end:'2024-03-01',grain:'monthly',bucket_column:'month',buckets:{'2024-02-01':['2024-02-01','2024-02-29'],'2024-03-01':['2024-03-01','2024-03-31']},horizon:null};
e.init(source,[{day:'2024-02-28',month:'2024-02-01',cost:0},{day:'2024-02-29',month:'2024-02-01',cost:1},{day:'2024-03-01',month:'2024-03-01',cost:2}]);
leap.input_columns=['cost'];let result=state(leap);assert(result.buckets[0].days===29&&result.buckets[0].observed===2&&result.buckets[1].days===31,'leap boundary');
console.log('calendar probes passed');
'''
    path = tmp_path / "calendar-probes.js"
    path.write_text(script)
    completed = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.heavy
def test_resource_limits_are_explicit(tmp_path):
    m = example()
    f = m.facts()
    d = m.declaration(f, "weekly", 7)
    huge = pd.concat([f] * 49, ignore_index=True)
    result = ValidationResult('size')
    _check_calendar_coverage([(DataSource(m.SOURCE, huge), "Source"), (d, "Note")], result)
    assert result.summary['fail'] == 1 and '10000' in result.checks[0].message
    d.input_columns = ('large',)
    f = f.assign(large='x' * 21000)
    result = ValidationResult('bytes')
    _check_calendar_coverage([(DataSource(m.SOURCE, f), "Source"), (d, "Note")], result)
    assert result.summary['fail'] == 1 and '4 MiB' in result.checks[0].message
