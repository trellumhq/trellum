"""The console header's "Jump to a report": the reports and analyses a viewer may open.

Visibility is the catalog's own (``visible_reports``), so a viewer never sees a
name they could not open. Up to ``INLINE_CHOICE_LIMIT`` rows arrive at once and
the browser filters them instantly; only a studio with more than that returns a
short preview and searches on the server as the viewer types. Nothing pages.
"""
from urllib.parse import quote

from django.db.models import Q, Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from apps.core import roles
from apps.core.choice_requests import INLINE_CHOICE_LIMIT, SEARCH_PREVIEW
from apps.core.permissions import require_studio_role
from apps.core.report_access import visible_reports

from .catalog import check_catalog_context
from .models import Report

QUERY_LIMIT = 200


def _hint(row):
    kind = "Analysis" if row["kind"] == Report.KIND_ANALYSIS else "Report"
    category = (row["category"] or "").strip()
    return f"{kind} · {category}" if category and category != "Uncategorized" else kind


def _item(request, row):
    prefix = f"/s/{request.org.slug}/{request.studio.slug}"
    entry = row["source_summary__html_entry"] or "index.html"
    # ``display=console`` opens the report inside the console, in the report host on catalog pages.
    href = f"{prefix}/r/{quote(row['slug'], safe='')}/{quote(entry, safe='/')}?display=console"
    return {"value": row["slug"], "label": row["display_name"], "hint": _hint(row), "href": href}


@require_studio_role(roles.VIEWER)
@require_GET
def api_jump(request, org_slug, studio_slug):  # noqa: ARG001
    check_catalog_context(request)
    q = request.GET.get("q", "").strip()
    if len(q) > QUERY_LIMIT or "\x00" in q:
        return JsonResponse({"error": "Invalid search."}, status=400)
    rows = visible_reports(
        request.user, Report.objects.filter(studio=request.studio, present_in_scan=True), request=request
    )
    if not request.user.is_active:
        rows = rows.none()
    rows = rows.exclude(slug="metrics", tags__contains=["metrics"]).annotate(
        display_name=Coalesce(NullIf("name", Value("")), "slug"),
    )
    if q:
        rows = rows.filter(Q(display_name__icontains=q) | Q(slug__icontains=q) | Q(category__icontains=q))
    rows = rows.order_by(Lower("display_name"), "slug", "pk").values(
        "slug", "kind", "category", "display_name", "source_summary__html_entry"
    )
    # Everything up to the inline limit; beyond it (or while searching) a short
    # list of the best matches, and ``has_next`` tells the browser to keep searching.
    limit = SEARCH_PREVIEW if q else INLINE_CHOICE_LIMIT
    sample = list(rows[: limit + 1])
    more = len(sample) > limit
    if more and not q:
        limit = SEARCH_PREVIEW
    sample = sample[:limit]
    response = JsonResponse({
        "results": [_item(request, row) for row in sample],
        "pagination": {"has_next": more},
    })
    response["Cache-Control"] = "private, no-store"
    return response
