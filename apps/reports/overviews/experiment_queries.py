"""Scoped, bounded experiment reads; projection maintenance belongs to workers.

The page shows every experiment on one page, exactly as the unbounded version
did. The bounds are invisible at realistic sizes: the table pages (with the
shared ``.ui-pager``) only beyond ``PAGE_SIZE`` rows, and the overlap timeline
draws at most ``TIMELINE_LANES`` lanes.
"""
from datetime import timezone as utc_timezone
from sys import float_info

from django.core.paginator import Paginator
from django.db.models import BooleanField, Case, CharField, Count, F, OuterRef, Q, Subquery, Value, When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce, NullIf
from django.utils import timezone
from django.utils.functional import cached_property

from apps.reports.models import Report, ReportExperimentSummary

PAGE_SIZE = 200
TIMELINE_LANES = 200
STATUSES = ('running', 'concluded', 'past_end', 'scheduled', 'unknown')
# Which aggregate counts the rows behind each ``?status=`` value.
STATUS_COUNTS = {'running': 'running', 'concluded': 'concluded', 'past_end': 'past_end_running', 'scheduled': 'scheduled', 'unknown': 'unknown'}


def relation(studio, today):
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
    # Results count when they belong to the current declarations of a supported,
    # observed source. Like the latest build the page used to read, they stay
    # visible until a newer observation replaces them. Range comparisons also
    # reject PostgreSQL NaN and infinities. Valid zero and nonsignificant finite
    # deltas still belong in the rate denominator.
    finite_delta = Q(delta_pct__gte=-float_info.max, delta_pct__lte=float_info.max)
    valid_ci = Q(ci_low__gte=-float_info.max, ci_low__lte=float_info.max, ci_high__gte=-float_info.max, ci_high__lte=float_info.max) & Q(ci_low__lte=F('ci_high'))
    # A missing interval is legitimate nonsignificant evidence. A malformed
    # partially present interval is not an eligible numeric result.
    shown = Q(summary__schema_version=2, declaration_digest=F('summary__declaration_digest'), summary__state='ready', summary__quality__in=('observed', 'verified'), summary__experiment_state='ready', payload_state='ready', higher_is_better__isnull=False) & (Q(ci_low__isnull=True, ci_high__isnull=True) | valid_ci)
    # A KPI row without a delta is still shown (as a dash) but cannot be judged.
    eligible = shown & finite_delta
    shown &= Q(delta_pct__isnull=True) | finite_delta
    significant = valid_ci & (Q(ci_low__gt=0) | Q(ci_high__lt=0))
    wins = significant & (Q(higher_is_better=True, delta_pct__gt=0) | Q(higher_is_better=False, delta_pct__lt=0))
    return entries, shown, eligible, significant, wins


class CountedPaginator(Paginator):
    """Reuse the count from the one scoped aggregate instead of a second COUNT."""

    def __init__(self, query, total):
        super().__init__(query, PAGE_SIZE)
        self.total = total

    @cached_property
    def count(self):
        return self.total


ORDER = ('summary__report__priority', 'summary__report__slug', 'ordinal')
FIELDS = ('ordinal', 'declaration', 'start_date', 'end_date', 'closed', 'test_name', 'split', 'control_label', 'test_label', 'payload_state', 'mode_label', 'generated_at', 'framework_version', 'metric', 'control_val', 'test_val', 'delta_pct', 'ci_low', 'ci_high', 'higher_is_better', 'summary__report__slug', 'summary__report__disabled', 'summary__report__schedule_cron', 'summary__state', 'summary__has_output', 'name')
LANE_FIELDS = ('start_date', 'end_date', 'closed', 'summary__report__slug', 'summary__report__disabled', 'summary__report__schedule_cron', 'name')


def overview(studio, *, params=None, now=None):
    # Local import avoids a cycle: the producer imports pure helpers there.
    from .experiments import _build_timeline, _month_floor, _shift_months, derive_status
    now = now or timezone.now()
    today = now.astimezone(utc_timezone.utc).date()
    params = params or {}
    status = params.get('status', '')
    status = status if status in STATUSES else ''
    raw_page = str(params.get('page', '1'))
    number = max(1, int(raw_page)) if len(raw_page) <= 8 and raw_page.isascii() and raw_page.isdecimal() else 1
    all_entries, shown, eligible, significant, wins = relation(studio, today)
    totals = all_entries.aggregate(total=Count('pk'), running=Count('pk', filter=Q(lifecycle__in=('running', 'past_end'))), past_end_running=Count('pk', filter=Q(lifecycle='past_end')), concluded=Count('pk', filter=Q(lifecycle='concluded')), scheduled=Count('pk', filter=Q(lifecycle='scheduled')), unknown=Count('pk', filter=Q(lifecycle='unknown')), concluded_with_metric=Count('pk', filter=Q(lifecycle='concluded') & eligible), wins=Count('pk', filter=Q(lifecycle='concluded') & eligible & wins))
    totals['win_rate'] = round(100 * totals['wins'] / totals['concluded_with_metric']) if totals['concluded_with_metric'] else None
    # One-to-one report coverage has no experiment join multiplication.
    reports = Report.objects.filter(studio=studio, present_in_scan=True)
    known = Q(source_summary__schema_version=2) & ~Q(source_summary__declaration_digest='') & ~Q(source_summary__experiment_state__in=('oversized', 'not-applicable'))
    no_experiment = Q(source_summary__schema_version=2, source_summary__experiment_state='not-applicable') & ~Q(source_summary__declaration_digest='')
    coverage = reports.aggregate(reports=Count('pk'), indexed=Count('pk', filter=known | no_experiment), oversized=Count('pk', filter=Q(source_summary__experiment_state='oversized')), unindexed=Count('pk', filter=~(known | no_experiment | Q(source_summary__experiment_state='oversized'))))
    coverage['uncovered'] = coverage['oversized'] + coverage['unindexed']

    def lifecycle(record):
        cfg = {'start_date': record['start_date'], 'end_date': record['end_date'], 'closed': record['closed']}
        return derive_status(cfg, today, schedule_active=not record['summary__report__disabled'] and bool(record['summary__report__schedule_cron']))

    def present(record):
        slug = record['summary__report__slug']
        declaration = record['declaration']
        row = dict(name=record['name'], slug=slug, test_name=declaration.get('test_name') or record['test_name'] or record['name'], split=declaration.get('split') or record['split'], control_label=declaration.get('control_label') or record['control_label'] or 'Control', test_label=declaration.get('test_label') or record['test_label'] or 'Test', url=f'/s/{studio.org.slug}/{studio.slug}/r/{slug}/?display=console', **lifecycle(record), mode_label='', primary=None, generated_at=None, framework_version=None,
                   built=True, payload_missing=False, pending=False, result_state='No KPI rows in the payload')
        if not record['current_result']:
            # Expired output reads as unbuilt; anything else is simply not
            # observed (yet), which the page shows as a quiet dash.
            if record['summary__state'] == 'expired':
                row['built'] = False
            else:
                row['pending'] = True
            return row
        if record['summary__has_output'] is False and record['summary__state'] == 'missing':
            row['built'] = False
            return row
        row.update(mode_label=record['mode_label'], generated_at=record['generated_at'], framework_version=record['framework_version'])
        state = record['payload_state']
        if state == 'missing':
            row['payload_missing'] = True
        elif state == 'ready' and record['higher_is_better'] is not None:
            if record['shown_result']:
                ci = [record['ci_low'], record['ci_high']] if record['ci_low'] is not None and record['ci_high'] is not None else None
                row['primary'] = dict(metric=record['metric'], delta_pct=record['delta_pct'], ci_pct=ci, higher_is_better=record['higher_is_better'], significant=record['ci_significant'], control_val=record['control_val'], test_val=record['test_val'])
            else:
                row['result_state'] = 'Invalid numeric result'
        elif state == 'invalid':
            row['result_state'] = 'Invalid numeric result'
        elif state != 'ready':
            row['pending'] = True
        return row

    entries = all_entries
    if status:
        entries = entries.filter(lifecycle__in=('running', 'past_end') if status == 'running' else (status,))
    current = Q(summary__schema_version=2, declaration_digest=F('summary__declaration_digest'), summary__state__in=('ready', 'missing'), summary__quality__in=('observed', 'verified'), summary__experiment_state__in=('ready', 'missing'))
    entries = entries.annotate(current_result=Case(When(current, then=Value(True)), default=Value(False), output_field=BooleanField()), shown_result=Case(When(shown, then=Value(True)), default=Value(False), output_field=BooleanField()), ci_significant=Case(When(significant, then=Value(True)), default=Value(False), output_field=BooleanField()))
    table = CountedPaginator(entries.order_by(*ORDER).values(*FIELDS, 'current_result', 'shown_result', 'ci_significant'), totals[STATUS_COUNTS[status]] if status else totals['total']).get_page(number)
    rows = [present(record) for record in table]
    # The timeline is the whole studio's overlap picture, whatever the status
    # filter, capped at a lane count far above a realistic studio.
    window_start = _shift_months(_month_floor(today), -3)
    window_end = _shift_months(_month_floor(today), 1)
    overlaps = (Q(start_date__isnull=False, start_date__lte=today, start_date__lt=window_end)
                & (Q(end_date__isnull=True) | Q(end_date__gte=window_start))
                & (Q(end_date__isnull=True) | Q(end_date__gte=F('start_date'))))
    lanes = list(all_entries.filter(overlaps).order_by(*ORDER).values(*LANE_FIELDS)[:TIMELINE_LANES + 1])
    lane_rows = [dict(name=r['name'], slug=r['summary__report__slug'], **lifecycle(r)) for r in lanes[:TIMELINE_LANES]]
    return dict(rows=rows, row_count=totals['total'], tiles=totals, coverage=coverage, timeline=_build_timeline(lane_rows, today),
                timeline_truncated=len(lanes) > TIMELINE_LANES, status_filter=status, page=table, generated_at=now.isoformat())
