"""Live queries: declarations, the sources behind them, and the components
that reference them.

A live query is a promise the artifact makes about a host that isn't there
at build time, so everything checkable has to be checked now: a dangling
query id or a file-backed datasource fails loudly here rather than as a 404
or a driver error on somebody's production host.
"""

from __future__ import annotations

from typing import Any

from trellum.validation.result import ValidationResult

_PARAM_TYPES = ("int", "float", "str", "date", "enum")

def _sql_placeholders(sql: str, dialect: str = "standard") -> set[str]:
    """Named ``:param`` placeholders in the SQL.

    String literals, quoted identifiers, and comments are skipped,
    so a colon inside ``'12:30'``, ``"a:b"`` or ``-- :note`` never reads as
    a placeholder, and ``::type`` casts are skipped by the shared tokenizer.
    """
    from trellum.data.query import _sql_param_spans

    return {name for _, _, name in _sql_param_spans(sql, dialect)}


def _source_type(ctx: Any, name: str) -> str | None:
    """The declared type of a data source, or None when it cannot be found."""
    try:
        source = ctx._resolve_source(name)
    except Exception:
        return None
    return str(source.get("type", ""))


def _is_sql_source(source_type: str) -> bool:
    """True when a registered connection driver exists for this type.

    Driver registration is the definition of "SQL source" here: sqlite and
    duckdb are drivers, file/api/excel/onedrive readers are not, and a
    project-registered custom driver counts — the host will execute the
    manifest's SQL through the same registry.
    """
    from trellum.data.drivers import get_driver

    try:
        get_driver(source_type)
    except ValueError:
        return False
    return True


def _check_live_queries(ctx: Any, comps: list[tuple[Any, str]], result: ValidationResult) -> None:
    """Category: live-query declarations and references."""
    registry: dict[str, dict] = getattr(ctx, "live_queries", None) or {}

    _check_declarations(ctx, registry, result)
    _check_legacy_datatable_config(comps, result)

    live_ds_list, live_ds_by_id = _index_live_datasources(comps, registry, result)
    _check_filterbar_bindings(comps, registry, live_ds_by_id, result)
    _check_explicit_bindings(live_ds_list, registry, result)


def _check_declarations(ctx: Any, registry: dict[str, dict], result: ValidationResult) -> None:
    """Param schema, SQL/param agreement, and datasource type -- for every
    declared query, independent of whether anything references it yet."""
    from trellum.data.query import sql_dialect_for

    for qid, q in registry.items():
        ds_name = q.get("datasource") or ""
        ds_type = _source_type(ctx, ds_name)
        for p in q.get("params") or []:
            _check_one_param_schema(qid, p, result)

        # The SQL and its params must agree, both directions.
        declared_names = {
            str(p.get("name")) for p in q.get("params") or [] if p.get("name")
        }
        placeholders = _sql_placeholders(
            str(q.get("sql") or ""), sql_dialect_for(ds_type or ""),
        )
        for name in sorted(placeholders - declared_names):
            result.fail(
                "live-query-sql-params",
                f"Live query '{qid}' SQL references :{name}, which is not a "
                f"declared param. The host binds exactly the declared "
                f"params, so :{name} would reach the database "
                f"unsubstituted.",
                component="declare_live_query",
            )
        for name in sorted(declared_names - placeholders):
            result.warn(
                "live-query-sql-params",
                f"Live query '{qid}' declares param '{name}' but the SQL "
                f"never references :{name} — the value would be accepted, "
                f"coerced, and ignored. Usually a typo on one side.",
                component="declare_live_query",
            )

        if ds_type is None:
            result.fail(
                "live-query-source-not-sql",
                f"Live query '{qid}' names datasource '{ds_name}', which is "
                f"not declared in report.yaml or data-sources/config.yaml. "
                f"The serving host could never resolve it.",
                component="declare_live_query",
            )
        elif not _is_sql_source(ds_type):
            result.fail(
                "live-query-source-not-sql",
                f"Live query '{qid}' runs against '{ds_name}' "
                f"(type '{ds_type}'), which has no SQL connection driver. "
                f"Live lookups execute SQL on demand — declare them on a "
                f"SQL source (sqlite, duckdb, postgres, ...), not on "
                f"file/API sources.",
                component="declare_live_query",
            )


def _check_one_param_schema(qid: str, p: dict, result: ValidationResult) -> None:
    name = p.get("name") or ""
    ptype = p.get("type") or ""
    if not name or ptype not in _PARAM_TYPES:
        result.fail(
            "live-query-param-schema",
            f"Live query '{qid}' param {name or '(unnamed)'!r} has "
            f"type {ptype!r}. Params need a name and a type from "
            f"int|float|str|date|enum.",
            component="declare_live_query",
        )
    elif ptype == "enum" and not p.get("values"):
        result.fail(
            "live-query-param-schema",
            f"Live query '{qid}' param '{name}' is an enum without "
            f"'values'. An enum with no values can never be "
            f"satisfied by the host.",
            component="declare_live_query",
        )
    elif not p.get("required", True):
        result.fail(
            "live-query-param-schema",
            f"Live query '{qid}' param '{name}' is required: false, "
            f"which the execution model does not support: params "
            f"bind by literal substitution, so an omitted value "
            f"would leave :{name} in the SQL unsubstituted and fail "
            f"on the host's database. Declare it required.",
            component="declare_live_query",
        )


def _check_legacy_datatable_config(comps: list[tuple[Any, str]], result: ValidationResult) -> None:
    """Clean break: the removed M1 ``DataTable(live=...)`` form. Loud in two
    places by owner decision: DataTable.render_html raises (tables.py), and
    this FAILs at validation time so the mistake is named with its section
    before the build ever reaches that raise."""
    for comp, sec in comps:
        live = getattr(comp, "live", None)
        if isinstance(live, dict) and type(comp).__name__ == "DataTable":
            result.fail(
                "live-query-legacy-config",
                "DataTable(live=...) is the removed M1 form -- it was "
                "replaced by LiveDataSource + FilterBar param bindings. "
                "See docs/COMPATIBILITY.md.",
                component="DataTable", section=sec,
            )


def _index_live_datasources(
    comps: list[tuple[Any, str]], registry: dict[str, dict], result: ValidationResult,
) -> tuple[list[tuple[Any, str]], dict[str, Any]]:
    """Every declared LiveDataSource, checked against its query (unknown /
    no-snapshot / duplicate-per-scope), and indexed by id for the FilterBar
    checks that follow."""
    from trellum.components.filterable import LiveDataSource

    live_ds_list: list[tuple[Any, str]] = []
    live_ds_by_id: dict[str, Any] = {}
    seen_query_scopes: dict[tuple[str, str], str] = {}  # (query_id, section) -> ds id
    for comp, sec in comps:
        if not isinstance(comp, LiveDataSource):
            continue
        live_ds_list.append((comp, sec))
        live_ds_by_id[comp.id] = comp

    for comp, sec in live_ds_list:
        qid = comp.query
        if qid not in registry:
            result.fail(
                "live-query-unknown",
                f"LiveDataSource '{comp.id}' references live query '{qid}', "
                f"which was never declared via ctx.declare_live_query(). "
                f"Declared: {sorted(registry) or '(none)'}.",
                component="LiveDataSource", section=sec,
            )
            continue
        declared_q = registry[qid]
        if not declared_q.get("has_snapshot"):
            # Escalated from WARN (the old per-table control) to FAIL: the
            # dataset IS the initial content now -- with no snapshot there
            # are no rows for every chart/table/KPI bound to it to show
            # before the first live commit, standalone or hosted.
            result.fail(
                "live-query-no-snapshot",
                f"LiveDataSource '{comp.id}' uses live query '{qid}' but "
                f"the declaration gave no snapshot_params, so the dataset "
                f"has no rows to render until the first live commit. Pass "
                f"snapshot_params to declare_live_query().",
                component="LiveDataSource", section=sec,
            )
        key = (qid, sec)
        if key in seen_query_scopes:
            result.fail(
                "live-query-duplicate-datasource",
                f"Live query '{qid}' is registered by more than one "
                f"LiveDataSource in section '{sec}' "
                f"('{seen_query_scopes[key]}' and '{comp.id}'). At most one "
                f"LiveDataSource per query per scope.",
                component="LiveDataSource", section=sec,
            )
        else:
            seen_query_scopes[key] = comp.id

    return live_ds_list, live_ds_by_id


def _check_filterbar_bindings(
    comps: list[tuple[Any, str]], registry: dict[str, dict],
    live_ds_by_id: dict[str, Any], result: ValidationResult,
) -> None:
    """Every FilterBar bound to a live dataset: each filter names a param
    (or min/max pair), every named param is declared, filter type agrees
    with param type, live-bound dropdowns are single-select, and every
    declared param ends up covered by some filter."""
    from trellum.components.filterable import FilterBar

    for comp, sec in comps:
        if not isinstance(comp, FilterBar):
            continue
        target = live_ds_by_id.get(comp.dataset_id)
        if target is None:
            continue  # an ordinary FilterBar -- nothing live-specific to check
        declared_q = registry.get(target.query) or {}
        schema_by_name = {
            str(p.get("name")): p for p in declared_q.get("params") or []
            if isinstance(p, dict)
        }
        bound_names = _check_filterbar_filters(comp, sec, target.query, schema_by_name, result)

        uncovered = sorted(set(schema_by_name) - bound_names)
        if uncovered:
            result.fail(
                "live-query-param-uncovered",
                f"Live query '{target.query}' declares param(s) {uncovered} "
                f"that no filter on FilterBar (dataset '{comp.dataset_id}') "
                f"binds -- they can never change from their snapshot "
                f"default. Every declared param needs a binding.",
                component="FilterBar", section=sec,
            )


def _check_filterbar_filters(
    comp: Any, sec: str, query_id: str, schema_by_name: dict, result: ValidationResult,
) -> set[str]:
    """Per-filter checks for one live FilterBar; returns the param names it
    binds (for the caller's coverage check)."""
    bound_names: set[str] = set()
    for f in comp.filters:
        if not isinstance(f, dict):
            continue
        ftype = f.get("type", "dropdown")
        param = f.get("param")
        min_param = f.get("min_param")
        max_param = f.get("max_param")
        names = [n for n in (param, min_param, max_param) if n]
        if not names:
            result.fail(
                "live-query-filter-missing-param",
                f"FilterBar bound to live dataset '{comp.dataset_id}' has "
                f"a '{ftype}' filter on column '{f.get('column')}' with "
                f"no 'param' (or 'min_param'/'max_param'). It can never "
                f"change the live query from its snapshot default.",
                component="FilterBar", section=sec,
            )
            continue
        for n in names:
            bound_names.add(n)
            if n not in schema_by_name:
                result.fail(
                    "live-query-param-uncovered",
                    f"FilterBar filter on column '{f.get('column')}' "
                    f"binds param '{n}', which live query "
                    f"'{query_id}' never declared. Declared params: "
                    f"{sorted(schema_by_name) or '(none)'}.",
                    component="FilterBar", section=sec,
                )
        _check_filter_type_param_compat(result, sec, f, ftype, schema_by_name,
                                         param, min_param, max_param, comp.df)
        if ftype == "dropdown" and f.get("multi", True) and param:
            result.fail(
                "live-query-dropdown-multi-unsupported",
                f"FilterBar dropdown on column '{f.get('column')}' binds "
                f"live param '{param}' with multi-select (the default). "
                f"Live-bound dropdowns are single-select in v1 -- pass "
                f"\"multi\": False.",
                component="FilterBar", section=sec,
            )
    return bound_names


def _check_explicit_bindings(
    live_ds_list: list[tuple[Any, str]], registry: dict[str, dict], result: ValidationResult,
) -> None:
    """Explicit ``LiveDataSource(bindings=...)`` (the propagate_to-only path
    -- no FilterBar of its own) gets the same coverage check."""
    for comp, sec in live_ds_list:
        if not comp.bindings:
            continue
        declared_q = registry.get(comp.query) or {}
        schema_by_name = {
            str(p.get("name")): p for p in declared_q.get("params") or []
            if isinstance(p, dict)
        }
        bound_names: set[str] = set()
        for b in comp.bindings:
            if not isinstance(b, dict):
                continue
            param, min_param, max_param = b.get("param"), b.get("min_param"), b.get("max_param")
            for n in (param, min_param, max_param):
                if n:
                    bound_names.add(n)
                    if n not in schema_by_name:
                        result.fail(
                            "live-query-param-uncovered",
                            f"LiveDataSource '{comp.id}' explicit binding on "
                            f"column '{b.get('column')}' names param '{n}', "
                            f"which live query '{comp.query}' never declared.",
                            component="LiveDataSource", section=sec,
                        )
        uncovered = sorted(set(schema_by_name) - bound_names)
        if uncovered:
            result.fail(
                "live-query-param-uncovered",
                f"LiveDataSource '{comp.id}' declares explicit bindings but "
                f"leaves param(s) {uncovered} unbound -- they can never "
                f"change from their snapshot default.",
                component="LiveDataSource", section=sec,
            )


def _check_filter_type_param_compat(
    result: ValidationResult, sec: str, f: dict, ftype: str,
    schema_by_name: dict, param: str | None, min_param: str | None, max_param: str | None,
    df: Any,
) -> None:
    """Filter-type <-> param-type compatibility, per the redesign's mapping
    table: dropdown/toggle/flag bind an ``enum``; slider/date_range bind
    numeric/date scalars (range-mode via two params); text binds any scalar.
    An hourly date_range bound live is a FAIL -- the ``date`` param type is
    strict ISO dates in v1 (hourly binding is a documented follow-up)."""
    col = f.get("column", "")

    def _fail(msg: str) -> None:
        result.fail("live-query-filter-type-mismatch", msg, component="FilterBar", section=sec)

    if ftype in ("dropdown", "toggle", "flag"):
        if param and param in schema_by_name and schema_by_name[param].get("type") != "enum":
            _fail(f"'{ftype}' filter on column '{col}' binds param '{param}', "
                  f"declared type '{schema_by_name[param].get('type')}' -- "
                  f"{ftype} filters bind an 'enum' param.")
    elif ftype == "slider":
        names = [param] if param else [min_param, max_param]
        for n in names:
            if n and n in schema_by_name and schema_by_name[n].get("type") not in ("int", "float"):
                _fail(f"slider filter on column '{col}' binds param '{n}', "
                      f"declared type '{schema_by_name[n].get('type')}' -- "
                      f"slider filters bind 'int'/'float' params.")
    elif ftype == "date_range":
        if (min_param or max_param) and _column_looks_hourly(df, col):
            result.fail(
                "live-query-hourly-unsupported",
                f"date_range filter on column '{col}' is hourly (its "
                f"values carry a time-of-day), but live binding is "
                f"strict-ISO 'date' params (YYYY-MM-DD) in v1. Bind a "
                f"day-granularity date column instead, or drop the live "
                f"binding on this filter.",
                component="FilterBar", section=sec,
            )
        for n in (min_param, max_param):
            if n and n in schema_by_name and schema_by_name[n].get("type") != "date":
                _fail(f"date_range filter on column '{col}' binds param "
                      f"'{n}', declared type "
                      f"'{schema_by_name[n].get('type')}' -- date_range "
                      f"filters bind 'date' params.")
    elif ftype == "text":
        if param and param in schema_by_name and schema_by_name[param].get("type") not in (
            "str", "int", "float",
        ):
            _fail(f"text filter on column '{col}' binds param '{param}', "
                  f"declared type '{schema_by_name[param].get('type')}' -- "
                  f"text filters bind 'str'/'int'/'float' params.")


def _column_looks_hourly(df: Any, col: str) -> bool:
    """True when *col* on *df* carries a non-midnight time-of-day -- the
    same detection ``components/filters/date_range.py`` uses to decide
    whether to render hour selectors."""
    import pandas as pd

    from trellum.components.filters.date_range import _detect_hourly

    if not isinstance(df, pd.DataFrame) or col not in df.columns:
        return False
    try:
        return _detect_hourly(df, col)
    except Exception:  # noqa: BLE001 - never let a validation check crash the build
        return False
