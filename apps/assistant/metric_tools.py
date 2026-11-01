"""Canonical metric tools. Registry/source text is never executable authority."""
from __future__ import annotations

import json
import hashlib
import os
import re
import stat

import yaml

from trellum.metric_execution import (
    MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, MetricUnavailable, bounded_json,
    execute_metric, fingerprint, make_binding, validate_binding,
)

METRIC_NAMES = {"list_metrics", "get_metric_definition", "query_metric"}
_COMMON = {
    "slug": {"type": "string", "description": "Explicit report binding"},
    "dataset_id": {"type": "string", "description": "Explicit report output dataset ID"},
    "metric_id": {"type": "string"},
}
METRIC_SCHEMAS = [
    {"name": name, "description": description, "mutates": False, "role": "viewer",
     "input_schema": {"type": "object", "additionalProperties": False,
                      "properties": properties, "required": required}}
    for name, description, properties, required in [
        ("list_metrics", "List only metrics explicitly bound to this report; missing metadata does not imply no source data.",
         {"slug": _COMMON["slug"]}, ["slug"]),
        ("get_metric_definition", "Read the bound canonical calculation and business semantics, without source SQL.",
         _COMMON, list(_COMMON)),
        ("query_metric", "Execute a canonical metric over complete pinned report output. Dates are [start, end) in its business timezone. No SQL or aggregation overrides. Unsupported artifacts may be investigated with granted source tools.",
         {**_COMMON, "start": {"type": "string"}, "end": {"type": "string"},
          "filters": {"type": "object", "description": "Declared dimension to typed value list"},
          "breakdown": {"type": "array", "items": {"type": "string"}},
          "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
         [*list(_COMMON), "start", "end"]),
    ]
]


def available(toolbox):
    # Availability never reads the studio registry or discovers source grants.
    return not toolbox._access_error(toolbox.report_slug if toolbox.is_report_bound else None)


def _read_payload(toolbox, slug):
    path = toolbox._report_output(slug) / "data.json"
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT_BYTES:
            raise MetricUnavailable("Report artifact exceeds canonical execution limits; query a granted source.")
        raw = source.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise MetricUnavailable("Report artifact exceeds canonical execution limits.")
    payload = json.loads(raw)
    bounded_json(payload)
    return payload


def _current_definition(toolbox, entry):
    """Uncached, bounded parse using the framework's existing definition hash."""
    from apps.assistant.metric_context import _load
    from trellum.metrics import MetricsLoadResult, _parse_datasets, _parse_entry

    raw = _load(toolbox.project_root, document=True)
    result = MetricsLoadResult(path="metrics.yaml")
    result.datasets = _parse_datasets(raw.get("datasets"), result)
    for i, value in enumerate(raw["metrics"]):
        metric = _parse_entry(value, i, result)
        if metric is not None:
            result.metrics[metric.name] = metric
    if any(level == "fail" for level, _ in result.problems):
        raise MetricUnavailable("Current canonical registry is invalid.")
    metric = result.metrics.get(entry["metric_id"])
    if metric is None:
        raise MetricUnavailable("Canonical definition is no longer available.")
    current = make_binding(metric, **{k: entry[k] for k in (
        "output_dataset", "columns", "coverage_start", "coverage_end", "source_revision", "build_id", "generated_at")})
    if current["fingerprint"] != entry["fingerprint"]:
        raise MetricUnavailable("Canonical definition or execution semantics changed; rebuild this report.")


def _entry(payload, mid, dataset):
    entry = payload.get("_metric_execution", {}).get(mid)
    claimed = payload.get("_metrics", {}).get(mid)
    if not isinstance(entry, dict) or not isinstance(claimed, dict):
        raise MetricUnavailable("No explicit canonical binding in this report; use granted source exploration if available.")
    if (entry.get("metric_id") != mid or entry.get("output_dataset") != dataset
            or entry.get("fingerprint") != fingerprint(entry)
            or any(claimed.get(k) != entry.get(k) for k in ("version", "definition_hash"))):
        raise MetricUnavailable("Binding, definition or fingerprint mismatch.")
    validate_binding(entry)
    return entry


def execute(toolbox, name, args):
    toolbox.provenance = None
    prechecked = toolbox._executing_prechecked
    toolbox._executing_prechecked = False
    try:
        bounded_json(args, 8000)
        schema = next(s for s in METRIC_SCHEMAS if s["name"] == name)["input_schema"]
        if (set(args) - set(schema["properties"]) or set(schema["required"]) - set(args)
                or any(not isinstance(args.get(k), str) or not args[k] for k in schema["required"])):
            raise MetricUnavailable("Specify the metric, report, output dataset and explicit dates required by this tool.", "clarification_required")
        slug = args["slug"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", slug):
            raise MetricUnavailable("Invalid report binding.", "clarification_required")
        if toolbox._access_error(slug):
            raise MetricUnavailable("This conversation cannot access that report.", "access_denied")
        before = toolbox.read_meta(slug)
        if before.get("last_status") != "success" or not before.get("metric_execution_build"):
            raise MetricUnavailable("Report has no successful pinned metric execution build.")
        if before.get("validation", {}).get("summary", {}).get("fail", 0):
            raise MetricUnavailable("Report validation failed; canonical execution is unavailable.")
        payload = _read_payload(toolbox, slug)
        if name == "list_metrics":
            items = []
            for mid, value in payload.get("_metric_execution", {}).items():
                entry = _entry(payload, mid, value.get("output_dataset"))
                if entry["build_id"] != before["metric_execution_build"]:
                    raise MetricUnavailable("Report build changed or metadata is incomplete.")
                items.append(dict(metric_id=mid, dataset_id=entry["output_dataset"],
                                  version=entry["version"], fingerprint=entry["fingerprint"]))
                if len(items) > 200:
                    raise MetricUnavailable("Metric catalog exceeds the execution budget.")
            output = dict(status="answered", metrics=items)
        else:
            entry = _entry(payload, args["metric_id"], args["dataset_id"])
            if entry["build_id"] != before["metric_execution_build"]:
                raise MetricUnavailable("Report build changed or metadata is incomplete.")
            if not toolbox.is_report_bound:
                _current_definition(toolbox, entry)
            if name == "get_metric_definition":
                output = dict(status="answered", definition=entry)
            else:
                output = execute_metric(payload, entry, **{k: args[k] for k in
                    ("start", "end", "filters", "breakdown", "limit") if k in args})
                evidence = dict(kind="metric_query", metric_id=entry["metric_id"],
                                version=entry["version"], fingerprint=entry["fingerprint"],
                                definition_hash=entry["definition_hash"], report=slug,
                                build_id=entry["build_id"], dataset_id=entry["output_dataset"],
                                source_revision=entry["source_revision"], generated_at=entry["generated_at"],
                                period={"start": args["start"], "end": args["end"], "bounds": "[start,end)"},
                                timezone=entry["semantics"]["timezone"], filters=args.get("filters", {}),
                                breakdown=args.get("breakdown", []), columns=output["columns"],
                                rows=output["rows"], truncated=output["truncated"])
                evidence.update(schema_version=1, assurance="Approved calculation", status="answered",
                                unit=output["unit"], currency=output["currency"], completeness=output["completeness"])
                evidence["evidence_id"] = hashlib.sha256(bounded_json(evidence).encode()).hexdigest()
                output["provenance"] = evidence
        if toolbox._access_error(slug):
            raise MetricUnavailable("Report access is no longer available.", "access_denied")
        if toolbox.read_meta(slug) != before:
            raise MetricUnavailable("Report build changed during execution; retry against one build.", "incomplete")
        if toolbox.session is not None:
            reports = set(toolbox.session.state.get("_metric_reports", []))
            reports.add(slug)
            if len(reports) > 100:
                raise MetricUnavailable("Start a new conversation; this metric history reached its report limit.")
            toolbox.session.state["_metric_reports"] = sorted(reports)
        encoded = bounded_json(output, MAX_OUTPUT_BYTES)
        if name == "query_metric":
            toolbox.provenance = evidence
        return encoded
    except MetricUnavailable as exc:
        return json.dumps(dict(status=exc.status, limitation=str(exc)))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, yaml.YAMLError):
        return json.dumps(dict(status="unsupported", limitation="Canonical artifact or registry unavailable, invalid or incomplete; use granted source exploration if available."))
    finally:
        toolbox._executing_prechecked = prechecked
