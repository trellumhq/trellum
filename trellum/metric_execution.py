"""Bounded canonical calculations over explicitly bound report artifacts.

Periods are ISO calendar dates [start, end), in the declared business timezone.
Only daily, complete artifacts are supported. No SQL or host dependencies.
Authors opt in with Metric.execution and report.yaml metric_bindings; ordinary
claims alone never confer execution eligibility. Fingerprints supplement (and
never change) the existing definition hash.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from zoneinfo import ZoneInfo

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_ROWS = 10_000
MAX_OUTPUT_BYTES = 32_000
MAX_PERIODS = 366


class MetricUnavailable(ValueError):
    def __init__(self, reason, status="unsupported"):
        super().__init__(reason)
        self.status = status


def bounded_json(value, maximum=MAX_INPUT_BYTES):
    encoded = json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) > maximum:
        raise MetricUnavailable("Execution exceeds the byte budget; use a granted source query.")
    return encoded


def fingerprint(entry):
    return "v1:" + hashlib.sha256(bounded_json(
        {k: v for k, v in entry.items() if k != "fingerprint"}
    ).encode()).hexdigest()


def semantics(metric):
    """Validate explicit semantics; absence is deliberately not inferred."""
    s = metric.execution
    required = {"timezone", "unit", "currency", "grain", "dimensions", "additive_dimensions",
                "filters", "time_aggregation", "null_policy", "zero_denominator", "max_age_seconds"}
    if not isinstance(s, dict) or set(s) != required:
        raise MetricUnavailable("Metric execution semantics are missing or unsupported.")
    bounded_json(s, 8000)
    try:
        ZoneInfo(s["timezone"])
    except (KeyError, ValueError, TypeError):
        raise MetricUnavailable("Invalid business timezone.") from None
    if (s["grain"] != "day" or s["time_aggregation"] not in ("sum", "last", "average", "none")
            or s["null_policy"] != "propagate" or s["zero_denominator"] != "null"
            or not isinstance(s["unit"], str) or not s["unit"]
            or (s["currency"] is not None and not isinstance(s["currency"], str))):
        raise MetricUnavailable("Unsupported metric semantics.")
    if type(s["max_age_seconds"]) is not int or not 1 <= s["max_age_seconds"] <= 31_536_000:
        raise MetricUnavailable("Declare a bounded freshness policy of at most one year.")
    for key in ("dimensions", "additive_dimensions", "filters"):
        if (not isinstance(s[key], list) or len(s[key]) > 12
                or any(not isinstance(v, str) for v in s[key]) or len(set(s[key])) != len(s[key])):
            raise MetricUnavailable("Invalid dimension declarations.")
    if not set(s["additive_dimensions"]) <= set(s["dimensions"]) or not set(s["filters"]) <= set(s["dimensions"]):
        raise MetricUnavailable("Invalid additive/filter declarations.")
    if metric.agg not in ("sum", "abssum", "ratio") or not metric.date_col or not metric.dataset:
        raise MetricUnavailable("Metric has no supported canonical calculation binding.")
    if metric.time_agg and {"avg": "average", "last": "last"}.get(metric.time_agg) != s["time_aggregation"]:
        raise MetricUnavailable("Conflicting metric time semantics.")
    if metric.agg == "ratio" and s["time_aggregation"] != "sum":
        raise MetricUnavailable("Only ratios of additive operands are supported.")
    return s


def make_binding(metric, *, output_dataset, columns, coverage_start, coverage_end,
                 source_revision, build_id, generated_at):
    """Publish an explicit canonical-column -> output-column mapping at build time.

    Coverage is an author declaration of complete daily periods; the executor
    also checks actual period presence and refuses chunks, overrides and gaps.
    """
    s = semantics(metric)
    operands = [metric.numerator, metric.denominator] if metric.agg == "ratio" else [metric.column]
    needed = set([metric.date_col, *s["dimensions"], *operands])
    if (None in needed or not isinstance(columns, dict) or set(columns) != needed
            or any(not isinstance(v, str) or not v for v in columns.values())
            or len(set(columns.values())) != len(columns)):
        raise MetricUnavailable("Every canonical column needs an explicit unique output mapping.")
    if not all(isinstance(v, str) and v for v in (output_dataset, source_revision, build_id, generated_at)):
        raise MetricUnavailable("Build/source identity is required.")
    generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    if generated.tzinfo is None:
        raise MetricUnavailable("Build timestamp requires an explicit timezone.")
    if date.fromisoformat(coverage_start) >= date.fromisoformat(coverage_end):
        raise MetricUnavailable("Invalid coverage.")
    entry = dict(metric_id=metric.name, version=metric.version, definition_hash=metric.definition_hash,
                 format=metric.format,
                 registry_dataset=metric.dataset, output_dataset=output_dataset, columns=columns,
                 spec=dict(agg=metric.agg, column=metric.column, numerator=metric.numerator,
                           denominator=metric.denominator, date_col=metric.date_col),
                 semantics=s, coverage_start=coverage_start, coverage_end=coverage_end,
                 source_revision=source_revision, build_id=build_id, generated_at=generated_at)
    entry["fingerprint"] = fingerprint(entry)
    return entry


def verify_claims(payload, entry):
    """Reject static and overridden claims, including another claim of this ID."""
    from trellum.metrics import Metric
    spec = entry["spec"]
    time_agg = {"average": "avg", "last": "last"}.get(entry["semantics"]["time_aggregation"])
    expected = Metric(name=entry["metric_id"], **spec, time_agg=time_agg).agg_spec()
    found = False
    keys = {"agg", "column", "numerator", "denominator", "date_col", "last_agg"}
    for component in payload.get("components", {}).values():
        if not isinstance(component, dict):
            continue
        claims = [component, *(component.get("kpis") or [])]
        for claim in claims:
            if not isinstance(claim, dict) or claim.get("metric") != entry["metric_id"]:
                continue
            found = True
            if ("value" in claim or claim.get("format", entry["format"]) != entry["format"]
                    or {k: claim[k] for k in keys if k in claim} != expected
                    or component.get("dataset_id") != entry["output_dataset"]):
                raise MetricUnavailable("Metric claim overrides or conflicts with its binding.")
    if not found:
        raise MetricUnavailable("No executable report claim for this binding.")


def publish_bindings(payload, bindings, *, registry, build_id, generated_at):
    """Build hook. Invalid opt-ins remain unavailable without breaking rendering."""
    published = {}
    if not isinstance(bindings, dict) or len(bindings) > 200:
        return
    for mid, binding in bindings.items():
        if mid not in payload.get("_metrics", {}) or mid not in registry:
            continue
        try:
            entry = make_binding(registry[mid], **binding, build_id=build_id, generated_at=generated_at)
            verify_claims(payload, entry)
            published[mid] = entry
        except (ValueError, TypeError, KeyError):
            continue
    if published:
        payload["_metric_execution"] = published


def validate_binding(entry):
    """Use the build contract again at execution, including standalone callers."""
    from trellum.metrics import Metric
    metric = Metric(name=entry["metric_id"], format=entry["format"], version=entry["version"],
                    definition_hash=entry["definition_hash"], dataset=entry["registry_dataset"],
                    execution=entry["semantics"], **entry["spec"])
    reconstructed = make_binding(metric, **{k: entry[k] for k in (
        "output_dataset", "columns", "coverage_start", "coverage_end", "source_revision", "build_id", "generated_at")})
    if reconstructed != entry:
        raise MetricUnavailable("Incomplete execution contract.")


def execute_metric(payload, entry, *, start, end, filters=None, breakdown=None, limit=50, now=None):
    """Compute completely before truncating. Throws a useful, non-approved limitation."""
    bounded_json(payload)
    if entry.get("fingerprint") != fingerprint(entry):
        raise MetricUnavailable("Execution fingerprint mismatch.")
    validate_binding(entry)
    verify_claims(payload, entry)
    if (filters is not None and not isinstance(filters, dict)) or (breakdown is not None and not isinstance(breakdown, list)):
        raise MetricUnavailable("Filters must be an object and breakdown a list.", "clarification_required")
    filters, breakdown = filters or {}, breakdown or []
    bounded_json([start, end, filters, breakdown, limit], 8000)
    s, spec = entry["semantics"], entry["spec"]
    clock = now or datetime.now(timezone.utc)
    generated = datetime.fromisoformat(entry["generated_at"].replace("Z", "+00:00"))
    age = (clock - generated).total_seconds()
    if age < -300 or age > s["max_age_seconds"]:
        raise MetricUnavailable("Build is outside the declared freshness policy; query a granted source.", "stale")
    first, stop = date.fromisoformat(start), date.fromisoformat(end)
    if first.isoformat() != start or stop.isoformat() != end:
        raise MetricUnavailable("Use ISO dates YYYY-MM-DD.", "clarification_required")
    days = (stop - first).days
    if not 0 < days <= MAX_PERIODS:
        raise MetricUnavailable("Use an explicit period of at most 366 days.", "clarification_required")
    if start < entry["coverage_start"] or end > entry["coverage_end"]:
        raise MetricUnavailable("Requested period is outside complete artifact coverage.", "incomplete")
    if not isinstance(filters, dict) or not set(filters) <= set(s["filters"]):
        raise MetricUnavailable("Unsupported filters.", "clarification_required")
    if (not isinstance(breakdown, list) or len(set(breakdown)) != len(breakdown)
            or not set(breakdown) <= set(s["dimensions"])):
        raise MetricUnavailable("Unsupported breakdown.", "clarification_required")
    if not set(s["dimensions"]) - set(breakdown) <= set(s["additive_dimensions"]):
        raise MetricUnavailable("Distinct/non-additive dimensions cannot be rolled up; query a granted source.")
    if s["time_aggregation"] == "none" and days != 1:
        raise MetricUnavailable("This metric cannot roll up periods; query a granted source.")
    ds = entry["output_dataset"]
    if ds in payload.get("_chunk_manifests", {}):
        raise MetricUnavailable("Chunked artifacts are not complete execution inputs; query a granted source.", "incomplete")
    data = payload.get("_ds_" + ds, {})
    cols, raw = data.get("_cols", []), data.get("_data", [])
    if len(raw) > MAX_ROWS or len(cols) != len(set(cols)) or not set(entry["columns"].values()) <= set(cols):
        raise MetricUnavailable("Input rows or mapped columns unavailable within execution limits.")
    mapping = entry["columns"]
    records = []
    for row in raw:
        if len(row) != len(cols):
            raise MetricUnavailable("Invalid output rows.")
        values = dict(zip(cols, row))
        for col, dictionary in data.get("_dict", {}).items():
            code = values.get(col)
            if type(code) is not int or not 0 <= code < len(dictionary):
                raise MetricUnavailable("Invalid dictionary encoding.")
            values[col] = dictionary[code]
        records.append({key: values[value] for key, value in mapping.items()})
    expected = {(first + timedelta(days=i)).isoformat() for i in range(days)}
    selected = [r for r in records if start <= str(r[spec["date_col"]]) < end]
    if {r[spec["date_col"]] for r in selected} != expected:
        raise MetricUnavailable("Requested daily coverage is incomplete.", "incomplete")

    def typed(value):
        if value is not None and type(value) not in (str, int, float, bool):
            raise MetricUnavailable("Dimensions require scalar typed values.")
        return (type(value).__name__, value)

    for key, wanted in filters.items():
        if (not isinstance(wanted, list) or not wanted or len(wanted) > 50
                or any(type(v) not in (str, int, float, bool) for v in wanted)):
            raise MetricUnavailable("Filters require bounded typed value lists.", "clarification_required")
        present = {typed(r[key]) for r in selected}
        if any(typed(v) not in present for v in wanted):
            raise MetricUnavailable("Unknown filter value for requested period.", "clarification_required")
    allowed = {k: {typed(v) for v in values} for k, values in filters.items()}

    def matches(row):
        return all(typed(row[k]) in values for k, values in allowed.items())

    # Require every observed full dimension tuple on each requested day. A
    # rollup can otherwise hide missing cells behind another dimension's row.
    # Do not invent a Cartesian product of unrelated dimension values.
    declared = {tuple(typed(r[d]) for d in s["dimensions"]) for r in records
                if entry["coverage_start"] <= str(r[spec["date_col"]]) < entry["coverage_end"]
                and matches(r)}
    selected = [r for r in selected if matches(r)]
    cells = defaultdict(set)
    groups = defaultdict(lambda: defaultdict(list))
    group_values = {}
    grain = set()
    for r in selected:
        dimensions = tuple(typed(r[d]) for d in s["dimensions"])
        identity = (r[spec["date_col"]], *dimensions)
        if identity in grain:
            raise MetricUnavailable("Duplicate rows at declared grain.")
        grain.add(identity)
        cells[dimensions].add(r[spec["date_col"]])
        key = tuple(typed(r[d]) for d in breakdown)
        group_values[key] = tuple(r[d] for d in breakdown)
        groups[key][r[spec["date_col"]]].append(r)
    if not declared or any(cells[d] != expected for d in declared):
        raise MetricUnavailable("Dimension has missing periods; zero cannot be inferred.", "incomplete")

    def total(rows, column, absolute=False):
        values = [r[column] for r in rows]
        if any(v is None for v in values):
            return None
        if any(type(v) not in (int, float) for v in values):
            raise MetricUnavailable("Non-numeric calculation input.")
        return sum((abs(Decimal(str(v))) if absolute else Decimal(str(v)) for v in values), Decimal(0))

    result = []
    for key, periods in sorted(groups.items(), key=lambda item: str(item[0])):
        if set(periods) != expected:
            raise MetricUnavailable("Breakdown has missing periods; zero cannot be inferred.", "incomplete")
        rows = [r for period in periods.values() for r in period]
        if spec["agg"] == "ratio":
            num, den = total(rows, spec["numerator"]), total(rows, spec["denominator"])
            value = None if num is None or den in (None, 0) else num / den
        else:
            by_period = {p: total(rs, spec["column"], spec["agg"] == "abssum") for p, rs in periods.items()}
            values = list(by_period.values())
            mode = s["time_aggregation"]
            if mode == "last":
                value = by_period[max(by_period)]
            else:
                value = None if None in values else sum(values, Decimal(0))
                if value is not None and mode == "average":
                    value /= len(values)
        result.append([*group_values[key], None if value is None else float(value)])
    if type(limit) is not int or not 1 <= limit <= 50:
        raise MetricUnavailable("Result limit must be 1–50.")
    output = dict(status="answered", columns=[*breakdown, "value"], rows=result[:limit],
                  truncated=len(result) > limit, completeness="complete", unit=s["unit"], currency=s["currency"])
    bounded_json(output, MAX_OUTPUT_BYTES)
    return output
