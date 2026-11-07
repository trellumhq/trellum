"""Strict opt-in ordinary DataTable display formats; raw cells stay authoritative."""
from collections.abc import Mapping

import pandas as pd


def column_formats_config(option, frame, displayed):
    """Validate and copy a declaration, including post-construction mutations."""
    if option is None:
        return {}
    if not isinstance(option, Mapping):
        raise ValueError('DataTable column_formats requires a mapping or None')
    result = {}
    for column, spec in option.items():
        if not isinstance(column, str) or column not in frame.columns or column not in displayed:
            raise ValueError('DataTable column_formats keys must name known displayed columns')
        if not isinstance(spec, Mapping) or 'format' not in spec or set(spec) - {'format', 'precision'}:
            raise ValueError('DataTable column_formats entries require format and optional precision only')
        fmt = spec['format']
        if not isinstance(fmt, str) or fmt not in {'number', 'currency', 'percent', 'ratio', 'plain'}:
            raise ValueError('DataTable column_formats format must be number/currency/percent/ratio/plain')
        if 'precision' in spec and (type(spec['precision']) is not int or not 0 <= spec['precision'] <= 12 or fmt == 'plain'):
            raise ValueError('DataTable column_formats precision must be an integer 0..12, not bool, and cannot accompany plain')
        # Exact decimal/string cells remain text. Refuse unsafe native integers
        # before a snapshot dataset could lose precision during JSON delivery.
        for value in frame[column]:
            if pd.api.types.is_bool(value):
                raise ValueError('DataTable column_formats boolean input: supply explicit text to preserve its meaning')
            if pd.api.types.is_integer(value) and not pd.api.types.is_bool(value) and abs(int(value)) > 2**53 - 1:
                raise ValueError('DataTable column_formats unsafe integer: supply exact text or precision-safe units')
        result[column] = dict(spec)
    return result
