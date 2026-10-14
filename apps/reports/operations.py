"""Bounded database observations for Operations; no storage reads or repairs.

Callers still mount authenticated views. These helpers independently check the
current actor's developer/full-studio access and bearer organization boundary.
Serving observations, durable attempts and current readiness are distinct facts.
"""
from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import re
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import EmptyResultSet, PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.db.models import (
    BooleanField, Case, CharField, Count, Exists, F, Func, IntegerField,
    JSONField, OuterRef, Q, Subquery, Value, When,
)
from django.db.models.functions import Coalesce, Length, Substr
from django.db.models.fields.json import KeyTextTransform
from django.utils import timezone

from apps.core import roles
from apps.core.permissions import get_effective
from apps.datasources.models import DataSource, RepoDataSource
from apps.datasources.status import SourceState, _state, effective_fields
from apps.reports.models import Report, ReportFavorite, live_query_rate_limit
from apps.runner.models import Run, WorkerHeartbeat
from apps.runner.stats import studio_run_stats

PAGE_SIZE = 25
DEPENDENCY_LIMIT = 25
REFERENCE_PREVIEW = 8
RESPONSE_BYTES = 128 * 1024
FRESH_SECONDS = 300
STATES = frozenset(('all', 'running', 'queued', 'waiting', 'error', 'oom',
                    'timeout', 'stopped', 'ok', 'not_run', 'unknown',
                    'v_fail', 'v_warn', 'healthy', 'v_unknown'))
SORTS = frozenset(('name', 'category', 'priority', 'status', 'last_run'))
CONTROL = re.compile(r'[\x00-\x1f\x7f\\/]')


@dataclass(frozen=True)
class OperationsParams:
    q: str = ''
    source_q: str = ''
    category: str = ''
    folder: str = 'all'
    state: str = 'all'
    page: int = 1
    source_page: int = 1
    sort: str = 'priority'
    ascending: bool = True
    recent: tuple = ()


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
    state = _scalar(params, 'state', 'all', 16)
    sort = _scalar(params, 'sort', 'priority', 12)
    asc = _scalar(params, 'asc', '1', 1)
    folder = _scalar(params, 'folder', 'all', 12)
    if state not in STATES or sort not in SORTS or asc not in ('0', '1') or folder not in ('all', 'favorites', 'recent'):
        raise ValueError('Invalid Operations filter')
    return OperationsParams(
        q=_scalar(params, 'q', '', 1000), source_q=_scalar(params, 'source_q', '', 1000),
        category=_scalar(params, 'category', '', 200), folder=folder, state=state,
        page=_page(_scalar(params, 'page', '1', 7)),
        source_page=_page(_scalar(params, 'source_page', '1', 7)),
        sort=sort, ascending=asc == '1', recent=validate_slugs(_values(params, 'recent'), maximum=8),
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


def _bounded(payload):
    if len(json.dumps(payload, cls=DjangoJSONEncoder).encode('utf-8')) <= RESPONSE_BYTES:
        return payload
    result = dict(version=1, state='oversized', message='This observation exceeds its presentation limit. Narrow the selection.')
    for key in ('pagination', 'coverage', 'counts', 'applied'):
        if key in payload:
            result[key] = payload[key]
    return result


def _pagination(page, total):
    return dict(page=page, page_size=PAGE_SIZE, total=total, pages=(total + PAGE_SIZE - 1) // PAGE_SIZE)


class ReferenceNames(Func):
    """Bounded distinct string/object references; counts stay in PostgreSQL."""
    output_field = JSONField()

    def __init__(self, expression, *, count=False, limit=REFERENCE_PREVIEW):
        super().__init__(expression, output_field=IntegerField() if count else JSONField())
        self.count, self.limit = count, limit

    def as_sql(self, compiler, connection):
        sql, params = compiler.compile(self.source_expressions[0])
        source = f"jsonb_array_elements(CASE WHEN jsonb_typeof({sql})='array' THEN {sql} ELSE '[]'::jsonb END)"
        name = "CASE WHEN jsonb_typeof(item)='string' THEN item#>>'{}' WHEN jsonb_typeof(item)='object' AND jsonb_typeof(item->'name')='string' THEN item->>'name' ELSE '' END"
        names = f"SELECT DISTINCT {name} AS name FROM {source} AS entries(item)"
        if self.count:
            query = f"SELECT count(*)::integer FROM ({names}) AS names WHERE name<>''"
        else:
            query = f"SELECT COALESCE(jsonb_agg(left(name,200) ORDER BY name),'[]'::jsonb) FROM (SELECT name FROM ({names}) AS names WHERE name<>'' ORDER BY name LIMIT {self.limit}) AS bounded"
        return f'({query})', params * 2


class NonemptyArray(Func):
    output_field = BooleanField()
    def as_sql(self, compiler, connection):
        sql, params = compiler.compile(self.source_expressions[0])
        return f"(CASE WHEN jsonb_typeof({sql})='array' THEN jsonb_array_length({sql})>0 ELSE FALSE END)", params * 2


class ValidationSummary(Func):
    output_field = JSONField()
    def as_sql(self, compiler, connection):
        sql, params = compiler.compile(self.source_expressions[0])
        # Key whitelist also bounds payload if a manually imported runtime
        # contains private messages/paths beyond the producer's compact shape.
        return f"(SELECT COALESCE(jsonb_object_agg(key,value),'{{}}'::jsonb) FROM jsonb_each(CASE WHEN jsonb_typeof({sql}->'validation'->'summary')='object' THEN {sql}->'validation'->'summary' ELSE '{{}}'::jsonb END) WHERE key IN ('pass','fail','warn','info','suppressed','total') AND jsonb_typeof(value)='number' AND length(value::text)<=20 AND value::text ~ '^[0-9]+$')", params * 2


class ValidValidation(Func):
    output_field = BooleanField()
    def as_sql(self, compiler, connection):
        sql, params = compiler.compile(self.source_expressions[0])
        # No producer summary means no knowledge, even on an otherwise ready
        # artifact. Reject malformed or negative values rather than coercing.
        return f"(CASE WHEN jsonb_typeof({sql}->'validation'->'summary')='object' THEN EXISTS (SELECT 1 FROM jsonb_each({sql}->'validation'->'summary') WHERE key IN ('pass','fail','warn','info','suppressed','total')) AND NOT EXISTS (SELECT 1 FROM jsonb_each({sql}->'validation'->'summary') WHERE key IN ('pass','fail','warn','info','suppressed','total') AND (jsonb_typeof(value)<>'number' OR length(value::text)>20 OR value::text !~ '^[0-9]+$')) ELSE FALSE END)", params * 3


class ValidationFlag(Func):
    output_field = BooleanField()
    def __init__(self, expression, key):
        super().__init__(expression)
        if key not in ('fail', 'warn'):
            raise ValueError('Invalid validation key')
        self.key = key
    def as_sql(self, compiler, connection):
        sql, params = compiler.compile(self.source_expressions[0])
        value = f"{sql}->'validation'->'summary'->'{self.key}'"
        return f"(CASE WHEN jsonb_typeof({value})='number' AND length(({value})::text)<=20 AND ({value})::text ~ '^[0-9]+$' THEN (({value})::text)::numeric>0 ELSE FALSE END)", params * 4


def _observations(relation, now):
    latest = Run.objects.filter(studio_id=OuterRef('studio_id'), slug=OuterRef('slug')).order_by('-created_at', '-pk')
    fresh = Q(source_summary__schema_version=2, source_summary__observed_at__gte=now-timedelta(seconds=FRESH_SECONDS), source_summary__observed_at__lte=now)
    compatible = Q(source_summary__schema_version=2, source_summary__observed_at__lte=now, source_summary__quality__in=('observed', 'verified'), source_summary__state__in=('ready','missing','uncertain'))
    # Runtime validation is output evidence, not proof from a recent failed
    # attempt. Stale/uncertain serving observations must retain unknown state.
    runtime_known = fresh & Q(source_summary__quality__in=('observed', 'verified'), source_summary__runtime_state='ready', source_summary__state__in=('ready', 'missing'))
    attempt_known = Q(source_summary__schema_version=2, source_summary__attempt_at__lte=now) & ~Q(source_summary__attempt_status='')
    relation = relation.annotate(
        display_name=Coalesce(Func(F('name'), Value(''), function='NULLIF'), F('slug')),
        display_category=Coalesce(Func(F('category'), Value(''), function='NULLIF'), Value('Uncategorized')),
        metrics_report=Case(When(slug='metrics', tags__contains=['metrics'], then=Value(True)), default=Value(False), output_field=BooleanField()),
        latest_status=Subquery(latest.values('status')[:1]),
        latest_at=Subquery(latest.values('created_at')[:1]),
        latest_error=Subquery(latest.annotate(short_error=Substr('stderr_tail', 1, 500)).values('short_error')[:1]),
        observation_known=Case(When(fresh & Q(source_summary__quality__in=('observed', 'verified'), source_summary__state__in=('ready', 'missing')), then=Value(True)), default=Value(False), output_field=BooleanField()),
        waiting_known=Case(When(compatible & attempt_known, then=Value(True)), default=Value(False), output_field=BooleanField()),
        waiting_stale=Case(When(compatible & ~fresh & attempt_known, then=Value(True)), default=Value(False), output_field=BooleanField()),
        runtime_known=Case(When(runtime_known, then=Value(True)), default=Value(False), output_field=BooleanField()),
        validation_valid=ValidValidation(F('source_summary__runtime')),
        has_waiting=NonemptyArray(F('source_summary__attempt_blocked_by')),
        validation_fail=ValidationFlag(F('source_summary__runtime'), 'fail'),
        validation_warn=ValidationFlag(F('source_summary__runtime'), 'warn'),
        attempt=Case(When(latest_status__isnull=False, then=F('latest_status')), When(attempt_known, then=F('source_summary__attempt_status')), default=Value('unknown'), output_field=CharField()),
    ).annotate(
        attempt_time=Coalesce(F('latest_at'), F('source_summary__attempt_at')),
        validation_known=Case(When(runtime_known=True, validation_valid=True, then=Value(True)), default=Value(False), output_field=BooleanField()),
        waiting_compatible=Case(When(waiting_known=True, latest_at__isnull=True, then=Value(True)), When(waiting_known=True, latest_at__lte=F('source_summary__attempt_at'), then=Value(True)), default=Value(False), output_field=BooleanField()),
    ).annotate(
        outcome=Case(
            When(attempt__in=(Run.STARTING, Run.RUNNING), then=Value('running')),
            When(attempt=Run.QUEUED, then=Value('queued')),
            When(waiting_compatible=True, has_waiting=True, attempt=Run.ERROR, source_summary__attempt_status=Run.ERROR, then=Value('waiting')),
            When(attempt=Run.SUCCESS, then=Value('ok')),
            When(attempt=Run.ERROR, then=Value('error')),
            When(attempt=Run.OOM_KILLED, then=Value('oom')),
            When(attempt=Run.TIMEOUT, then=Value('timeout')),
            When(attempt=Run.STOPPED, then=Value('stopped')),
            When(attempt='not_run', then=Value('not_run')),
            default=Value('unknown'), output_field=CharField()),
        is_favorite=Exists(ReportFavorite.objects.filter(user_id=OuterRef('favorite_actor'), report_id=OuterRef('pk'))),
    )
    return relation


def _filtered(relation, state):
    if state.q:
        relation = relation.filter(Q(name__icontains=state.q) | Q(slug__icontains=state.q) | Q(description__icontains=state.q))
    if state.category:
        relation = relation.filter(display_category=state.category)
    if state.folder == 'favorites':
        relation = relation.filter(is_favorite=True)
    if state.folder == 'recent':
        relation = relation.filter(slug__in=state.recent)
    return relation


def _state_filter(relation, state):
    if state == 'error':
        return relation.filter(outcome__in=('error', 'oom', 'timeout'))
    if state == 'v_unknown':
        return relation.filter(validation_known=False)
    if state == 'v_fail':
        return relation.filter(validation_known=True, validation_fail=True)
    if state == 'v_warn':
        return relation.filter(validation_known=True, validation_fail=False, validation_warn=True)
    if state == 'healthy':
        return relation.filter(validation_known=True, validation_fail=False, validation_warn=False)
    return relation if state == 'all' else relation.filter(outcome=state)


def _counts(relation):
    counts = relation.aggregate(
        total=Count('pk'), **{key: Count('pk', filter=Q(outcome=key)) for key in STATES if key not in ('all', 'v_fail', 'v_warn', 'healthy', 'v_unknown')},
        validation_known_count=Count('pk', filter=Q(validation_known=True)),
        v_fail=Count('pk', filter=Q(validation_known=True, validation_fail=True)),
        v_warn=Count('pk', filter=Q(validation_known=True, validation_fail=False, validation_warn=True)),
        healthy=Count('pk', filter=Q(validation_known=True, validation_fail=False, validation_warn=False)),
        waiting_known_count=Count('pk', filter=Q(waiting_compatible=True)),
        waiting_stale_count=Count('pk', filter=Q(waiting_compatible=True, waiting_stale=True)),
    )
    counts['v_unknown'] = counts['total']-counts['validation_known_count']
    coverage = dict(waiting=dict(known=counts.pop('waiting_known_count'), stale=counts.pop('waiting_stale_count')),
                    validation=dict(known=counts.pop('validation_known_count'), unknown=counts['v_unknown']))
    coverage['waiting']['unknown'] = counts['total']-coverage['waiting']['known']
    counts['outcomes'] = {key: counts[key] for key in ('running','queued','waiting','error','oom','timeout','stopped','ok','not_run','unknown')}
    # The existing Failed pill includes timeout/OOM while their own pills
    # remain available. Exclusive outcome buckets are separate for totals.
    counts['error'] += counts['oom'] + counts['timeout']
    counts['waiting_observed'] = counts['waiting']
    return counts, coverage


def _relation(request, now):
    return _observations(operations_relation(request).annotate(favorite_actor=Value(request.user.pk, output_field=IntegerField())), now)


def _source_states(request, names):
    """Only selected identities may cause encrypted bindings to be decoded."""
    names = tuple(names)
    if len(names) > DEPENDENCY_LIMIT:
        raise ValueError('Operations source dependency limit exceeded')
    if not names:
        return {}
    declarations = {row.name: row for row in RepoDataSource.objects.filter(studio=request.studio, present=True, name__in=names)}
    # DISTINCT ON resolves shadowing in SQL before credentials are decoded;
    # never decrypt the shadowed organization binding too.
    bindings = {row.name: row for row in DataSource.objects.filter(
        Q(org=request.org) | Q(studio=request.studio), name__in=names
    ).order_by('name', 'studio_id').distinct('name')}
    return {name: _state(name, declarations.get(name), bindings.get(name), []) for name in names}


def _safe_entry(value):
    if not isinstance(value, str) or not value or len(value) > 255 or value.startswith('/') or '\\' in value or ':' in value or re.search(r'[\x00-\x20\x7f?#]', value):
        return None
    return value if all(part not in ('', '.', '..') for part in value.split('/')) else None


def _next_run(cron, zone, now):
    if not cron:
        return None
    from apscheduler.triggers.cron import CronTrigger
    try:
        return CronTrigger.from_crontab(cron, timezone=ZoneInfo(zone or 'UTC')).get_next_fire_time(None, now)
    except (ValueError, ZoneInfoNotFoundError):
        return None


CATEGORY_LIMIT = 100


def _folders(request):
    """Studio-wide folder tabs, independent of the active filters (3 queries)."""
    base = operations_relation(request).order_by()
    category = Coalesce(Func(F('category'), Value(''), function='NULLIF'), Value('Uncategorized'))
    categories = list(base.annotate(display=category).values('display').annotate(n=Count('pk')).order_by('display')[:CATEGORY_LIMIT + 1])
    favorites = ReportFavorite.objects.filter(user_id=request.user.pk, report__in=base).count()
    return dict(
        all=base.count(), favorites=favorites,
        categories=[dict(name=row['display'][:200], count=row['n']) for row in categories[:CATEGORY_LIMIT]],
        categories_truncated=len(categories) > CATEGORY_LIMIT,
    )


def operations_page_payload(request, params, *, now=None):
    state = validate_operations_params(params)
    now = now or timezone.now()
    relation = _filtered(_relation(request, now), state)
    counts, coverage = _counts(relation)
    selected = _state_filter(relation, state.state)
    total = selected.count()
    sort = {'name': 'display_name', 'category': 'display_category', 'priority': 'priority', 'status': 'outcome', 'last_run': 'attempt_time'}[state.sort]
    order = F(sort).asc(nulls_last=True) if state.ascending else F(sort).desc(nulls_last=True)
    rows = list(selected.annotate(
        description_preview=Substr('description', 1, 500), description_length=Length('description'),
        references=ReferenceNames(F('config__data_sources')),
        reference_dependencies=ReferenceNames(F('config__data_sources'), limit=DEPENDENCY_LIMIT+1),
        referenced_count=ReferenceNames(F('config__data_sources'), count=True),
        blocked_preview=ReferenceNames(F('source_summary__attempt_blocked_by'), limit=DEPENDENCY_LIMIT+1),
        blocked_count=ReferenceNames(F('source_summary__attempt_blocked_by'), count=True),
        validation_preview=ValidationSummary(F('source_summary__runtime')),
        framework_preview=Substr(KeyTextTransform('framework_version', 'source_summary__runtime'), 1, 100),
    ).order_by(order, 'slug', 'pk').values(
        'id', 'slug', 'kind', 'metrics_report', 'display_name', 'display_category', 'description_preview', 'description_length',
        'schedule_cron', 'schedule_timezone', 'disabled', 'data_expired_at', 'outcome', 'attempt', 'latest_at', 'latest_error',
        'observation_known', 'validation_known', 'waiting_known', 'waiting_stale', 'references', 'reference_dependencies', 'referenced_count',
        'blocked_preview', 'blocked_count', 'waiting_compatible', 'validation_preview', 'framework_preview', 'source_summary__attempt_at', 'source_summary__attempt_error',
        'source_summary__observed_at', 'source_summary__served_at', 'source_summary__quality', 'source_summary__state',
        'source_summary__has_output', 'source_summary__html_entry', 'source_summary__has_live_queries',
    )[(state.page-1)*PAGE_SIZE:state.page*PAGE_SIZE])
    names = sorted({name for row in rows for name in row['reference_dependencies']})[:DEPENDENCY_LIMIT]
    sources = _source_states(request, names)
    live_enabled = live_query_rate_limit(request.org) > 0
    presentation = []
    for row in rows:
        refs = row['reference_dependencies']
        complete = row['referenced_count'] <= len(refs) and all(name in sources and sources[name].state != SourceState.UNKNOWN for name in refs)
        complete = complete and row['blocked_count'] <= len(row['blocked_preview'])
        blockers = [name for name in row['blocked_preview'] if name in refs and name in sources and sources[name].blocking]
        if not row['waiting_compatible']:
            complete = False
            blockers = []
        # Blocked preview alone cannot establish exact absence if it truncated.
        waiting_current = False if row['disabled'] else (bool(blockers) if complete else None)
        known = row['observation_known']
        failing = next((sources[name] for name in refs if name in sources and sources[name].state == SourceState.FAILING), None)
        presentation.append(dict(
            id=row['id'], slug=row['slug'], kind=row['kind'], metrics_report=row['metrics_report'], name=row['display_name'], category=row['display_category'],
            description=row['description_preview'], description_truncated=row['description_length'] > 500,
            schedule=row['schedule_cron'], schedule_timezone=row['schedule_timezone'], next_run=_next_run(row['schedule_cron'], row['schedule_timezone'], now) if not row['disabled'] else None, disabled=row['disabled'],
            state=row['outcome'], build_status=row['attempt'], last_attempt_at=row['latest_at'] or row['source_summary__attempt_at'],
            last_error=(row['latest_error'] or row['source_summary__attempt_error']) if row['attempt'] == Run.ERROR else None,
            waiting_observed=row['outcome'] == 'waiting', waiting_current=waiting_current,
            failing_source=dict(name=failing.name, since=failing.last_check_at) if failing else None,
            waiting_quality='current' if waiting_current is not None else 'unknown', blocked_by=blockers[:REFERENCE_PREVIEW], blocked_by_count=len(blockers),
            references=row['references'], referenced_count=row['referenced_count'],
            has_output=(False if row['data_expired_at'] else row['source_summary__has_output']) if known or row['data_expired_at'] else None,
            html_entry=_safe_entry(row['source_summary__html_entry']) if known and not row['data_expired_at'] else None,
            live=(bool(row['source_summary__has_live_queries']) and live_enabled and row['kind'] == Report.KIND_REPORT) if known and row['source_summary__has_live_queries'] is not None else None,
            validation=dict(summary=row['validation_preview'], quality='observed') if row['validation_known'] else None,
            validation_details='on-demand', framework_version=row['framework_preview'] if known else None,
            observed_at=row['source_summary__observed_at'], served_at=row['source_summary__served_at'],
            quality=row['source_summary__quality'] or 'unknown', observation_known=known,
        ))
    return _bounded(dict(version=1, state='ready', rows=presentation, pagination=_pagination(state.page, total), counts=counts, folders=_folders(request), coverage=coverage,
                         applied=dict(q=state.q, category=state.category, folder=state.folder, state=state.state, sort=state.sort, ascending=state.ascending), observed_at=now))


def operations_status_payload(request, page_slugs, *, now=None):
    slugs = validate_slugs(page_slugs)
    now = now or timezone.now()
    relation = operations_relation(request)
    authorized = tuple(relation.filter(slug__in=slugs).values_list('slug', flat=True)) if slugs else ()
    runs = Run.objects.filter(studio=request.studio, status__in=Run.ACTIVE_STATUSES)
    totals = runs.aggregate(total=Count('pk'), selected=Count('pk', filter=Q(slug__in=authorized)), running=Count('pk', filter=Q(status__in=(Run.STARTING, Run.RUNNING))), queued=Count('pk', filter=Q(status=Run.QUEUED)))
    page = list(runs.filter(slug__in=authorized).order_by('priority', 'created_at', 'pk').values('id', 'slug', 'status', 'created_at', 'started_at', 'priority', 'cache_mode')[:PAGE_SIZE]) if authorized else []
    beat = WorkerHeartbeat.alive().order_by('-last_beat_at').values('max_concurrent').first()
    return _bounded(dict(version=1, state='ready', active_jobs_total=totals, rows=page,
                         coverage=dict(selected_slugs=len(authorized), page_limit=PAGE_SIZE, active_rows_total=totals['selected'], active_rows_complete=totals['selected'] <= PAGE_SIZE),
                         worker_alive=beat is not None, max_concurrent=beat['max_concurrent'] if beat else settings.WORKER_MAX_CONCURRENT, observed_at=now))


def operations_stats_payload(request, page_slugs, *, now=None):
    slugs = validate_slugs(page_slugs)
    relation = operations_relation(request)
    authorized = tuple(relation.filter(slug__in=slugs).values_list('slug', flat=True)) if slugs else ()
    return _bounded(dict(version=1, state='ready', stats=studio_run_stats(request.studio, slugs=authorized), window_days=30, observed_at=now or timezone.now()))


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
        cursor.execute(cte + f' SELECT left(name,200),length(name),md5(name),type FROM sources {condition} ORDER BY lower(name),name LIMIT %s OFFSET %s', (*arguments, *search_args, PAGE_SIZE, (state.source_page-1)*PAGE_SIZE))
        identities = cursor.fetchall()
        names = [name for name, length, digest, type_ in identities if length <= 64]
    # Malformed imported reference identities are visible but cannot trigger
    # credential/name operations or be represented as configured sources.
    valid_names = [name for name in names if re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', name)]
    source_states = _source_states(request, valid_names)
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
        upload = bool(source and source.type in ('sqlite','duckdb','file','image') and effective_fields(source.declaration, source.binding).get('upload'))
        rows.append(dict(
            identity=dict(org_id=request.org.pk, studio_id=request.studio.pk, name=name if source else None, reference_digest=digest if source is None else None), name=name, name_truncated=name_length>200,
            type=source.type[:64] if source else '', state=source.state if source else 'unknown', detail=source.detail[:300] if source else 'Unindexed source reference',
            declared=source.declared if source else False, binding_scope=source.binding_scope if source else None,
            declaration_id=source.declaration.pk if source and source.declaration else None,
            binding_id=source.binding.pk if source and source.binding else None,
            last_check_at=source.last_check_at if source else None, last_check_ok=source.last_check_ok if source else None,
            used_by=preview, used_by_total=count, used_by_complete=count<=REFERENCE_PREVIEW,
            blocks_observed=block_preview, blocks_observed_total=block_count, blocks_current=None,
            upload_allowed=upload and source.binding_scope != 'org',
            download_available=None, quality='configured' if source and source.state != 'unknown' else 'unknown',
        ))
    return _bounded(dict(version=1, state='ready', rows=rows, pagination=_pagination(state.source_page, total),
                         coverage=dict(readiness='page-only', global_current_readiness='unknown'), applied=dict(source_q=state.source_q), observed_at=now))


def operations_source_reports_payload(request, name, params, *, observed_blocks=False, now=None):
    """Complete paged access behind source usage/observed-block previews.

    This selects current authorized references, never searches report names to
    approximate source usage. Observed blockers remain labelled observations.
    """
    if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', name):
        raise ValueError('Invalid Operations source name')
    state = validate_operations_params(params)
    now = now or timezone.now()
    cte, arguments = _source_cte(request, now)
    table = 'observed_blocks' if observed_blocks else 'source_refs'
    # table is a local allowlist; source identity and pagination are parameters.
    with connection.cursor() as cursor:
        cursor.execute(cte + f' SELECT count(DISTINCT report_id) FROM {table} WHERE name=%s', (*arguments, name))
        total = cursor.fetchone()[0]
        cursor.execute(cte + f' SELECT DISTINCT report_id,slug FROM {table} WHERE name=%s ORDER BY slug,report_id LIMIT %s OFFSET %s', (*arguments, name, PAGE_SIZE, (state.page-1)*PAGE_SIZE))
        rows = [dict(id=identity, slug=slug) for identity, slug in cursor.fetchall()]
    return _bounded(dict(version=1, state='ready', rows=rows, source=name,
                         mode='last-observed-blocks' if observed_blocks else 'current-references',
                         pagination=_pagination(state.page, total), observed_at=now))
