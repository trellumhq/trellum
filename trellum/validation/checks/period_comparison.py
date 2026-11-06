"""Per-native-scope period projection and strict point-context admission."""
from trellum.components.period_comparison import PeriodComparison, snapshot_parent, validate_definition
from trellum.components.charts.line_area import LineChart
from trellum.components.charts._point_context import point_context_config
from trellum.components.controls import Toggle
from trellum.components.filterable import DataSource, ScopedDataSource, LiveDataSource
from trellum.components.metric_views import MetricView
from trellum.validation.walk import _walk_components, _resolve_ds_columns


def check_period_comparison(ctx, result):
    sections = list(getattr(ctx, 'sections', []) or [])
    scopes = getattr(ctx, 'scopes', {}) or {}
    views = [sections + list(s) for s in scopes.values()] if scopes else [sections]
    for view in views:
        full = [(c, title) for section in view for c, title in _walk_components(section.get('components', []), section_title=section.get('title', ''))]
        earlier, datasets = [], {}
        for comp, title in full:
            if isinstance(comp, PeriodComparison):
                try:
                    validate_definition(comp)
                    snapshot_parent(comp, earlier, connections=[c for c, _ in full])
                    owners = [c for c in earlier if isinstance(c, Toggle) and c.id == comp.toggle_id]
                    if len(owners) != 1 or owners[0].options != list(comp.presets) or owners[0].default != comp.default:
                        raise ValueError('PeriodComparison requires one earlier native Toggle with identical ordered preset labels and default')
                    for output in comp.outputs:
                        if output.id in datasets:
                            raise ValueError('PeriodComparison output id collides with an earlier dataset')
                        datasets[output.id] = output
                except (ValueError, TypeError, KeyError) as exc:
                    result.fail('period-comparison-invalid', str(exc), component='PeriodComparison', section=title, dataset_id=comp.id)
            elif isinstance(comp, LineChart) and getattr(comp, 'point_context', None) is not None:
                try:
                    cfg = point_context_config(comp)['point_context']
                    if comp.dataset_id:
                        cols = _resolve_ds_columns(comp.dataset_id, datasets)
                        if not cols or any(not {r['date_column'], r['window_column']} <= cols for r in cfg):
                            raise ValueError('LineChart point_context must reference earlier known date/window schema')
                        ds = datasets.get(comp.dataset_id)
                        if isinstance(ds, ScopedDataSource):
                            ds = datasets.get(ds.parent)
                        from trellum.components.period_comparison import PeriodResult
                        if not isinstance(ds, (DataSource, PeriodResult)) or getattr(ds, 'chunk_by', None):
                            raise ValueError('LineChart point_context requires an ordinary unchunked snapshot or PeriodComparison daily output')
                        frame = getattr(ds, 'df', None)
                        if frame is not None and len(frame):
                            if frame[comp.x].duplicated().any():
                                raise ValueError('LineChart point_context requires unique x rows; ambiguous aggregation unsupported')
                            for rule in cfg:
                                from trellum.components.period_comparison import iso_day
                                for d, w in zip(frame[rule['date_column']], frame[rule['window_column']]):
                                    if d is None:
                                        continue
                                    iso_day(d)
                                    if not isinstance(w, str) or not w.strip() or len(w) > 240 or any(ord(c) < 32 for c in w):
                                        raise ValueError('LineChart point_context window must be plain text')
                except (ValueError, TypeError, KeyError) as exc:
                    result.fail('line-chart-point-context-invalid', str(exc), component='LineChart', section=title)
            if isinstance(comp, (DataSource, ScopedDataSource, LiveDataSource, MetricView)):
                if comp.id in datasets:
                    result.fail('period-comparison-output-collision', 'Dataset id collides with an earlier declaration', section=title)
                datasets[comp.id] = comp
            earlier.append(comp)
    result.ran('period-comparison')
