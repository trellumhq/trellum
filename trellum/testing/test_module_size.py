"""Modules and functions have to stay a size a person can read.

This is the guard for the property that actually rotted. Nothing here grew on
purpose: charts.py reached 2,552 lines, validation.py 2,013, runner.py 1,599,
and `generate_js_runtime` was a single 2,313-line function -- each one an
accretion nobody decided on, because there was no point at which anything
objected. This test is that point.

The limits are deliberately loose. They are not a style opinion about ideal
module length; they are the size past which a file stops being readable in one
sitting and starts being grepped instead. A file that trips this has usually
absorbed a second responsibility, and the fix is to give that responsibility
its own module -- not to raise the number.

Raising a limit or adding to the allow-list is allowed, but it is a decision
someone makes in a diff with a reason attached, which is the whole point.
"""

from __future__ import annotations

import ast
import io
import os
from pathlib import Path

import pytest

#: Repo root, which IS the `trellum` package.
ROOT = Path(__file__).resolve().parent.parent

MAX_MODULE_LINES = 600
MAX_FUNCTION_LINES = 120

#: Directories that are not the framework: the demo project, the integration
#: harness, the tests themselves, vendored assets.
_SKIP_DIRS = {
    ".venv", ".git", "__pycache__", "demo", "itest", "testing", "examples",
    "docs", "static", "output", "build", ".claude", ".lavish", ".query_cache",
}

#: Functions that already exceed the limit, with the length they were at when
#: this guard was written. A ratchet, not an exemption: each one may shrink or
#: be split, and none may grow. Splitting these is real work -- they are mostly
#: validator rules and the build pipeline, where a careless extraction changes
#: behaviour quietly -- so they are recorded rather than rushed.
#:
#: To fix one properly: split it, then delete its line here. To add a line:
#: don't. That is what this test is for.
_LONG_FUNCTION_BUDGET = {
    ("validation/checks/columns.py", "_check_columns"): 371,
    ("runner/execute.py", "run_report"): 295,
    ("validation/checks/datasource.py", "_check_datasource_filterbar"): 294,
    ("runner/__init__.py", "main"): 242,
    ("reporting/diagnostics/filters.py", "_compute_view_matrix"): 191,
    ("validation/checks/rawhtml.py", "_check_rawhtml"): 182,
    ("runner/events.py", "_load_events"): 155,
    ("validation/checks/theme.py", "_check_theme"): 149,
    ("validation/checks/effectiveness.py", "_check_filter_effectiveness"): 148,
    ("stats/ab/users.py", "validate_user_df"): 136,
    ("reporting/gallery.py", "render_gallery"): 130,
    # A linear assembly with ~14 locals live across its phases (scopes,
    # assets, page, chunk writes). Cutting it up needs a carrier object for
    # that state; done blindly it just moves the problem into argument lists.
    ("rendering/html_builder.py", "render_report"): 401,
}


def _framework_modules() -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        for name in filenames:
            if name.endswith(".py"):
                out.append(Path(dirpath) / name)
    return sorted(out)


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_the_framework_has_modules_to_measure():
    """A walk that finds nothing would make every assertion below vacuous."""
    mods = _framework_modules()
    assert len(mods) > 50, f"only found {len(mods)} modules -- the walk is wrong"


@pytest.mark.parametrize("path", _framework_modules(), ids=_rel)
def test_module_is_readable_in_one_sitting(path: Path):
    lines = sum(1 for _ in io.open(path, encoding="utf-8"))
    assert lines <= MAX_MODULE_LINES, (
        f"{_rel(path)} is {lines} lines, over the {MAX_MODULE_LINES}-line "
        f"limit. This usually means the module has taken on a second "
        f"responsibility -- give that one its own module rather than raising "
        f"the limit."
    )


@pytest.mark.parametrize("path", _framework_modules(), ids=_rel)
def test_functions_stay_small_enough_to_follow(path: Path):
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    rel = _rel(path)
    too_long = []
    grew = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        span = (node.end_lineno or node.lineno) - node.lineno + 1
        budget = _LONG_FUNCTION_BUDGET.get((rel, node.name))
        if budget is not None:
            if span > budget:
                grew.append(f"{node.name} ({span} lines, budgeted {budget})")
        elif span > MAX_FUNCTION_LINES:
            too_long.append(f"{node.name} ({span} lines, line {node.lineno})")

    assert not too_long, (
        f"{rel}: {', '.join(too_long)} exceed {MAX_FUNCTION_LINES} lines. "
        f"Extract the phases that already have names in the comments."
    )
    assert not grew, (
        f"{rel}: {', '.join(grew)}. These are already over the limit and are "
        f"recorded so they cannot grow further -- split one rather than "
        f"raising its budget."
    )


def test_every_shipped_js_module_is_actually_loaded():
    """An asset nobody loads is dead code that looks live.

    The runtime and the review overlay both name their modules in an explicit
    tuple, because concatenation order is execution order. That makes it
    possible to add a file to the directory and never load it -- which reads,
    from the outside, exactly like a file that runs.
    """
    from trellum.assets import js_dir
    from trellum.rendering.js_runtime import RUNTIME_MODULES
    from trellum.review.inject import OVERLAY_MODULES

    for subdir, declared in (("runtime", RUNTIME_MODULES),
                             ("review", OVERLAY_MODULES)):
        on_disk = {p.stem for p in js_dir(subdir).glob("*.js")}
        assert on_disk == set(declared), (
            f"static/js/{subdir}/ and its module list disagree: "
            f"on disk only {sorted(on_disk - set(declared))}, "
            f"declared only {sorted(set(declared) - on_disk)}"
        )
