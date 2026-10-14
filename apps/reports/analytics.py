"""Bounded, studio-wide report analytics without artifact reads."""
from datetime import timedelta
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Count, F, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.utils import timezone

from .models import Report, ReportViewDaily, ReportViewEvent

PAGE_SIZE = 25
SOURCE_FRESH_SECONDS = 300
SORTS = ("report", "views", "unique", "share", "last_viewed")


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


def _observation(report, now):
    summary = getattr(report, "source_summary", None)
    fresh = bool(summary and summary.schema_version == 2 and summary.observed_at
                 and now-timedelta(seconds=SOURCE_FRESH_SECONDS) <= summary.observed_at <= now)
    if not fresh:
        return False, "Build observation unavailable"
    if summary.state == "missing" and summary.quality in ("observed", "verified") and not summary.attempt_at:
        return False, "Not built"
    if summary.state != "ready" or summary.quality not in ("observed", "verified"):
        return False, "Build observation unavailable"
    recent_success = bool(summary.attempt_status == "success" and summary.attempt_at
                          and now-timedelta(days=7) <= summary.attempt_at <= now)
    return report.views_30d == 0 and recent_success, ""


def analytics_payload(studio, params, days, *, now=None):
    now = now or timezone.now()
    relation = analytics_relation(studio, days, now)
    totals = relation.aggregate(report_count=Count("pk"), reports_viewed_count=Count("pk", filter=Q(views__gt=0)))
    # Scope is a DB subquery, never a materialized report-ID list.
    totals["total_share_views"] = ReportViewEvent.objects.filter(
        report__studio=studio, report__present_in_scan=True,
        created_at__gte=now-timedelta(days=days), share_link__isnull=False,
    ).count()
    q = str(params.get("q", "")).strip()[:200]
    sort = params.get("sort", "report")
    sort = sort if sort in SORTS else "report"
    direction = params.get("direction", "asc")
    direction = direction if direction in ("asc", "desc") else "asc"
    if q:
        relation = relation.filter(Q(display_name__icontains=q) | Q(slug__icontains=q))
    field = {"report": "name_order", "unique": "unique_total", "share": "share_views"}.get(sort, sort)
    order = F(field).asc(nulls_first=True) if direction == "asc" else F(field).desc(nulls_last=True)
    relation = relation.order_by(order, "name_order", "pk").select_related("source_summary").only(
        "id", "slug", "name", "kind", "source_summary__id", "source_summary__schema_version",
        "source_summary__state", "source_summary__quality", "source_summary__observed_at",
        "source_summary__attempt_at", "source_summary__attempt_status",
    )
    paginator = Paginator(relation, PAGE_SIZE)
    raw_page = str(params.get("page", "1"))
    # Bound integer parsing even for pathological input.
    page_number = int(raw_page) if raw_page.isascii() and raw_page.isdigit() and len(raw_page) <= 9 else 1
    page = paginator.get_page(max(1, page_number))
    rows = []
    for report in page.object_list:
        stale, observation = _observation(report, now)
        rows.append(dict(report=report, views=report.views, unique_users=report.unique_users,
                         unique_links=report.unique_links, unique_total=report.unique_total,
                         share_views=report.share_views, last_viewed=report.last_viewed,
                         stale=stale, observation=observation))

    def url(**changes):
        values = dict(days=days, q=q, sort=sort, direction=direction)
        values.update(changes)
        return "?" + urlencode(values)

    headers = []
    for key, label in zip(SORTS, ("Report", "Views", "Unique viewers", "Share views", "Last viewed")):
        active = sort == key
        next_direction = "desc" if active and direction == "asc" else "asc"
        headers.append(dict(key=key, label=label, url=url(sort=key, direction=next_direction),
                            aria_sort=("ascending" if direction == "asc" else "descending") if active else "none",
                            indicator=("↑" if direction == "asc" else "↓") if active else "↕"))
    return dict(totals, rows=rows, days=days, q=q, sort=sort, direction=direction,
                headers=headers, ranges=[dict(days=d, url=url(days=d)) for d in (7, 30, 90)],
                page=page, previous_url=url(page=page.previous_page_number()) if page.has_previous() else "",
                next_url=url(page=page.next_page_number()) if page.has_next() else "")
