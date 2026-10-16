"""Bounded database observations for Operations; no storage reads or repairs.

Callers still mount authenticated views. These helpers independently check the
current actor's developer/full-studio access and bearer organization boundary.
The report rows are the registry rows (``apps.reports.registry_rows``): the
table and the dashboard cards cannot disagree about a report.
"""
import re
import time
from dataclasses import dataclass
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import EmptyResultSet, PermissionDenied
from django.db import connection
from django.db.models import Count, Q
from django.utils import timezone

from apps.core import roles
from apps.core.limits import INLINE_LIMIT
from apps.core.pagination import page_metadata
from apps.core.permissions import get_effective
from apps.datasources.models import INLINE_TYPES, DataSource, RepoDataSource
from apps.datasources.status import effective_fields
from apps.reports.catalog import bounded_payload, search
from apps.reports.models import Report
from apps.reports.registry_rows import (
    download_available,
    page_of_rows,
    registry_rows,
    source_states_for,
)
from apps.runner.models import Run, WorkerHeartbeat
from apps.runner.stats import studio_run_stats

PAGE_SIZE = 25
#: The Operations page lists every report up to this many rows with no pager;
#: past it, search (on the server) reaches the rest.
ROW_CAP = INLINE_LIMIT
BYTES_PER_ROW = 4096
REFERENCE_PREVIEW = 8
RESPONSE_BYTES = 128 * 1024
CONTROL = re.compile(r'[\x00-\x1f\x7f\\/]')


@dataclass(frozen=True)
class OperationsParams:
    q: str = ''
    source_q: str = ''
    page: int = 1
    page_size: int = PAGE_SIZE


def _values(params, key):
    if hasattr(params, 'getlist'):
        return params.getlist(key)
    value = params.get(key)
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _scalar(params, key, default, limit):
    values = _values(params, key)
    if len(values) > 1:
        raise ValueError('Duplicate Operations parameter')
    value = values[0] if values else default
    if not isinstance(value, str) or len(value) > limit or len(value.encode('utf-8')) > limit * 4 or '\x00' in value:
        raise ValueError('Invalid Operations parameter')
    return value


def _page(value):
    if not re.fullmatch(r'[0-9]{1,7}', value) or not 1 <= int(value) <= 1000000:
        raise ValueError('Invalid Operations page')
    return int(value)


def _page_size(value):
    if not re.fullmatch(r'[0-9]{1,3}', value) or not 1 <= int(value) <= ROW_CAP:
        raise ValueError('Invalid Operations page size')
    return int(value)


def validate_slugs(slugs, *, maximum=PAGE_SIZE):
    if not isinstance(slugs, (list, tuple)) or len(slugs) > maximum:
        raise ValueError('Invalid Operations selection')
    if any(not isinstance(s, str) or not s or len(s) > 200 or len(s.encode('utf-8')) > 800 or CONTROL.search(s) for s in slugs):
        raise ValueError('Invalid Operations slug')
    return tuple(dict.fromkeys(slugs))


def validate_operations_params(params):
    """Validate raw query parameters; duplicate scalars are never ambiguous."""
    if not hasattr(params, 'get'):
        raise ValueError('Invalid Operations parameters')
    # Bound even unused parameters; adapters must not admit unlimited query work.
    pairs = [(key, value) for key in params for value in _values(params, key)]
    if len(pairs) > 64 or any(not isinstance(k, str) or not isinstance(v, str) for k, v in pairs):
        raise ValueError('Invalid Operations parameters')
    if len(urlencode(pairs).encode('ascii')) > 32768:
        raise ValueError('Operations query is oversized')
    return OperationsParams(
        q=_scalar(params, 'q', '', 1000), source_q=_scalar(params, 'source_q', '', 1000),
        page=_page(_scalar(params, 'page', '1', 7)), page_size=_page_size(_scalar(params, 'page_size', str(PAGE_SIZE), 3)),
    )


def _check_context(request):
    org, studio = request.org, request.studio
    user = request.user
    key = getattr(request, 'api_key', None)
    if (not user.is_authenticated or not user.is_active or not org.is_active
            or studio.org_id != org.pk or (key is not None and key.org_id != org.pk)):
        raise PermissionDenied('Operations scope is unavailable')
    effective = get_effective(request, org)
    if not roles.at_least(effective.role_for(studio), roles.DEVELOPER) or not effective.has_full_studio_visibility(studio):
        raise PermissionDenied('Operations requires full developer access')


def operations_relation(request):
    """All present report kinds, including the generated Metrics report."""
    _check_context(request)
    # Developer/full visibility is authoritative for private reports too. No
    # viewer or C14 generated-metrics exclusion belongs in this relation.
    return Report.objects.filter(studio_id=request.studio.pk, present_in_scan=True)


def _row_bytes(page_size):
    """Byte ceiling for a page: the default page keeps its fixed ceiling."""
    return RESPONSE_BYTES if page_size <= PAGE_SIZE else page_size * BYTES_PER_ROW


def _next_run(cron, zone, now):
    if not cron:
        return None
    from apscheduler.triggers.cron import CronTrigger
    try:
        return CronTrigger.from_crontab(cron, timezone=ZoneInfo(zone or 'UTC')).get_next_fire_time(None, now)
    except (ValueError, ZoneInfoNotFoundError):
        return None


def operations_page_payload(request, params, *, now=None):
    """One page of the studio's reports as registry rows, plus when each is next scheduled."""
    state = validate_operations_params(params)
    now = now or timezone.now()
    relation = search(operations_relation(request), state.q)
    total = relation.values('pk').count()
    raw = page_of_rows(relation, ('priority', 'slug', 'pk'), state.page_size, (state.page - 1) * state.page_size)
    rows = registry_rows(request.studio, request.org, raw, now=now)
    for row in rows:
        # A paused or disabled report has no next run: the scheduler no longer fires it.
        paused = row['disabled'] or row['refresh_suspended_at']
        row['next_run'] = None if paused else _next_run(row['schedule'], row['schedule_timezone'], now)
    return bounded_payload({'version': 1, 'state': 'ready', 'rows': rows, 'pagination': page_metadata(state.page, state.page_size, total), 'observed_at': now},
                           _row_bytes(state.page_size))


def _server_uptime():
    from apps.reports.views import _SERVER_START
    return round(time.time() - _SERVER_START, 1)


def operations_status_payload(request, *, now=None):
    """Active runs of every authorized report of the studio, up to ROW_CAP rows."""
    now = now or timezone.now()
    authorized = operations_relation(request).order_by().values('slug')
    runs = Run.objects.filter(studio=request.studio, status__in=Run.ACTIVE_STATUSES)
    totals = runs.aggregate(total=Count('pk'), selected=Count('pk', filter=Q(slug__in=authorized)), running=Count('pk', filter=Q(status__in=(Run.STARTING, Run.RUNNING))), queued=Count('pk', filter=Q(status=Run.QUEUED)))
    page = list(runs.filter(slug__in=authorized).order_by('priority', 'created_at', 'pk').values('id', 'slug', 'status', 'created_at', 'started_at', 'priority', 'cache_mode')[:ROW_CAP])
    beat = WorkerHeartbeat.alive().order_by('-last_beat_at').values('max_concurrent').first()
    return bounded_payload({'version': 1, 'state': 'ready', 'active_jobs_total': totals, 'rows': page,
                                'coverage': {'page_limit': ROW_CAP, 'active_rows_total': totals['selected'], 'active_rows_complete': totals['selected'] <= ROW_CAP},
                                'worker_alive': beat is not None, 'max_concurrent': beat['max_concurrent'] if beat else settings.WORKER_MAX_CONCURRENT,
                                'server_uptime_seconds': _server_uptime(), 'observed_at': now}, RESPONSE_BYTES * 4)


def operations_stats_payload(request, *, now=None):
    authorized = tuple(operations_relation(request).order_by().values_list('slug', flat=True)[:ROW_CAP])
    return bounded_payload({'version': 1, 'state': 'ready', 'stats': studio_run_stats(request.studio, slugs=authorized), 'window_days': 30, 'observed_at': now or timezone.now()},
                           RESPONSE_BYTES * 4)


def _source_cte(request, now):
    relation = operations_relation(request).order_by().values('id')
    try:
        authorized_sql, authorized_params = relation.query.sql_with_params()
    except EmptyResultSet:
        authorized_sql, authorized_params = 'SELECT NULL::bigint WHERE FALSE', ()
    # Identifiers come solely from model metadata. Every scope/search/name
    # value is a DB parameter, including the authorization subquery above.
    quote = connection.ops.quote_name
    reports = quote(Report._meta.db_table)
    declarations = quote(RepoDataSource._meta.db_table)
    bindings = quote(DataSource._meta.db_table)
    from apps.reports.models import ReportSourceSummary
    summaries = quote(ReportSourceSummary._meta.db_table)
    sql = f"""WITH authorized AS ({authorized_sql}), source_refs AS (
      SELECT r.id AS report_id,r.slug,n.name FROM {reports} r JOIN authorized a ON a.id=r.id
      CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(r.config->'data_sources')='array' THEN r.config->'data_sources' ELSE '[]'::jsonb END) entry
      CROSS JOIN LATERAL (SELECT CASE WHEN jsonb_typeof(entry)='string' THEN entry#>>'{{}}' WHEN jsonb_typeof(entry)='object' AND jsonb_typeof(entry->'name')='string' THEN entry->>'name' ELSE '' END AS name) n WHERE n.name<>''
    ), observed_blocks AS (
      SELECT refs.name,refs.report_id,refs.slug FROM source_refs refs
      JOIN {summaries} summary ON summary.report_id=refs.report_id
      WHERE summary.schema_version=2 AND summary.quality IN ('observed','verified')
        AND summary.attempt_status='error' AND summary.observed_at<=%s AND summary.attempt_at<=%s
        AND CASE WHEN jsonb_typeof(summary.attempt_blocked_by)='array' THEN summary.attempt_blocked_by ? refs.name ELSE FALSE END
    ), effective_bindings AS (
      SELECT DISTINCT ON (name) id,name,type FROM {bindings} WHERE studio_id=%s OR org_id=%s ORDER BY name,studio_id NULLS LAST
    ), source_names AS (
      SELECT name FROM {declarations} WHERE studio_id=%s AND present
      UNION SELECT name FROM effective_bindings UNION SELECT name FROM source_refs
    ), sources AS (
      SELECT names.name,COALESCE(d.type,b.type,'') AS type FROM source_names names
      LEFT JOIN {declarations} d ON d.name=names.name AND d.studio_id=%s AND d.present
      LEFT JOIN effective_bindings b ON b.name=names.name
    )"""
    return sql, (*authorized_params, now, now, request.studio.pk, request.org.pk, request.studio.pk, request.studio.pk)


def operations_sources_payload(request, params, *, now=None):
    state = validate_operations_params(params)
    now = now or timezone.now()
    cte, arguments = _source_cte(request, now)
    condition = 'WHERE name ILIKE %s OR type ILIKE %s' if state.source_q else ''
    # LIKE wildcards in text are literals, matching ORM icontains semantics.
    query = state.source_q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    search_args = (f'%{query}%', f'%{query}%') if state.source_q else ()
    with connection.cursor() as cursor:
        cursor.execute(cte + f' SELECT count(*) FROM sources {condition}', (*arguments, *search_args))
        total = cursor.fetchone()[0]
        cursor.execute(cte + f' SELECT left(name,200),length(name),md5(name),type FROM sources {condition} ORDER BY lower(name),name LIMIT %s OFFSET %s', (*arguments, *search_args, state.page_size, 0))
        identities = cursor.fetchall()
        names = [name for name, length, digest, type_ in identities if length <= 64]
    # Malformed imported reference identities are visible but cannot trigger
    # credential/name operations or be represented as configured sources.
    valid_names = [name for name in names if re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', name)]
    source_states = source_states_for(request.studio, request.org, valid_names)
    usage = {}
    blocks = {}
    digests = [digest for name, length, digest, type_ in identities]
    if digests:
        with connection.cursor() as cursor:
            cursor.execute(cte + " SELECT md5(name),count(DISTINCT report_id), ARRAY(SELECT DISTINCT p.slug FROM source_refs p WHERE p.name=refs.name ORDER BY p.slug LIMIT 8) FROM source_refs refs WHERE md5(name)=ANY(%s) GROUP BY name", (*arguments, digests))
            usage = {name: (count, slugs) for name, count, slugs in cursor.fetchall()}
            cursor.execute(cte + " SELECT md5(name),count(DISTINCT report_id), ARRAY(SELECT DISTINCT p.slug FROM observed_blocks p WHERE p.name=refs.name ORDER BY p.slug LIMIT 8) FROM observed_blocks refs WHERE md5(name)=ANY(%s) GROUP BY name", (*arguments, digests))
            blocks = {name: (count, slugs) for name, count, slugs in cursor.fetchall()}
    rows = []
    for name, name_length, digest, type_ in identities:
        source = source_states.get(name) if name_length <= 64 else None
        count, preview = usage.get(digest, (0, []))
        block_count, block_preview = blocks.get(digest, (0, []))
        upload = bool(source and source.type in INLINE_TYPES and effective_fields(source.declaration, source.binding).get('upload'))
        rows.append({
            'identity': {'org_id': request.org.pk, 'studio_id': request.studio.pk, 'name': name if source else None, 'reference_digest': digest if source is None else None}, 'name': name, 'name_truncated': name_length>200,
            'type': source.type[:64] if source else '', 'state': source.state if source else 'unknown', 'detail': source.detail[:300] if source else 'Unindexed source reference',
            'declared': source.declared if source else False, 'binding_scope': source.binding_scope if source else None,
            'declaration_id': source.declaration.pk if source and source.declaration else None,
            'binding_id': source.binding.pk if source and source.binding else None,
            'last_check_at': source.last_check_at if source else None, 'last_check_ok': source.last_check_ok if source else None,
            'used_by': preview, 'used_by_total': count, 'used_by_complete': count<=REFERENCE_PREVIEW,
            'blocks_observed': block_preview, 'blocks_observed_total': block_count, 'blocks_current': None,
            'upload_allowed': upload and source.binding_scope != 'org',
            'download_available': bool(source and download_available(source)), 'quality': 'configured' if source and source.state != 'unknown' else 'unknown',
        })
    return bounded_payload({'version': 1, 'state': 'ready', 'rows': rows, 'pagination': page_metadata(1, state.page_size, total), 'observed_at': now},
                           _row_bytes(state.page_size))
