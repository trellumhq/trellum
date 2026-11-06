"""Bounded, read-only metric rows over delivered snapshot facts."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import re

import pandas as pd

from trellum.assets import load_js
from trellum.components.base import Component, RenderContext

MAX_ROWS = 10_000
MAX_BYTES = 4 * 1024 * 1024
MAX_SAFE = 2**53 - 1
_RESERVED = {'group_id', 'group_label', 'matching_rows', 'Notes', '__proto__', 'constructor', 'prototype'}
_KEY = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')


def _text(value, name, maximum=120):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'MetricView {name} must be nonblank text of at most {maximum} characters')


def validate_definition(id, parent, groups, metrics):
    for value, name in ((id, 'id'), (parent, 'parent')):
        _text(value, name, 64)
        if value in {'__proto__', 'constructor', 'prototype'}:
            raise ValueError('MetricView dataset ids must not use prototype property names')
    if id == parent:
        raise ValueError('MetricView cannot parent itself or form a cycle')
    if not isinstance(groups, list) or not 1 <= len(groups) <= 32:
        raise ValueError('MetricView requires 1–32 groups')
    seen = set()
    for group in groups:
        if not isinstance(group, dict) or set(group) - {'id', 'label', 'where'} or not {'id', 'label'} <= set(group):
            raise ValueError('MetricView groups require id, label and optional where only')
        _text(group['id'], 'group id', 64)
        _text(group['label'], 'group label')
        if group['id'] in seen:
            raise ValueError('MetricView group ids must be unique')
        seen.add(group['id'])
        if 'where' in group:
            where = group['where']
            if not isinstance(where, dict) or set(where) != {'column', 'values'}:
                raise ValueError('MetricView where requires column and values only')
            _text(where['column'], 'membership column', 128)
            values = where['values']
            if not isinstance(values, list) or not 1 <= len(values) <= 32:
                raise ValueError('MetricView membership requires 1–32 literal string keys')
            for value in values:
                _text(value, 'membership key', 120)
            if len(set(values)) != len(values):
                raise ValueError('MetricView membership keys must be unique')
    if not isinstance(metrics, dict) or not 1 <= len(metrics) <= 32:
        raise ValueError('MetricView requires 1–32 metrics')
    output = set(_RESERVED)
    for key, spec in metrics.items():
        if not isinstance(key, str) or not _KEY.fullmatch(key) or key in output:
            raise ValueError('MetricView needs safe unique output-column keys; reserved/status/reason names cannot collide')
        output.add(key)
        if not isinstance(spec, dict):
            raise ValueError('MetricView metric definitions must be mappings')
        agg = spec.get('agg')
        keys = {'sum': {'agg', 'column'}, 'count': {'agg'}, 'ratio': {'agg', 'numerator', 'denominator'}}.get(agg) if isinstance(agg, str) else None
        if keys is None or set(spec) != keys:
            raise ValueError('MetricView supports only exact sum, count and ratio-of-sums definitions')
        for col in keys - {'agg'}:
            _text(spec[col], 'metric source column', 128)
    generated = [f'{key}_{suffix}' for key in metrics for suffix in ('status', 'reason')]
    if len(set(generated)) != len(generated) or set(generated) & output:
        raise ValueError('MetricView output metric/status/reason columns conflict')


def validate_binding(binding, value=None):
    if binding is None:
        return
    if not isinstance(binding, dict) or set(binding) != {'dataset_id', 'group', 'metric'}:
        raise ValueError('MiniKpi binding requires exactly dataset_id, group, metric')
    for key, item in binding.items():
        _text(item, f'binding {key}', 64)
    if value is not None:
        raise ValueError('MiniKpi supplied value plus binding is ambiguous; use value=None')


def source_columns(groups, metrics):
    return sorted({g['where']['column'] for g in groups if 'where' in g} |
                  {v for spec in metrics.values() for k, v in spec.items() if k != 'agg'})


def validate_source(rows, columns, groups, metrics):
    """Check complete payload, before filtering; null/nonfinite inputs stay unavailable."""
    required = source_columns(groups, metrics)
    if not set(required) <= set(columns):
        raise ValueError(f'MetricView source columns missing: {sorted(set(required) - set(columns))}')
    if len(rows) > MAX_ROWS:
        raise ValueError('MetricView exceeds 10,000 complete source rows; deliver a smaller complete snapshot')
    numeric = {v for spec in metrics.values() for k, v in spec.items() if k != 'agg'}
    payload = []
    for row in rows:
        relevant = {}
        for col in required:
            val = row.get(col)
            if col in numeric and val is not None and val is not pd.NA:
                if type(val) not in (int, float):
                    raise ValueError(f'MetricView {col}: use plain native numeric additive facts; Decimal/string/bool inputs need explicit precision-safe preparation')
                if type(val) is int and abs(val) > MAX_SAFE:
                    raise ValueError(f'MetricView {col}: unsafe integer; prepare precision-safe units before delivery')
            relevant[col] = None if val is pd.NA or (type(val) is float and not math.isfinite(val)) else val
        payload.append(relevant)
    if len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()) > MAX_BYTES:
        raise ValueError('MetricView relevant source payload exceeds 4 MiB; deliver a smaller complete snapshot')


def snapshot_parent(view, earlier, *, connections=None):
    """Resolve one optional scope; reject live admission and chunked/chained results."""
    from trellum.components.filterable import DataSource, FilterBar, ScopedDataSource
    sources = {c.id: c for c in earlier if isinstance(c, (DataSource, ScopedDataSource, MetricView))}
    parent = sources.get(view.parent)
    if isinstance(parent, ScopedDataSource):
        parent = sources.get(parent.parent)
    if type(parent) is not DataSource or parent.chunk_by:
        raise ValueError('MetricView parent must be an earlier ordinary unchunked snapshot DataSource, optionally through one ScopedDataSource; live/chained/missing/cyclic parents are unsupported')
    connections = earlier if connections is None else connections
    for comp in connections:
        if isinstance(comp, FilterBar) and getattr(comp, 'propagate_to', None) and {view.parent, parent.id} & set(comp.propagate_to):
            # Live FilterBars can propagate server-result scope to snapshots.
            from trellum.components.live_filterable import LiveDataSource
            if any(isinstance(c, LiveDataSource) and c.id == comp.dataset_id for c in connections):
                raise ValueError('MetricView propagated-live parents are unsupported')
    if len(parent.df) > MAX_ROWS:
        raise ValueError('MetricView exceeds 10,000 complete source rows; deliver a smaller complete snapshot')
    if len(parent.df) and not len(parent.df.columns):
        raise ValueError('MetricView source facts require at least one delivered column')
    if not parent.df.columns.is_unique:
        raise ValueError('MetricView source columns must be unique')
    required = source_columns(view.groups, view.metrics)
    if not set(required) <= set(parent.df.columns):
        raise ValueError(f'MetricView source columns missing: {sorted(set(required) - set(parent.df.columns))}')
    records = parent.df[required].to_dict('records') if required else [{} for _ in range(len(parent.df))]
    validate_source(records, list(parent.df.columns), view.groups, view.metrics)
    return parent


@dataclass
class MetricView(Component):
    """Publish named grouped sum/count/ratio rows from selected complete facts.

    See ``docs/report-metric-views.md`` for the deterministic output schema,
    unavailable reasons, limits, and filter lineage.
    """
    id: str
    parent: str = field(kw_only=True)
    groups: list[dict] = field(kw_only=True)
    metrics: dict[str, dict] = field(kw_only=True)
    _component_type: str = field(default='metric_view', init=False, repr=False)
    _no_section_wrap: bool = field(default=True, init=False, repr=False)

    def __post_init__(self):
        validate_definition(self.id, self.parent, self.groups, self.metrics)

    @property
    def output_columns(self):
        return ['group_id', 'group_label', 'matching_rows', *self.metrics,
                *[f'{key}_{suffix}' for key in self.metrics for suffix in ('status', 'reason')], 'Notes']

    def empty_frame(self):
        """Schema-only frame for native DataTable, without duplicating source rows."""
        return pd.DataFrame({col: pd.Series(dtype='float64' if col in self.metrics or col == 'matching_rows' else 'str')
                             for col in self.output_columns})

    def render_html(self, ctx: RenderContext):
        validate_definition(self.id, self.parent, self.groups, self.metrics)
        configs = list(ctx.component_data.values())
        parent = next((c for c in configs if c.get('dataset_id') == self.parent), None)
        if parent and parent['type'] == 'scoped_data_source':
            parent = next((c for c in configs if c.get('dataset_id') == parent['parent_id']), None)
        if not parent or parent['type'] != 'data_source' or getattr(ctx, '_chunk_manifests', {}).get(parent['dataset_id']):
            raise ValueError('MetricView requires an earlier unchunked snapshot parent; validate_report checks the complete source contract')
        raw = ctx.raw_data_blocks.get('_ds_' + parent['dataset_id'], {})
        cols, dictionaries = raw.get('_cols', []), raw.get('_dict', {})
        if len(raw.get('_data', [])) > MAX_ROWS:
            raise ValueError('MetricView exceeds 10,000 complete source rows')
        if len(set(cols)) != len(cols):
            raise ValueError('MetricView source columns must be unique')
        if any(c.get('live_dataset') and {self.parent, parent['dataset_id']} & set(c.get('propagate_to', {})) for c in configs):
            raise ValueError('MetricView propagated-live parents are unsupported')
        required = set(source_columns(self.groups, self.metrics))
        positions = [(i, col) for i, col in enumerate(cols) if col in required]
        rows = [{col: dictionaries[col][row[i]] if col in dictionaries else row[i]
                 for i, col in positions} for row in raw.get('_data', [])]
        validate_source(rows, cols, self.groups, self.metrics)
        cid = ctx.next_id()
        ctx.register(cid, {'type': 'metric_view', 'dataset_id': self.id, 'parent_id': self.parent,
                           'groups': self.groups, 'metrics': self.metrics, 'columns': self.output_columns})
        return f'<div id="{cid}" hidden></div>'

    @classmethod
    def client_js(cls):
        return load_js('components/metric_views.js')
