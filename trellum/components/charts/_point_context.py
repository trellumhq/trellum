"""Strict per-series original-date context for ordinary wide LineChart points."""
from __future__ import annotations

from trellum.components.period_comparison import iso_day
from trellum.components.base import Component
from trellum.assets import load_js


class _LinePointContextAssets(Component):
    def render_html(self, ctx):
        return ""

    @classmethod
    def client_js(cls):
        return load_js("runtime/point_context.js")


def point_context_config(chart, ctx=None):
    value = getattr(chart, 'point_context', None)
    if value is None:
        return {}
    if chart.stack_by or chart.stack_by_options or chart.ratios or chart.monthly_average or chart.month_context:
        raise ValueError('LineChart point_context supports ordinary wide series only; dynamic splits, ratios and calendar overlays are unsupported')
    if not isinstance(value, list) or not value:
        raise ValueError('LineChart point_context requires a nonempty list of series declarations')
    from trellum.components.charts.line_area import _actual_line_captions
    captions = _actual_line_captions(chart)
    result, seen = [], set()
    for rule in value:
        if not isinstance(rule, dict):
            raise ValueError('LineChart point_context entries must be mappings')
        ref = rule.get('series')
        matches = ([ref] if type(ref) is int and 0 <= ref < len(captions) else
                   [i for i, label in enumerate(captions) if label == ref] if isinstance(ref, str) else [])
        if len(matches) != 1 or matches[0] in seen or captions.count(captions[matches[0]]) != 1:
            raise ValueError('LineChart point_context must identify each target series unambiguously once')
        index = matches[0]; seen.add(index)
        if chart.dataset_id:
            if set(rule) != {'series', 'date_column', 'window_column'} or any(
                not isinstance(rule[k], str) or not rule[k].strip() for k in ('date_column', 'window_column')):
                raise ValueError('Bound LineChart point_context requires only series, date_column, window_column')
            if not {rule['date_column'], rule['window_column'], chart.x} <= set(chart.df.columns):
                raise ValueError('LineChart point_context schema is missing date/window/x columns')
            item = dict(rule)
        else:
            if set(rule) != {'series', 'records'} or not isinstance(rule['records'], list) or len(rule['records']) != len(chart.df):
                raise ValueError('Static LineChart point_context requires one date/window record or null per source point')
            for record in rule['records']:
                if record is None:
                    continue
                if not isinstance(record, dict) or set(record) != {'date', 'window'}:
                    raise ValueError('LineChart point_context records require date and window only')
                iso_day(record['date'])
                if not isinstance(record['window'], str) or not record['window'].strip() or len(record['window']) > 240 or any(ord(c) < 32 for c in record['window']):
                    raise ValueError('LineChart point_context window must be literal plain text, at most 240 characters')
            item = {'series': index, 'records': [dict(r) if r else None for r in rule['records']]}
        result.append({**item, 'series': index})
    if ctx is not None and chart.dataset_id:
        validate_bound_source(chart, ctx, result)
    return {'point_context': result}


def validate_bound_source(chart, ctx, rules):
    """Rendering must reject unsupported bindings even without central dispatch."""
    configs = list(ctx.component_data.values())
    ds_id = chart.dataset_id
    source = next((c for c in configs if c.get('dataset_id') == ds_id or
                   c.get('type') == 'period_comparison' and ds_id in c.get('outputs', [])), None)
    if source and source['type'] == 'scoped_data_source':
        ds_id = source['parent_id']
        source = next((c for c in configs if c.get('dataset_id') == ds_id), None)
    required = {chart.x} | {r[k] for r in rules for k in ('date_column', 'window_column')}
    if source and source['type'] == 'period_comparison' and ds_id == source['dataset_id'] + '_daily':
        if not required <= set(source['output_schemas'][ds_id]):
            raise ValueError('LineChart point_context derived date/window schema is missing')
        return
    if not source or source['type'] != 'data_source' or getattr(ctx, '_chunk_manifests', {}).get(ds_id):
        raise ValueError('LineChart point_context requires an earlier ordinary unchunked snapshot or PeriodComparison daily output')
    if any(c.get('live_dataset') and {chart.dataset_id, ds_id} & set(c.get('propagate_to', {})) for c in configs):
        raise ValueError('LineChart point_context propagated-live bindings are unsupported')
    raw = ctx.raw_data_blocks.get('_ds_' + ds_id, {})
    cols, dictionaries = raw.get('_cols', []), raw.get('_dict', {})
    if not required <= set(cols):
        raise ValueError('LineChart point_context bound date/window schema is missing')
    positions = {c: cols.index(c) for c in required}
    def cell(row, column):
        value = row[positions[column]]
        return dictionaries[column][value] if column in dictionaries and value is not None else value
    seen = set()
    for row in raw.get('_data', []):
        x = str(cell(row, chart.x))
        if x in seen:
            raise ValueError('LineChart point_context requires unique x rows; ambiguous aggregation unsupported')
        seen.add(x)
        for rule in rules:
            original, window = cell(row, rule['date_column']), cell(row, rule['window_column'])
            if original is None:
                continue
            iso_day(original)
            if not isinstance(window, str) or not window.strip() or len(window) > 240 or any(ord(c) < 32 for c in window):
                raise ValueError('LineChart point_context bound window must be plain text')
