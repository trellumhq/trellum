"""DB-only bounded choices and served generated-workspace decisions."""
from datetime import timedelta
import re

from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import HttpResponseBadRequest
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.core import roles
from apps.core.choice_requests import choice_response
from apps.core.permissions import require_studio_role
from apps.core.report_access import require_full_studio_visibility
from .models import MetricDefinition, Report, ReportMetricClaim, ReportSourceSummary
from .metric_summaries import SOURCE_FRESH_SECONDS, claimed_count, usage_pending


def definition_choices(studio, query='', page=1):
    """One bounded page of matching metrics for the search endpoint."""
    rows = MetricDefinition.objects.filter(studio=studio, present_in_scan=True).only('pk', 'name', 'label')
    matched = rows.filter(Q(name__icontains=query) | Q(label__icontains=query)) if query else rows
    current = Paginator(matched.order_by('name', 'pk'), 25).get_page(page)
    return list(current.object_list), current


def inline_definition_choices(studio, selected=()):
    """Every metric for the pickers, or a preview plus search for a huge studio."""
    from apps.core.choice_requests import inline_choices

    rows = MetricDefinition.objects.filter(studio=studio, present_in_scan=True).only('pk', 'name', 'label').order_by('name', 'pk')
    choices, searchable = inline_choices(rows)
    missing = {value for value in selected if value} - {row.name for row in choices}
    if missing:
        choices.extend(rows.filter(name__in=missing))
    return choices, searchable


def workspace_projection(studio, *, legacy=False):
    report = Report.objects.filter(studio=studio, present_in_scan=True, kind=Report.KIND_REPORT,
        slug='metrics', tags__contains=['metrics'], data_expired_at__isnull=True).select_related('source_summary').only(
            'pk', 'slug', 'source_summary__id', 'source_summary__report_id', 'source_summary__state',
            'source_summary__quality', 'source_summary__observed_at', 'source_summary__served_at',
            'source_summary__workspace_version', 'source_summary__source_identity').first()
    try:
        summary = report.source_summary if report else None
    except ReportSourceSummary.DoesNotExist:
        summary = None
    fresh = bool(summary and summary.observed_at and summary.observed_at >= timezone.now() - timedelta(seconds=SOURCE_FRESH_SECONDS))
    # Freshness is reported, not required: the frame loads the live report.
    ready = bool(summary and summary.state == 'ready' and summary.quality in ('observed', 'verified'))
    version = summary.workspace_version if summary else None
    known_legacy = bool(report and version == 0 and summary.state not in ('removed', 'expired', 'expiring'))
    usable = ready and version == 1 or legacy and known_legacy
    report_url = f'/s/{studio.org.slug}/{studio.slug}/r/metrics/' if report else ''
    statistics = MetricDefinition.objects.filter(studio=studio, present_in_scan=True).aggregate(
        defined_count=Count('pk'), synced_at=Max('last_scanned_at'))
    # Unknown, not zero, while reports are still being indexed (see the Overview).
    pending = usage_pending(studio)
    count = None if pending else claimed_count(studio)
    return {**statistics, 'claimed_count': count, 'usage_pending': pending, 'metrics_report_url': report_url,
        'metrics_report_built': usable, 'workspace_version': version,
        'workspace_quality': summary.quality if summary else 'unknown',
        'workspace_state': summary.state if summary else 'unknown',
        'workspace_fresh': fresh, 'workspace_legacy': known_legacy,
        'workspace_requested_legacy': usable and version == 0,
        'workspace_report_exists': bool(report), 'workspace_unknown': not ready or version is None,
        'workspace_unsupported': ready and version not in (None, 0, 1),
        'storage_unavailable': bool(summary and summary.state == 'unavailable'),
        '_workspace_report': report, '_workspace_summary': summary}


@require_studio_role(roles.VIEWER)
@require_full_studio_visibility
@require_GET
def metric_choices(request, org_slug, studio_slug):
    query = request.GET.get('q', '').strip()
    selected = request.GET.get('selected', '')
    raw_page = request.GET.get('page', '1')
    if (len(query) > 200 or len(selected) > 200 or selected and not re.fullmatch(r'[a-z0-9][a-z0-9_]*', selected)
            or len(raw_page) > 10 or not raw_page.isascii() or not raw_page.isdigit() or not 0 < int(raw_page) <= 2**31 - 1):
        return HttpResponseBadRequest('Choose a valid metric search, page and selected metric.')
    _rows, page = definition_choices(request.studio, query, int(raw_page))
    def item(row):
        return {'value': row.name, 'label': row.label or row.name, 'hint': row.name}
    chosen = MetricDefinition.objects.filter(studio=request.studio, present_in_scan=True, name=selected).only('name', 'label').first() if selected else None
    return choice_response(page, item, selected=chosen, pagination={
        'page': page.number, 'pages': page.paginator.num_pages,
        'total': page.paginator.count, 'has_next': page.has_next(), 'has_previous': page.has_previous()})
