"""trellum.data and trellum.rendering export everything, and load pandas late.

The portal reaches light submodules (trellum.data.live_query_guard,
trellum.rendering.cdn) on every request path. Importing those must not load
pandas, numpy or pyarrow; the names that need them resolve on first use.
"""

from __future__ import annotations

import os
import subprocess
import sys

import trellum.data
import trellum.rendering

_PROBE = """
import sys
import trellum.data.connections, trellum.data.live_query_guard
import trellum.rendering.cdn, trellum.rendering.js_runtime
print(",".join(m for m in ("pandas", "numpy", "pyarrow") if m in sys.modules))
"""


def test_light_submodules_do_not_load_pandas():
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True,
        env=env, cwd=os.getcwd(), timeout=60, check=True,
    )
    assert result.stdout.strip() == ""


def test_every_exported_name_resolves():
    for package in (trellum.data, trellum.rendering):
        for name in package.__all__:
            assert getattr(package, name) is not None, (package.__name__, name)
            assert name in dir(package)


def test_lazy_names_are_the_real_objects():
    from trellum.data import df_to_json_records, query_df
    from trellum.data.query import query_df as real_query_df
    from trellum.data.transforms import df_to_json_records as real_records
    from trellum.rendering import render_report
    from trellum.rendering.html_builder import render_report as real_render

    assert query_df is real_query_df
    assert df_to_json_records is real_records
    assert render_report is real_render


def test_unknown_names_still_raise_attribute_error():
    for package in (trellum.data, trellum.rendering):
        try:
            package.no_such_name  # noqa: B018
        except AttributeError:
            continue
        raise AssertionError(f"{package.__name__}.no_such_name resolved")
