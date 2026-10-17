"""Authorized, database-only registry reads with bounded presentation.

``/api/registry`` is the one read behind the dashboard cards, the list view and
API clients. With ``?page=`` it returns one bounded slice; without, every
visible row, assembled from the same slices. Both read the same relation (the
reports the viewer may list under current access readiness) and build rows with
``apps.reports.registry_rows``.
"""
import json

from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import (
    Exists,
    Func,
    OuterRef,
    TextField,
    Value,
)
from django.db.models.functions import Coalesce, Concat, Lower, NullIf
from django.utils import timezone

from apps.core.pagination import page_metadata
from apps.core.report_access import visible_reports

from .models import Report, ReportFavorite
from .registry_rows import (
    REGISTRY_ORDER,
    ROWS_PER_PAGE,
    all_rows,
    page_of_rows,
    registry_rows,
)

#: A slice over the byte ceiling is asked again by the client as smaller pages.
PAGE_BYTES = 256 * 1024
TABS = ("all", "_favorites", "_recent")
MAX_RECENT = 8


def check_catalog_context(request):
    """Call from studio-role guarded views; never widen a bearer key's org."""
    key = getattr(request, "api_key", None)
    if request.org.pk != request.studio.org_id or (key is not None and key.org_id != request.studio.org_id):
        raise PermissionDenied("Catalog scope is unavailable")


def bounded_payload(payload, ceiling):
    # Match JsonResponse's default escaping, separators and encoder; UTF-8
    # character counts alone undercount escaped non-ASCII response bodies.
    if len(json.dumps(payload, cls=DjangoJSONEncoder).encode("utf-8")) <= ceiling:
        return payload
    result = {"version": 1, "state": "oversized", "message": "This result exceeds the presentation limit. Narrow the selection."}
    if "pagination" in payload:
        result["pagination"] = payload["pagination"]
    return result


class JoinedTags(Func):
    output_field = TextField()
    template = "(SELECT COALESCE(string_agg(tag, ' ' ORDER BY n), '') FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(%(expressions)s) = 'array' THEN %(expressions)s ELSE '[]'::jsonb END) WITH ORDINALITY AS tags(tag,n))"


def page_number(value):
    try:
        page = int(value or 1)
    except (ValueError, TypeError):
        raise ValueError("Invalid page") from None
    if page < 1 or page > 1000000:
        raise ValueError("Invalid page")
    return page


def recent_slugs(params):
    values = params.getlist("recent") if hasattr(params, "getlist") else params.get("recent", [])
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)) or len(values) > MAX_RECENT or any(not isinstance(v, str) or len(v) > 200 or "\x00" in v for v in values):
        raise ValueError("Invalid recent reports")
    return list(dict.fromkeys(values))


def search(relation, q):
    """Reports whose name, slug, description, tags, category or studio contain ``q``.

    The one search both the dashboard and Operations use, in the same fields
    as their local, instant filters."""
    q = str(q or "").lower()
    if not q:
        return relation
    text = Concat(
        Coalesce(NullIf("name", Value("")), "slug"), Value(" "), "description", Value(" "), JoinedTags("tags"), Value(" "),
        Coalesce(NullIf("category", Value("")), Value("Uncategorized")), Value(" "), "studio__slug", Value(" "), "slug",
        output_field=TextField(),
    )
    return relation.annotate(search_text=Lower(text)).filter(search_text__contains=q)


def registry_relation(request, *, kind=None):
    """The present reports the viewer may list, in registry order.

    Listings fail closed like every availability-filtered list: while selected
    or private report access cannot be enforced, a viewer sees only what
    studio-wide access shows. The unpaged and the paged registry both read
    this one relation."""
    check_catalog_context(request)
    if kind not in (None, Report.KIND_REPORT, Report.KIND_ANALYSIS):
        raise ValueError("Invalid catalog kind")
    relation = Report.objects.filter(studio=request.studio, present_in_scan=True)
    if kind is not None:
        relation = relation.filter(kind=kind)
    relation = visible_reports(request.user, relation, request=request)
    return relation if request.user.is_active else relation.none()


def _params(params):
    for key, limit in (("q", 1000), ("tab", 200)):
        value = params.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > limit or "\x00" in value):
            raise ValueError("Invalid catalog state")
    if params.get("tab", "all") not in TABS:
        raise ValueError("Invalid catalog state")
    recent_slugs(params)
    page = page_number(params.get("page"))
    try:
        limit = int(params.get("limit") or ROWS_PER_PAGE)
    except (TypeError, ValueError):
        raise ValueError("Invalid page size") from None
    if not 5 <= limit <= ROWS_PER_PAGE:
        raise ValueError("Invalid page size")
    return page, limit


def registry_page_payload(request, params, *, kind=None, now=None):
    """One bounded slice of registry rows.

    ``q`` narrows by the search text; ``tab`` of ``_favorites`` or ``_recent``
    (with ``recent`` slugs) returns just those rows. Rows follow the registry
    order (priority, then slug) so the client can page through them and sort
    locally."""
    page, limit = _params(params)
    now = now or timezone.now()
    relation = registry_relation(request, kind=kind)
    recent = recent_slugs(params)
    q, tab = params.get("q", ""), params.get("tab", "all")
    if q:
        relation = search(relation, q)
    elif tab == "_favorites":
        relation = relation.filter(Exists(ReportFavorite.objects.filter(user=request.user, report_id=OuterRef("pk"))))
    elif tab == "_recent":
        # The client orders these by when each was last opened.
        relation = relation.filter(slug__in=recent)
    total = relation.values("pk").count()
    raw = page_of_rows(relation, REGISTRY_ORDER, limit, (page - 1) * limit)
    payload = {
        "version": 1, "reports": registry_rows(request.studio, request.org, raw, now=now),
        "pagination": page_metadata(page, limit, total), "generated_at": now.isoformat(),
    }
    return bounded_payload(payload, PAGE_BYTES)


def registry_payload(request, *, kind=None, now=None):
    """Every visible row, read as consecutive slices of the paged registry."""
    now = now or timezone.now()
    relation = registry_relation(request, kind=kind)
    return {"reports": all_rows(relation, request.studio, request.org, now=now), "generated_at": now.isoformat()}
