"""Sankey schema, effective snapshot, resource and live-connection checks."""

from trellum.components.charts.sankey import SankeyChart


def check_sankey_charts(comps, ds_map, result):
    """Check actual bound sources, not merely a component's author frame.

    ``comps`` follows the validator's (component, section) convention. Call
    after collecting ``ds_map`` and before applying suppressions.
    """
    from trellum.components.filterable import DataSource, FilterBar, LiveDataSource, ScopedDataSource

    def ancestry(ds_id):
        seen = set()
        while ds_id:
            if ds_id in seen or ds_id not in ds_map:
                raise ValueError("Sankey source is missing or cyclic")
            seen.add(ds_id)
            ds = ds_map[ds_id]
            if isinstance(ds, LiveDataSource):
                raise ValueError("Sankey does not support LiveDataSource ancestry")
            if isinstance(ds, ScopedDataSource):
                ds_id = ds.parent
                continue
            if not isinstance(ds, DataSource):
                raise ValueError("Sankey requires an ordinary snapshot DataSource")
            if ds.chunk_by:
                raise ValueError("Sankey requires a complete unchunked snapshot")
            return ds, seen
        raise ValueError("Sankey source is missing")

    for comp, section in comps:
        if not isinstance(comp, SankeyChart):
            continue
        try:
            comp.snapshot(comp.df)
            if comp.dataset_id:
                ds, connected = ancestry(comp.dataset_id)
                comp.snapshot(ds.df)
                changed = True
                while changed:
                    changed = False
                    # Scoped ancestry is part of the same filter connection.
                    for key, source in ds_map.items():
                        if isinstance(source, ScopedDataSource) and (key in connected or source.parent in connected):
                            group = {key, source.parent}
                            if not group <= connected:
                                connected.update(group)
                                changed = True
                    for fb, _ in comps:
                        if isinstance(fb, FilterBar):
                            group = {fb.dataset_id, *(fb.propagate_to or {})}
                            if group & connected and not group <= connected:
                                connected.update(group)
                                changed = True
                if any(isinstance(ds_map.get(key), LiveDataSource) for key in connected):
                    raise ValueError("Sankey does not support live-connected filter propagation")
            result.passed("sankey-valid", "Sankey schema, complete snapshot and graph bounds are valid.",
                          component="SankeyChart", section=section, dataset_id=comp.dataset_id or "")
        except (ValueError, TypeError, OverflowError) as exc:
            result.fail("sankey-invalid", str(exc), component="SankeyChart", section=section,
                        dataset_id=comp.dataset_id or "")
