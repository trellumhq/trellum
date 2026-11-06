"""Admission checks for author-supplied precomputed cohort snapshots."""
from __future__ import annotations

import json

from trellum.components.cohort_table import CohortTable
from trellum.components.filterable import DataSource, FilterBar, LiveDataSource, ScopedDataSource


def check_cohort_tables(comps, ds_map, result):
    """Validate effective sources, all source rows and participating filters.

    ``comps`` uses the standard flattened ``(component, section)`` pairs;
    ``ds_map`` is returned by ``_check_datasource_filterbar``. This check must
    run before rendering, including when the component's own df is smaller.
    """
    bars = [c for c, _ in comps if isinstance(c, FilterBar)]
    # Treat propagation as a connected scope: a live endpoint in either
    # direction cannot establish a stable, authored snapshot population.
    graph = {identity: set() for identity in ds_map}
    for identity, ds in ds_map.items():
        if isinstance(ds, ScopedDataSource):
            graph[identity].add(ds.parent)
            graph.setdefault(ds.parent, set()).add(identity)
    for bar in bars:
        for target in (bar.propagate_to or {}):
            graph.setdefault(bar.dataset_id, set()).add(target)
            graph.setdefault(target, set()).add(bar.dataset_id)

    for table, section in comps:
        if not isinstance(table, CohortTable):
            continue
        kw = dict(component="CohortTable", section=section, dataset_id=table.dataset_id or "")
        try:
            # Revalidate the declaration as well as the effective frame: both
            # are mutable after construction.
            table.snapshot(table.df)
            frame = table.df
            ancestors = set()
            source = table.dataset_id
            while source:
                if source in ancestors:
                    raise ValueError("CohortTable source ancestry is cyclic")
                ancestors.add(source)
                ds = ds_map.get(source)
                if isinstance(ds, LiveDataSource):
                    raise ValueError("CohortTable does not support LiveDataSource ancestry")
                if isinstance(ds, ScopedDataSource):
                    source = ds.parent
                elif isinstance(ds, DataSource):
                    frame = ds.df
                    break
                else:
                    raise ValueError(f"CohortTable effective snapshot DataSource {source!r} is missing")
            pending, connected = list(ancestors), set()
            while pending:
                node = pending.pop()
                if node in connected:
                    continue
                connected.add(node)
                if isinstance(ds_map.get(node), LiveDataSource):
                    raise ValueError("CohortTable does not support live-connected filter propagation")
                pending.extend(graph.get(node, set()) - connected)
            payload = table.snapshot(frame)
            forbidden = {table.horizon, table.observed, table.observed_state, table.forecast, table.reason}
            groups = {}
            for row in payload["data"]:
                groups.setdefault(row[table.cohort], []).append(row)

            def check_column(column):
                if column not in frame.columns:
                    raise ValueError(f"CohortTable participating filter column {column!r} is missing")
                if column in forbidden:
                    raise ValueError(f"CohortTable filter on {column!r} can slice precomputed cells")
                for rows in groups.values():
                    if len({json.dumps(row[column], ensure_ascii=False) for row in rows}) > 1:
                        raise ValueError(f"CohortTable filter column {column!r} must be constant within each cohort")

            if table.dataset_id:
                for bar in bars:
                    for spec in bar.filters:
                        column = spec.get("column")
                        if bar.dataset_id in ancestors:
                            check_column(column)
                        for target, mapping in (bar.propagate_to or {}).items():
                            if target in ancestors and column in mapping:
                                check_column(mapping[column])
            result.passed("cohort-table-snapshot", "Cohort source cells, metadata and whole-cohort filter scope are valid.", **kw)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            result.fail("cohort-table-invalid", str(error), **kw)
