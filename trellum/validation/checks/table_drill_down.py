"""Check row-action populations and existing dropdown ownership."""

from trellum.components._table_drill_down import drill_down_config
from trellum.validation.walk import _resolve_ds_columns


def check_table_drill_down(comps, ds_map, result):
    from trellum.components.filterable import FilterBar, LiveDataSource, ScopedDataSource
    from trellum.components.filters.dropdown import validate_parent_clear
    from trellum.components.tables import DataTable

    bars = [comp for comp, _ in comps if isinstance(comp, FilterBar)]
    graph = {key: set() for key in ds_map}

    def connect(a, b):
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)

    for key, ds in ds_map.items():
        if isinstance(ds, ScopedDataSource):
            connect(key, ds.parent)
    for bar in bars:
        for target in bar.propagate_to or {}:
            connect(bar.dataset_id, target)
    live_connected = set()
    pending = [key for key, ds in ds_map.items() if isinstance(ds, LiveDataSource)]
    while pending:
        key = pending.pop()
        if key in live_connected:
            continue
        live_connected.add(key)
        pending.extend(graph.get(key, ()))

    for comp, sec in comps:
        if isinstance(comp, FilterBar):
            for f in comp.filters:
                try:
                    validate_parent_clear(f)
                except ValueError as error:
                    result.fail("filter-parent-clear-invalid", str(error), component="FilterBar", section=sec)
        if not isinstance(comp, DataTable) or comp.drill_down is None:
            continue
        try:
            cfg = drill_down_config(comp.drill_down, comp.df, comp.columns or list(comp.df.columns))
            population = comp.dataset_id
            target = cfg["dataset_id"]
            if target not in ds_map:
                raise ValueError("drill_down target dataset must exist")
            if population in live_connected or target in live_connected:
                raise ValueError("drill_down does not support live datasets, live-backed scopes or live-connected propagation")
            population_cols = _resolve_ds_columns(population, ds_map) if population else set(comp.df.columns)
            if not {cfg["value_column"], cfg["label_column"]} <= population_cols:
                raise ValueError("drill_down value_column and label_column must exist in the effective table datasource")
            target_cols = _resolve_ds_columns(target, ds_map)
            for column in {cfg["column"], *(item["column"] for item in cfg["context"])}:
                owners = [f for bar in bars if bar.dataset_id == target for f in bar.filters
                          if f.get("column") == column]
                if (column not in target_cols or len(owners) != 1
                        or owners[0].get("type", "dropdown") != "dropdown"):
                    raise ValueError(f"drill_down '{column}' needs exactly one compatible existing dropdown on '{target}'")
        except ValueError as error:
            result.fail("datatable-drill-down-invalid", str(error), component="DataTable", section=sec)
