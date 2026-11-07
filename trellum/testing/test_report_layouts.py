"""Layout API compatibility and pane size validation."""

import inspect
from dataclasses import dataclass

import pytest

from trellum.components import Grid, RawHTML, SplitPane


def test_new_layout_options_preserve_inherited_positional_arguments():
    @dataclass
    class NamedGrid(Grid):
        name: str = ""

    @dataclass
    class NamedSplit(SplitPane):
        name: str = ""

    child = RawHTML("<p>Context</p>")
    grid = NamedGrid([child], 3, True, 280, "tiles", show_titles=False)
    split = NamedSplit(child, child, "2:1", "details", right_min_width=240)
    assert (grid.columns, grid.card, grid.min_width, grid.name) == (3, True, 280, "tiles")
    assert (split.ratio, split.name) == ("2:1", "details")
    for cls, names in ((Grid, ("show_titles", "show_controls")),
                       (SplitPane, ("left_min_width", "right_min_width"))):
        for name in names:
            assert inspect.signature(cls).parameters[name].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("ratio", ["0:1", "1:-1", "nan:1", "1:inf", "1", "1:2:3", "a:b", None])
def test_split_rejects_invalid_ratio(ratio):
    with pytest.raises(ValueError, match="SplitPane"):
        SplitPane("left", "right", ratio)


@pytest.mark.parametrize("side", ["left_min_width", "right_min_width"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), "wide", None])
def test_split_rejects_invalid_minimum(side, value):
    with pytest.raises(ValueError, match="SplitPane"):
        SplitPane("left", "right", **{side: value})


@pytest.mark.parametrize("ratio", ["2:1", "0.2:0.1", " 2 : 1 ", "2e100:1e100"])
def test_split_bases_are_proportional_and_satisfy_both_minimums(ratio):
    split = SplitPane("left", "right", ratio, left_min_width=340, right_min_width=240)
    left, right, left_basis, right_basis = split._pane_sizes()
    assert left_basis / right_basis == pytest.approx(2)
    assert left / right == pytest.approx(2)
    assert min(left, right) >= 1  # Each pane must fill a line after wrapping.
    assert left_basis >= 340 and right_basis >= 240


def test_mutated_split_options_are_validated_before_render():
    split = SplitPane("left", "right")
    split.ratio = "1:0"
    with pytest.raises(ValueError, match="SplitPane"):
        split.render_html(None)
