"""Bounded, authored cohort snapshots; no aggregation or maturity inference."""
from __future__ import annotations

from dataclasses import dataclass, field, KW_ONLY
from datetime import datetime
import json
import math
from numbers import Integral, Real
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from trellum.assets import load_css, load_js
from trellum.components.base import Component, RenderContext

MAX_COHORTS = 120
MAX_HORIZONS = 64
MAX_RECORDS = 7680
MAX_BYTES = 4 * 1024 * 1024
MAX_TEXT = 2048


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise ValueError(f"CohortTable {name} must be nonblank text of at most {MAX_TEXT} characters")
    return value


def _instant(value, name):
    _text(value, name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if "T" not in value or parsed.utcoffset() is None:
            raise ValueError
    except (ValueError, OverflowError):
        raise ValueError(f"CohortTable {name} requires an offset-aware ISO datetime") from None


def _null(value):
    return value is None or value is pd.NA or value is pd.NaT or (
        isinstance(value, (float, np.floating)) and math.isnan(value))


def _number(value, name):
    # bool (and numpy bool_) is not a genuine numeric observation.
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"CohortTable {name} must be a finite genuine number")
    if isinstance(value, Integral):
        native = int(value)
        # Reject integers the snapshot engine cannot preserve rather than
        # silently round their raw CSV representation.
        if abs(native) > 2**53 - 1:
            raise ValueError(f"CohortTable {name} exceeds exact snapshot integer precision")
        return native
    if not isinstance(value, (float, np.floating)) or (
            isinstance(value, np.floating) and value.dtype.itemsize > 8):
        raise ValueError(f"CohortTable {name} requires native binary floating-point snapshot precision")
    try:
        native = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"CohortTable {name} must be a finite genuine number") from None
    if not math.isfinite(native):
        raise ValueError(f"CohortTable {name} must be a finite genuine number")
    return native


def _primitive(value):
    if _null(value):
        return None
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise ValueError(f"CohortTable source text exceeds {MAX_TEXT} characters")
        return value
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return bool(value)
    if isinstance(value, Real):
        return _number(value, "source value")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise ValueError("CohortTable source fields must be snapshot scalar values")


@dataclass
class CohortTable(Component):
    """Display precomputed cells, selecting whole cohort identities only.

    See ``docs/report-cohort-tables.md`` for the state, provenance, filtering,
    export and sampled milestone contracts. All options after title are keywords.
    """
    df: pd.DataFrame
    title: str = ""
    _: KW_ONLY
    cohort: str
    cohort_month: str
    horizon: str
    observed: str
    observed_state: str
    population: dict
    forecast: str | None = None
    reason: str | None = None
    forecast_metadata: dict | None = None
    value_format: str = "percent"
    milestone: dict | None = None
    dataset_id: str | None = None
    static: bool = False

    _component_type: str = field(default="cohort_table", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def __post_init__(self):
        self.snapshot(self.df)

    def snapshot(self, frame: pd.DataFrame) -> dict:
        """Validate every source row before allocating the display cross-product.

        Called again at render/validation so mutations cannot bypass admission.
        The complete source row is retained for runtime identity verification.
        """
        if not isinstance(frame, pd.DataFrame) or not frame.columns.is_unique:
            raise ValueError("CohortTable requires a DataFrame with unique columns")
        if len(frame) > MAX_RECORDS:
            raise ValueError(f"CohortTable exceeds {MAX_RECORDS} source records")
        if not isinstance(self.title, str) or len(self.title) > MAX_TEXT:
            raise ValueError("CohortTable title is too long or is not text")
        if self.dataset_id is not None:
            _text(self.dataset_id, "dataset_id")
        if self.static and self.dataset_id:
            raise ValueError("CohortTable static=True cannot also select a dataset_id")
        columns = [self.cohort, self.cohort_month, self.horizon, self.observed, self.observed_state]
        columns += [c for c in (self.forecast, self.reason) if c is not None]
        for col in columns:
            _text(col, "column")
            if col not in frame.columns:
                raise ValueError(f"CohortTable required column {col!r} is missing")
        for col in frame.columns:
            _text(col, "source column")
        if self.value_format not in {"percent", "number", "currency", "ratio"}:
            raise ValueError("CohortTable value_format requires percent, number, currency or ratio")
        if not isinstance(self.population, dict) or set(self.population) - {"label", "as_of", "timezone", "source_time"}:
            raise ValueError("CohortTable population requires label, as_of, timezone and optional source_time")
        for key in ("label", "as_of", "timezone"):
            _text(self.population.get(key), f"population.{key}")
        _instant(self.population["as_of"], "population.as_of")
        if "source_time" in self.population:
            _instant(self.population["source_time"], "population.source_time")
        try:
            ZoneInfo(self.population["timezone"])
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("CohortTable population.timezone requires a valid IANA timezone") from None
        if self.forecast is not None:
            if not isinstance(self.forecast_metadata, dict) or set(self.forecast_metadata) != {"model", "scenario", "issued_at", "scope"}:
                raise ValueError("CohortTable forecast_metadata requires model, scenario, issued_at and scope")
            for key, value in self.forecast_metadata.items():
                _text(value, f"forecast_metadata.{key}")
            _instant(self.forecast_metadata["issued_at"], "forecast_metadata.issued_at")
        elif self.forecast_metadata is not None:
            raise ValueError("CohortTable forecast_metadata requires a forecast column")
        if self.milestone is not None:
            if not isinstance(self.milestone, dict) or set(self.milestone) != {"threshold", "label"}:
                raise ValueError("CohortTable milestone requires threshold and label")
            _number(self.milestone["threshold"], "milestone.threshold")
            _text(self.milestone["label"], "milestone.label")
        records, cohorts, horizons, seen = [], {}, set(), set()
        for row in frame.to_dict("records"):
            identity = _text(row[self.cohort], "cohort identity")
            month = row[self.cohort_month]
            if not isinstance(month, str) or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month) or month.startswith("0000"):
                raise ValueError("CohortTable cohort_month must be strictly YYYY-MM")
            day = row[self.horizon]
            if isinstance(day, bool) or not isinstance(day, Integral) or day < 0 or day > 2**53 - 1:
                raise ValueError("CohortTable horizon must be a nonnegative integer day (not bool)")
            if (identity, day) in seen:
                raise ValueError("CohortTable duplicate (cohort, horizon) record")
            seen.add((identity, day))
            if identity in cohorts and cohorts[identity] != month:
                raise ValueError("CohortTable month must be consistent within each cohort")
            cohorts[identity] = month
            horizons.add(int(day))
            if len(cohorts) > MAX_COHORTS or len(horizons) > MAX_HORIZONS:
                raise ValueError(f"CohortTable exceeds {MAX_COHORTS} cohorts or {MAX_HORIZONS} horizons")
            state, value = row[self.observed_state], row[self.observed]
            if not isinstance(state, str) or state not in {"observed", "unavailable", "not_mature"}:
                raise ValueError("CohortTable observed_state requires observed, unavailable or not_mature")
            if state == "observed":
                _number(value, "observed")
            elif not _null(value):
                raise ValueError("CohortTable non-observed states require null observed")
            if self.forecast and not _null(row[self.forecast]):
                _number(row[self.forecast], "forecast")
            if self.reason and not _null(row[self.reason]):
                if not isinstance(row[self.reason], str) or len(row[self.reason]) > MAX_TEXT:
                    raise ValueError("CohortTable reason must be text of at most 2048 characters or null")
            records.append({col: _primitive(v) for col, v in row.items()})
        milestone = None
        if self.milestone is not None:
            milestone = {**self.milestone, "threshold": _number(self.milestone["threshold"], "milestone.threshold")}
        cfg = dict(type="cohort_table", title=self.title, cohort=self.cohort, cohort_month=self.cohort_month,
                   horizon=self.horizon, observed=self.observed, observed_state=self.observed_state,
                   forecast=self.forecast, reason=self.reason, population=dict(self.population),
                   forecast_metadata=dict(self.forecast_metadata) if self.forecast_metadata else None,
                   value_format=self.value_format, milestone=milestone,
                   data=records, source_columns=list(frame.columns))
        if self.dataset_id:
            cfg["dataset_id"] = self.dataset_id
        if len(json.dumps(cfg, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > MAX_BYTES:
            raise ValueError("CohortTable exceeds 4 MiB cell/metadata serialization")
        return cfg

    def render_html(self, ctx: RenderContext) -> str:
        frame = self.df
        if self.dataset_id:
            frame = _render_source_frame(ctx, self.dataset_id)
        cfg = self.snapshot(frame)
        cid = ctx.next_id()
        ctx.register(cid, cfg)
        return (f'<div id="{cid}" class="fw-cohort">'
                f'<button type="button" class="fw-csv-btn" data-csv-target="{cid}" '
                'aria-label="Download CSV" title="Download CSV">CSV</button>'
                '<div class="fw-cohort-content" aria-live="polite"></div></div>')

    @classmethod
    def css(cls):
        return load_css("components/data_table.css") + load_css("components/cohort_table.css")

    @classmethod
    def client_js(cls):
        return load_js("components/cohort_table.js")


def _render_source_frame(ctx, dataset_id):
    """Resolve the serialized effective snapshot, including every deferred chunk."""
    configs = {c.get("dataset_id"): c for c in ctx.component_data.values()
               if c.get("type") in {"data_source", "scoped_data_source", "live_data_source"}}
    seen = set()
    source = dataset_id
    while source:
        if source in seen or source not in configs:
            raise ValueError("CohortTable requires its snapshot DataSource before the table")
        seen.add(source)
        cfg = configs[source]
        if cfg["type"] == "live_data_source":
            raise ValueError("CohortTable does not support LiveDataSource ancestry")
        if cfg["type"] == "scoped_data_source":
            source = cfg["parent_id"]
        else:
            break
    raw = ctx.raw_data_blocks.get(f"_ds_{source}")
    if not raw:
        raise ValueError("CohortTable effective snapshot is missing")
    parts = [raw]
    manifest = ctx._chunk_manifests.get(source, {})
    if manifest:
        import orjson
        parts += [orjson.loads(ctx._pending_chunks[c["file"]])[f"_ds_{source}"]
                  for c in manifest["chunks"].values()]
    if sum(len(part.get("_data", [])) for part in parts) > MAX_RECORDS:
        raise ValueError(f"CohortTable exceeds {MAX_RECORDS} source records")
    rows = []
    for part in parts:
        cols, vocab = part["_cols"], part.get("_dict", {})
        for values in part["_data"]:
            rows.append({col: vocab[col][v] if col in vocab else v for col, v in zip(cols, values)})
    # Object dtype retains genuine integer horizons when nullable values occur.
    return pd.DataFrame(rows, columns=raw["_cols"], dtype=object)
