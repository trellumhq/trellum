"""Scoped, bounded experiment reads; projection maintenance belongs to workers."""
from datetime import timedelta, timezone as utc_timezone
from sys import float_info
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Case, CharField, Count, F, OuterRef, Q, Subquery, Value, When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce, NullIf
from django.utils import timezone

from apps.reports.models import Report, ReportExperimentSummary

PAGE_SIZE = 25
STATUSES = ('running', 'concluded', 'past_end', 'scheduled', 'unknown')


def relation(studio, today, now):
    """Typed lifecycle and result eligibility without reading report config/runtime."""
    count = ReportExperimentSummary.objects.filter(summary_id=OuterRef('summary_id')).order_by().values('summary_id').annotate(n=Count('pk')).values('n')
    report_name = Coalesce(NullIf(F('summary__report__name'), Value('')), F('summary__report__slug'))
    entries = ReportExperimentSummary.objects.filter(summary__report__studio=studio, summary__report__present_in_scan=True)
    entries = entries.annotate(declaration_count=Subquery(count), declaration_name=KeyTextTransform('name', 'declaration'), declared_test_name=KeyTextTransform('test_name', 'declaration'))
    entries = entries.annotate(name=Case(When(declaration_count__gt=1, then=Coalesce(NullIf(F('declaration_name'), Value('')), NullIf(F('declared_test_name'), Value('')), report_name, output_field=CharField())), default=report_name, output_field=CharField()), lifecycle=Case(
        When(start_date__isnull=True, then=Value('unknown')),
        When(start_date__gt=today, then=Value('scheduled')),
        When(Q(end_date__lt=today) & Q(closed=False) & Q(summary__report__disabled=False) & ~Q(summary__report__schedule_cron=''), then=Value('past_end')),
        When(end_date__lt=today, then=Value('concluded')),
        default=Value('running'), output_field=CharField()))
    # Range comparisons also reject PostgreSQL NaN and infinities. Valid zero
    # and nonsignificant finite deltas still belong in the rate denominator.
    eligible = Q(summary__schema_version=2, declaration_digest=F('summary__declaration_digest'), summary__state='ready', summary__quality__in=('observed', 'verified'), summary__experiment_state='ready', summary__observed_at__gte=now-timedelta(seconds=300), summary__observed_at__lte=now, payload_state='ready', delta_pct__gte=-float_info.max, delta_pct__lte=float_info.max, higher_is_better__isnull=False)
    valid_ci = Q(ci_low__gte=-float_info.max, ci_low__lte=float_info.max, ci_high__gte=-float_info.max, ci_high__lte=float_info.max) & Q(ci_low__lte=F('ci_high'))
    # A missing interval is legitimate nonsignificant evidence. A malformed
    # partially present interval is not an eligible numeric result.
    eligible &= (Q(ci_low__isnull=True, ci_high__isnull=True) | valid_ci)
    significant = valid_ci & (Q(ci_low__gt=0) | Q(ci_high__lt=0))
    wins = significant & (Q(higher_is_better=True, delta_pct__gt=0) | Q(higher_is_better=False, delta_pct__lt=0))
    return entries, eligible, significant, wins


def url(params, **changes):
    values = {k: v for k, v in params.items() if v not in ('', None)}
    values.update(changes)
    return '?' + urlencode({k: v for k, v in values.items() if v not in ('', None)})


def pagination(query, number, params, key):
    page = Paginator(query, PAGE_SIZE).get_page(number)
    controls = dict(number=page.number, total=page.paginator.count, pages=page.paginator.num_pages)
    if page.has_previous():
        controls['previous'] = url(params, **{key: page.previous_page_number()})
    if page.has_next():
        controls['next'] = url(params, **{key: page.next_page_number()})
    return page, controls


FIELDS = ('ordinal', 'declaration', 'declaration_digest', 'start_date', 'end_date', 'closed', 'test_name', 'split', 'control_label', 'test_label', 'payload_state', 'mode_label', 'generated_at', 'framework_version', 'metric', 'control_val', 'test_val', 'delta_pct', 'ci_low', 'ci_high', 'higher_is_better', 'summary__report_id', 'summary__report__slug', 'summary__report__disabled', 'summary__report__schedule_cron', 'summary__schema_version', 'summary__declaration_digest', 'summary__state', 'summary__quality', 'summary__observed_at', 'summary__experiment_state', 'summary__has_output', 'name', 'lifecycle')


def overview(studio, *, params=None, now=None):
    # Local import avoids a cycle: the producer imports pure helpers there.
    from .experiments import _build_timeline, _month_floor, _shift_months, derive_status
    now = now or timezone.now()
    today = now.astimezone(utc_timezone.utc).date()
    params = params or {}
    status = params.get('status', '')
    status = status if status in STATUSES else ''
    q = str(params.get('q', '')).strip()[:200]
    def page_number(key):
        raw = str(params.get(key, '1'))
        return max(1, int(raw)) if len(raw) <= 8 and raw.isascii() and raw.isdecimal() else 1
    state = {'status': status, 'q': q, 'page': page_number('page'), 'timeline_page': page_number('timeline_page')}
    entries, eligible, significant, wins = relation(studio, today, now)
    totals = entries.aggregate(total=Count('pk'), running=Count('pk', filter=Q(lifecycle__in=('running', 'past_end'))), past_end_running=Count('pk', filter=Q(lifecycle='past_end')), concluded=Count('pk', filter=Q(lifecycle='concluded')), scheduled=Count('pk', filter=Q(lifecycle='scheduled')), unknown=Count('pk', filter=Q(lifecycle='unknown')), concluded_with_metric=Count('pk', filter=Q(lifecycle='concluded') & eligible), wins=Count('pk', filter=Q(lifecycle='concluded') & eligible & wins))
    totals['excluded'] = totals['concluded'] - totals['concluded_with_metric']
    totals['win_rate'] = round(100 * totals['wins'] / totals['concluded_with_metric']) if totals['concluded_with_metric'] else None
    # One-to-one report coverage has no experiment join multiplication.
    reports = Report.objects.filter(studio=studio, present_in_scan=True)
    known = Q(source_summary__schema_version=2) & ~Q(source_summary__declaration_digest='') & ~Q(source_summary__experiment_state__in=('oversized', 'not-applicable'))
    no_experiment = Q(source_summary__schema_version=2, source_summary__experiment_state='not-applicable') & ~Q(source_summary__declaration_digest='')
    coverage = reports.aggregate(reports=Count('pk'), indexed=Count('pk', filter=known | no_experiment), oversized=Count('pk', filter=Q(source_summary__experiment_state='oversized')), unindexed=Count('pk', filter=~(known | no_experiment | Q(source_summary__experiment_state='oversized'))))
    coverage['uncovered'] = coverage['oversized'] + coverage['unindexed']
    if status:
        entries = entries.filter(lifecycle__in=('running', 'past_end') if status == 'running' else (status,))
    if q:
        entries = entries.filter(Q(name__icontains=q) | Q(test_name__icontains=q) | Q(summary__report__slug__icontains=q))
    entries = entries.order_by('name', 'summary__report_id', 'ordinal')
    def present(record):
        cfg = {'start_date': record['start_date'], 'end_date': record['end_date'], 'closed': record['closed']}
        lifecycle = derive_status(cfg, today, schedule_active=not record['summary__report__disabled'] and bool(record['summary__report__schedule_cron']))
        slug = record['summary__report__slug']
        declaration = record['declaration']
        row = dict(name=record['name'], slug=slug, test_name=declaration.get('test_name') or record['test_name'] or record['name'], split=declaration.get('split') or record['split'], control_label=declaration.get('control_label') or record['control_label'] or 'Control', test_label=declaration.get('test_label') or record['test_label'] or 'Test', url=f'/s/{studio.org.slug}/{studio.slug}/r/{slug}/?display=console', **lifecycle, mode_label='', primary=None, generated_at=None, framework_version=None)
        row['result_state'] = 'Observation unavailable'
        if record['current_result'] and record['summary__has_output'] is False and record['summary__state'] == 'missing':
            row['result_state'] = 'Not built yet'
            return row
        if record['current_result']:
            row.update(mode_label=record['mode_label'], generated_at=record['generated_at'], framework_version=record['framework_version'])
            if record['payload_state'] == 'missing':
                row['result_state'] = 'No A/B payload in the observed build'
            elif record['payload_state'] == 'ready' and record['higher_is_better'] is None:
                row['result_state'] = 'No KPI rows in the payload'
            elif record['numeric_result']:
                row['primary'] = dict(metric=record['metric'], delta_pct=record['delta_pct'], ci_pct=[record['ci_low'], record['ci_high']] if record['ci_low'] is not None else None, higher_is_better=record['higher_is_better'], significant=record['ci_significant'], control_val=record['control_val'], test_val=record['test_val'])
            elif record['payload_state'] == 'invalid':
                row['result_state'] = 'Invalid numeric result'
            else:
                row['result_state'] = 'Numeric result unavailable'
        return row
    current = Q(summary__schema_version=2, declaration_digest=F('summary__declaration_digest'), summary__state__in=('ready', 'missing'), summary__quality__in=('observed', 'verified'), summary__experiment_state__in=('ready', 'missing'), summary__observed_at__gte=now-timedelta(seconds=300), summary__observed_at__lte=now)
    from django.db.models import BooleanField
    entries = entries.annotate(current_result=Case(When(current, then=Value(True)), default=Value(False), output_field=BooleanField()), numeric_result=Case(When(eligible, then=Value(True)), default=Value(False), output_field=BooleanField()), ci_significant=Case(When(significant, then=Value(True)), default=Value(False), output_field=BooleanField()))
    values = entries.values(*FIELDS, 'current_result', 'numeric_result', 'ci_significant')
    table, table_controls = pagination(values, state['page'], state, 'page')
    window_start = _shift_months(_month_floor(today), -3)
    window_end = _shift_months(_month_floor(today), 1)
    overlap = values.filter(start_date__isnull=False, start_date__lte=today, start_date__lt=window_end).filter(Q(end_date__isnull=True) | Q(end_date__gte=window_start)).filter(Q(end_date__isnull=True) | Q(end_date__gte=F('start_date')))
    timeline_page, timeline_controls = pagination(overlap, state['timeline_page'], state, 'timeline_page')
    filters = [dict(label=label, value=value, count=totals[count], url=url({'q': q}, status=value)) for label, value, count in (('All', '', 'total'), ('Running', 'running', 'running'), ('Concluded', 'concluded', 'concluded'), ('Past end', 'past_end', 'past_end_running'), ('Scheduled', 'scheduled', 'scheduled'), ('Unknown', 'unknown', 'unknown'))]
    return dict(rows=[present(r) for r in table], row_count=totals['total'], filtered_count=table_controls['total'], tiles=totals, coverage=coverage, timeline=_build_timeline([present(r) for r in timeline_page], today), table_page=table_controls, timeline_page=timeline_controls, status_filter=status, q=q, filters=filters, generated_at=now.isoformat())
