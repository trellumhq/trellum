"""Authorized, database-only catalog reads with bounded presentation."""
from datetime import timedelta
import json

from django.core.exceptions import EmptyResultSet, PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.db.models import BooleanField, Case, Count, Exists, Func, IntegerField, OuterRef, Q, TextField, Value, When
from django.db.models.functions import Coalesce, Length, Lower, NullIf, Substr
from django.utils import timezone

from apps.core.report_access import visible_reports
from .models import Report, ReportFavorite

PAGE_SIZE = 25
FRESH_SECONDS = 300


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
    result = dict(version=1, state="oversized", message="This result exceeds the presentation limit. Narrow the selection or open report details.")
    for key in ("pagination", "coverage"):
        if key in payload:
            result[key] = payload[key]
    return result


def validate_params(params):
    for key, limit in (("q", 1000), ("tab", 200), ("category_q", 200), ("studio", 512), ("view", 5), ("sort", 12), ("csort", 12), ("asc", 1)):
        value = params.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > limit or "\x00" in value):
            raise ValueError("Invalid catalog state")
    if params.get("view", "cards") not in ("cards", "list") or params.get("asc", "1") not in ("0", "1"):
        raise ValueError("Invalid catalog state")
    if params.get("sort", "last_run") not in ("name", "category", "studio", "status", "schedule", "last_run") or params.get("csort", "priority") not in ("priority", "name", "last_run", "status"):
        raise ValueError("Invalid catalog sort")
    recent_slugs(params)
    for key in ("page", "favorite_page", "category_page"):
        page_number(params.get(key))


class JoinedTags(Func):
    output_field = TextField()
    template = "(SELECT COALESCE(string_agg(tag, ' ' ORDER BY n), '') FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(%(expressions)s) = 'array' THEN %(expressions)s ELSE '[]'::jsonb END) WITH ORDINALITY AS tags(tag,n))"


class TagPreview(Func):
    template = "(SELECT COALESCE(jsonb_agg(tag ORDER BY n), '[]'::jsonb) FROM (SELECT left(tag,80) AS tag,n FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(%(expressions)s) = 'array' THEN %(expressions)s ELSE '[]'::jsonb END) WITH ORDINALITY AS tags(tag,n) LIMIT 8) AS preview)"


class TagCount(Func):
    output_field = IntegerField()
    template = "CASE WHEN jsonb_typeof(%(expressions)s) = 'array' THEN jsonb_array_length(%(expressions)s) ELSE 0 END"


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
    if not isinstance(values, (list, tuple)) or len(values) > 8 or any(not isinstance(v, str) or len(v) > 200 or "\x00" in v for v in values):
        raise ValueError("Invalid recent reports")
    return list(dict.fromkeys(values))


def catalog_relation(request, *, kind="report", now=None, fresh_seconds=FRESH_SECONDS):
    check_catalog_context(request)
    if kind not in (Report.KIND_REPORT, Report.KIND_ANALYSIS):
        raise ValueError("Invalid catalog kind")
    now = now or timezone.now()
    relation = visible_reports(request.user, Report.objects.filter(studio=request.studio, present_in_scan=True, kind=kind), request=request)
    if not request.user.is_active:
        relation = relation.none()
    relation = relation.exclude(slug="metrics", tags__contains=["metrics"])
    fresh = Q(source_summary__schema_version=2, source_summary__observed_at__gte=now-timedelta(seconds=fresh_seconds), source_summary__observed_at__lte=now)
    output_known = fresh & Q(source_summary__quality__in=("observed", "verified")) & Q(source_summary__state__in=("ready", "missing"))
    # Worker attempt timestamps remain authoritative while local output is
    # uncertain; fresh observation also renews older terminal attempt evidence.
    recent_attempt = Q(source_summary__schema_version=2, source_summary__attempt_at__gte=now-timedelta(seconds=fresh_seconds), source_summary__attempt_at__lte=now) & ~Q(source_summary__attempt_status="")
    observed_attempt = fresh & (Q(source_summary__state__in=("ready", "missing")) | (Q(source_summary__state="uncertain") & ~Q(source_summary__attempt_status=""))) & (Q(source_summary__attempt_at__lte=now) | Q(source_summary__attempt_at__isnull=True))
    known = recent_attempt | observed_attempt
    return relation.annotate(
        display_name=Coalesce(NullIf("name", Value("")), "slug"),
        display_category=Coalesce(NullIf("category", Value("")), Value("Uncategorized")),
        observation_known=Case(When(known, then=Value(True)), default=Value(False), output_field=BooleanField()),
        output_observation_known=Case(When(output_known, then=Value(True)), default=Value(False), output_field=BooleanField()),
        is_favorite=Exists(ReportFavorite.objects.filter(user=request.user, report_id=OuterRef("pk"))),
    ).annotate(
        name_key=Lower("display_name"), category_key=Lower("display_category"), studio_key=Lower("studio__slug"), schedule_key=Lower("schedule_cron"),
        status_rank=Case(When(observation_known=False, then=Value(4)), When(source_summary__attempt_status="success", then=Value(0)), When(source_summary__attempt_status="error", then=Value(1)), When(source_summary__attempt_status__in=("", "not_run"), then=Value(2)), default=Value(3), output_field=IntegerField()),
    )


def _filtered(relation, params, recent):
    q = str(params.get("q", "")).lower()
    folder = str(params.get("tab", "all"))
    if q:
        from django.db.models.functions import Concat
        relation = relation.annotate(search_text=Lower(Concat("display_name", Value(" "), "description", Value(" "), JoinedTags("tags"), Value(" "), "display_category", Value(" "), "studio__slug", output_field=TextField()))).filter(search_text__contains=q)
    elif folder == "_favorites":
        relation = relation.filter(is_favorite=True)
    elif folder == "_recent":
        relation = relation.filter(slug__in=recent)
    elif folder != "all":
        relation = relation.filter(display_category=folder)
    studios = params.get("studio")
    if studios is not None:
        relation = relation.filter(studio__slug__in=str(studios).split(","))
    return relation, q, folder


def _order(params, recent):
    cards = params.get("view", "cards") != "list"
    sort = params.get("csort", "priority") if cards else params.get("sort", "last_run")
    allowed = ("priority", "name", "last_run", "status") if cards else ("name", "category", "studio", "status", "schedule", "last_run")
    if sort not in allowed:
        raise ValueError("Invalid catalog sort")
    ascending = sort != "last_run" if cards else params.get("asc", "1") != "0"
    direction = "ASC" if ascending else "DESC"
    stable = '"priority" ASC, "slug" ASC, "id" ASC'
    if sort == "priority":
        if params.get("tab") == "_recent" and not params.get("q"):
            # CASE values remain bound; never interpolate user-provided slugs.
            return "recent_key ASC, " + stable, sort, ascending
        return stable, sort, ascending
    if sort == "status":
        return f'"observation_known" DESC, "status_rank" {direction}, ' + stable, sort, ascending
    if sort == "last_run":
        return f'"observation_known" DESC, COALESCE("source_summary__attempt_at", to_timestamp(0)) {direction}, ' + stable, sort, ascending
    return f'"{sort}_key" {direction}, ' + stable, sort, ascending


FIELDS = ("id", "slug", "kind", "display_name", "display_category", "studio__slug", "schedule_cron", "priority", "disabled", "description_preview", "description_length", "tags_preview", "tags_count", "is_favorite", "observation_known", "output_observation_known", "name_key", "category_key", "studio_key", "schedule_key", "status_rank", "source_summary__schema_version", "source_summary__state", "source_summary__runtime_state", "source_summary__quality", "source_summary__observed_at", "source_summary__attempt_at", "source_summary__attempt_status", "source_summary__has_output", "source_summary__html_entry", "source_summary__has_live_queries")


def _rows(relation, params, recent, page, *, grouped, limit=PAGE_SIZE):
    relation = relation.annotate(description_preview=Substr("description", 1, 600), description_length=Length("description"), tags_preview=TagPreview("tags"), tags_count=TagCount("tags"), recent_key=Case(*[When(slug=slug, then=Value(i)) for i, slug in enumerate(recent)], default=Value(8), output_field=IntegerField()))
    order, _, _ = _order(params, recent)
    query = relation.order_by().values(*FIELDS, "recent_key")
    try:
        sql, arguments = query.query.sql_with_params()
    except EmptyResultSet:
        return []
    # Explicit CTE column aliases are independent of Django's related-column
    # SELECT alias policy. Every identifier is from this static field tuple.
    column_names = ", ".join(connection.ops.quote_name(f) for f in (*FIELDS, "recent_key"))
    ranked = f'WITH eligible ({column_names}) AS ({sql}), ranked AS (SELECT eligible.*, row_number() OVER (ORDER BY {order}) AS catalog_rank FROM eligible) '
    if grouped:
        sql = ranked + 'SELECT * FROM (SELECT ranked.*, min(catalog_rank) OVER (PARTITION BY display_category) AS category_rank FROM ranked) AS grouped ORDER BY category_rank, catalog_rank LIMIT %s OFFSET %s'
    else:
        sql = ranked + 'SELECT * FROM ranked ORDER BY catalog_rank LIMIT %s OFFSET %s'
    with connection.cursor() as cursor:
        cursor.execute(sql, (*arguments, limit, (page-1)*limit))
        columns = [c[0] for c in cursor.description]
        return [_presentation(dict(zip(columns, row))) for row in cursor.fetchall()]


def _presentation(row):
    known = row["observation_known"]
    state = row["source_summary__state"] or "pending"
    runtime = row["source_summary__runtime_state"] or "pending"
    trusted_output = row["output_observation_known"]
    output_known = trusted_output and runtime == "ready" and row["source_summary__has_output"] is not None
    if row["source_summary__schema_version"] not in (None, 2):
        state = "unsupported"
    elif state in ("ready", "missing") and not trusted_output:
        state = "stale"
    tags = row["tags_preview"]
    if isinstance(tags, str):
        import json
        tags = json.loads(tags)
    return dict(id=row["id"], slug=row["slug"], kind=row["kind"], name=row["display_name"], category=row["display_category"], studio=row["studio__slug"], description=row["description_preview"], description_more=row["description_length"] > 600, tags=tags, tags_count=row["tags_count"], tags_more=row["tags_count"] > 8 or any(len(t) == 80 for t in tags), schedule=row["schedule_cron"], is_favorite=row["is_favorite"], observation_state=state, output_state="known" if output_known else state if not trusted_output else runtime, has_output=row["source_summary__has_output"] if output_known else None, html_entry=row["source_summary__html_entry"] if output_known else "", last_status=(row["source_summary__attempt_status"] or "not_run") if known else None, last_run=row["source_summary__attempt_at"].isoformat() if known and row["source_summary__attempt_at"] else None)


def pagination(page, total):
    return dict(page=page, per_page=PAGE_SIZE, total=total, has_previous=page > 1, has_next=page*PAGE_SIZE < total)


def catalog_categories_payload(request, params, *, kind="report", now=None):
    validate_params(params)
    relation = catalog_relation(request, kind=kind, now=now)
    q = params.get("category_q", "")
    facets = relation.order_by().values("display_category").annotate(count=Count("pk"))
    if q:
        facets = facets.filter(display_category__icontains=q)
    page = page_number(params.get("category_page"))
    total = facets.count()
    rows = list(facets.order_by(Lower("display_category"), "display_category")[(page-1)*PAGE_SIZE:page*PAGE_SIZE])
    selected = params.get("tab", "all")
    pinned = None
    if selected not in ("all", "_favorites", "_recent") and not any(r["display_category"] == selected for r in rows):
        pinned = relation.filter(display_category=selected).values("pk").aggregate(count=Count("pk"))
        pinned["display_category"] = selected
    return bounded_payload(dict(version=1, categories=rows, selected=pinned, pagination=pagination(page, total)), 24*1024)


def catalog_payload(request, params, *, kind="report", now=None):
    validate_params(params)
    relation = catalog_relation(request, kind=kind, now=now)
    recent = recent_slugs(params)
    selected, q, folder = _filtered(relation, params, recent)
    _, sort, ascending = _order(params, recent)
    page = page_number(params.get("page"))
    # Count distinct identities without selecting/comparing full config JSON.
    total = selected.values("pk").count()
    coverage = relation.values("pk", "observation_known").aggregate(total=Count("pk"), known=Count("pk", filter=Q(observation_known=True)))
    coverage["unknown"] = coverage["total"]-coverage["known"]
    return bounded_payload(dict(version=1, rows=_rows(selected, params, recent, page, grouped=not q and folder == "all"), pagination=pagination(page, total), coverage=coverage, categories=catalog_categories_payload(request, params, kind=kind, now=now), applied=dict(kind=kind, q=q, tab=folder, sort=sort, ascending=ascending)), 128*1024)


def catalog_personal_payload(request, params, *, kind="report", now=None):
    validate_params(params)
    relation = catalog_relation(request, kind=kind, now=now)
    studios = params.get("studio")
    if studios is not None:
        relation = relation.filter(studio__slug__in=str(studios).split(","))
    recent = recent_slugs(params)
    favorite_page = page_number(params.get("favorite_page"))
    favorites = relation.filter(is_favorite=True)
    total = favorites.values("pk").count()
    # Favorites follow the current comparator; All's recent order is local.
    favorite_params = dict(params.items())
    favorite_params["tab"] = "_favorites"
    recent_params = {"tab": "_recent", "view": "cards", "csort": "priority"}
    return bounded_payload(dict(version=1, favorites=_rows(favorites, favorite_params, recent, favorite_page, grouped=False), pagination=pagination(favorite_page, total), recent=_rows(relation.filter(slug__in=recent), recent_params, recent, 1, grouped=False), recent_limit=8), 160*1024)


# ── Dashboard slices ────────────────────────────────────────────────────────
# The Reports/Analyses dashboard renders every card client-side from the legacy
# registry row shape. This reads that shape from the database in bounded
# slices, so a studio of any size costs the same per request while a normal
# studio (one slice) looks exactly like the full registry.

CARDS_PAGE = 50
# The worker keeps summaries current; the dashboard shows the last observation
# for up to a week instead of blanking every card five minutes after it.
CARDS_FRESH_SECONDS = 7 * 24 * 3600
CARDS_BYTES = 256 * 1024
_CARD_STATUSES = ("success", "error", "not_run")


def _cards_params(params):
    for key, limit in (("q", 1000), ("tab", 200)):
        value = params.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > limit or "\x00" in value):
            raise ValueError("Invalid catalog state")
    if params.get("tab", "all") not in ("all", "_favorites", "_recent"):
        raise ValueError("Invalid catalog state")
    recent_slugs(params)
    page = page_number(params.get("page"))
    try:
        limit = int(params.get("limit") or CARDS_PAGE)
    except (TypeError, ValueError):
        raise ValueError("Invalid page size") from None
    if not 5 <= limit <= CARDS_PAGE:
        raise ValueError("Invalid page size")
    return page, limit


def _registry_row(row, indicator, live_enabled):
    status = row["last_status"] if row["last_status"] in _CARD_STATUSES else ("error" if row["last_status"] not in (None, "running") else "not_run")
    last_error = indicator.get("last_error")
    return dict(
        id=row["id"], kind=row["kind"], slug=row["slug"], name=row["name"], description=row["description"] or "",
        category=row["category"], studio=row["studio"], tags=row["tags"], schedule=row["schedule"],
        last_run=row["last_run"], last_status=status, build_status=indicator.get("build_status") or status,
        last_error=last_error or None, blocked_by=indicator.get("blocked_by") or [], waiting=bool(indicator.get("waiting")),
        failing_source=indicator.get("failing_source"),
        # An output that has not been observed yet is shown as openable, with
        # the default entry the dashboard falls back to (``index.html``), so the
        # console report host still opens it.
        has_output=True if row["has_output"] is None else bool(row["has_output"]),
        html_entry=row["html_entry"] or "",
        views_30d=indicator.get("views_30d") or 0, last_viewed=indicator.get("last_viewed"),
        stale=bool(indicator.get("stale")), live=bool(live_enabled and indicator.get("live")),
        metrics_report=False,
    )


def catalog_cards_payload(request, params, *, kind="report", now=None):
    """One bounded slice of the dashboard's registry-shaped rows.

    ``q`` narrows by the catalog's search text; ``tab`` of ``_favorites`` or
    ``_recent`` (with ``recent`` slugs) returns just those rows. Rows follow
    the registry order (priority, then slug) so the client can page through
    them and sort locally.
    """
    from django.utils import timezone as tz
    from .catalog_indicators import catalog_indicators_payload
    from .models import live_query_rate_limit

    page, limit = _cards_params(params)
    now = now or tz.now()
    relation = catalog_relation(request, kind=kind, now=now, fresh_seconds=CARDS_FRESH_SECONDS)
    recent = recent_slugs(params)
    selected, _, _ = _filtered(relation, {"q": params.get("q", ""), "tab": params.get("tab", "all")}, recent)
    total = selected.values("pk").count()
    rows = _rows(selected, {"view": "cards", "csort": "priority"}, recent, page, grouped=False, limit=limit)
    indicators = catalog_indicators_payload(request, [row["id"] for row in rows], now=now, fresh_seconds=CARDS_FRESH_SECONDS) if rows else {}
    by_id = indicators.get("reports", {}) if indicators.get("state") != "oversized" else {}
    live_enabled = live_query_rate_limit(request.org) > 0
    payload = dict(
        version=1, reports=[_registry_row(row, by_id.get(row["id"], {}), live_enabled) for row in rows],
        pagination=dict(page=page, per_page=limit, total=total, has_next=page * limit < total),
        generated_at=now.isoformat(),
    )
    return bounded_payload(payload, CARDS_BYTES)
