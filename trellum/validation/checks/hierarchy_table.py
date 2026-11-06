"""Full snapshot scope and shape checks for optional hierarchy tables."""

from decimal import Decimal

import pandas as pd

from trellum.components.hierarchy_table import HierarchyTable, typed_dimension


def _check_bound_identity(comp, frame):
    """Refuse a snapshot codec that merges distinct typed dimension keys."""
    from trellum.components.filterable import _serialize_columnar

    names = [*comp.rows] + ([comp.column] if comp.column else [])
    encoded = _serialize_columnar(frame[names])
    dictionaries = encoded.get("_dict", {})
    for raw, delivered in zip(frame[names].itertuples(index=False, name=None), encoded["_data"]):
        for index, name in enumerate(names):
            value = delivered[index]
            if name in dictionaries:
                value = dictionaries[name][value]
            if typed_dimension(raw[index]) != typed_dimension(value):
                raise ValueError(
                    "HierarchyTable: DataSource encoding changes typed dimension "
                    "identity; use static typed facts or distinct string keys"
                )


def check_hierarchy_tables(comps, ds_map, result):
    """Run after datasource collection, including for hidden/revealed views."""
    from trellum.components.filterable import DataSource, FilterBar, LiveDataSource, ScopedDataSource

    graph = {key: set() for key in ds_map}

    def connect(a, b):
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)

    for identity, source in ds_map.items():
        if isinstance(source, ScopedDataSource):
            connect(identity, source.parent)
    for other, _ in comps:
        if isinstance(other, FilterBar):
            for target in other.propagate_to or {}:
                connect(other.dataset_id, target)
    live_connected, pending = set(), [key for key, source in ds_map.items() if isinstance(source, LiveDataSource)]
    while pending:
        identity = pending.pop()
        if identity not in live_connected:
            live_connected.add(identity)
            pending.extend(graph.get(identity, ()))

    def ancestry(identity):
        seen = set()
        while identity:
            if identity in seen or identity not in ds_map:
                raise ValueError("HierarchyTable: missing or cyclic source ancestry")
            seen.add(identity)
            source = ds_map[identity]
            if isinstance(source, LiveDataSource):
                raise ValueError("HierarchyTable: live ancestry cannot supply complete parent scope")
            if isinstance(source, ScopedDataSource):
                identity = source.parent
            elif isinstance(source, DataSource):
                if source.chunk_by:
                    raise ValueError("HierarchyTable: chunked source cannot guarantee complete population")
                return seen, source.df
            else:
                raise ValueError("HierarchyTable: unsupported source")
        raise ValueError("HierarchyTable: source is missing")

    for comp, section in comps:
        if not isinstance(comp, HierarchyTable):
            continue
        try:
            frame = comp.df
            if comp.dataset_id:
                chain, frame = ancestry(comp.dataset_id)
                if chain & live_connected:
                    raise ValueError("HierarchyTable: live ancestry or live-connected propagation cannot supply complete parent scope")
            comp.prepare(frame)
            if comp.dataset_id:
                _check_bound_identity(comp, frame)
                for value in frame[comp.value]:
                    if isinstance(value, Decimal) or (pd.api.types.is_integer(value) and
                            not -(2**63) <= int(value) <= 2**64 - 1):
                        raise ValueError("HierarchyTable: source cannot deliver Decimal or integers outside JSON source range; use static typed facts for exact values")
        except (ValueError, TypeError, OverflowError) as error:
            result.fail("hierarchy-table-invalid", str(error), component="HierarchyTable", section=section)
    result.ran("hierarchy_tables")
