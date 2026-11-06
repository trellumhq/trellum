"""Bounded Sankey diagrams of explicitly supplied paths or adjacent edges."""

from __future__ import annotations

import json
import math
from dataclasses import KW_ONLY, dataclass, field
from html import escape
from numbers import Integral, Real

import pandas as pd

from trellum.assets import load_css, load_js
from trellum.components.base import Component, RenderContext
from trellum.components.charts.base import _ChartBase, _header_context_html, _title_html
from trellum.components.charts._sankey_summary import sankey_summary_config

MAX_ROWS = 10_000
MAX_NODES = 200
MAX_EDGES = MAX_PATHS = 1_000
MAX_BYTES = 4 * 1024 * 1024
SAFE_INTEGER = 2**53 - 1
MAX_QUANTITY = 10**15


def _text(value, name, limit=512):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Sankey {name} must be nonblank text of at most {limit} characters")
    return value


def _category(value):
    # pandas missing classifications are unavailable observations, never exits.
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, Real) and not isinstance(value, Integral) and math.isnan(value):
        return None
    return _text(value, "category", 512)


def _weight(value, kind):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("Sankey weight must be a finite nonnegative numeric value, not bool or text")
    if isinstance(value, Integral) and value > SAFE_INTEGER:
        raise ValueError("Sankey unsafe integer weight")
    if value < 0 or not math.isfinite(value):
        raise ValueError("Sankey weight must be finite and nonnegative")
    if kind == "count":
        if value > SAFE_INTEGER or value != int(value):
            raise ValueError("Sankey count weight must be a safe nonnegative integer")
        return int(value)
    if value > MAX_QUANTITY:
        raise ValueError("Sankey quantity exceeds 1e15")
    return float(value)


def _sum(a, b, kind):
    total = a + b
    if not math.isfinite(total) or total > (SAFE_INTEGER if kind == "count" else MAX_QUANTITY):
        raise ValueError("Sankey grouped total exceeds the safe numerical bound")
    return total


@dataclass
class SankeyChart(Component):
    """Author-declared complete paths or adjacent transitions; see report-sankey-charts.md."""

    df: pd.DataFrame
    title: str = ""
    _: KW_ONLY
    mode: str
    stages: list[dict]
    weight: str
    unit: str
    weight_kind: str = "count"
    source: str | None = None
    target: str | None = None
    source_stage: str | None = None
    target_stage: str | None = None
    height: int = 320
    min_width: int = 640
    header_context: str | None = None
    dataset_id: str | None = None
    static: bool = False
    header_summary: dict | None = None

    _component_type: str = field(default="sankey_chart", init=False, repr=False)
    _supports_dataset_id: bool = field(default=True, init=False, repr=False)

    def __post_init__(self):
        self.snapshot(self.df)

    def config(self):
        if self.mode not in ("paths", "edges"):
            raise ValueError("Sankey mode must be paths or edges")
        if self.weight_kind not in ("count", "quantity"):
            raise ValueError("Sankey weight_kind must be count or quantity")
        if not isinstance(self.stages, list) or not 2 <= len(self.stages) <= 12:
            raise ValueError("Sankey stages must be an ordered list of 2..12 stages")
        keys = set()
        for stage in self.stages:
            if not isinstance(stage, dict) or set(stage) != {"key", "label"}:
                raise ValueError("Sankey stages require exactly key and label")
            key = _text(stage["key"], "stage key", 128)
            _text(stage["label"], "stage label", 256)
            if key in keys:
                raise ValueError("Sankey stage keys must be unique")
            keys.add(key)
        _text(self.weight, "weight column", 128)
        _text(self.unit, "unit", 128)
        for name in ("source", "target", "source_stage", "target_stage"):
            value = getattr(self, name)
            if self.mode == "paths" and value is not None:
                raise ValueError("Sankey paths must omit source/target and stage columns")
            if self.mode == "edges":
                _text(value, name + " column", 128)
        roles = [self.weight] + ([s["key"] for s in self.stages] if self.mode == "paths" else
                                [self.source, self.target, self.source_stage, self.target_stage])
        if len(set(roles)) != len(roles):
            raise ValueError("Sankey columns must have distinct roles")
        for name, lo, hi in (("height", 160, 1200), ("min_width", 320, 2400)):
            value = getattr(self, name)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"Sankey {name} must be an integer in {lo}..{hi}")
        if not isinstance(self.title, str) or len(self.title) > 512:
            raise ValueError("Sankey title must be text of at most 512 characters")
        if self.header_context is not None and (
            not isinstance(self.header_context, str) or len(self.header_context) > 4096
        ):
            raise ValueError("Sankey header_context must be text of at most 4096 characters")
        if self.dataset_id is not None:
            _text(self.dataset_id, "dataset_id", 128)
        if type(self.static) is not bool:
            raise ValueError("Sankey static must be bool")
        return {**{name: getattr(self, name) for name in (
            "mode", "stages", "weight", "unit", "weight_kind", "source", "target",
            "source_stage", "target_stage", "height", "min_width", "title", "header_context",
        )}, **sankey_summary_config(self)}

    def required_columns(self):
        return list(dict.fromkeys([self.weight] + (
            [s["key"] for s in self.stages] if self.mode == "paths" else
            [self.source, self.target, self.source_stage, self.target_stage]
        )))

    def snapshot(self, df):
        """Validate before graph creation and return normalized relevant source rows."""
        cfg = self.config()
        if not isinstance(df, pd.DataFrame) or not df.columns.is_unique:
            raise ValueError("Sankey source must be a DataFrame with unique columns")
        cols = self.required_columns()
        if any(c not in df.columns for c in cols):
            raise ValueError(f"Sankey missing required columns: {set(cols) - set(df.columns)}")
        if len(df) > MAX_ROWS:
            raise ValueError("Sankey source exceeds 10000 rows")
        stage_index = {s["key"]: i for i, s in enumerate(self.stages)}
        rows = []
        nodes, edges, paths = set(), {}, {}
        boundaries = [0] * (len(self.stages) - 1)
        total = 0
        # Bound the relevant payload incrementally before creating the chart graph.
        size = len(json.dumps(cfg, ensure_ascii=False).encode("utf-8"))
        for values in df[cols].itertuples(index=False, name=None):
            r = dict(zip(cols, values))
            w = _weight(r[self.weight], self.weight_kind)
            if self.mode == "paths":
                cats = tuple(_category(r[s["key"]]) for s in self.stages)
                normalized = {s["key"]: cat for s, cat in zip(self.stages, cats)}
                links = [(i, cats[i], cats[i + 1]) for i in range(len(cats) - 1)]
                paths[cats] = _sum(paths.get(cats, 0), w, self.weight_kind)
                total = _sum(total, w, self.weight_kind)
                nodes.update(enumerate(cats))
            else:
                sk, tk = r[self.source_stage], r[self.target_stage]
                if not isinstance(sk, str) or not isinstance(tk, str) or sk not in stage_index or tk not in stage_index:
                    raise ValueError("Sankey unknown stage key")
                i = stage_index[sk]
                if stage_index[tk] != i + 1:
                    raise ValueError("Sankey edges must connect adjacent stages in declared order")
                a, b = _category(r[self.source]), _category(r[self.target])
                normalized = {self.source_stage: sk, self.target_stage: tk, self.source: a, self.target: b}
                links = [(i, a, b)]
                nodes.update(((i, a), (i + 1, b)))
            normalized[self.weight] = w
            rows.append(normalized)
            size += len(json.dumps(normalized, ensure_ascii=False, allow_nan=False).encode("utf-8")) + 2
            for link in links:
                edges[link] = _sum(edges.get(link, 0), w, self.weight_kind)
                boundaries[link[0]] = _sum(boundaries[link[0]], w, self.weight_kind)
            if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES or len(paths) > MAX_PATHS:
                raise ValueError("Sankey exceeds 200 nodes, 1000 edges or 1000 complete paths")
            if size > MAX_BYTES:
                raise ValueError("Sankey relevant payload exceeds 4 MiB")
        domain = sorted({cat for _, cat in nodes if cat is not None})
        if size + len(json.dumps(domain, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
            raise ValueError("Sankey relevant payload exceeds 4 MiB")
        return {"rows": rows, "domain": domain, "paths": paths, "edges": edges,
                "boundaries": boundaries, "total": total if self.mode == "paths" else None}

    def _render_source(self, ctx):
        """Resolve the effective serialized source, including scoped ancestry."""
        configs = list(ctx.component_data.values())
        registrations = {c.get("dataset_id"): c for c in configs if c.get("type") in (
            "data_source", "scoped_data_source", "live_data_source")}
        seen, ds_id = set(), self.dataset_id
        while ds_id:
            if ds_id in seen or ds_id not in registrations:
                raise ValueError("Sankey source is missing, cyclic or not declared before the chart")
            seen.add(ds_id)
            reg = registrations[ds_id]
            if reg["type"] == "live_data_source":
                raise ValueError("Sankey does not support LiveDataSource ancestry")
            if reg["type"] != "scoped_data_source":
                break
            ds_id = reg["parent_id"]
        # Reject a live-connected filter graph, including transitive propagation.
        connected = set(seen)
        changed = True
        while changed:
            changed = False
            for key, reg in registrations.items():
                if reg["type"] == "scoped_data_source" and (key in connected or reg["parent_id"] in connected):
                    group = {key, reg["parent_id"]}
                    if not group <= connected:
                        connected.update(group)
                        changed = True
            for cfg in configs:
                if cfg.get("type") != "filter_bar":
                    continue
                group = {cfg.get("dataset_id"), *(cfg.get("propagate_to") or {})}
                if group & connected and not group <= connected:
                    connected.update(group)
                    changed = True
        if any(registrations.get(k, {}).get("type") == "live_data_source" for k in connected):
            raise ValueError("Sankey does not support live-connected filter propagation")
        if ds_id in ctx._chunk_manifests:
            raise ValueError("Sankey requires a complete unchunked snapshot")
        payload = ctx.raw_data_blocks.get("_ds_" + ds_id)
        if not isinstance(payload, dict) or "_cols" not in payload or "_data" not in payload:
            raise ValueError("Sankey source snapshot is not ready")
        cols, data = payload["_cols"], payload["_data"]
        if not isinstance(cols, list) or len(set(cols)) != len(cols):
            raise ValueError("Sankey effective source must have unique columns")
        if len(data) > MAX_ROWS:
            raise ValueError("Sankey source exceeds 10000 rows")
        relevant = self.required_columns()
        if any(c not in cols for c in relevant):
            raise ValueError("Sankey effective source is missing required columns")
        indices = [cols.index(c) for c in relevant]
        dictionaries = payload.get("_dict", {})
        rows = [[dictionaries[c][r[i]] if c in dictionaries else r[i]
                 for c, i in zip(relevant, indices)] for r in data]
        return pd.DataFrame(rows, columns=relevant, dtype=object)

    def render_html(self, ctx: RenderContext) -> str:
        self.snapshot(self.df)  # Reject mutated author data/options, too.
        snapshot = self.snapshot(self._render_source(ctx) if self.dataset_id else self.df)
        cid = ctx.next_id()
        cfg = {"type": "sankey", **self.config(), "domain": snapshot["domain"]}
        if self.dataset_id:
            cfg["dataset_id"] = self.dataset_id
        else:
            cfg["rows"] = snapshot["rows"]
        ctx.register(cid, cfg)
        described_by = f"{cid}-population"
        if self.header_context and self.header_context.strip():
            described_by += f" {cid}-header-context"
        return (
            f'<div class="fw-sankey fw-chart-container" data-sankey="{cid}" style="--sankey-height:{self.height}px;--sankey-width:{self.min_width}px">'
            + _title_html(escape(self.title)) + _header_context_html(cid, self.header_context)
            + f'<div class="fw-sankey-scroll" tabindex="0" role="region" aria-label="{escape(self.title or "Sankey diagram", quote=True)}; scroll horizontally for ordered stages">'
            + '<div class="fw-sankey-plot">'
            + f'<canvas id="{cid}" role="img" aria-label="Flow diagram; exact values in details below" aria-describedby="{described_by}"></canvas>'
            + '</div></div>'
            + f'<p class="fw-sankey-state" role="status"></p><p id="{cid}-population" class="fw-sankey-population"></p>'
            + '<details class="fw-sankey-details"><summary>Exact supplied records</summary><div class="fw-sankey-records" tabindex="0" role="region" aria-label="Exact Sankey records; scroll horizontally"></div></details>'
            + '<button type="button" class="fw-sankey-download" title="Download CSV">Download CSV</button></div>'
        )

    @classmethod
    def cdn_deps(cls):
        return _ChartBase.cdn_deps() + ["chartjs_sankey"]

    @classmethod
    def css(cls):
        return _ChartBase.css() + load_css("components/sankey.css")

    @classmethod
    def client_js(cls):
        return load_js("components/sankey.js")
