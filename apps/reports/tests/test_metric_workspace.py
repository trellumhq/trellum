"""Whole-request DB-only workspace decisions and bounded native/remote choices."""
from datetime import timedelta
import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import roles
from apps.reports.models import MetricDefinition, ReportSourceSummary, ReportMetricClaim
from apps.reports.tests.test_metrics_bounded import catalog, _source, _url

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('version,quality,state,fresh,automatic', [
    (1, 'verified', 'ready', True, True), (1, 'observed', 'ready', True, True),
    (0, 'verified', 'ready', True, False), (None, 'verified', 'ready', True, False),
    (2, 'verified', 'ready', True, False), (1, 'verified', 'unavailable', True, False),
    (1, 'verified', 'ready', False, False), (1, 'historical', 'ready', True, False)])
def test_format_and_source_state_gate_before_any_frame(catalog, monkeypatch, version, quality, state, fresh, automatic):
    studio, client, rows = catalog
    source = _source(studio, 'metrics', workspace_version=version)
    ReportSourceSummary.objects.filter(pk=source.pk).update(quality=quality, state=state,
        observed_at=timezone.now() if fresh else timezone.now() - timedelta(hours=1))
    monkeypatch.setattr('trellum.metrics.load_metrics_result', lambda *args, **kwargs: pytest.fail('GET parsed YAML'))
    with CaptureQueriesContext(connection) as sql:
        response = client.get(_url(studio) + '/metrics/comparison')
    assert response.status_code == 200
    assert ('<iframe' in response.content.decode()) is automatic
    assert len(sql) <= 28
    if version == 0 and state == 'ready' and fresh:
        explicit = client.get(_url(studio) + '/metrics/comparison', {'legacy': '1'})
        assert b'<iframe' in explicit.content and b'Legacy artifact loaded explicitly' in explicit.content


def test_remote_choices_full_search_and_offpage_selected_without_foreign_labels(catalog, other_studio):
    studio, client, rows = catalog
    MetricDefinition.objects.filter(pk__in=[row.pk for row in rows[:25]] + [rows[-1].pk]).update(
        sql='heavy-secret-sql-' + 'x' * 20000, description='heavy-description-' + 'y' * 20000)
    MetricDefinition.objects.create(studio=other_studio, name='foreign_metric', label='Secret foreign label')
    url = _url(studio) + '/metrics/choices'
    with CaptureQueriesContext(connection) as sql:
        response = client.get(url, {'selected': rows[-1].name})
    assert response.status_code == 200 and response['Cache-Control'] == 'private, no-store'
    data = response.json()
    assert len(data['results']) == 25 and data['pagination']['total'] == 2000
    assert data['selected']['value'] == rows[-1].name
    labels = [query['sql'].split(' FROM ')[0] for query in sql if 'FROM "reports_metricdefinition"' in query['sql']]
    assert labels and all('"sql"' not in query and '"description"' not in query for query in labels)
    assert len(sql) <= 26
    searched = client.get(url, {'q': 'metric_1999'}).json()
    assert searched['results'][0]['value'] == rows[-1].name and searched['pagination']['total'] == 1
    foreign = client.get(url, {'selected': 'foreign_metric'}).json()
    assert foreign['selected'] is None and 'Secret' not in str(foreign)
    assert client.post(url, {}).status_code == 405
    for query in ({'q': 'x' * 201}, {'page': '-1'}, {'selected': '../secret'}, {'page': '9' * 5000}):
        assert client.get(url, query).status_code == 400


def test_native_options_bounded_selected_additive_and_binding_facts(catalog, monkeypatch):
    studio, client, rows = catalog
    source = _source(studio, 'metrics', workspace_version=1)
    ReportMetricClaim.objects.create(summary=source, metric_name=rows[-1].name, definition_hash='current')
    rows[-1].binding = {'dataset': 'daily', 'time_column': 'date', 'grain': 'week', 'time_agg': 'last'}
    rows[-1].save(update_fields=['binding'])
    monkeypatch.setattr('trellum.metrics.load_metrics_result', lambda *args, **kwargs: pytest.fail('GET parsed YAML'))
    response = client.get(_url(studio) + '/metrics/compare', {'a': rows[-1].name, 'b': rows[-2].name, 'page': 2,
        'from': '2026-01-01', 'to': '2026-02-01', 'split_a': 'Platform'})
    assert response.status_code == 200
    html = response.content.decode()
    selects = re.findall(r'<select\b[^>]*>(.*?)</select>', html, re.S)
    assert len(selects) == 2 and all(value.count('<option') <= 28 for value in selects)
    assert 'value="metric_1999" selected' in html and 'value="metric_1998" selected' in html
    assert 'Grain: week' in html and 'Time rollup: last' in html
    assert 'name="from" value="2026-01-01"' in html and 'value="Platform"' in html
    assert client.get(_url(studio) + '/metrics/compare', {'a': 'foreign_metric'}).status_code == 400
