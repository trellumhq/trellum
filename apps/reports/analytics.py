"""Bounded, studio-wide report analytics without artifact reads.

A studio of up to ``CLIENT_ROWS`` reports gets every row in one response, with
filtering and sorting done in the browser. Only a larger studio switches to
server-side search and sort with ``PAGE_SIZE`` rows per page.
"""
from datetime import timedelta
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Count, F, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.utils import timezone

from .models import Report, ReportViewDaily, ReportViewEvent
from .scan import _STALE_BUILD_WINDOW_DAYS

CLIENT_ROWS = 500
PAGE_SIZE = 200
SORTS = ("report", "views", "unique", "share", "last_viewed")
DAY_CHOICES = (7, 30, 90)


def analytics_relation(studio, days, now):
    """Independent indexed subqueries keep daily and raw event grains separate."""
    since = now - timedelta(days=days)
    daily = ReportViewDaily.objects.filter(report_id=OuterRef("pk"))
    events = ReportViewEvent.objects.filter(report_id=OuterRef("pk"))
    window = events.filter(created_at__gte=since).order_by().values("report_id")

    def number(query, field):
        return Coalesce(Subquery(query.values(field)[:1], output_field=IntegerField()), Value(0))

    return Report.objects.filter(studio=studio, present_in_scan=True).annotate(
        display_name=Coalesce(NullIf("name", Value("")), "slug"),
        views=number(daily.filter(date__gte=since.date()).order_by().values("report_id").annotate(n=Sum("views")), "n"),
        views_30d=number(daily.filter(date__gte=(now-timedelta(days=30)).date()).order_by().values("report_id").annotate(n=Sum("views")), "n"),
        unique_users=number(window.annotate(n=Count("user_id", distinct=True)), "n"),
        unique_links=number(window.annotate(n=Count("share_link_id", distinct=True)), "n"),
        share_views=number(window.filter(share_link__isnull=False).annotate(n=Count("pk")), "n"),
        last_viewed=Subquery(events.order_by("-created_at").values("created_at")[:1]),
    ).annotate(unique_total=F("unique_users") + F("unique_links"), name_order=Lower("display_name"))


def _unseen(report, now):
    """No views in 30 days although the last build succeeded recently.

    Reads the recorded last attempt; a report whose attempt is unknown simply
    shows no chip.
    """
    summary = getattr(report, "source_summary", None)
    if summary is None or summary.attempt_status != "success" or not summary.attempt_at:
        return False
    return report.views_30d == 0 and now - summary.attempt_at <= timedelta(days=_STALE_BUILD_WINDOW_DAYS)


def analytics_payload(studio, params, days, *, now=None):
    now = now or timezone.now()
    relation = analytics_relation(studio, days, now)
    totals = relation.aggregate(report_count=Count("pk"), reports_viewed_count=Count("pk", filter=Q(views__gt=0)))
    # Scope is a DB subquery, never a materialized report-ID list.
    totals["total_share_views"] = ReportViewEvent.objects.filter(
        report__studio=studio, report__present_in_scan=True,
        created_at__gte=now-timedelta(days=days), share_link__isnull=False,
    ).count()
    server_side = totals["report_count"] > CLIENT_ROWS
    q, sort, direction, page = "", "report", "asc", None
    if server_side:
        q = str(params.get("q", "")).strip()[:200]
        sort = params.get("sort", "report")
        sort = sort if sort in SORTS else "report"
        direction = params.get("direction", "asc")
        direction = direction if direction in ("asc", "desc") else "asc"
        if q:
            relation = relation.filter(Q(display_name__icontains=q) | Q(slug__icontains=q))
        field = {"report": "name_order", "unique": "unique_total", "share": "share_views"}.get(sort, sort)
        order = F(field).asc(nulls_first=True) if direction == "asc" else F(field).desc(nulls_last=True)
    else:
        order = F("name_order").asc()
    relation = relation.order_by(order, "name_order", "pk").select_related("source_summary").only(
        "id", "slug", "name", "kind", "source_summary__id", "source_summary__attempt_at",
        "source_summary__attempt_status",
    )
    if server_side:
        raw_page = str(params.get("page", "1"))
        # Bound integer parsing even for pathological input.
        page_number = int(raw_page) if raw_page.isascii() and raw_page.isdigit() and len(raw_page) <= 9 else 1
        page = Paginator(relation, PAGE_SIZE).get_page(max(1, page_number))
        reports = page.object_list
    else:
        reports = relation
    rows = [dict(report=report, views=report.views, unique_users=report.unique_users,
                 unique_links=report.unique_links, unique_total=report.unique_total,
                 share_views=report.share_views, last_viewed=report.last_viewed,
                 stale=_unseen(report, now)) for report in reports]
    # Range links keep a server-side search and sort; the client-side filter
    # has nothing to carry over.
    kept = dict(q=q, sort=sort, direction=direction) if server_side and (q or sort != "report" or direction != "asc") else {}
    return dict(totals, rows=rows, days=days, day_choices=DAY_CHOICES, server_side=server_side,
                q=q, sort=sort, direction=direction, page=page,
                ranges=[dict(days=d, url="?" + urlencode(dict(days=d, **kept))) for d in DAY_CHOICES])
