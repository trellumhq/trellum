"""Optional, bounded hierarchy over explicitly supplied additive leaf facts."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import pandas as pd

from trellum.assets import load_css, load_js
from trellum.components._table_totals import typed_inputs
from trellum.components.base import Component, RenderContext

MAX_FACTS = 50_000
MAX_NODES = 2_000
MAX_COLUMNS = 32
MAX_CELLS = 64_000
MAX_BYTES = 8 * 1024 * 1024
MAX_TEXT = 1024


def _error(message):
    raise ValueError("HierarchyTable: " + message)


def _text(value, name, limit=MAX_TEXT):
    if not isinstance(value, str) or not value or len(value) > limit:
        _error(f"{name} must be a nonempty string of at most {limit} characters")
    return value


def typed_dimension(value):
    """JSON-safe scalar with explicit identity; no delimiter or display keys."""
    if value is None or value is pd.NA or value is pd.NaT:
        return ["null", None]
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            _error("dimension text exceeds 1024 characters")
        return ["string", value]
    if pd.api.types.is_bool(value):
        return ["boolean", bool(value)]
    if pd.api.types.is_integer(value) or pd.api.types.is_float(value):
        if pd.api.types.is_float(value) and math.isnan(value):
            return ["null", None]
        if value < -(2**53 - 1) or value > 2**53 - 1 or not math.isfinite(value):
            _error("dimension numeric identity must be finite and JS-safe")
        return ["number", int(value) if value == int(value) else float(value)]
    _error("dimensions must be scalar null, string, boolean or safe finite number")


def _key(path):
    return tuple((part[0], part[1]) for part in path)


@dataclass
class HierarchyTable(Component):
    """Expand dimension prefixes without changing the additive population.

    Every input row is one leaf fact, identified by ``record_id``. Authors
    must exclude input parent summaries, rates, distinct counts and repeated
    budgets. See docs/report-hierarchy-tables.md for completeness and bounds.
    """

    df: pd.DataFrame
    title: str = ""
    rows: list[str] = field(kw_only=True)
    value: str = field(kw_only=True)
    record_id: str = field(kw_only=True)
    column: str | None = field(default=None, kw_only=True)
    value_format: str = field(default="number", kw_only=True)
    precision: int = field(default=2, kw_only=True)
    known_subtotals: bool = field(default=False, kw_only=True)
    expanded_paths: list[list] | None = field(default=None, kw_only=True)
    dataset_id: str | None = field(default=None, kw_only=True)
    static: bool = field(default=False, kw_only=True)

    _component_type: str = field(default="hierarchy_table", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def __post_init__(self):
        self.rows = list(self.rows) if isinstance(self.rows, list) else self.rows
        self.prepare(self.df)
        if self.expanded_paths is not None:
            self.expanded_paths = [list(path) for path in self.expanded_paths]

    def prepare(self, frame):
        """Validate the full supplied population and encode exact typed inputs."""
        if not isinstance(self.rows, list) or not 1 <= len(self.rows) <= 8:
            _error("rows requires a list of 1..8 distinct dimension names")
        for name in [*self.rows, self.value, self.record_id] + ([self.column] if self.column is not None else []):
            _text(name, "column name", 128)
        names = [*self.rows, self.value, self.record_id] + ([self.column] if self.column is not None else [])
        if len(set(names)) != len(names):
            _error("dimension, bucket, value and record identity columns must be distinct")
        if not isinstance(self.title, str) or len(self.title) > 512:
            _error("title must be text of at most 512 characters")
        if self.dataset_id is not None:
            _text(self.dataset_id, "dataset_id", 128)
        if type(self.static) is not bool or type(self.known_subtotals) is not bool:
            _error("static and known_subtotals must be bool")
        if self.value_format not in ("number", "currency", "plain"):
            _error("only additive number/currency/plain formats are supported")
        if type(self.precision) is not int or not 0 <= self.precision <= 6:
            _error("precision must be an integer from 0 to 6")
        if not isinstance(frame, pd.DataFrame) or not frame.columns.is_unique:
            _error("a dataframe with unique column names is required")
        if set(names) - set(frame.columns):
            _error("required columns missing from source: " + str(sorted(set(names) - set(frame.columns))))
        if len(frame) > MAX_FACTS:
            _error("source exceeds 50,000 leaf facts")
        inputs = typed_inputs(frame, {self.value: {"agg": "sum"}})[self.value]
        facts, ids, paths, buckets = [], set(), set(), set()
        size = 2
        for index, values in enumerate(frame[names].itertuples(index=False, name=None)):
            raw = dict(zip(names, values))
            identity = _text(raw[self.record_id], "record_id")
            if identity in ids:
                _error("duplicate record_id: " + identity)
            ids.add(identity)
            path = [typed_dimension(raw[name]) for name in self.rows]
            for depth in range(1, len(path) + 1):
                paths.add(_key(path[:depth]))
            bucket = typed_dimension(raw[self.column]) if self.column else ["null", None]
            buckets.add(_key([bucket]))
            if len(paths) > MAX_NODES:
                _error("source exceeds 2,000 path nodes")
            if len(buckets) > MAX_COLUMNS:
                _error("source exceeds 32 column categories")
            fact = {"id": identity, "path": path, "bucket": bucket, "input": inputs[index],
                    "raw": str(raw[self.value]) if raw[self.value] is not None else ""}
            # Unsupported values remain unavailable; raw text is bounded too.
            if len(fact["raw"]) > 2048:
                # Never silently shorten an export of a numeric input.
                _error("raw metric text exceeds export bounds")
            size += len(json.dumps(fact, ensure_ascii=False).encode("utf-8")) + 1
            if size > MAX_BYTES:
                _error("relevant serialized payload exceeds 8 MiB")
            facts.append(fact)
        if (len(paths) + 1) * ((len(buckets) if self.column else 0) + 1) > MAX_CELLS:
            _error("source exceeds 64,000 numeric cells")
        expanded = []
        if self.expanded_paths is not None:
            if not isinstance(self.expanded_paths, list) or len(self.expanded_paths) > MAX_NODES:
                _error("expanded_paths must be a bounded list of raw dimension-prefix lists")
            for path in self.expanded_paths:
                if not isinstance(path, list) or not 1 <= len(path) <= len(self.rows):
                    _error("expanded_paths entries must be raw dimension-prefix lists")
                typed = [typed_dimension(v) for v in path]
                if _key(typed) not in paths:
                    _error("expanded path does not exist in source")
                expanded.append(typed)
        return facts, expanded

    def render_html(self, ctx: RenderContext) -> str:
        facts, expanded = self.prepare(self.df)
        cid = ctx.next_id()
        cfg = {"type": "hierarchy_table", "dimensions": self.rows[:], "value": self.value,
               "recordId": self.record_id, "column": self.column, "format": self.value_format,
               "precision": self.precision, "knownSubtotals": self.known_subtotals,
               "expanded": expanded, "title": self.title}
        if self.dataset_id:
            cfg["dataset_id"] = self.dataset_id
        else:
            cfg["facts"] = facts
        ctx.register(cid, cfg)
        return f'<div id="{cid}" class="fw-hierarchy" tabindex="-1"></div>'

    @classmethod
    def css(cls):
        return load_css("components/hierarchy_table.css")

    @classmethod
    def client_js(cls):
        return load_js("components/data_table_totals.js") + "\n" + load_js("components/hierarchy_table.js")
