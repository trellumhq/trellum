"""Read-only annotation projection queries for an already-authorized studio.

These helpers do not authorize requests or establish a transaction snapshot.
The eventual HTTP owner must do both, including repeatable-read setup before
its authorization queries. No helper repairs a projection or accesses files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db import connections
from django.db.models import (
    BooleanField, Case, CharField, Count, F, Func, IntegerField, OuterRef,
    Q, QuerySet, Subquery, Value, When,
)
from django.utils import timezone

from .annotation_projection import SCHEMA_VERSION
from .models import AnnotationCatalogState, AnnotationEvent, AnnotationSourceState, Report

PAGE_SIZE = 25
GRID_DAYS = 42
FRESH_SECONDS = 300
TYPE_ORDER = ("campaign", "ab_test", "release", "incident", "event")


@dataclass(frozen=True)
class EventFilter:
    """Internal filter contract; no request values or arbitrary SQL expressions.

    scope=None means All; scope='' deliberately selects empty local scope.
    Hidden membership is a one-column CharField queryset, never evaluated here.
    A later separately reviewed state producer can supply that relation without
    transferring its entire membership into an event request. Its actor/studio
    scoping is the caller's responsibility, just as studio authorization is.
    """

    search: str = ""
    scope: str | None = None
    defaults: bool = False
    hidden_types: QuerySet | None = field(default=None, compare=False, repr=False)

    def __post_init__(self):
        if not isinstance(self.search, str) or "\x00" in self.search or len(self.search) > 200 or len(self.search.encode()) > 800:
            raise ValueError("invalid search")
        if self.scope is not None and (not isinstance(self.scope, str) or "\x00" in self.scope or len(self.scope) > 256 or len(self.scope.encode()) > 1024):
            raise ValueError("invalid scope")
        if not isinstance(self.defaults, bool):
            raise ValueError("invalid visibility mode")
        if self.hidden_types is not None:
            if not isinstance(self.hidden_types, QuerySet):
                raise ValueError("hidden membership must be a queryset")
            # A narrow, unsliced relation is required; no eager list or multi-
            # column model queryset can accidentally become a huge SQL array.
            query = self.hidden_types.query
            if len(query.values_select) != 1 or query.annotation_select or query.is_sliced:
                raise ValueError("hidden membership must select one field")
            selected = self.hidden_types.model._meta.get_field(query.values_select[0])
            if not isinstance(selected, CharField):
                raise ValueError("hidden membership must select text")


@dataclass(frozen=True)
class EventPosition:
    """Internal keyset position; HTTP signing/filter/revision binding is external."""

    start_date: date
    label: str
    source_id: int
    ordinal: int
    id: int


@dataclass(frozen=True)
class FacetPosition:
    priority: int
    value: str


@dataclass(frozen=True)
class Page:
    rows: list[dict]
    total: int
    next_after: EventPosition | FacetPosition | None


def _success(prefix=""):
    return Q(**{
        prefix + "schema_version": SCHEMA_VERSION,
        prefix + "indexed_at__isnull": False,
        prefix + "published_generation__gt": 0,
    }) & ~Q(**{prefix + "status": "removed"})


def _current(now, prefix=""):
    same_generation = Q(**{prefix + "desired_generation": F(prefix + "published_generation")})
    report = Q(**{prefix + "kind": "report", prefix + "status": "ready"})
    global_source = Q(**{
        prefix + "kind": "global", prefix + "status__in": ("ready", "missing"),
        prefix + "observed_at__gte": now - timedelta(seconds=FRESH_SECONDS),
        prefix + "observed_at__lte": now,
    }) & ~Q(**{prefix + "owner_workspace_id": ""})
    return _success(prefix) & same_generation & (report | global_source)


class _TypeDefault(Func):
    """Extract only the requested type's boolean inside PostgreSQL."""

    output_field = BooleanField()
    template = "(COALESCE(jsonb_extract_path_text(%(expressions)s), 'true') = 'true')"


def _defaults(using):
    # Catalog defaults belong to the global last-successful declaration.
    # An incompatible successful schema cannot lend visibility semantics to
    # otherwise compatible local events. Pending/error compatible successes
    # retain their defaults, just as they retain their indexed history.
    return AnnotationCatalogState.objects.using(using).filter(
        _success("studio__annotation_sources__"),
        studio_id=OuterRef("studio_id"), studio__annotation_sources__kind="global",
    ).values("type_defaults")[:1]


def catalog(studio, *, using="default") -> dict | None:
    """One narrow lookup; never transfers the full type_defaults JSON."""
    return AnnotationCatalogState.objects.using(using).filter(studio_id=studio.pk).values(
        "revision", "published_revision"
    ).first()


def events(studio, filters=None, *, now=None, using="default"):
    """Unevaluated full-history relation including labeled compatible successes."""
    filters = filters or EventFilter()
    if not isinstance(filters, EventFilter):
        raise TypeError("filters must be EventFilter")
    now = now or timezone.now()
    membership = Q(source__kind="global") | Q(source__kind="report", source__report__present_in_scan=True, source__report__studio_id=studio.pk)
    qs = AnnotationEvent.objects.using(using).filter(
        _success("source__"), membership, studio_id=studio.pk,
        source__studio_id=studio.pk, generation=F("source__published_generation"),
    ).annotate(source_current=Case(When(_current(now, "source__"), then=Value(True)), default=Value(False), output_field=BooleanField()))
    if filters.scope is not None:
        qs = qs.filter(scope=filters.scope)
    if filters.search:
        text = filters.search
        search = Q(label__icontains=text) | Q(event_type__icontains=text) | Q(scope__icontains=text) | Q(source__report__slug__icontains=text)
        # The constant source labels are searchable too, without fetching rows.
        if text.casefold() in "events.yaml":
            search |= Q(source__kind="global")
        if text.casefold() in "report: ":
            search |= Q(source__kind="report")
        # Include searches spanning the report-label prefix and slug.
        from django.db.models.functions import Concat
        qs = qs.alias(_source_label=Concat(Value("report: "), F("source__report__slug")))
        search |= Q(source__kind="report", _source_label__icontains=text)
        qs = qs.filter(search)
    if filters.defaults:
        defaults = _defaults(using)
        qs = qs.alias(_type_default=_TypeDefault(Subquery(defaults), F("event_type"))).filter(_type_default=True)
    if filters.hidden_types is not None:
        if filters.hidden_types.db != using:
            raise ValueError("hidden membership must use the same database")
        # Remove nulls to preserve NOT IN semantics even for nullable fields.
        column = filters.hidden_types.query.values_select[0]
        hidden = filters.hidden_types.filter(**{column + "__isnull": False}).order_by()
        qs = qs.exclude(event_type__in=Subquery(hidden))
    return qs


def _window(qs, start, end):
    if type(start) is not date or type(end) is not date or end < start or (end - start).days >= GRID_DAYS:
        raise ValueError("date window must contain 1..42 days")
    # Reversed ranges stay list-visible, but occupy no calendar date.
    point = (Q(end_date__isnull=True) | Q(end_date=F("start_date"))) & Q(start_date__range=(start, end))
    ranged = Q(end_date__gt=F("start_date"), start_date__lte=end, end_date__gte=start)
    return qs.filter(point | ranged)


_EVENT_FIELDS = (
    "id", "source_id", "ordinal", "start_date", "end_date", "label",
    "event_type", "scope", "visible_default", "source_current",
    "source__kind", "source__report__slug", "source__indexed_at",
)


def event_page(qs, *, after=None, ascending=False, start=None, end=None):
    """Count in SQL and fetch at most26 rows; never offset/download-all/slice."""
    if after is not None and not isinstance(after, EventPosition):
        raise TypeError("after must be EventPosition")
    if not isinstance(ascending, bool):
        raise ValueError("ascending must be boolean")
    if start is not None or end is not None:
        qs = _window(qs, start, end)
    return _event_page(qs, total=qs.count(), after=after, ascending=ascending)


def _event_page(qs, *, total, after=None, ascending=False):
    # Internal only: callers that provide total must have counted exactly this
    # relation in the same response snapshot. No HTTP count input is accepted.
    fields = ("start_date", "label", "source_id", "ordinal", "id")
    order = (("start_date" if ascending else "-start_date"), *fields[1:])
    if after is not None:
        if not isinstance(after, EventPosition):
            raise TypeError("after must be EventPosition")
        values = (after.start_date, after.label, after.source_id, after.ordinal, after.id)
        predicate = Q(pk__in=[])
        equal = Q()
        for i, (name, value) in enumerate(zip(fields, values)):
            lookup = "lt" if i == 0 and not ascending else "gt"
            predicate |= equal & Q(**{name + "__" + lookup: value})
            equal &= Q(**{name: value})
        qs = qs.filter(predicate)
    rows = list(qs.order_by(*order).values(*_EVENT_FIELDS)[:PAGE_SIZE + 1])
    more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    next_after = EventPosition(*(rows[-1][name] for name in fields)) if more else None
    return Page(rows, total, next_after)


def _calendar_counts(qs, start, end):
    """One PostgreSQL aggregate returns <=42 buckets, including known zeros.

    These are indexed-data counts. The caller must expose coverage before
    presenting them as current totals. Filter SQL is compiled by Django;
    request strings are never interpolated into this query.
    """
    qs = _window(qs, start, end)
    connection = connections[qs.db]
    if connection.vendor != "postgresql":
        raise RuntimeError("annotation day aggregates require PostgreSQL")
    relation = qs.order_by().values("start_date", "end_date")
    sql, params = relation.query.get_compiler(using=qs.db).as_sql()
    statement = f"""
        WITH filtered AS ({sql}), days AS (
            SELECT generate_series(%s::date, %s::date, interval '1 day')::date AS day
        )
        SELECT day, (SELECT count(*) FROM filtered) AS event_total,
            count(f.start_date) FILTER (WHERE f.end_date IS NULL OR f.end_date = f.start_date) AS point_count,
            count(f.start_date) FILTER (WHERE f.end_date > f.start_date) AS range_count
        FROM days LEFT JOIN filtered f ON
            ((f.end_date IS NULL OR f.end_date = f.start_date) AND f.start_date = day)
            OR (f.end_date > f.start_date AND f.start_date <= day AND f.end_date >= day)
        GROUP BY day ORDER BY day
    """
    with connection.cursor() as cursor:
        cursor.execute(statement, (*params, start, end))
        records = cursor.fetchall()
        return records[0][1], [dict(day=d, point_count=p, range_count=r, count=p + r) for d, _total, p, r in records]


def day_counts(qs, start, end):
    """One SQL request yields <=42 indexed-count buckets; expose coverage too."""
    return _calendar_counts(qs, start, end)[1]


def month_data(qs, start, end):
    """Two queries: exact day/unique counts and at most25 overlapping previews.

    Catalog + coverage + this helper meet the four data-query month goal.
    The caller must establish one snapshot before executing these helpers.
    """
    total, days = _calendar_counts(qs, start, end)
    preview = _event_page(_window(qs, start, end), total=total, ascending=True)
    return {"days": days, "events": preview}


def facet_page(qs, field, *, search="", after=None):
    """Discover all full-history type/scope choices,25 at a time.

    Caller supplies the discoverability relation (usually before active type/
    scope hiding), so facets need not lose choices excluded by current filters.
    """
    if field not in ("event_type", "scope"):
        raise ValueError("invalid facet")
    if not isinstance(search, str) or "\x00" in search or len(search) > 200 or len(search.encode()) > 800:
        raise ValueError("invalid facet search")
    if search:
        qs = qs.filter(**{field + "__icontains": search})
    order = TYPE_ORDER if field == "event_type" else ("shared",)
    priority = Case(*(When(**{field: name}, then=Value(i)) for i, name in enumerate(order)), default=Value(len(order)), output_field=IntegerField())
    groups = qs.order_by().values(field).annotate(count=Count("pk"), priority=priority)
    if field == "event_type":
        defaults = _defaults(qs.db)
        # Group explicitly by studio_id for the correlated default lookup;
        # events() already confines this relation to one authorized studio.
        groups = qs.order_by().values(field, "studio_id").annotate(count=Count("pk"), priority=priority, default_visible=_TypeDefault(Subquery(defaults), F(field)))
    connection = connections[qs.db]
    if connection.vendor != "postgresql":
        raise RuntimeError("annotation facets require PostgreSQL")
    sql, params = groups.query.get_compiler(using=qs.db).as_sql()
    predicate, page_params = "TRUE", ()
    if after is not None:
        if not isinstance(after, FacetPosition):
            raise TypeError("after must be FacetPosition")
        predicate = f"(priority > %s OR (priority = %s AND {connection.ops.quote_name(field)} > %s))"
        page_params = (after.priority, after.priority, after.value)
    column = connection.ops.quote_name(field)
    # Count grouped choices before the keyset predicate. The LEFT JOIN yields
    # one count-only row even when a valid cursor has no remaining choices.
    statement = f"""
        WITH grouped AS ({sql})
        SELECT totals.total, page.* FROM (SELECT count(*) AS total FROM grouped) totals
        LEFT JOIN LATERAL (
            SELECT * FROM grouped WHERE {predicate}
            ORDER BY priority, {column} LIMIT %s
        ) page ON TRUE ORDER BY page.priority, page.{column}
    """
    with connection.cursor() as cursor:
        cursor.execute(statement, (*params, *page_params, PAGE_SIZE + 1))
        names = [c[0] for c in cursor.description]
        records = [dict(zip(names, row)) for row in cursor.fetchall()]
    total = records[0]["total"]
    rows = [row for row in records if row[field] is not None]
    more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    next_after = FacetPosition(rows[-1]["priority"], rows[-1][field]) if more else None
    for row in rows:
        row.pop("total")
        row.pop("studio_id", None)
        row["value"] = row.pop(field)
    return Page(rows, total, next_after)


def coverage(studio, *, now=None, using="default"):
    """One SQL aggregate over expected sources; no full source/default transfer.

    A present report whose declaration is absent/removed is unknown. Absent
    global state also counts as unknown. Attempted schema does not invalidate
    a compatible last successful schema; incompatible successes are excluded.
    """
    now = now or timezone.now()
    connection = connections[using]
    if connection.vendor != "postgresql":
        raise RuntimeError("annotation coverage requires PostgreSQL")
    quote = connection.ops.quote_name
    sources = quote(AnnotationSourceState._meta.db_table)
    reports = quote(Report._meta.db_table)
    catalogs = quote(AnnotationCatalogState._meta.db_table)
    statement = f"""
        WITH expected AS (
            SELECT id AS report_id, 'report' AS kind FROM {reports}
              WHERE studio_id = %s AND present_in_scan
            UNION ALL SELECT NULL::bigint, 'global'
        ), states AS (
            SELECT s.kind, s.status, s.desired_generation, s.published_generation,
              s.indexed_at, s.owner_workspace_id, s.error_code,
              (s.schema_version = %s AND s.indexed_at IS NOT NULL
               AND s.published_generation > 0 AND s.status <> 'removed') AS success,
              (e.kind = 'global' AND (s.observed_at IS NULL OR s.observed_at < %s OR s.observed_at > %s)) AS stale
            FROM expected e LEFT JOIN {sources} s ON s.studio_id = %s
              AND s.kind = e.kind AND (s.report_id = e.report_id OR e.kind = 'global')
        ), classified AS (
            SELECT *, COALESCE(success AND desired_generation = published_generation
              AND ((kind = 'report' AND status = 'ready') OR
                   (kind = 'global' AND status IN ('ready', 'missing')
                    AND owner_workspace_id <> '' AND NOT stale)), FALSE) AS current
            FROM states
        )
        SELECT count(*) AS expected, count(*) FILTER (WHERE current) AS current,
          count(*) FILTER (WHERE success AND NOT current) AS retained,
          count(*) FILTER (WHERE NOT COALESCE(success, FALSE)) AS unknown,
          count(*) FILTER (WHERE stale) AS stale,
          count(*) FILTER (WHERE error_code <> '' AND status <> 'removed') AS errors,
          min(indexed_at) FILTER (WHERE success AND NOT current) AS retained_since,
          EXISTS(SELECT 1 FROM {catalogs} WHERE studio_id = %s) AS catalog_known
        FROM classified
    """
    with connection.cursor() as cursor:
        cursor.execute(statement, (studio.pk, SCHEMA_VERSION, now - timedelta(seconds=FRESH_SECONDS), now, studio.pk, studio.pk))
        result = dict(zip((c[0] for c in cursor.description), cursor.fetchone()))
    result["complete"] = result["catalog_known"] and result["current"] == result["expected"]
    return result
