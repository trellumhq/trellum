"""Bounded DB-only metric usage reads from worker-produced source indexes."""
from datetime import timedelta
from urllib.parse import urlencode

from django.utils import timezone
from django.db import transaction
from django.db.models import Count, F, Q, Window, Min, Max, OuterRef
from django.db.models.functions import RowNumber, JSONObject
from django.contrib.postgres.expressions import ArraySubquery
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.core import roles
from apps.core.limits import INLINE_LIMIT
from apps.core.permissions import require_studio_role
from apps.core.report_access import require_full_studio_visibility
from .models import MetricCatalogState, MetricDefinition, Report, ReportMetricClaim, ReportMetricPreview


# Observations older than five minutes are historical. This is a freshness
# window, not a worker convergence guarantee: large or slow catalogs may be
# partial while reconciliation progresses. GET requests never backfill.
SOURCE_FRESH_SECONDS = 300
MAX_DATABASE_ID = 2**63 - 1


# A report the worker has not reached yet (no summary row, or one still waiting
# for its first look) says nothing about the metrics it claims. Right after an
# upgrade that is every report, for a few seconds to minutes. A failed or
# uncertain first look is a different, settled state and is not "not yet".
NOT_YET_INDEXED = Q(source_summary__isnull=True) | Q(
    source_summary__state="pending", source_summary__observed_at__isnull=True)


def _coverage(studio):
    cutoff = timezone.now() - timedelta(seconds=SOURCE_FRESH_SECONDS)
    return _reports(studio).aggregate(total=Count("pk"), covered=Count("pk", filter=
        Q(source_summary__state__in=["ready", "missing"], source_summary__quality="verified",
          source_summary__observed_at__gte=cutoff)),
        unindexed=Count("pk", filter=NOT_YET_INDEXED),
        stale_sources=Count("pk", filter=Q(source_summary__observed_at__lt=cutoff)),
        earliest_observed_at=Min("source_summary__observed_at"),
        latest_observed_at=Max("source_summary__observed_at"))


def _revisions(studio):
    return MetricCatalogState.objects.filter(studio=studio).values_list(
        "definition_revision", "summary_revision").first() or (0, 0)


def _reports(studio):
    return Report.objects.filter(studio=studio, present_in_scan=True, kind=Report.KIND_REPORT,
                                 data_expired_at__isnull=True)


def _claims(studio):
    return ReportMetricClaim.objects.filter(summary__report__in=_reports(studio)).exclude(
        summary__state__in=["expired", "removed", "expiring"])


def usage_pending(studio):
    """Is any live report still waiting to be indexed, so that usage is unknown?"""
    return _reports(studio).filter(NOT_YET_INDEXED).exists()


def claimed_count(studio):
    """Definitions at least one live report claims."""
    return _claims(studio).filter(metric_name__in=MetricDefinition.objects.filter(
        studio=studio, present_in_scan=True).values("name")).values("metric_name").distinct().count()


def _claim_json(claim, definition):
    report = claim.summary.report
    summary = claim.summary
    return {"report": report.name or report.slug, "report_url":
            f"/s/{report.studio.org.slug}/{report.studio.slug}/r/{report.slug}/?display=console",
            "built_at": summary.served_at, "observed_at": summary.observed_at,
            "quality": summary.quality, "source_state": summary.state,
            "fresh": bool(summary.observed_at and summary.observed_at >= timezone.now() - timedelta(seconds=SOURCE_FRESH_SECONDS)),
            "current": bool(claim.definition_hash) and claim.definition_hash == definition.definition_hash,
            "claimed_version": claim.version}


def _changed():
    return JsonResponse({"state": "changed", "message": "Metrics changed while loading."}, status=409)


@require_studio_role(roles.VIEWER)
@require_full_studio_visibility
@require_GET
def metrics_summary(request, org_slug, studio_slug):
    raw = request.GET.get("ids", "").split(",")
    try:
        if not 1 <= len(raw) <= 50 or any(not v.isascii() or not v.isdigit() for v in raw):
            raise ValueError
        ids = [int(value) for value in raw]
        revision = int(request.GET["definition_revision"])
        if len(set(ids)) != len(ids) or not 0 <= revision <= MAX_DATABASE_ID or any(not 0 < value <= MAX_DATABASE_ID for value in ids):
            raise ValueError
    except (KeyError, ValueError):
        return HttpResponseBadRequest("Choose at most 50 distinct definition IDs and a valid revision.")
    studio = request.studio
    with transaction.atomic():
        before = _revisions(studio)
        if before[0] != revision:
            return _changed()
        definitions = list(MetricDefinition.objects.filter(studio=studio, present_in_scan=True, pk__in=ids))
        if len(definitions) != len(ids):
            return HttpResponseBadRequest("Choose available metrics from this studio.")
        names = {row.name: row for row in definitions}
        coverage = _coverage(studio)
        complete = coverage["total"] == coverage["covered"]
        # Usage is unknown, not zero, for a metric nothing indexed claims while
        # some reports are not indexed yet: the card says it is measuring and
        # the page polls until the worker has been through every report.
        measuring = coverage["unindexed"] > 0
        generated = _reports(studio).filter(slug="metrics", tags__contains=["metrics"]).annotate(
            spark_rows=ArraySubquery(ReportMetricPreview.objects.filter(
                summary__report_id=OuterRef("pk"), metric_name__in=names).order_by("metric_name").values(
                    json=JSONObject(name="metric_name", points="points")))
        ).values("pk", "source_summary__state", "source_summary__quality",
                 "source_summary__preview_state", "source_summary__observed_at",
                 "source_summary__workspace_version", "spark_rows").first()
        claims = _claims(studio).filter(metric_name__in=names)
        # Compute full counts before rank filtering. Every claiming report is
        # listed inline up to INLINE_LIMIT per metric; a metric with thousands
        # of claims returns that many and links to the searchable list.
        previews = list(claims.select_related("summary__report__studio__org").annotate(
            claim_total=Window(Count("pk"), partition_by=[F("metric_name")]),
            generated_total=Window(Count("pk", filter=Q(summary__report_id=generated["pk"] if generated else None)),
                                   partition_by=[F("metric_name")]),
            preview_rank=Window(RowNumber(), partition_by=[F("metric_name")],
                                order_by=[F("summary__report__name").asc(), F("summary__report_id").asc()])
                        ).filter(preview_rank__lte=INLINE_LIMIT).order_by("metric_name", "preview_rank"))
        grouped, counts, generated_claims = {}, {}, set()
        for claim in previews:
            grouped.setdefault(claim.metric_name, []).append(_claim_json(claim, names[claim.metric_name]))
            counts[claim.metric_name] = claim.claim_total
            if claim.generated_total:
                generated_claims.add(claim.metric_name)
        # Whether a chart can be drawn is not a question about worker timing:
        # the frame loads the live report, and a missing metric is reported by
        # the frame itself. Only the indexed state and format decide it; the
        # five-minute freshness window applies to usage coverage, not charts.
        generated_source_ready = bool(generated and generated["source_summary__state"] == "ready" and
                               generated["source_summary__quality"] in ('verified', 'observed'))
        workspace_version = generated['source_summary__workspace_version'] if generated else None
        generated_ready = generated_source_ready and workspace_version == 1
        # No Metrics report, or one that has never produced output.
        unbuilt = generated is None or generated["source_summary__state"] == "missing"
        # Existing indexed previews remain useful even when an old artifact
        # needs explicit loading. Format gating controls frame creation only.
        sparks = {item["name"]: item["points"] for item in generated["spark_rows"]} if generated_source_ready and generated['source_summary__quality'] == 'verified' else {}
        result = []
        for definition in definitions:
            monitored = generated_ready and definition.name in generated_claims
            base = f"/s/{org_slug}/{studio_slug}/r/metrics/"
            result.append({"id": definition.pk, "claim_count": counts.get(definition.name, 0),
                "usage_state": "measuring" if measuring and not counts.get(definition.name) else "known",
                "count_complete": complete, "claims": grouped.get(definition.name, []),
                "details_url": reverse("studio-metric-claims", args=[org_slug, studio_slug, definition.pk]) +
                               f"?definition_revision={revision}",
                "spark": " ".join(sparks.get(definition.name, "").split()[:60]),
                "chart_state": "ready" if monitored else "not-monitored" if generated_ready else
                               "legacy" if generated_source_ready and workspace_version == 0 else
                               "unbuilt" if unbuilt else "unknown",
                'workspace_version': workspace_version,
                'workspace_quality': generated['source_summary__quality'] if generated else 'unknown',
                'legacy_url': f'{base}?only=metric-{definition.name}' if generated_source_ready and workspace_version == 0 else '',
                "embed_url": f"{base}?only=metric-{definition.name}" if monitored else "",
                "chart_url": f"{base}?only=metric-{definition.name}" if monitored else "",
                "overview_url": f"{base}#metric-{definition.name}" if monitored else ""})
        # The page header reports how many metrics are claimed. It asks for that
        # number only while it is still waiting on the worker.
        claimed = claimed_count(studio) if request.GET.get("claimed") == "1" else None
        if _revisions(studio) != before:
            return _changed()
    response = JsonResponse({"state": "ready" if complete else "partial", "definition_revision": revision,
        "summary_revision": before[1], "coverage": coverage, "usage_pending": measuring,
        **({"claimed_count": claimed} if claimed is not None else {}), "metrics": result})
    response["Cache-Control"] = "private, no-store"
    return response


def _details_url(studio, definition_id, revision, query=""):
    params = {"definition_revision": revision, **({"q": query} if query else {})}
    return reverse("studio-metric-claims", args=[studio.org.slug, studio.slug, definition_id]) + "?" + urlencode(params)


@require_studio_role(roles.VIEWER)
@require_full_studio_visibility
@require_GET
def metric_claim_details(request, org_slug, studio_slug, definition_id):
    """Every claim of one metric past the inline list, searched on the server.

    Reached only from a card whose claims exceed INLINE_LIMIT. It shows the
    first INLINE_LIMIT matching reports and narrows as the person types; it
    never pages. A definition revision that no longer matches is refreshed by
    redirecting to the current one, not by asking the person to reload.
    """
    query = request.GET.get("q", "").strip()
    try:
        revision = int(request.GET["definition_revision"])
        if not 0 <= revision <= MAX_DATABASE_ID or definition_id > MAX_DATABASE_ID or len(query) > 200:
            raise ValueError
    except (KeyError, ValueError):
        return HttpResponseBadRequest("A definition revision and a search of at most 200 characters are required.")
    studio = request.studio
    with transaction.atomic():
        before = _revisions(studio)
        definition = MetricDefinition.objects.filter(pk=definition_id, studio=studio, present_in_scan=True).first()
        if revision != before[0]:
            # The catalog moved on since this link was built: take the current
            # one, or the Metrics page when the metric is gone.
            if definition is None:
                return redirect("studio-metrics", org_slug=org_slug, studio_slug=studio_slug)
            return redirect(_details_url(studio, definition.pk, before[0], query))
        if definition is None:
            raise Http404("No such metric.")
        claims = _claims(studio).filter(metric_name=definition.name)
        total = claims.count()
        matching = claims.filter(Q(summary__report__name__icontains=query) | Q(summary__report__slug__icontains=query)) if query else claims
        matched = matching.count() if query else total
        page = matching.select_related("summary__report__studio__org").order_by("summary__report__name", "pk")[:INLINE_LIMIT]
        coverage = _coverage(studio)
        rows = [_claim_json(claim, definition) for claim in page]
        if _revisions(studio) != before:
            return redirect(_details_url(studio, definition.pk, _revisions(studio)[0], query))
    response = render(request, "reports/metric_claims.html", {
        "org": request.org, "studio": studio, "definition": definition,
        "claims": rows, "claim_total": total, "matching_count": matched, "shown_count": len(rows),
        "truncated": matched > len(rows), "q": query, "definition_revision": revision,
        "coverage": coverage, "coverage_complete": coverage["total"] == coverage["covered"],
    })
    response["Cache-Control"] = "private, no-store"
    return response
