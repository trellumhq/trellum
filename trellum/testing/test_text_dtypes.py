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

TEXT_DTYPES = ["object", "str"]


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
