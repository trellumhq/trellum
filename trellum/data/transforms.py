"""Data transformation utilities for JSON serialization and formatting."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


def df_to_json_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a DataFrame to a JSON-serializable list of dicts.

    Uses vectorized column operations instead of iterrows() for performance.
    On large DataFrames this is 10-100x faster than row-by-row iteration.

    Handles:
    - NaN / NaT → None (JSON null)
    - numpy int/float → Python int/float
    - datetime / date → ISO format string
    - Timestamps → ISO format string
    """
    out = df.copy()
    for col in out.columns:
        dtype = out[col].dtype
        if pd.api.types.is_datetime64_any_dtype(dtype):
            out[col] = out[col].dt.strftime("%Y-%m-%dT%H:%M:%S")
            out[col] = out[col].where(out[col].notna(), None)
        elif pd.api.types.is_bool_dtype(dtype):
            mask = out[col].isna()
            out[col] = out[col].astype(object)
            out.loc[mask, col] = None
        elif pd.api.types.is_integer_dtype(dtype):
            mask = out[col].isna()
            out[col] = out[col].astype(object)
            out.loc[mask, col] = None
        elif pd.api.types.is_float_dtype(dtype):
            mask = out[col].isna() | np.isinf(out[col].to_numpy())
            out[col] = out[col].astype(object)
            out.loc[mask, col] = None
        else:
            if out[col].dtype == object:
                mask = out[col].apply(
                    lambda v: isinstance(v, (pd.Timestamp, datetime, date))
                )
                if mask.any():
                    out[col] = out[col].apply(
                        lambda v: v.isoformat() if isinstance(v, (pd.Timestamp, datetime, date)) else v
                    )
                na_mask = out[col].apply(lambda v: v is pd.NaT or (isinstance(v, float) and np.isnan(v)))
                if na_mask.any():
                    out.loc[na_mask, col] = None
    return out.to_dict(orient="records")


def _clean_value(val: Any) -> Any:
    """Convert a single value to a JSON-safe Python type."""
    if val is None:
        return None
    if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
        return None
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return float(val)
    if isinstance(val, (np.bool_,)):
        return bool(val)
    if isinstance(val, pd.Timestamp):
        return val.isoformat()
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, (pd.NaT.__class__,)) or pd.isna(val):
        return None
    return val


def format_value(val: Any) -> str:
    """Format a numeric value for display in tables."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "-"
    if isinstance(val, float):
        if abs(val) >= 1000:
            return f"{val:,.2f}"
        return f"{val:.2f}"
    if isinstance(val, (int, np.integer)):
        return f"{val:,}"
    return str(val)
