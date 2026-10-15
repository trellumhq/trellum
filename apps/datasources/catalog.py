"""Bounded reads behind the data-source lists.

The lists used to build a state for every source (and read every report's
whole ``config``) before paginating. Here the *names* are paged in SQL first
-- search on name/type/description included, the studio's own source
shadowing the shared one of the same name -- and only the page's rows are
enriched, in batched queries. Report references come from
``config->'data_sources'`` alone, never the whole config.
"""
from __future__ import annotations

from django.db import connection
from django.db.models import Exists, F, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce, Collate

from apps.datasources.models import DataSource, RepoDataSource
from apps.datasources.status import SourceState, _state, referenced_names

UNKNOWN_PAGE = 25
BANNER_SOURCES = 10

#: Python sorts names by code point; PostgreSQL's default collation does not.
_C = "C"

_REFS_SQL = """
SELECT r.slug, e.name FROM {reports} r
CROSS JOIN LATERAL (
  SELECT CASE jsonb_typeof(x) WHEN 'string' THEN x #>> '{{}}' WHEN 'object' THEN x ->> 'name' END AS name
  FROM jsonb_array_elements(CASE WHEN jsonb_typeof(r.config -> 'data_sources') = 'array'
                                 THEN r.config -> 'data_sources' ELSE '[]'::jsonb END) x
) e
WHERE r.studio_id = %s AND r.present_in_scan AND e.name IS NOT NULL AND e.name <> ''
"""


def _reports_table() -> str:
    from apps.reports.models import Report

    return connection.ops.quote_name(Report._meta.db_table)


def effective_bindings(studio):
    """The studio's bindings with shared ones shadowed by its own (a queryset)."""
    shadowed = DataSource.objects.filter(studio=studio, name=OuterRef("name"))
    return DataSource.objects.filter(Q(studio=studio) | Q(org_id=studio.org_id, studio__isnull=True)).exclude(
        Q(studio__isnull=True) & Exists(shadowed)
    )


def _contains(text: str):
    return Q(name__icontains=text) | Q(eff_type__icontains=text)


def studio_names(studio, q: str = ""):
    """Sorted queryset of the names of the sources the settings page lists:
    declared or bound, matching ``q`` on name/type/description."""
    declarations = RepoDataSource.objects.filter(studio=studio, present=True)
    bindings = effective_bindings(studio).annotate(
        eff_type=Coalesce(Subquery(declarations.filter(name=OuterRef("name")).values("type")[:1]), F("type"))
    )
    unbound = declarations.annotate(eff_type=F("type")).exclude(
        Exists(effective_bindings(studio).filter(name=OuterRef("name")))
    )
    if q:
        bindings = bindings.filter(_contains(q) | Q(description__icontains=q))
        unbound = unbound.filter(_contains(q))
    return (
        bindings.annotate(n=Collate("name", _C)).values_list("n", flat=True)
        .union(unbound.annotate(n=Collate("name", _C)).values_list("n", flat=True), all=True)
        .order_by("n")
    )


def usage_for(studio, names) -> dict[str, list[str]]:
    """name -> slugs of the studio's present reports referencing it, for just ``names``."""
    from apps.reports.models import Report

    names = list(names)
    if not names:
        return {}
    cond = Q()
    for name in names:
        cond |= Q(config__data_sources__contains=[name]) | Q(config__data_sources__contains=[{"name": name}])
    wanted = set(names)
    usage: dict[str, list[str]] = {}
    rows = (Report.objects.filter(studio=studio, present_in_scan=True).filter(cond)
            .order_by("slug").values_list("slug", "config__data_sources"))
    for slug, refs in rows:
        for name in referenced_names({"data_sources": refs}):
            if name in wanted:
                usage.setdefault(name, []).append(slug)
    return usage


def states_for(studio, names, *, with_usage: bool = True) -> dict[str, SourceState]:
    """name -> state, for exactly these names (batched, no studio-wide pass)."""
    names = list(names)
    declarations = {d.name: d for d in RepoDataSource.objects.filter(studio=studio, present=True, name__in=names)}
    bindings = {b.name: b for b in effective_bindings(studio).filter(name__in=names)}
    usage = usage_for(studio, names) if with_usage else {}
    return {n: _state(n, declarations.get(n), bindings.get(n), usage.get(n, [])) for n in names}


def unknown_names(studio, limit: int = UNKNOWN_PAGE + 1) -> list[str]:
    """Names reports reference that are neither declared nor bound (first ``limit``)."""
    q = connection.ops.quote_name
    sql = (
        'SELECT DISTINCT n.name COLLATE "C" AS name FROM (' + _REFS_SQL.format(reports=_reports_table()) + ") n(slug, name) "
        f"WHERE NOT EXISTS (SELECT 1 FROM {q(RepoDataSource._meta.db_table)} d "
        "WHERE d.studio_id = %s AND d.present AND d.name = n.name) "
        f"AND NOT EXISTS (SELECT 1 FROM {q(DataSource._meta.db_table)} b "
        "WHERE (b.studio_id = %s OR b.org_id = %s) AND b.name = n.name) "
        "ORDER BY name LIMIT %s"
    )
    with connection.cursor() as cur:
        cur.execute(sql, [studio.pk, studio.pk, studio.pk, studio.org_id, limit])
        return [row[0] for row in cur.fetchall()]


def needing_sources(studio) -> list[SourceState]:
    """Declared sources still waiting for credentials or a file. Only declared
    sources can be in those states, so no report is read."""
    declarations = list(RepoDataSource.objects.filter(studio=studio, present=True))
    if not declarations:
        return []
    bindings = {b.name: b for b in effective_bindings(studio).filter(
        name__in=[d.name for d in declarations])}
    needs = (SourceState.NEEDS_CREDENTIALS, SourceState.NEEDS_UPLOAD)
    states = [_state(d.name, d, bindings.get(d.name), []) for d in declarations]
    return sorted((s for s in states if s.state in needs), key=lambda s: s.name)


def waiting_report_count(studio, names) -> int:
    """Present reports referencing any of ``names`` (one aggregate)."""
    names = list(names)
    if not names:
        return 0
    sql = (
        "SELECT count(DISTINCT n.slug) FROM (" + _REFS_SQL.format(reports=_reports_table())
        + ") n(slug, name) WHERE n.name = ANY(%s)"
    )
    with connection.cursor() as cur:
        cur.execute(sql, [studio.pk, names])
        return cur.fetchone()[0]


def banner(studio) -> dict | None:
    needs = needing_sources(studio)
    if not needs:
        return None
    return {
        "count": len(needs),
        "waiting": waiting_report_count(studio, [s.name for s in needs]),
        "noun": "a file" if all(s.state == SourceState.NEEDS_UPLOAD for s in needs) else "credentials",
        "sources": needs[:BANNER_SOURCES],
    }


def org_bindings(org, q: str = ""):
    """The organization's shared bindings, matching ``q`` on name/type/description
    (type is the first declaring studio's when a studio declares the name)."""
    declared_type = Subquery(
        RepoDataSource.objects.filter(name=OuterRef("name"), present=True, studio__org_id=OuterRef("org_id"))
        .order_by("studio_id").values("type")[:1]
    )
    qs = DataSource.objects.filter(org=org).annotate(eff_type=Coalesce(declared_type, F("type")))
    if q:
        qs = qs.filter(_contains(q) | Q(description__icontains=q))
    return qs.order_by("name")


def org_declarations(org, names) -> dict[str, tuple[RepoDataSource, int]]:
    """name -> (first declaring studio's declaration, number of declaring studios)."""
    out: dict[str, RepoDataSource] = {}
    counts: dict[str, int] = {}
    for d in RepoDataSource.objects.filter(studio__org=org, present=True, name__in=list(names)).order_by("studio_id"):
        out.setdefault(d.name, d)
        counts[d.name] = counts.get(d.name, 0) + 1
    return {n: (d, counts[n]) for n, d in out.items()}
