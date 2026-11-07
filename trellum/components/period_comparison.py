"""Bounded UTC calendar-period projection over delivered snapshot facts."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace

import pandas as pd

from trellum.assets import load_js
from trellum.components.base import Component, RenderContext
from trellum.components.metric_views import validate_definition as metric_definition
from trellum.components.metric_views import validate_source as metric_source

_KEY = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
ROW_COLUMNS = ['metric', 'name', 'group', 'A', 'B', 'change', 'A_reason', 'B_reason', 'change_reason',
               'preset', 'A_start', 'A_end', 'B_start', 'B_end', 'A_days', 'B_days']


def iso_day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('PeriodComparison requires exact ISO UTC Gregorian daily dates')
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('PeriodComparison invalid Gregorian date') from exc


def _key(value):
    if not isinstance(value, str) or not _KEY.fullmatch(value) or value in {'constructor', 'prototype', '__proto__'}:
        raise ValueError('PeriodComparison requires safe distinct identifier keys (1–64 characters)')


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 120 or any(ord(c) < 32 for c in value):
        raise ValueError('PeriodComparison labels must be plain nonblank text, at most 120 characters')


class _PeriodMetricAssets(Component):
    def render_html(self, ctx):
        return ""

    @classmethod
    def client_js(cls):
        return load_js("components/metric_views.js")


@dataclass
class PeriodResult:
    """Schema and lineage descriptor; never contains another copy of facts."""
    id: str
    parent: str
    output_columns: list[str]


def validate_definition(view):
    for value in (view.id, view.parent, view.toggle_id):
        _key(value)
    if view.id == view.parent:
        raise ValueError('PeriodComparison cannot parent itself')
    _text(view.date_column)
    if not isinstance(view.presets, dict) or not 1 <= len(view.presets) <= 16 or view.default not in view.presets:
        raise ValueError('PeriodComparison requires 1–16 presets and a declared default')
    for name, preset in view.presets.items():
        _text(name)
        if name != name.strip() or any(c in name for c in '<>&"\''):
            raise ValueError('PeriodComparison preset labels must be safe native Toggle labels')
        if not isinstance(preset, dict) or set(preset) != {'A', 'B'}:
            raise ValueError('PeriodComparison presets require explicit A and B inclusive windows')
        for window in preset.values():
            if not isinstance(window, (tuple, list)) or len(window) != 2:
                raise ValueError('PeriodComparison window requires start and end')
            start, end = map(iso_day, window)
            if not 1 <= (end - start).days + 1 <= 366:
                raise ValueError('PeriodComparison windows must be ordered and contain 1–366 inclusive days')
    if not isinstance(view.groups, dict) or not 1 <= len(view.groups) <= 8:
        raise ValueError('PeriodComparison requires 1–8 authored groups')
    for key, label in view.groups.items():
        _key(key)
        _text(label)
    if not isinstance(view.metrics, dict) or not 1 <= len(view.metrics) <= 32:
        raise ValueError('PeriodComparison requires 1–32 metrics')
    allowed = {'definition', 'name', 'group', 'direction', 'format', 'precision', 'change_measure', 'change_precision', 'daily'}
    for key, spec in view.metrics.items():
        _key(key)
        if not isinstance(spec, dict) or set(spec) - allowed or not {'definition', 'name', 'group', 'direction', 'format', 'precision'} <= set(spec):
            raise ValueError('PeriodComparison metric requires explicit definition, name, group, direction, format, precision')
        _text(spec['name'])
        if spec['group'] not in view.groups or spec['direction'] not in {'neutral', 'higher', 'lower'} or spec['format'] not in {'number', 'currency', 'percent', 'ratio'}:
            raise ValueError('PeriodComparison metric group, direction or format is invalid')
        for prop in ('precision', 'change_precision'):
            if type(spec.get(prop, 2)) is not int or not 0 <= spec.get(prop, 2) <= 12:
                raise ValueError('PeriodComparison precision must be an integer 0–12')
        if type(spec.get('daily', False)) is not bool:
            raise ValueError('PeriodComparison daily must be bool')
        if spec.get('change_measure', 'relative') not in {'relative', 'percentage_points'}:
            raise ValueError('PeriodComparison change_measure must be relative or percentage_points')
        if spec.get('change_measure') == 'percentage_points' and spec['format'] != 'percent':
            raise ValueError('PeriodComparison percentage_points requires a declared fraction-unit percent metric')
    metric_definition(view.id, view.parent, [{'id': 'all', 'label': 'All'}], view.reducer_metrics)
    if set(view.groups) != {m['group'] for m in view.metrics.values()}:
        raise ValueError('PeriodComparison authored groups must have exact nonempty metric partitions')


def validate_source(view, rows, columns):
    metric_source(rows, columns, [], view.reducer_metrics)
    if view.date_column not in columns:
        raise ValueError('PeriodComparison source date column missing')
    for row in rows:
        iso_day(row.get(view.date_column))
    # Include date text in the same relevant-payload byte admission as metrics.
    from trellum.components.metric_views import MAX_BYTES
    payload = [{k: (None if r.get(k) is pd.NA or
                    (type(r.get(k)) is float and not math.isfinite(r[k])) else r.get(k))
                for k in view.source_columns} for r in rows]
    if len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()) > MAX_BYTES:
        raise ValueError('PeriodComparison relevant source payload exceeds 4 MiB')


def snapshot_parent(view, earlier, connections=None):
    from trellum.components.metric_views import snapshot_parent as metric_parent
    # Reuse the ordinary snapshot/live/scope admission; calendar_mean is a sum before division.
    adapter = SimpleNamespace(id=view.id, parent=view.parent, groups=[], metrics=view.reducer_metrics)
    parent = metric_parent(adapter, earlier, connections=connections)
    validate_source(view, parent.df[view.source_columns].to_dict('records'), list(parent.df.columns))
    return parent


@dataclass
class PeriodComparison(Component):
    """Nonvisual period registrar. Declare after its snapshot and native Toggle.

    Outputs: ``id`` (all rows), ``id_daily`` and ``id_group_<key>``.
    Every calculation follows the parent filters; group membership is authored.
    """
    id: str
    parent: str = field(kw_only=True)
    toggle_id: str = field(kw_only=True)
    default: str = field(kw_only=True)
    date_column: str = field(kw_only=True)
    presets: dict = field(kw_only=True)
    metrics: dict = field(kw_only=True)
    groups: dict = field(kw_only=True)
    _component_type: str = field(default='period_comparison', init=False, repr=False)
    _no_section_wrap: bool = field(default=True, init=False, repr=False)

    def __post_init__(self):
        validate_definition(self)

    @property
    def reducer_metrics(self):
        result = {}
        for key, spec in self.metrics.items():
            definition = spec['definition']
            if not isinstance(definition, dict):
                raise ValueError('PeriodComparison definition must be a mapping')
            if definition.get('agg') == 'calendar_mean':
                if set(definition) != {'agg', 'column'}:
                    raise ValueError('PeriodComparison calendar_mean requires only a declared sum column')
                definition = {**definition, 'agg': 'sum'}
            result[key] = definition
        return result

    @property
    def source_columns(self):
        return sorted({self.date_column} | {v for m in self.reducer_metrics.values() for k, v in m.items() if k != 'agg'})

    @property
    def daily_columns(self):
        return ['day', 'A_date', 'B_date', 'A_window', 'B_window', *[
            f'{key}_{side}{suffix}' for key, spec in self.metrics.items() if spec.get('daily')
            for side in ('A', 'B') for suffix in ('', '_state', '_reason')]]

    @property
    def outputs(self):
        return [PeriodResult(self.id, self.parent, ROW_COLUMNS), PeriodResult(self.id + '_daily', self.parent, self.daily_columns),
                *[PeriodResult(self.id + '_group_' + key, self.parent, ROW_COLUMNS) for key in self.groups]]

    def empty_frame(self, daily=False):
        numeric = {'day', 'A', 'B', 'change', 'A_days', 'B_days'} | {f'{k}_{s}' for k in self.metrics for s in ('A', 'B')}
        return pd.DataFrame({c: pd.Series(dtype='float64' if c in numeric else 'str') for c in (self.daily_columns if daily else ROW_COLUMNS)})

    def comparison_context(self):
        return {'metric_column': 'metric', 'period_labels': {'A': 'A', 'B': 'B'},
                'reason_columns': {c: c + '_reason' for c in ('A', 'B', 'change')},
                'metrics': {k: {p: s.get(p, 2) for p in ('direction', 'format', 'precision', 'change_precision')} |
                           {'change_measure': ('percentage_points' if s.get('change_measure') == 'percentage_points' else 'relative_percent')} for k, s in self.metrics.items()}}

    def render_html(self, ctx: RenderContext):
        validate_definition(self)
        configs = list(ctx.component_data.values())
        parent = next((c for c in configs if c.get('dataset_id') == self.parent), None)
        if parent and parent['type'] == 'scoped_data_source':
            parent = next((c for c in configs if c.get('dataset_id') == parent['parent_id']), None)
        if not parent or parent['type'] != 'data_source' or getattr(ctx, '_chunk_manifests', {}).get(parent['dataset_id']):
            raise ValueError('PeriodComparison requires an earlier ordinary unchunked snapshot')
        if any(c.get('live_dataset') and {self.parent, parent['dataset_id']} & set(c.get('propagate_to', {})) for c in configs):
            raise ValueError('PeriodComparison propagated-live parents are unsupported')
        raw = ctx.raw_data_blocks.get('_ds_' + parent['dataset_id'], {})
        if len(raw.get('_data', [])) > 10000:
            raise ValueError('PeriodComparison exceeds 10,000 source rows')
        cols, dictionaries = raw.get('_cols', []), raw.get('_dict', {})
        if len(set(cols)) != len(cols):
            raise ValueError('PeriodComparison source columns must be unique')
        positions = [(i, c) for i, c in enumerate(cols) if c in self.source_columns]
        rows = [{c: dictionaries[c][r[i]] if c in dictionaries else r[i] for i, c in positions} for r in raw.get('_data', [])]
        validate_source(self, rows, cols)
        ctx.render_child(_PeriodMetricAssets())
        cid = ctx.next_id()
        ctx.register(cid, {'type': 'period_comparison', 'dataset_id': self.id, 'parent_id': self.parent,
                          'toggle_id': self.toggle_id, 'default': self.default, 'date_column': self.date_column,
                          'presets': self.presets, 'metrics': self.metrics, 'groups': self.groups,
                          'source_columns': self.source_columns, 'outputs': [o.id for o in self.outputs],
                          'output_schemas': {o.id: o.output_columns for o in self.outputs}})
        return f'<div id="{cid}" hidden></div>'

    @classmethod
    def client_js(cls):
        return load_js('components/period_comparison.js')
