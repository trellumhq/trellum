"""The per-studio Metrics catalog (semantic-layer Phase 2).

This is a studio-wide roll-up over built output (see
``apps.reports.overviews``). Business-metric definitions belong beside the
reports that use them.

``MetricDefinition`` rows (synced by ``apps.reports.scan.sync_studio_metrics``)
are the source for a metric's identity, spec and description -- always the
*current* definition, per the design: the catalog reads what metrics.yaml
says today, then layers on, per claiming report, whether that report's last
build actually reflects it.

The "current vs. stale" comparison never opens a report's data.json. Each
report's own ``_meta.json`` already carries, per claim, the
``definition_hash``/``version`` it was built against (schema v2, see
``trellum.meta.normalize_metrics_used`` and ``docs/COMPATIBILITY.md`` in the
framework) -- comparing that against ``MetricDefinition.definition_hash`` is
one small read per report (``apps.core.storage.read_meta``), not a directory
pull, which matters because a report's data.json can be read from S3 in the
remote backend (internal planning#93/#67).

The card sparklines are the one thing here that does read a data.json, and it
is exactly ONE: the generated metrics report's, read and parsed once per
overview (so once per cache window, not once per metric). Nothing is
recomputed and no query is issued -- the build already holds every series this
page draws, and ``_spark_series`` only re-buckets what the report's own charts
plot. See it there for what it costs and where it stops.
"""
from __future__ import annotations

import json
import logging
import threading
import time as _time
from datetime import datetime

from django.db.models import Max
from django.shortcuts import render

from apps.core import roles
from apps.core.permissions import require_studio_role
from apps.core.report_access import require_full_studio_visibility
from apps.reports.models import MetricDefinition, Report

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_cache: dict[int, tuple[float, dict]] = {}
_CACHE_TTL = 5.0

#: The report ``python -m trellum metrics --report`` scaffolds: generated from
#: ``metrics.yaml``, edited by nobody, and the build that produces the charts
#: this page shows. Recognised by what the framework's own scaffold writes
#: into report.yaml for its own reasons -- the slug it hardcodes and the tag
#: it sets (``trellum/cli/commands/metrics.py``) -- so report.yaml needs no
#: portal-only key for the portal to tell it apart. Both, not either: a
#: hand-written report may legitimately be called "metrics".
METRICS_REPORT_SLUG = "metrics"


def is_generated_metrics_report(report) -> bool:
    """Is this ``Report`` row the generated metrics report?"""
    return (report.slug == METRICS_REPORT_SLUG
            and METRICS_REPORT_SLUG in (report.tags or []))


def invalidate_metrics_overview_cache(studio=None) -> None:
    with _cache_lock:
        if studio is None:
            _cache.clear()
        else:
            _cache.pop(studio.pk, None)


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _claims_by_metric_id(studio, reports: list[Report]) -> tuple[dict[str, list[dict]], set, bool]:
    """``{metric_id: [claim, ...]}`` across every report's own ``_meta.json``.

    A claim is ``{"report": Report, "built_at": datetime|None,
    "definition_hash": str|None, "version": int|None}``. One
    ``storage.read_meta`` call per report -- never a report's data.json.

    Mirrors ``apps.reports.experiments.studio_experiments``'s
    single-latch degradation: once the store proves unreachable for one
    report, every remaining report in the pass is skipped without a further
    attempt, rather than retrying a store already known to be down. Returns
    ``(claims, slugs_with_a_build, storage_unavailable)`` -- the middle one so
    "this report has never been built" can be told apart from "it was built
    and claims nothing", which are different things to say to a reader.
    """
    from apps.core import storage
    from trellum.meta import normalize_metrics_used

    claims: dict[str, list[dict]] = {}
    built: set[str] = set()
    storage_down = False
    for report in reports:
        if storage_down:
            continue
        try:
            runtime_meta = storage.read_meta(studio, report.slug)
        except Exception as exc:  # noqa: BLE001 - driver raises per-operation classes
            logger.warning(f"report storage unreachable: {type(exc).__name__}: {exc}")
            storage_down = True
            continue
        built_at = _parse_iso(runtime_meta.get("last_run"))
        if built_at is not None:
            built.add(report.slug)
        for claim in normalize_metrics_used(runtime_meta.get("metrics_used")):
            claims.setdefault(claim["id"], []).append({
                "report": report,
                "built_at": built_at,
                "definition_hash": claim["definition_hash"],
                "version": claim["version"],
            })
    return claims, built, storage_down


#: Sparkline box, in user units. The <svg> is rendered at exactly this size,
#: so the viewBox is 1:1 and a stroke is a stroke.
_SPARK_W, _SPARK_H = 140.0, 36.0
_SPARK_PAD = 2.0

#: Most recent buckets kept per sparkline. 140px of width cannot show more
#: than this legibly, and it is what bounds the markup: ~60 points is ~700
#: bytes of `points`, times the metrics on the page.
_SPARK_POINTS = 60

#: ponytail: refuse to parse a data.json bigger than this for sparklines --
#: the page still renders, just without them. The generated metrics report is
#: one dataset per bound dataset and nothing else, so it is small (~400KB for
#: the demo's four); a studio that blows past this has a report that wants a
#: precomputed series in _meta.json rather than a bigger limit here.
_SPARK_MAX_DATA_BYTES = 32 * 1024 * 1024


def _spark_columns(entry: dict, metric_id: str) -> tuple[str, str, bool] | None:
    """``(numerator, denominator, absolute)`` a metric's series needs, from its
    ``_metrics`` entry -- the agg spec ``trellum.metrics.Metric.agg_spec``
    wrote into the build. ``denominator`` is ``""`` for anything but a ratio.

    ``avg_by_date`` needs no special case: the metric's own chart plots the
    per-date sum (``trellum.metrics_report._chart`` passes ``y=m.column``),
    and the average across dates is a KPI, not a shape.
    """
    agg = entry.get("agg")
    if agg == "ratio":
        num, den = entry.get("numerator"), entry.get("denominator")
        return (num, den, False) if num and den else None
    if agg == "count":
        # The report adds a literal-1 column for exactly this (see
        # trellum.metrics_report.generate), so a count sums like anything else.
        return (entry.get("column") or f"{metric_id}_rows", "", False)
    column = entry.get("column")
    if agg in ("sum", "abssum", "avg_by_date") and column:
        return (column, "", agg == "abssum")
    return None


def _bucket_sums(payload: object, x: str, picks: list[tuple[str, bool]]) -> list[dict]:
    """One columnar ``_ds_*`` payload -> per-bucket sums, ordered by bucket.

    ``picks`` is ``[(column, absolute)]``; the result is one dict per bucket
    keyed by ``(column, absolute)``. A single pass over the rows serves every
    metric bound to the dataset, which is why the callers group by dataset
    first. Values that are not numbers are skipped rather than guessed at.
    """
    if not isinstance(payload, dict):
        return []
    cols = payload.get("_cols") or []
    idx = {c: i for i, c in enumerate(cols)}
    if x not in idx or any(c not in idx for c, _ in picks):
        return []
    # The time column is dictionary-encoded (`_dict`), so a cell is a code
    # into a label list -- and the codes run in order of first appearance,
    # not in date order. Sort on the label.
    labels = (payload.get("_dict") or {}).get(x)
    xi, taken = idx[x], [(key, idx[key[0]]) for key in picks]

    sums: dict[object, dict] = {}
    for row in payload.get("_data") or []:
        try:
            code = row[xi]
        except (IndexError, TypeError):
            continue
        bucket = sums.get(code)
        if bucket is None:
            bucket = sums[code] = dict.fromkeys(picks, 0.0)
        for key, i in taken:
            value = row[i]
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                bucket[key] += abs(value) if key[1] else value

    def label(code):
        if labels and isinstance(code, int) and 0 <= code < len(labels):
            return str(labels[code])
        return str(code)

    ordered = sorted(sums, key=label)
    # ponytail: the most recent N buckets, not every Nth -- a sparkline is
    # "what has it been doing lately", and dropping every other day would
    # make a weekday cycle read as noise. Widen the box before the window.
    return [sums[code] for code in ordered[-_SPARK_POINTS:]]


def _polyline(values: list[float]) -> str:
    """Normalised ``points`` for one ``<polyline>``, or ``""`` for a series
    too short to be a line. A flat series draws through the middle rather
    than collapsing onto the floor or the ceiling."""
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    span = high - low
    inner = _SPARK_H - 2 * _SPARK_PAD
    step = _SPARK_W / (len(values) - 1)
    mid = _SPARK_PAD + inner / 2
    return " ".join(
        f"{i * step:.1f},"
        f"{mid if not span else _SPARK_PAD + inner - (v - low) / span * inner:.1f}"
        for i, v in enumerate(values)
    )


def _spark_series(data: dict) -> dict[str, str]:
    """``{metric_id: polyline points}`` for one built report's data.json.

    The build already carries everything: ``_metrics`` resolves a metric to
    its agg spec and the component that claims it, that component names the
    ``dataset_id``, and the report's own chart for the same dataset names the
    time column it plots against -- so the axis a sparkline uses is the axis
    the full chart uses, never a grain re-derived here.

    Chunked datasets need no handling: a chunked build keeps the recent
    periods inline and pushes the older ones into ``data_chunk_*`` files, and
    recent is exactly the window a sparkline wants.
    """
    components = data.get("components") or {}
    if not isinstance(components, dict):
        return {}
    time_col = {
        c["dataset_id"]: c["x"]
        for c in components.values()
        if isinstance(c, dict) and c.get("type") == "chart" and c.get("x") and c.get("dataset_id")
    }

    # Group by dataset first: one pass over a dataset's rows feeds every
    # metric bound to it, instead of one pass per metric per request.
    wanted: dict[str, dict[str, tuple[str, str, bool]]] = {}
    for metric_id, entry in (data.get("_metrics") or {}).items():
        if not isinstance(entry, dict):
            continue
        claimant = components.get(next(iter(entry.get("component_ids") or []), None)) or {}
        dataset = claimant.get("dataset_id")
        columns = _spark_columns(entry, metric_id)
        if dataset in time_col and columns:
            wanted.setdefault(dataset, {})[metric_id] = columns

    out: dict[str, str] = {}
    for dataset, per_metric in wanted.items():
        picks = sorted({(num, absolute) for num, _, absolute in per_metric.values()}
                       | {(den, False) for _, den, _ in per_metric.values() if den})
        buckets = _bucket_sums(data.get(f"_ds_{dataset}"), time_col[dataset], picks)
        for metric_id, (num, den, absolute) in per_metric.items():
            if den:
                # A bucket with nothing in the denominator is dropped, not
                # plotted as zero: the ratio is undefined there, and a spike
                # to the floor would be a claim the report never makes.
                values = [b[(num, absolute)] / b[(den, False)]
                          for b in buckets if b[(den, False)]]
            else:
                values = [b[(num, absolute)] for b in buckets]
            if points := _polyline(values):
                out[metric_id] = points
    return out


def _sparklines(studio, slug: str) -> dict[str, str]:
    """Every monitored metric's sparkline for ``studio``, from ONE read of the
    generated metrics report's data.json. ``{}`` for anything that goes wrong
    -- an absent build, an oversized file, unreadable JSON: the card simply
    has no chart on it, which is what a never-built report already looks
    like."""
    from apps.core import storage

    try:
        path = storage.output_root(studio, slug) / "data.json"
        if not path.is_file():
            return {}
        if path.stat().st_size > _SPARK_MAX_DATA_BYTES:
            logger.warning("sparklines: %s/%s data.json is %d bytes -- skipping",
                           studio, slug, path.stat().st_size)
            return {}
        with open(path, encoding="utf-8") as fh:
            return _spark_series(json.load(fh))
    except Exception as exc:  # noqa: BLE001 - a sparkline must never 500 the catalog
        logger.warning(f"sparklines unavailable for {studio}/{slug}: "
                       f"{type(exc).__name__}: {exc}")
        return {}


def studio_metrics_overview(studio) -> dict:
    """Every currently-defined metric in ``studio``, with its usage across
    every report that claims it and whether each claim is current.

    Cached briefly (like ``build_registry_payload`` / ``studio_experiments``)
    since the catalog page's one render does one storage read per report in
    the studio.
    """
    now = _time.time()
    with _cache_lock:
        hit = _cache.get(studio.pk)
        if hit is not None and (now - hit[0]) < _CACHE_TTL:
            return hit[1]

    defs = list(
        MetricDefinition.objects.filter(studio=studio, present_in_scan=True).order_by("name")
    )
    synced_at = MetricDefinition.objects.filter(studio=studio).aggregate(
        Max("last_scanned_at")
    )["last_scanned_at__max"]

    reports = list(Report.objects.filter(studio=studio, present_in_scan=True))
    claims_by_id, built_slugs, storage_down = _claims_by_metric_id(studio, reports)

    # The generated metrics report is where a metric's own chart comes from:
    # each bound metric is a block in it, anchored ``metric-<name>``, and
    # ``?only=`` renders that block alone (trellum.metrics_report). A metric
    # it claims is therefore a metric this page can draw.
    gen = next((r for r in reports if is_generated_metrics_report(r)), None)
    gen_url = f"/s/{studio.org.slug}/{studio.slug}/r/{gen.slug}/" if gen else ""
    gen_built = gen is not None and gen.slug in built_slugs
    # One read, one parse, for every card on the page.
    sparks = _sparklines(studio, gen.slug) if gen_built else {}

    metrics = []
    claimed_count = 0
    for row in defs:
        claims = claims_by_id.get(row.name, [])
        if claims:
            claimed_count += 1
        usage = []
        for claim in claims:
            report = claim["report"]
            claimed_hash = claim["definition_hash"]
            # A claim with no recorded hash (a build from before schema v2,
            # or a host that only ever passed bare ids) cannot be VERIFIED
            # current -- treated as stale rather than assumed fine, since the
            # whole point of the badge is not to let a changed definition go
            # unnoticed.
            current = bool(claimed_hash) and claimed_hash == row.definition_hash
            usage.append({
                "report": report,
                "report_url": (
                    f"/s/{studio.org.slug}/{studio.slug}/r/{report.slug}/?display=console"
                ),
                "built_at": claim["built_at"],
                "current": current,
                "claimed_version": claim["version"],
            })
        usage.sort(key=lambda u: (u["report"].name or u["report"].slug).lower())
        monitored = gen is not None and any(u["report"].pk == gen.pk for u in usage)
        metrics.append({
            "row": row,
            "spec": row.spec_text(),
            "usage": usage,
            "all_current": bool(usage) and all(u["current"] for u in usage),
            "monitored": monitored,
            # A <polyline>'s points, or "" -- no build, no binding, no data
            # and no series short of two points all read the same way on the
            # card: no sparkline at all, rather than a line pretending.
            "spark": sparks.get(row.name, "") if monitored else "",
            # The one block, framed on its own; and the same block in the
            # whole report, for "open full page".
            "embed_url": f"{gen_url}?only=metric-{row.name}" if monitored else "",
            "chart_url": f"{gen_url}#metric-{row.name}" if monitored else "",
            # Only meaningful once the report HAS been built -- until then
            # "not monitored" would be a guess, so the template says the
            # report needs a run instead of naming a reason.
            "not_monitored_reason": "" if monitored else (
                "descriptive — no aggregation spec to compute"
                if not row.agg else "not bound to a dataset in metrics.yaml"
            ),
        })

    payload = {
        "metrics": metrics,
        "synced_at": synced_at,
        "defined_count": len(defs),
        "claimed_count": claimed_count,
        "storage_unavailable": storage_down,
        "metrics_report_url": gen_url,
        "metrics_report_built": gen_built,
        # The box every ``spark`` was normalised into, so the template's
        # viewBox and the maths above cannot drift apart.
        "spark_w": int(_SPARK_W),
        "spark_h": int(_SPARK_H),
    }
    with _cache_lock:
        _cache[studio.pk] = (now, payload)
    return payload


@require_studio_role(roles.VIEWER)
@require_full_studio_visibility
def metrics_page(request, org_slug, studio_slug):  # noqa: ARG001
    from apps.studios.models import StudioRepo

    data = studio_metrics_overview(request.studio)
    # A reverse OneToOneField raises DoesNotExist (not AttributeError) when
    # unset, so plain getattr(..., "repo", None) would not catch it.
    try:
        repo_url = request.studio.repo.repo_url
    except StudioRepo.DoesNotExist:
        repo_url = ""
    return render(
        request,
        "reports/metrics.html",
        {
            "org": request.org,
            "studio": request.studio,
            "metrics": data["metrics"],
            "synced_at": data["synced_at"],
            "defined_count": data["defined_count"],
            "claimed_count": data["claimed_count"],
            "storage_unavailable": data["storage_unavailable"],
            "metrics_report_url": data["metrics_report_url"],
            "metrics_report_built": data["metrics_report_built"],
            "spark_w": data["spark_w"],
            "spark_h": data["spark_h"],
            # Footer "edit via pull request" link -- the studio's own repo,
            # when one is configured. Plain text otherwise (a fresh studio, or
            # one whose repo hasn't been set up yet); never a dead link.
            "repo_url": repo_url,
        },
    )
