"""metrics.yaml and the claims against it: every claimed id must resolve,
and every executable claim must be computable on its DataSource."""

from __future__ import annotations

from typing import Any

from trellum.validation.result import ValidationResult
from trellum.validation.walk import _resolve_ds_columns

#: Claim keys that, when present on the claim itself, override the definition.
_OVERRIDE_KEYS = ("agg", "column", "numerator", "denominator", "format", "date_col", "last_agg")


def _iter_claims(comps: list[tuple[Any, str]]):
    """Yield ``(metric_id, claim_dict, kind, dataset_id, section)`` per claim.

    ``kind`` is ``"live"`` for a dict inside a live KpiRow, ``"static"`` for
    a static-row dict, a KpiCard or a MiniKpi. The claim dict is the raw
    authored one -- overrides are what the author wrote, not the expansion.
    """
    from trellum.components.kpis import KpiCard, KpiRow, MiniKpi

    for comp, sec in comps:
        if isinstance(comp, KpiRow):
            ds_id = getattr(comp, "dataset_id", None)
            kind = "live" if ds_id else "static"
            for kpi in getattr(comp, "kpis", []) or []:
                if isinstance(kpi, dict) and kpi.get("metric"):
                    yield str(kpi["metric"]), kpi, kind, ds_id, sec
        elif isinstance(comp, (KpiCard, MiniKpi)):
            if getattr(comp, "metric", None):
                claim = {"metric": comp.metric, "value": comp.value}
                if comp.label:
                    claim["label"] = comp.label
                if comp.format != "number":
                    claim["format"] = comp.format
                yield str(comp.metric), claim, "static", None, sec


def _check_metrics(
    ctx: Any,
    comps: list[tuple[Any, str]],
    ds_map: dict[str, Any],
    result: ValidationResult,
) -> None:
    """Category: metrics.yaml schema and metric claims."""
    from trellum.metrics import expand_claim, load_metrics_result

    load = load_metrics_result()
    registry = load.metrics

    # Visibility controls and focused URLs both depend on the metric anchor.
    from trellum.metrics_report import _MetricBlock
    for comp, sec in comps:
        if isinstance(comp, _MetricBlock):
            claims = [claim for child in comp.children for claim in getattr(child, "kpis", [])
                      if isinstance(claim, dict) and claim.get("metric")]
            if len(claims) != 1 or comp.anchor != f"metric-{claims[0]['metric']}":
                result.fail("metric-workspace-anchor", "A workspace block must anchor its single metric claim.",
                            component="Section", section=sec)

    # ── metrics-yaml-schema: the file's own structural problems ─────────
    # Computed once by the loader (duplicate ids, bad slugs, unknown
    # format/agg, ratio missing parts as FAIL; missing label/description as
    # WARN) and emitted here, where a build reads them.
    for level, message in load.problems:
        if level == "fail":
            result.fail("metrics-yaml-schema", message)
        else:
            result.warn("metrics-yaml-schema", message)

    claimed_ids: set[str] = set()
    for metric_id, claim, kind, ds_id, sec in _iter_claims(comps):
        claimed_ids.add(metric_id)
        metric = registry.get(metric_id)

        if metric is None:
            result.fail(
                "metric-undefined",
                f"KPI claims metric '{metric_id}', which is not defined in "
                f"metrics.yaml. Define it there (the whole point of a claim "
                f"is a shared definition), or drop the claim and write an "
                f"inline KPI dict.",
                component="KpiRow" if ds_id else "KpiCard", section=sec,
            )
            continue

        # ── metric-overridden: the claim contradicts the definition ─────
        expected = {**metric.agg_spec(), "format": metric.format}
        overridden = [
            k for k in _OVERRIDE_KEYS
            if k in claim and expected.get(k) not in (None, "")
            and str(claim[k]) != str(expected[k])
        ]
        if overridden:
            result.warn(
                "metric-overridden",
                f"KPI claims metric '{metric_id}' but overrides "
                f"{overridden} inline. The card no longer computes the "
                f"definition it names -- either drop the overrides, or bump "
                f"the metric in metrics.yaml so every claimant moves "
                f"together.",
                component="KpiRow" if ds_id else "KpiCard", section=sec,
            )

        has_value = claim.get("value") is not None
        has_own_spec = any(
            k in claim for k in ("agg", "column", "columns", "numerator")
        )

        expanded = expand_claim(claim, registry)
        if kind == "live" and expanded.get("agg") in ("avg_by_date", "last_by_date"):
            cols = _resolve_ds_columns(ds_id, ds_map) if ds_id else set()
            date_col = expanded.get("date_col")
            if not date_col or (ds_id in ds_map and date_col not in cols):
                result.fail(
                    "metric-column-missing",
                    f"metric '{metric_id}' needs its declared time column "
                    f"'{date_col}' in DataSource '{ds_id}' for {expanded['agg']}. "
                    "Return that column or correct the claim's date_col.",
                    component="KpiRow", section=sec, dataset_id=ds_id or "",
                )

        if kind == "live" and not has_value and not has_own_spec:
            # ── the claim must be computable on its DataSource ──────────
            if not metric.executable:
                result.fail(
                    "metric-column-missing",
                    f"metric '{metric_id}' has no executable spec "
                    f"(agg/column) but is claimed in a live KpiRow with no "
                    f"value. A descriptive metric can only badge a KPI that "
                    f"supplies its own number -- add agg/column to the "
                    f"definition, or pass a value.",
                    component="KpiRow", section=sec,
                )
                continue
            cols = _resolve_ds_columns(ds_id, ds_map) if ds_id else set()
            if not cols:
                continue
            needed = [c for c in (metric.column, metric.numerator,
                                  metric.denominator) if c]
            missing = [c for c in needed if c not in cols]
            if missing:
                result.fail(
                    "metric-column-missing",
                    f"metric '{metric_id}' needs column(s) {missing} which "
                    f"are not in DataSource '{ds_id}'. The definition names "
                    f"warehouse columns; this report's query does not "
                    f"return them.",
                    component="KpiRow", section=sec, dataset_id=ds_id or "",
                )
        elif kind == "static" and not has_value:
            result.fail(
                "metric-column-missing",
                f"static KPI claims metric '{metric_id}' but supplies no "
                f"value. A static card cannot aggregate -- compute the "
                f"number in the generator and pass value=, or claim inside "
                f"a live KpiRow(dataset_id=...).",
                component="KpiCard", section=sec,
            )

    # ── metric-description-weak: the definition is the product ──────────
    # INFO, mirroring report-description-weak: scoped to metrics this report
    # actually claims, so the nudge lands on the report that popularises the
    # metric rather than on every build in the project.
    for metric_id in sorted(claimed_ids):
        metric = registry.get(metric_id)
        if metric is None:
            continue
        desc = metric.description.strip()
        if desc and len(desc) < 60:
            result.info(
                "metric-description-weak",
                f"metric '{metric_id}' description is only {len(desc)} "
                f"chars. The definition is what makes a claim worth more "
                f"than an inline dict: state what the number includes, at "
                f"what grain, and what it deliberately excludes.",
            )
