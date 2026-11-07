"""Grid fallback ownership, authored positions and shared option validation."""

import inspect
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from trellum.components import AreaChart, BarChart, Grid, LineChart, Panel, RawHTML, StackedBar
from trellum.components.base import Component, RenderContext
from trellum.report import ReportContext
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report

BASELINE = "ab68c7bf0ba101008019508a08a7ce146b1528be"
ROOT = Path(__file__).resolve().parents[2]


def frame():
    return pd.DataFrame({"day": ["2026-10-01", "2026-10-02", "2026-10-03"],
                         "value": [0, 2, 7], "other": [1, 3, 4], "platform": ["A", "B", "A"]})


def configs(children):
    ctx = RenderContext(DEFAULT_THEME)
    ctx.render_children(children)
    return list(ctx.component_data.values())


def test_six_slots_cycle_at_palette_end_and_keep_values():
    kinds = (LineChart, AreaChart, BarChart)
    charts = [kinds[i % 3](frame(), "day", "value", str(i))
              for i in range(len(DEFAULT_THEME.chart_colors) + 2)]
    plain = configs(charts)
    colored = configs([Grid(charts, color_cycle="theme")])
    for i, (before, after) in enumerate(zip(plain, colored)):
        ds = after["datasets"][0]
        assert ds["_fwPaletteSlot"] == i
        color = DEFAULT_THEME.chart_colors[i % len(DEFAULT_THEME.chart_colors)]
        assert ds["borderColor"] == color
        assert ds["data"] == before["datasets"][0]["data"] == [0, 2, 7]
    assert len({c["datasets"][0]["borderColor"] for c in colored[:6]}) == 6


def test_reused_nested_off_and_wrapper_ownership_does_not_leak():
    reused = LineChart(frame(), "day", "value")
    original = dict(reused.__dict__)
    result = configs([
        Grid([LineChart(frame(), "day", "value"), reused], color_cycle="theme"),
        reused,
        Grid([RawHTML("<p>Spacer</p>"), Grid([reused]), Panel([reused]),
              Grid([reused, reused], color_cycle="theme"), reused], color_cycle="theme"),
    ])
    assert [c["datasets"][0].get("_fwPaletteSlot") for c in result] == [0, 1, None, None, None, 0, 1, 4]
    assert result[2]["datasets"][0]["borderColor"] == DEFAULT_THEME.chart_colors[0]
    assert reused.__dict__ == original


def test_render_exception_restores_outer_scope():
    class Broken(LineChart):
        def render_html(self, ctx):
            assert ctx.grid_color_slot(self) == 0
            raise RuntimeError("tile failed")

    ctx = RenderContext(DEFAULT_THEME)
    owner = Component()
    with ctx.grid_color_scope(owner, 8):
        with pytest.raises(RuntimeError, match="tile failed"):
            ctx.render_child(Grid([Broken(frame(), "day", "value")], color_cycle="theme"))
        assert ctx.grid_color_slot(owner) == 8
    assert ctx._grid_color_owner is None


def test_exclusions_overrides_and_all_authored_tiles_keep_positions():
    df = frame()
    category = {"domain": "platform", "column": "platform", "slots": {"A": 3, "B": 1}}
    charts = [RawHTML("<p>First tile</p>"),
              LineChart(df, "day", "value", colors=["#abcdef"]),
              LineChart(df, "day", ["value", "other"]),
              LineChart(df, "day", "value", dataset_id="d", stack_by="platform", category_colors=category),
              LineChart(df, "day", "value", dataset_id="d", stack_by_options={"Platform": "platform"}),
              AreaChart(df, "day", "value", stacked=True),
              BarChart(df, "day", "value", "Net", net_color=True, dataset_id="d"),
              StackedBar(df, "day", ["value"], "Stack", dataset_id="d", stack_by="platform"),
              BarChart(df, "day", "value", "Ordinary", dataset_id="d")]
    result = configs([Grid(charts, color_cycle="theme")])
    assert result[0]["datasets"][0]["borderColor"] == "#abcdef"
    assert result[0]["datasets"][0]["_themeManaged"] is False
    assert all("grid_color_slot" not in c for c in result[:-1])
    assert all("_fwPaletteSlot" not in ds for c in result[:-1] for ds in c.get("datasets", []))
    assert result[-1]["grid_color_slot"] == 8
    assert result[2]["category_colors"] == category


@pytest.mark.parametrize("kind", [LineChart, AreaChart, BarChart])
def test_bound_single_metric_slot_metadata(kind):
    chart = kind(frame(), "day", "value", "Bound", dataset_id="d")
    cfg = configs([Grid([RawHTML(""), chart], color_cycle="theme")])[0]
    assert cfg["grid_color_slot"] == 1 and "grid_color" not in cfg


@pytest.mark.parametrize("kind", [LineChart, BarChart])
def test_single_ratio_keeps_one_metric_slot_but_multi_ratio_does_not(kind):
    ratio = {"numerator": "value", "denominator": "other", "label": "Rate"}
    charts = [kind(frame(), "day", "value", "Ratio", dataset_id="d", ratios=[ratio]),
              kind(frame(), "day", "value", "Two ratios", dataset_id="d", ratios=[ratio, {**ratio, "label": "Other"}])]
    first, second = configs([Grid(charts, color_cycle="theme")])
    assert first["grid_color_slot"] == 0
    assert "grid_color_slot" not in second


@pytest.mark.parametrize("value", [True, False, 0, 1, "", "other", [], {}, ["theme"]])
def test_declaration_mutation_render_and_report_validation(value, tmp_path):
    with pytest.raises(ValueError, match="color_cycle"):
        Grid([], color_cycle=value)
    grid = Grid([])
    grid.color_cycle = value
    ctx = ReportContext({"name": "Grid contract", "description": "Grid option validation"}, "grid", str(tmp_path))
    ctx.add_section("Grid", [grid])
    assert any(c.id == "grid-color-cycle-invalid" and c.level == "fail" for c in validate_report(ctx).checks)
    with pytest.raises(ValueError, match="color_cycle"):
        grid.render_html(RenderContext(DEFAULT_THEME))


def test_keyword_only_and_subclass_positional_compatibility():
    @dataclass
    class NamedGrid(Grid):
        name: str = ""

    assert inspect.signature(Grid).parameters["color_cycle"].kind is inspect.Parameter.KEYWORD_ONLY
    assert NamedGrid([], 2, False, 0, "tiles").name == "tiles"


def test_omitted_and_none_match_baseline_html_and_payload():
    # Load the original layout module without another checkout or changing imports.
    import trellum.components.layout as layout
    namespace = dict(vars(layout))
    source = subprocess.check_output(["git", "show", f"{BASELINE}:trellum/components/layout.py"], cwd=ROOT, text=True)
    exec(compile(source, "baseline-layout.py", "exec"), namespace)
    charts = [kind(frame(), "day", "value", "Metric") for kind in (LineChart, AreaChart, BarChart)]
    reference = RenderContext(DEFAULT_THEME)
    before = reference.render_child(namespace["Grid"](charts))
    for options in ({}, {"color_cycle": None}):
        ctx = RenderContext(DEFAULT_THEME)
        assert ctx.render_child(Grid(charts, **options)) == before
        assert ctx.component_data == reference.component_data
