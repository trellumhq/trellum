"""MetricView declarations, snapshot admission and MiniKpi result references."""
from trellum.components.metric_views import (
    MetricView, snapshot_parent, validate_binding, validate_definition,
)
from trellum.components.kpis import MiniKpi
from trellum.validation.walk import _walk_components


def check_metric_views(ctx, result):
    """Run per native view so a different scope cannot satisfy a missing parent."""
    sections = list(getattr(ctx, 'sections', []) or [])
    scopes = getattr(ctx, 'scopes', {}) or {}
    views = [sections + list(value) for value in scopes.values()] if scopes else [sections]
    for view in views:
        full = [comp for section in view for comp, _ in _walk_components(section.get('components', []))]
        earlier = []
        by_id = {}
        for section in view:
            for comp, title in _walk_components(section.get('components', []), section_title=section.get('title', '')):
                if isinstance(comp, MetricView):
                    try:
                        validate_definition(comp.id, comp.parent, comp.groups, comp.metrics)
                        snapshot_parent(comp, earlier, connections=full)
                        if comp.id in by_id:
                            raise ValueError('MetricView dataset id conflicts with an earlier dataset')
                    except (ValueError, TypeError, KeyError) as exc:
                        result.fail('metric-view-invalid', str(exc), component='MetricView', section=title, dataset_id=comp.id)
                elif isinstance(comp, MiniKpi) and comp.binding is not None:
                    try:
                        validate_binding(comp.binding, comp.value)
                        target = by_id.get(comp.binding['dataset_id'])
                        if not isinstance(target, MetricView):
                            raise ValueError('MiniKpi binding must reference an earlier MetricView')
                        if comp.binding['group'] not in {g['id'] for g in target.groups}:
                            raise ValueError('MiniKpi binding group is not declared')
                        if comp.binding['metric'] not in target.metrics:
                            raise ValueError('MiniKpi binding metric is not declared')
                    except (ValueError, TypeError, KeyError) as exc:
                        result.fail('mini-kpi-binding-invalid', str(exc), component='MiniKpi', section=title)
                if hasattr(comp, 'id'):
                    by_id[comp.id] = comp
                earlier.append(comp)
    result.ran('metric-views')


def check_column_formats(comps, result):
    """Dedicated mutation check; central validator dispatch can call this hook."""
    from trellum.components.tables import DataTable
    from trellum.components._table_column_formats import column_formats_config
    for comp, section in comps:
        if not isinstance(comp, DataTable):
            continue
        try:
            config = column_formats_config(comp.column_formats, comp.df, comp.columns or list(comp.df.columns))
            if config and comp._comparison is not None:
                raise ValueError('DataTable column_formats is unsupported on ComparisonTable')
        except ValueError as exc:
            result.fail('datatable-column-formats-invalid', str(exc), component='DataTable', section=section)
    result.ran('table-column-formats')
