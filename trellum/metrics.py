"""Business metric definitions: ``metrics.yaml`` and the claims that use it.

A project-root ``metrics.yaml`` names the business numbers that must mean the
same thing everywhere -- "gross revenue is IAP plus ad revenue" -- and a KPI
component **claims** one by id. The claim expands, at build time, into the
exact aggregation config the client engine already executes, so wherever a
metric is claimed, consistency holds by construction. There is no new
computation engine, and unclaimed inline KPI dicts keep working forever.

The file is a **list**, not a mapping, so a duplicated id survives parsing and
can be linted instead of being silently swallowed by the YAML loader::

    version: 1
    metrics:
      - name: gross_revenue            # ^[a-z0-9][a-z0-9_]*$
        label: "Gross Revenue"
        description: >
          IAP plus ad revenue, gross of platform fees, daily grain.
        owner: finance@example.com
        format: currency               # any KpiCard format
        version: 2                     # optional human version, default 1
        agg: sum                       # executable spec (optional)
        column: total_revenue
        sql: "iap_revenue + ad_revenue"   # canonical derivation, informational
        dimensions: [event_date, title]   # informational
        tags: [revenue]

A metric with no ``agg`` spec is **descriptive**: claimable by a static
KpiCard for identity/label/format only, where a value is supplied. A metric
with a spec is **executable** and can be claimed inside a live ``KpiRow``.

The loader follows ``trellum.data.datasource_config``: explicit-path capable,
cached with an invalidate hook, ``{}`` when the file is absent, and a warning
rather than a crash on a malformed file -- a broken metrics.yaml must not take
every report build down with it. Structural problems (duplicate ids, bad
slugs, unknown formats) are recorded on the load result so the validator's
``metrics-yaml-schema`` check can surface them where they will be read.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field

#: Formats the KpiCard client runtime understands. Anything else falls back to
#: fmtCompact in the browser; the schema check flags it here instead.
VALID_FORMATS = {"number", "currency", "chips", "percent", "ratio", "plain"}

#: Aggregations a metric may declare. Deliberately narrower than the KpiRow
#: runtime's full set: ``purchase_pct`` and ``avg_by_date`` carry report-local
#: parameters (match values, date columns) that do not belong in a
#: project-wide definition.
VALID_AGGS = {"sum", "abssum", "count", "ratio"}

#: Dataset time grains (descriptive in v1: the client cannot re-bucket rows).
VALID_GRAINS = {"hour", "day", "week", "month"}

#: Rollup across time when not summed: ``avg`` per period (``avg_by_date``) or ``last``.
VALID_TIME_AGGS = {"avg", "last"}

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")

#: The claim key on a KPI dict, and the key the registered config carries.
CLAIM_KEY = "metric"


@dataclass(frozen=True)
class Dataset:
    """One ``datasets:`` entry: the grain metrics bind to -- one time axis
    (``time_column`` None for ``time: none``), one set of ``dimensions``, one
    fetch: ``source`` + ``table`` [+ ``where``, derived ``columns``], or a
    ``provider`` ``module:function`` called as ``(ctx, req) -> DataFrame``."""

    name: str
    source: str | None = None
    table: str | None = None
    where: str = ""
    columns: dict[str, str] = field(default_factory=dict)
    provider: str | None = None
    time_column: str | None = None
    grain: str | None = None
    dimensions: tuple[str, ...] = ()
    lookback: int | None = None


@dataclass(frozen=True)
class Metric:
    """One entry of ``metrics.yaml``, with its computed ``definition_hash``."""

    name: str
    label: str = ""
    description: str = ""
    owner: str = ""
    format: str = "number"
    version: int = 1
    agg: str | None = None
    column: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    sql: str = ""
    dimensions: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    definition_hash: str = ""
    #: Binding and rollup -- provenance, not meaning, so neither is hashed.
    dataset: str | None = None
    time_agg: str | None = None
    #: The bound dataset's time column, so ``agg_spec`` can emit ``date_col``.
    date_col: str | None = None

    @property
    def executable(self) -> bool:
        """True when the metric carries an aggregation spec a live KPI can run."""
        return bool(self.agg)

    def agg_spec(self) -> dict:
        """The aggregation keys a claim expands into. Empty when descriptive."""
        if not self.agg:
            return {}
        spec: dict = {"agg": self.agg}
        if self.agg == "ratio":
            if self.numerator:
                spec["numerator"] = self.numerator
            if self.denominator:
                spec["denominator"] = self.denominator
        elif self.column:
            spec["column"] = self.column
            if self.time_agg == "avg" and self.date_col:
                spec["agg"] = "avg_by_date"
                spec["date_col"] = self.date_col
        return spec

    def spec_text(self) -> str:
        """Human-readable one-line spec, for CLI listings."""
        if not self.agg:
            return "(descriptive)"
        if self.agg == "ratio":
            return f"ratio({self.numerator or '?'} / {self.denominator or '?'})"
        if self.agg == "count":
            return "count(rows)"
        return f"{self.agg}({self.column or '?'})"


def compute_definition_hash(
    name: str,
    format: str,
    agg: str | None,
    column: str | None,
    numerator: str | None,
    denominator: str | None,
    sql: str,
    version: int,
) -> str:
    """Stable hash over the canonical spec of a metric.

    Covers exactly the fields that change what a claimed KPI computes or how
    it is read (name, format, the agg spec, the canonical sql, the human
    version) -- not prose like description or owner, so editing documentation
    does not read as a definition change. Serialized as sorted-key JSON so the
    hash cannot depend on dict ordering, truncated to 12 hex chars because the
    value is an identity check, not a security boundary.
    """
    canonical = json.dumps(
        {
            "name": name,
            "format": format,
            "agg": agg,
            "column": column,
            "numerator": numerator,
            "denominator": denominator,
            "sql": sql,
            "version": version,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


@dataclass
class MetricsLoadResult:
    """The parsed registry plus every structural problem found on the way.

    ``problems`` is a list of ``(level, message)`` pairs with level ``"fail"``
    or ``"warn"``; the validator's ``metrics-yaml-schema`` check emits them
    verbatim. Recording them here rather than raising keeps the loader safe to
    call from a build while still making every problem loud somewhere.
    """

    metrics: dict[str, Metric] = field(default_factory=dict)
    datasets: dict[str, Dataset] = field(default_factory=dict)
    problems: list[tuple[str, str]] = field(default_factory=list)
    path: str = ""


_cache: dict[str, MetricsLoadResult] = {}


def _metrics_path(project_root: str | None = None) -> str:
    from trellum.project import get_project_root

    root = project_root or get_project_root()
    return os.path.join(os.path.abspath(root), "metrics.yaml")


def invalidate_metrics_cache() -> None:
    """Force a re-read of metrics.yaml on the next load."""
    _cache.clear()


def load_metrics_result(project_root: str | None = None) -> MetricsLoadResult:
    """Read ``metrics.yaml`` and return the registry plus its problems.

    An absent file is an empty registry, not an error -- most projects start
    without one. A malformed file warns and returns empty rather than
    crashing the build; a claim against the now-empty registry then fails
    validation where the failure is specific and actionable.
    """
    import yaml

    path = _metrics_path(project_root)
    if path in _cache:
        return _cache[path]

    result = MetricsLoadResult(path=path)
    if not os.path.isfile(path):
        _cache[path] = result
        return result

    try:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:
        print(f"  [warn] could not parse {path}: {exc}", flush=True)
        result.problems.append(
            ("warn", f"metrics.yaml could not be parsed ({exc}); "
                     f"no metrics are defined until it is fixed.")
        )
        _cache[path] = result
        return result

    if not isinstance(raw, dict):
        result.problems.append(
            ("fail", f"metrics.yaml root must be a mapping with a `metrics:` "
                     f"list, got {type(raw).__name__}.")
        )
        _cache[path] = result
        return result

    entries = raw.get("metrics")
    if entries is None:
        entries = []
    if isinstance(entries, dict):
        result.problems.append(
            ("fail", "metrics.yaml `metrics:` must be a LIST of entries, not a "
                     "mapping -- a mapping silently swallows duplicate ids "
                     "before they can be linted.")
        )
        entries = []
    if not isinstance(entries, list):
        result.problems.append(
            ("fail", f"metrics.yaml `metrics:` must be a list, got "
                     f"{type(entries).__name__}.")
        )
        entries = []

    result.datasets = _parse_datasets(raw.get("datasets"), result)
    for i, entry in enumerate(entries):
        metric = _parse_entry(entry, i, result)
        if metric is not None:
            result.metrics[metric.name] = metric

    _cache[path] = result
    return result


def _parse_datasets(raw: object, result: MetricsLoadResult) -> dict[str, Dataset]:
    """The optional ``datasets:`` mapping -> ``{name: Dataset}``. Every problem
    is a FAIL -- an unfetchable dataset makes every metric bound to it wrong.
    Files without the block stay valid (``{}``)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        result.problems.append(("fail", f"metrics.yaml `datasets:` must be a mapping "
                                        f"of name -> dataset, got {type(raw).__name__}."))
        return {}
    out: dict[str, Dataset] = {}
    for name, spec in raw.items():
        name = str(name)
        if not isinstance(spec, dict):
            result.problems.append(("fail", f"dataset '{name}' is not a mapping."))
            continue
        provider = spec.get("provider")
        source, table = spec.get("source"), spec.get("table")
        if not provider and not (source and table):
            result.problems.append(("fail", f"dataset '{name}' needs either `provider: "
                                            f"module:function` or `source` + `table`."))
        time_col = grain = None
        time = spec.get("time")
        if time is None:
            result.problems.append(("fail", f"dataset '{name}' has no `time`; give it "
                                            f"{{column, grain}} or `time: none`."))
        elif time != "none":
            time_col = str(time.get("column") or "") if isinstance(time, dict) else ""
            grain = str(time.get("grain") or "day") if isinstance(time, dict) else ""
            if not time_col or grain not in VALID_GRAINS:
                result.problems.append(("fail", f"dataset '{name}' `time` must be {{column, "
                                                f"grain}} with grain one of "
                                                f"{sorted(VALID_GRAINS)}, or `time: none`."))
        dims = spec.get("dimensions") or []
        cols = spec.get("columns") or {}
        out[name] = Dataset(
            name=name,
            source=str(source) if source else None,
            table=str(table) if table else None,
            where=str(spec.get("where") or ""),
            columns={str(k): str(v) for k, v in cols.items()} if isinstance(cols, dict) else {},
            provider=str(provider) if provider else None,
            time_column=time_col or None,
            grain=grain or None,
            dimensions=tuple(str(d) for d in dims) if isinstance(dims, list) else (),
            lookback=int(spec["lookback"]) if spec.get("lookback") is not None else None,
        )
    return out


def _parse_entry(entry: object, i: int, result: MetricsLoadResult) -> Metric | None:
    """One metrics.yaml entry -> a Metric, recording problems on the way.

    Returns None for entries that cannot become a metric at all (no name,
    duplicate id); recoverable problems (bad slug, unknown format/agg) still
    yield the metric so a claim against it keeps working while the schema
    check complains.
    """
    if not isinstance(entry, dict):
        result.problems.append(
            ("fail", f"metrics.yaml entry #{i + 1} is not a mapping "
                     f"({type(entry).__name__}).")
        )
        return None

    name = str(entry.get("name") or "").strip()
    if not name:
        result.problems.append(
            ("fail", f"metrics.yaml entry #{i + 1} has no `name`.")
        )
        return None
    if not _NAME_RE.match(name):
        result.problems.append(
            ("fail", f"metric name '{name}' is not a valid slug "
                     f"(^[a-z0-9][a-z0-9_]*$). Claims address metrics by "
                     f"this id, so it has to be stable and unambiguous.")
        )
    if name in result.metrics:
        result.problems.append(
            ("fail", f"metric '{name}' is defined more than once in "
                     f"metrics.yaml. Two definitions of one id is exactly "
                     f"the inconsistency this file exists to remove; the "
                     f"first definition wins until this is fixed.")
        )
        return None

    fmt = str(entry.get("format") or "number")
    if fmt not in VALID_FORMATS:
        result.problems.append(
            ("fail", f"metric '{name}' format '{fmt}' is not one of "
                     f"{sorted(VALID_FORMATS)}.")
        )

    agg = entry.get("agg")
    agg = str(agg) if agg is not None else None
    column = entry.get("column")
    numerator = entry.get("numerator")
    denominator = entry.get("denominator")
    if agg is not None:
        if agg not in VALID_AGGS:
            result.problems.append(
                ("fail", f"metric '{name}' agg '{agg}' is not one of "
                         f"{sorted(VALID_AGGS)}.")
            )
        elif agg == "ratio":
            for key, val in (("numerator", numerator),
                             ("denominator", denominator)):
                if not val:
                    result.problems.append(
                        ("fail", f"metric '{name}' agg 'ratio' is missing "
                                 f"`{key}`.")
                    )
        elif agg in ("sum", "abssum") and not column:
            result.problems.append(
                ("fail", f"metric '{name}' agg '{agg}' is missing `column`.")
            )

    label = str(entry.get("label") or "")
    description = str(entry.get("description") or "").strip()
    if not label:
        result.problems.append(
            ("warn", f"metric '{name}' has no `label`; claims will fall "
                     f"back to the raw id as the card title.")
        )
    if not description:
        result.problems.append(
            ("warn", f"metric '{name}' has no `description`. The definition "
                     f"is the point of this file -- state what the number "
                     f"means, at what grain, and what it includes.")
        )

    try:
        version = int(entry.get("version", 1))
    except (TypeError, ValueError):
        result.problems.append(
            ("fail", f"metric '{name}' version {entry.get('version')!r} "
                     f"must be an integer.")
        )
        version = 1

    sql = str(entry.get("sql") or "")
    dims = entry.get("dimensions") or []
    tags = entry.get("tags") or []
    column = str(column) if column is not None else None
    numerator = str(numerator) if numerator is not None else None
    denominator = str(denominator) if denominator is not None else None

    dataset, time_agg, date_col = _parse_binding(entry, name, agg, result)

    return Metric(
        dataset=dataset,
        time_agg=time_agg,
        date_col=date_col,
        name=name,
        label=label,
        description=description,
        owner=str(entry.get("owner") or ""),
        format=fmt,
        version=version,
        agg=agg,
        column=column,
        numerator=numerator,
        denominator=denominator,
        sql=sql,
        dimensions=tuple(str(d) for d in dims) if isinstance(dims, list) else (),
        tags=tuple(str(t) for t in tags) if isinstance(tags, list) else (),
        definition_hash=compute_definition_hash(
            name, fmt, agg, column, numerator, denominator, sql, version,
        ),
    )


def _parse_binding(entry: dict, name: str, agg: str | None, result: MetricsLoadResult):
    """``(dataset, time_agg, date_col)`` of one entry, checked against the datasets."""
    dataset = entry.get("dataset")
    dataset = str(dataset) if dataset is not None else None
    if dataset is not None and dataset not in result.datasets:
        result.problems.append(("fail", f"metric '{name}' binds to unknown dataset "
                                        f"'{dataset}'; declared: "
                                        f"{', '.join(result.datasets) or '(none)'}."))
    time_agg = entry.get("time_agg")
    time_agg = str(time_agg) if time_agg is not None else None
    if time_agg is not None and (time_agg not in VALID_TIME_AGGS
                                 or agg not in ("sum", "abssum")):
        result.problems.append(("fail", f"metric '{name}' time_agg '{time_agg}' must be one "
                                        f"of {sorted(VALID_TIME_AGGS)} on an agg of sum or "
                                        f"abssum."))
    bound = result.datasets.get(dataset) if dataset else None
    return dataset, time_agg, bound.time_column if bound else None


def load_metrics(project_root: str | None = None) -> dict[str, Metric]:
    """The project's ``metrics.yaml`` as ``{name: Metric}``.

    ``{}`` when the file is absent or unreadable. This is the covered surface
    (see docs/COMPATIBILITY.md); ``load_metrics_result`` additionally carries
    the structural problems for the validator and the linter.
    """
    return load_metrics_result(project_root).metrics


def expand_claim(kpi: dict, registry: dict[str, Metric] | None = None) -> dict:
    """Expand a ``{"metric": id, ...overrides}`` KPI dict at build time.

    Fills ``label``, ``format`` and -- when the metric is executable and the
    claim does not carry its own value or spec -- the aggregation keys, from
    the registry. **Explicit keys always win**: an override is a deliberate
    act the validator flags (``metric-overridden``) but never silently
    reverses. The returned dict still carries ``"metric": id`` so the claim
    survives into ``data.json`` and downstream readers can group by it.

    An unknown id expands to the claim unchanged (plus a console warning);
    the ``metric-undefined`` validation check is the loud failure.
    """
    metric_id = kpi.get(CLAIM_KEY)
    if not metric_id:
        return kpi
    if registry is None:
        registry = load_metrics()
    metric = registry.get(metric_id)
    if metric is None:
        print(f"  [warn] KPI claims unknown metric '{metric_id}' -- "
              f"not in metrics.yaml.", flush=True)
        return kpi

    expanded: dict = {CLAIM_KEY: metric_id}
    expanded["label"] = metric.label or metric.name
    expanded["format"] = metric.format
    # A claim that brings its own value is static and receives identity only.
    # Otherwise the full spec is filled and explicit keys override it
    # PIECEWISE -- overriding `column` alone must not silently drop the
    # definition's `agg` on the way through.
    if metric.executable and "value" not in kpi:
        expanded.update(metric.agg_spec())
    expanded.update(kpi)
    return expanded


def collect_claimed(component_data: dict[str, dict]) -> dict[str, list[str]]:
    """``{metric_id: [component_ids]}`` for every claim in registered data.

    Walks the shapes KPI components register: a flat ``metric`` key on
    ``kpi``/``mini_kpi`` entries, and the ``kpis`` list inside a
    ``kpi_row_live``/``kpi_row`` entry. Used by the HTML builder to assemble
    the ``_metrics`` block of data.json.
    """
    claimed: dict[str, set[str]] = {}

    def _note(metric_id: object, cid: str) -> None:
        if isinstance(metric_id, str) and metric_id:
            claimed.setdefault(metric_id, set()).add(cid)

    for cid, data in component_data.items():
        if not isinstance(data, dict):
            continue
        _note(data.get(CLAIM_KEY), cid)
        for kpi in data.get("kpis") or []:
            if isinstance(kpi, dict):
                _note(kpi.get(CLAIM_KEY), cid)
    return {mid: sorted(cids) for mid, cids in claimed.items()}


def metrics_artifact(
    claimed: dict[str, list[str]],
    registry: dict[str, Metric] | None = None,
) -> dict[str, dict]:
    """The ``_metrics`` block for data.json: claimed metrics only.

    Claims of undefined ids are omitted -- validation has already failed them,
    and an artifact entry with no definition behind it would claim an
    authority it does not have.
    """
    if registry is None:
        registry = load_metrics()
    out: dict[str, dict] = {}
    for metric_id, component_ids in sorted(claimed.items()):
        metric = registry.get(metric_id)
        if metric is None:
            continue
        entry: dict = {
            "label": metric.label or metric.name,
            "format": metric.format,
            "version": metric.version,
            "definition_hash": metric.definition_hash,
            "component_ids": component_ids,
        }
        entry.update(metric.agg_spec())
        out[metric_id] = entry
    return out


def claimed_metrics_block(component_data_maps) -> dict[str, dict]:
    """The full ``_metrics`` block from any number of component-data maps.

    One call for the HTML builder: merges claims across every render context
    (each scope renders through its own) and resolves them against the
    registry. ``{}`` when nothing claims, so the builder can leave the key
    out entirely.
    """
    claimed: dict[str, set[str]] = {}
    for component_data in component_data_maps:
        for mid, cids in collect_claimed(component_data).items():
            claimed.setdefault(mid, set()).update(cids)
    return metrics_artifact({m: sorted(c) for m, c in claimed.items()})


def metrics_used_entries(metrics_block: dict[str, dict]) -> list[dict]:
    """The ``_meta.json`` ``metrics_used`` shape (schema v2) for a build's
    ``_metrics`` block: one ``{"id", "definition_hash", "version"}`` per
    claim, in ``_metrics``' own order. ``_write_meta``/
    ``normalize_metrics_used`` (``trellum.meta``) sort and dedupe on write --
    this only reshapes what ``claimed_metrics_block`` already resolved.
    """
    return [
        {
            "id": metric_id,
            "definition_hash": entry.get("definition_hash"),
            "version": entry.get("version"),
        }
        for metric_id, entry in metrics_block.items()
    ]
