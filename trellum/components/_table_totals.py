"""Declarations and typed snapshot inputs for optional DataTable totals."""

from collections.abc import Mapping
from decimal import Decimal
import math

import pandas as pd


def totals_config(option, frame, displayed):
    """Return an independent validated declaration, without inferring additivity."""
    def invalid(message):
        raise ValueError("DataTable totals: " + message)

    if option is None:
        return {}
    if not isinstance(option, Mapping):
        invalid("expected a mapping or None")
    result = {}
    for column, spec in option.items():
        if not isinstance(column, str) or column not in frame.columns or column not in displayed:
            invalid("metric keys must name supplied, displayed columns")
        if displayed and column == displayed[0]:
            invalid("the first displayed column is reserved for the Total label")
        if not isinstance(spec, Mapping):
            invalid("each metric requires a mapping")
        agg = spec.get("agg")
        if not isinstance(agg, str) or agg not in ("sum", "ratio"):
            invalid("agg must be sum or ratio")
        required = {"agg"} if agg == "sum" else {"agg", "numerator", "denominator"}
        if not required <= spec.keys() or spec.keys() - required - {"format", "precision"}:
            invalid("missing required or unknown options for " + agg)
        formats = ("number", "currency", "plain") if agg == "sum" else ("number", "currency", "plain", "percent", "ratio")
        fmt, precision = spec.get("format", "number"), spec.get("precision", 2)
        if not isinstance(fmt, str) or fmt not in formats:
            invalid("unsupported format for " + agg)
        if type(precision) is not int or not 0 <= precision <= 6:
            invalid("precision must be an integer from 0 to 6 (not bool)")
        normalized = {"agg": agg, "format": fmt, "precision": precision}
        if agg == "ratio":
            for key in ("numerator", "denominator"):
                name = spec[key]
                if not isinstance(name, str) or not name or name not in frame.columns:
                    invalid(key + " must name a supplied dataframe column")
                normalized[key] = name
        result[column] = normalized
    return result


def typed_inputs(frame, declarations):
    """Keep genuine numeric text separate from display, search and CSV cells.

    Arrays use original row positions, including duplicate dataframe indexes.
    Range markers avoid expanding extreme Decimal exponents or large integers.
    """
    columns = dict.fromkeys(
        name for column, spec in declarations.items()
        for name in ([column] if spec["agg"] == "sum" else [spec["numerator"], spec["denominator"]])
    )
    return {column: [_typed(value) for value in frame[column]] for column in columns}


def _typed(value):
    if isinstance(value, bool) or pd.api.types.is_bool(value):
        return None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        parts = value.as_tuple()
        if len(parts.digits) > 1000 or abs(parts.exponent) > 1000:
            return {"error": "range"}
    elif pd.api.types.is_integer(value):
        # 10**1000 needs 3322 bits; avoid decimal conversion of huge integers.
        if int(value).bit_length() > 3322:
            return {"error": "range"}
        if len(str(value).lstrip("-")) > 1000:
            return {"error": "range"}
    elif pd.api.types.is_float(value):
        if not math.isfinite(value):
            return None
    else:
        return None
    return str(value)
