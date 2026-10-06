"""`metrics`: the project's business metric definitions, and who claims them.

The command an agent runs BEFORE inventing a KPI dict. If the number is
already defined in metrics.yaml, claim it by id and the definition expands at
build time; if it is a business number and undefined, the right move is to
define it once rather than hard-code a second meaning for it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _claims_by_metric(root: str) -> dict[str, list[str]] | None:
    """``{metric_id: [report slugs]}`` read from built reports' _meta.json.

    Returns None when no built report exists at all -- coverage is then
    unknowable rather than zero, and the caller should say so instead of
    reporting every metric as an orphan.
    """
    from trellum.meta import normalize_metrics_used

    out_dir = os.path.join(root, "output")
    if not os.path.isdir(out_dir):
        return None
    found_any = False
    claims: dict[str, list[str]] = {}
    for slug in sorted(os.listdir(out_dir)):
        meta_path = os.path.join(out_dir, slug, "_meta.json")
        if not os.path.isfile(meta_path):
            continue
        found_any = True
        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
        except Exception:
            continue
        # normalize_metrics_used handles both the current build's shape
        # (id/definition_hash/version objects) and a v1 build's bare id
        # list -- this CLI reads whatever is on disk, old builds included.
        for entry in normalize_metrics_used(meta.get("metrics_used")):
            claims.setdefault(entry["id"], []).append(slug)
    return claims if found_any else None


def _coverage_line(metric_ids: list[str], claims: dict[str, list[str]] | None) -> str:
    if claims is None:
        return ("coverage unknown -- no built reports under output/. Build "
                "first: python -m trellum.run --all --no-serve")
    claimed = [m for m in metric_ids if claims.get(m)]
    orphans = [m for m in metric_ids if not claims.get(m)]
    line = (f"{len(claimed)} of {len(metric_ids)} defined metric(s) claimed "
            f"in built reports")
    if orphans:
        line += f"; unclaimed: {', '.join(orphans)}"
    return line


def _print_one(metric, claims: dict[str, list[str]] | None) -> None:
    print(f"{metric.name} -- {metric.label or '(no label)'}")
    if metric.description:
        print(f"  {metric.description.strip()}")
    print(f"  spec:    {metric.spec_text()}")
    print(f"  format:  {metric.format}")
    if metric.sql:
        print(f"  sql:     {metric.sql}")
    if metric.dimensions:
        print(f"  dims:    {', '.join(metric.dimensions)}")
    if metric.owner:
        print(f"  owner:   {metric.owner}")
    if metric.tags:
        print(f"  tags:    {', '.join(metric.tags)}")
    print(f"  version: {metric.version}   hash: {metric.definition_hash}")
    if metric.dataset:
        rollup = f"   time_agg: {metric.time_agg}" if metric.time_agg else ""
        print(f"  dataset: {metric.dataset}{rollup}   -> ctx.metrics([\"{metric.name}\"])")
    if claims is not None:
        slugs = claims.get(metric.name) or []
        where = ", ".join(slugs) if slugs else "no built report claims it"
        print(f"  claimed: {where}")
    if metric.executable:
        print(f'  claim it: {{"metric": "{metric.name}"}} in a '
              f'KpiRow(dataset_id=...) kpi list')
    else:
        print(f'  claim it: KpiCard(metric="{metric.name}", value=...) -- '
              f'descriptive, so a value must be supplied')


def _cmd_metrics(args: argparse.Namespace) -> int:
    """What metrics.yaml defines, read at call time -- never cached to disk."""
    from trellum.metrics import load_metrics_result
    from trellum.project import get_project_root

    root = get_project_root()
    load = load_metrics_result(root)
    metrics = load.metrics

    if getattr(args, "lint", False):
        return _lint(load, root)
    if getattr(args, "report", False):
        return _scaffold_report(root)

    if not metrics:
        print("No metrics defined. Create metrics.yaml at the project root:")
        print("")
        print("    version: 1")
        print("    metrics:")
        print("      - name: gross_revenue")
        print('        label: "Gross Revenue"')
        print("        description: >")
        print("          IAP plus ad revenue, gross of platform fees, daily grain.")
        print("        format: currency")
        print("        agg: sum")
        print("        column: total_revenue")
        print("")
        print('Then claim it in any KpiRow: {"metric": "gross_revenue"} -- the')
        print("definition expands at build time, so every claimant computes the")
        print("same number.")
        for level, message in load.problems:
            print(f"\n  [{level.upper()}] {message}")
        return 0

    claims = _claims_by_metric(root)

    name = getattr(args, "name", None)
    if name:
        metric = metrics.get(name)
        if metric is None:
            print(f"no such metric: {name}. Defined: {', '.join(metrics)}",
                  file=sys.stderr)
            return 1
        _print_one(metric, claims)
        return 0

    width = max(len(n) for n in metrics)
    for metric in metrics.values():
        print(f"  {metric.name:<{width}}  {metric.label or '(no label)':<24} "
              f"{metric.spec_text():<32} {metric.format:<9}"
              f"{' -> ' + metric.dataset if metric.dataset else ''}")
    if load.datasets:
        print("\nDatasets (the grain a metric binds to; ctx.metrics([...]) fetches one):")
        for ds in load.datasets.values():
            where = ds.provider or f"{ds.source}.{ds.table}" + (f" where {ds.where}" if ds.where else "")
            time = f"{ds.grain} over {ds.time_column}" if ds.time_column else "no time axis"
            print(f"  {ds.name:<{width}}  {where}; {time}; dims: "
                  f"{', '.join(ds.dimensions) or '-'}")
    print("")
    print(_coverage_line(list(metrics), claims))
    print("")
    print('Claim one by id instead of writing an inline KPI dict: '
          '{"metric": "<name>"}')
    print("  python -m trellum metrics <name>     the full definition")
    print("  python -m trellum metrics --lint     duplicates and orphans")
    print("  python -m trellum metrics --report   scaffold reports/metrics/, a "
          "monitoring page for every bound metric")
    return 0


_REPORT_YAML = """\
# Generated by: python -m trellum metrics --report
# Every metric in metrics.yaml with a `dataset:` binding, monitored: a KPI
# and a trend per metric, sectioned by tag. Nothing to edit here to add a
# metric -- bind it in metrics.yaml and rebuild this report.
name: "Metrics"
slug: metrics
description: "Every business metric defined in metrics.yaml and bound to a dataset, monitored over time: a KPI and a trend per metric at the dataset's grain, sectioned by tag and filterable by the shared dimensions, plus the metrics defined but not yet bound to a dataset."
version: 0.1.0
tags:
  - metrics
studio: default
category: Metrics
data_sources:
{sources}
display:
  icon: activity
  priority: 50
"""


def _scaffold_report(root: str) -> int:
    """Write ``reports/metrics/`` once; existing files are kept, never rewritten."""
    from trellum.data.datasource_config import load_datasource_config

    sources = "\n".join(f"  - {n}" for n in load_datasource_config()) or "  []"
    files = {
        "__init__.py": "",
        "report.yaml": _REPORT_YAML.format(sources=sources),
        "generator.py": "from trellum.metrics_report import MetricsReport  # noqa: F401\n",
    }
    report_dir = os.path.join(root, "reports", "metrics")
    os.makedirs(report_dir, exist_ok=True)
    for name, text in files.items():
        path = os.path.join(report_dir, name)
        if os.path.exists(path):
            print(f"  kept     reports/metrics/{name}")
            continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"  wrote    reports/metrics/{name}")
    print("\nBuild it:  python -m trellum.run reports/metrics --no-serve")
    return 0


def _lint(load, root: str) -> int:
    """Project-wide report: schema problems, then orphans."""
    metrics = load.metrics
    fails = [(lvl, msg) for lvl, msg in load.problems if lvl == "fail"]
    warns = [(lvl, msg) for lvl, msg in load.problems if lvl != "fail"]

    for _, msg in fails:
        print(f"  FAIL  {msg}")
    for _, msg in warns:
        print(f"  WARN  {msg}")

    if not metrics and not load.problems:
        print("No metrics.yaml (or it defines nothing). Nothing to lint.")
        return 0

    claims = _claims_by_metric(root)
    if claims is None:
        print(f"\n{len(metrics)} metric(s) defined. Orphan detection needs "
              f"built reports -- none found under output/. Build first:")
        print("  python -m trellum.run --all --no-serve")
    else:
        orphans = [m for m in metrics if not claims.get(m)]
        for m in orphans:
            print(f"  ORPHAN  {m} is defined but no built report claims it.")
        print(f"\n{len(metrics)} metric(s), "
              f"{len(metrics) - len(orphans)} claimed, {len(orphans)} orphan(s), "
              f"{len(fails)} schema FAIL(s), {len(warns)} WARN(s).")

    return 1 if fails else 0
