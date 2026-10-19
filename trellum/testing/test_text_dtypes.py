"""Text columns behave the same whichever dtype holds them.

A report's text arrives as ``object`` (a frame built from Python lists of
``str`` with the pandas option off, or from older code), as pandas 3's ``str``
dtype (any frame built from Python strings), or as ``category`` (``query_df``
for repeated text). Components must not care which.
"""

from __future__ import annotations

import pandas as pd
import pytest

from trellum.components import PivotTable, RenderContext
from trellum.data.transforms import df_to_json_records
from trellum.themes import DefaultTheme

TEXT_DTYPES = ["object", "str", "category"]


def _frame(text_dtype: str) -> pd.DataFrame:
    df = pd.DataFrame({
        "event_date": ["2026-04-05", "2026-04-06", "2026-04-07", "2026-04-08"],
        "platform": ["iOS", "Android", None, "iOS"],
        "revenue": [100.0, 200.0, 150.0, 300.0],
        "users": [50, 80, 60, 90],
    })
    for col in ("event_date", "platform"):
        df[col] = df[col].astype(text_dtype)
    return df


@pytest.mark.parametrize("text_dtype", TEXT_DTYPES)
def test_pivot_table_treats_text_columns_as_dimensions(text_dtype):
    ctx = RenderContext(theme=DefaultTheme)
    PivotTable(_frame(text_dtype), rows=["platform"], values="revenue").render_html(ctx)
    (cfg,) = ctx.component_data.values()
    assert cfg["dim_cols"] == ["event_date", "platform"]
    assert cfg["num_cols"] == ["revenue", "users"]


@pytest.mark.parametrize("text_dtype", TEXT_DTYPES)
def test_json_records_turn_missing_text_into_none(text_dtype):
    records = df_to_json_records(_frame(text_dtype))
    assert [r["platform"] for r in records] == ["iOS", "Android", None, "iOS"]
    assert records[0] == {"event_date": "2026-04-05", "platform": "iOS",
                          "revenue": 100.0, "users": 50}


# ── Category columns render exactly like plain text ──────────

def _wide_frame() -> pd.DataFrame:
    """Ninety days, three platforms, two regions (one sometimes missing)."""
    dates = pd.date_range("2026-01-01", periods=90, freq="D").strftime("%Y-%m-%d")
    rows = []
    for i, day in enumerate(dates):
        for j, platform in enumerate(["iOS", "Android", "Web"]):
            rows.append({
                "event_date": day,
                "platform": platform,
                "category": "A" if (i + j) % 3 else "B",
                "region": None if (i * 3 + j) % 7 == 0 else ("EU" if j % 2 else "NA"),
                "step": f"Step {j + 1}",
                "revenue": float(100 + (i * 7 + j * 13) % 50),
                "net_revenue": float(90 + (i * 5 + j * 11) % 40),
                "users": 10 + (i + j) % 9,
                "transactions": 1 + (i * j) % 5,
                "value": 100 - j * 20,
            })
    return pd.DataFrame(rows)


def _as_categories(df: pd.DataFrame) -> pd.DataFrame:
    from trellum.data.fetch import compact_text_columns

    out = compact_text_columns(df.copy())
    assert isinstance(out["platform"].dtype, pd.CategoricalDtype)
    assert isinstance(out["event_date"].dtype, pd.CategoricalDtype)
    return out


def _subset(df: pd.DataFrame) -> pd.DataFrame:
    """Drops a platform, so a category column keeps a category no row uses."""
    return df[df["platform"] != "Web"]


def _render(component) -> bytes:
    import orjson

    ctx = RenderContext(theme=DefaultTheme)
    html = component.render_html(ctx)
    state = {
        "html": html,
        "component_data": ctx.component_data,
        "raw": ctx._raw_data_blocks,
        "chunks": {k: v.decode() for k, v in ctx._pending_chunks.items()},
        "manifests": ctx._chunk_manifests,
    }
    return orjson.dumps(state, option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS
                        | orjson.OPT_SORT_KEYS)


def _component_specs():
    from trellum.components import DataSource, DataTable, FilterBar
    from trellum.testing.test_components import _CHART_SPECS

    specs = list(_CHART_SPECS)
    specs += [(f"{name}_dataset", (lambda f: lambda df: _with_dataset(f(df)))(factory))
              for name, factory in _CHART_SPECS]
    specs += [
        ("DataTable", lambda df: DataTable(df, title="Table")),
        ("PivotTable", lambda df: PivotTable(df, rows=["platform"], values="revenue")),
        ("FilterBar", lambda df: FilterBar("ds", df, filters=[
            {"column": "event_date", "label": "Date", "type": "date_range", "default_days": 30},
            {"column": "platform", "label": "Platform"},
            {"column": "category", "label": "Category", "type": "toggle"},
            {"column": "region", "label": "Region"},
            {"column": "revenue", "label": "Revenue", "type": "slider"},
        ])),
        ("DataSource", lambda df: DataSource("ds", df)),
        ("DataSource_month_chunks", lambda df: DataSource("ds", df, chunk_by="month", default_chunks=1)),
        ("DataSource_week_chunks", lambda df: DataSource("ds", df, chunk_by="week", default_chunks=2)),
    ]
    return specs


def _with_dataset(component):
    if hasattr(component, "dataset_id"):
        component.dataset_id = "ds"
    return component


@pytest.mark.parametrize("subset", [False, True], ids=["all-rows", "subset"])
@pytest.mark.parametrize("name,factory", _component_specs(), ids=[s[0] for s in _component_specs()])
def test_category_columns_render_like_plain_text(name, factory, subset):
    plain = _wide_frame()
    categories = _as_categories(plain)
    if subset:
        plain, categories = _subset(plain), _subset(categories)
    assert _render(factory(categories)) == _render(factory(plain)), name
