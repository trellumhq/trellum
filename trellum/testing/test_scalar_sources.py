"""Scalar input contracts and secret filtering through a complete build."""
import json
import logging
from pathlib import Path

import pytest

from trellum.report import BaseReport, ReportContext
from trellum.runner.execute import run_report


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr('trellum.project.get_project_root', lambda: str(tmp_path))
    monkeypatch.setattr('trellum.runner.execute.get_project_root', lambda: str(tmp_path))
    from trellum.data import datasource_config, resolvers
    monkeypatch.setattr(datasource_config, 'get_project_root', lambda: str(tmp_path))
    monkeypatch.delenv('SCALAR_TEST_VALUE', raising=False)
    datasource_config.invalidate_datasource_cache()
    monkeypatch.setattr(resolvers, '_dotenv_loaded', False)
    yield tmp_path
    datasource_config.invalidate_datasource_cache()


def context(kind, **extra):
    return ReportContext({'data_sources': [{'name': 'input', 'type': kind,
                         'credentials': {'local': 'SCALAR_TEST'}, **extra}]}, 'test', '/tmp')


@pytest.mark.parametrize('kind,raw,expected', [
    ('string', ' hello \n', 'hello'), ('secret', ' token ', 'token'),
    ('text', '\nhello\nworld\n', 'hello\nworld'),
    ('number', '12.5', 12.5), ('number', '0', 0),
    ('boolean', 'false', False), ('boolean', 'true', True),
    ('json', '{"a": [1, true]}', {'a': [1, True]}),
])
def test_scalar_types(project, monkeypatch, kind, raw, expected):
    monkeypatch.setenv('SCALAR_TEST_VALUE', raw)
    ctx = context(kind)
    value = ctx.read_text('input') if kind in {'string', 'secret', 'text'} else ctx.read_value('input')
    assert value == expected and type(value) is type(expected)


@pytest.mark.parametrize('kind,raw', [('number','true'), ('number','NaN'),
    ('number','"12"'), ('boolean','1'), ('boolean','yes'), ('json','[]'),
    ('json','null'), ('json','BAD_PRIVATE_VALUE')])
def test_invalid_values_never_echo_input(project, monkeypatch, kind, raw):
    monkeypatch.setenv('SCALAR_TEST_VALUE', raw)
    with pytest.raises(ValueError, match='requires a valid') as error:
        context(kind).read_value('input')
    assert raw not in str(error.value) if raw == 'BAD_PRIVATE_VALUE' else True


def test_dotenv_and_membership(project):
    (project / '.env').write_text('SCALAR_TEST_VALUE=from-dotenv\n')
    assert context('string').read_text('input') == 'from-dotenv'
    sources = project / 'data-sources'
    sources.mkdir()
    (sources / 'config.yaml').write_text('sources:\n  hidden:\n    type: string\n')
    with pytest.raises(ValueError, match='not declared'):
        context('string')._resolve_source('hidden')


@pytest.mark.parametrize('extension', ['txt', 'json', 'yaml', 'yml'])
def test_text_upload(project, extension):
    path = project / f'notes.{extension}'
    path.write_text('  {"hello": "world"}\n')
    assert context('file', mode='text', path=path.name).read_text('input') == '{"hello": "world"}'


@pytest.mark.parametrize('raw', [None, '', '  \n'])
def test_empty_is_lazy_and_catchable(project, monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv('SCALAR_TEST_VALUE', raising=False)
    else:
        monkeypatch.setenv('SCALAR_TEST_VALUE', raw)
    ctx = context('string')
    with pytest.raises(ValueError, match='no value configured'):
        ctx.read_text('input')


def build(project, monkeypatch, generate, **source):
    directory = project / 'reports' / 'test'
    directory.mkdir(parents=True)
    (directory / 'report.yaml').write_text(json.dumps({'slug': 'test', 'name': 'Test',
        'data_sources': [{'name': 'input', 'type': 'secret',
                          'credentials': {'local': 'SCALAR_TEST'}, **source}]}))
    class Report(BaseReport):
        pass
    Report.generate = generate
    monkeypatch.setattr('trellum.runner.execute._prepare_report', lambda *a: Report())
    monkeypatch.setattr('trellum.runner.execute._load_events', lambda *a: [])
    return Path(run_report(str(directory), fail_on_validation=False))


def test_build_redacts_print_html_data_and_validator(project, monkeypatch, capsys):
    secret = 'private<&"token'
    monkeypatch.setenv('SCALAR_TEST_VALUE', secret)
    def generate(self, ctx):
        value = ctx.read_text('input')
        print(value[:5], end='', flush=True)
        print(value[5:])
        logging.warning('credential %s', value)
        ctx.add_validation_check('custom-secret', 'warn', value, component=value)
        ctx.set_custom_output('<html>' + value + '</html>', {'value': value})
    out = build(project, monkeypatch, generate)
    logs = capsys.readouterr()
    assert secret not in logs.out + logs.err
    for path in out.iterdir():
        if path.suffix in {'.json', '.html'}:
            assert secret not in path.read_text()
    assert json.loads((out / 'data.json').read_text())['value'] == '[REDACTED]'
    assert '[REDACTED]' in (out / '_validation.json').read_text()


def test_build_error_and_exception_chain_redacted(project, monkeypatch, capsys):
    secret = 'private-error-token'
    monkeypatch.setenv('SCALAR_TEST_VALUE', secret)
    def generate(self, ctx):
        raise RuntimeError(ctx.read_text('input'))
    with pytest.raises(RuntimeError) as error:
        build(project, monkeypatch, generate)
    assert secret not in str(error.value)
    assert secret not in capsys.readouterr().out


def test_caught_empty_still_publishes(project, monkeypatch):
    monkeypatch.setenv('SCALAR_TEST_VALUE', '')
    def generate(self, ctx):
        try:
            ctx.read_text('input')
        except ValueError:
            ctx.set_custom_output('<html>optional input missing</html>', {'ok': True})
    out = build(project, monkeypatch, generate)
    assert json.loads((out / 'data.json').read_text()) == {'ok': True}


def test_host_secret_filtered_without_reading_source(project, monkeypatch, capsys):
    monkeypatch.setenv('TRELLUM_SCALAR_SECRETS', json.dumps(['host-secret']))
    def generate(self, ctx):
        print('host-secret')
        ctx.set_custom_output('<html>host-secret</html>', {'token': 'host-secret'})
    out = build(project, monkeypatch, generate)
    assert 'host-secret' not in capsys.readouterr().out
    assert json.loads((out / 'data.json').read_text())['token'] == '[REDACTED]'


def test_normal_render_filters_escaped_html_and_json(project, monkeypatch, capsys):
    import html

    from trellum.components import RawHTML
    secret = 'normal<&"render'
    monkeypatch.setenv('SCALAR_TEST_VALUE', secret)
    def generate(self, ctx):
        value = ctx.read_text('input')
        ctx.add_section(value, [RawHTML(html=html.escape(value),
                        data_key='private', data={'token': value})])
    out = build(project, monkeypatch, generate)
    for path in out.iterdir():
        if path.suffix in {'.html', '.json'}:
            text = path.read_text()
            assert secret not in text and html.escape(secret) not in text
    assert '[REDACTED]' in (out / 'index.html').read_text()
    assert '[REDACTED]' in (out / 'data.json').read_text()


def test_multiline_and_split_log_writes(project, capsys, monkeypatch):
    import sys

    from trellum.redaction import build_redaction
    monkeypatch.setenv('TRELLUM_SCALAR_SECRETS', json.dumps(['first\nsecond']))
    with build_redaction({}):
        for piece in ['fi', 'rst\n', 'sec', 'ond', '\n']:
            sys.stdout.write(piece)
            sys.stdout.flush()
    assert capsys.readouterr().out == '[REDACTED]\n'


def test_numeric_secret_keeps_valid_json(project, monkeypatch):
    monkeypatch.setenv('SCALAR_TEST_VALUE', '12')
    def generate(self, ctx):
        ctx.set_custom_output('<html>ok</html>', {'secret': ctx.read_value('input'),
                                                'larger': 123, 'text': '12'})
    out = build(project, monkeypatch, generate, type='number', secret=True)
    assert json.loads((out / 'data.json').read_text()) == {
        'secret': '[REDACTED]', 'larger': 123, 'text': '[REDACTED]'}


def test_error_cause_notes_and_logger_traceback_are_filtered(project, monkeypatch, capsys, caplog):
    import traceback
    secret = 'private-chain-token'
    monkeypatch.setenv('SCALAR_TEST_VALUE', secret)
    def generate(self, ctx):
        try:
            error = ValueError(ctx.read_text('input'))
            error.add_note(ctx.read_text('input'))
            raise error
        except ValueError:
            logging.exception('source failed')
            raise RuntimeError('outer safe error') from error
    with pytest.raises(RuntimeError) as error:
        build(project, monkeypatch, generate)
    assert secret not in ''.join(traceback.format_exception(error.value))
    assert secret not in caplog.text
    assert secret not in capsys.readouterr().err


def test_secret_json_fields_are_filtered_when_rendered_separately(project, monkeypatch):
    monkeypatch.setenv('SCALAR_TEST_VALUE', '{"credential": "private-json-token"}')
    def generate(self, ctx):
        value = ctx.read_value('input')
        ctx.set_custom_output('<html>' + value['credential'] + '</html>', value)
    out = build(project, monkeypatch, generate, type='json', secret=True)
    assert 'private-json-token' not in (out / 'index.html').read_text()
    assert 'private-json-token' not in (out / 'data.json').read_text()


def test_inline_scalar_uses_materialized_prefix(project, monkeypatch):
    sources = project / 'data-sources'
    sources.mkdir()
    (sources / 'config.yaml').write_text(json.dumps({'sources': {'input': {
        'type': 'secret', 'credentials': {'local': 'PORTAL_SCALAR'}}}}))
    monkeypatch.setenv('PORTAL_SCALAR_VALUE', 'synthetic-portal-inline')
    assert context('secret').read_text('input') == 'synthetic-portal-inline'


def test_report_thread_logs_are_filtered(project, monkeypatch, capsys):
    from threading import Thread

    from trellum.redaction import build_redaction

    monkeypatch.setenv('SCALAR_TEST_VALUE', 'synthetic-thread-key')
    with build_redaction(context('secret')._config):
        worker = Thread(target=lambda: print('synthetic-thread-key'))
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert 'synthetic-thread-key' not in capsys.readouterr().out


def test_joined_worker_log_suffixes_are_not_lost(project, monkeypatch, capsys):
    import sys
    from threading import Thread

    from trellum.redaction import build_redaction

    monkeypatch.setenv('SCALAR_TEST_VALUE', 'synthetic-thread-key')
    with build_redaction(context('secret')._config):
        worker = Thread(target=lambda: (sys.stdout.write('ordinary syn'), sys.stderr.write('warning syn')))
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
    captured = capsys.readouterr()
    assert captured.out == 'ordinary syn'
    assert captured.err == 'warning syn'


def test_report_supplied_validation_identifier_is_filtered(project, monkeypatch):
    secret = 'synthetic-check-identifier'
    monkeypatch.setenv('SCALAR_TEST_VALUE', secret)
    def generate(self, ctx):
        ctx.add_validation_check(ctx.read_text('input'), 'warn', 'Missing optional input')
        ctx.set_custom_output('<html>Available</html>', {})
    out = build(project, monkeypatch, generate)
    for filename in ('_validation.json', '_meta.json'):
        assert secret not in (out / filename).read_text()
    checks = json.loads((out / '_validation.json').read_text())['checks']
    assert any(check['id'] == '[REDACTED]' for check in checks)


def test_oserror_filename_does_not_escape_redaction(project, monkeypatch):
    from trellum.redaction import build_redaction

    monkeypatch.setenv('SCALAR_TEST_VALUE', 'synthetic-error-filename')
    with pytest.raises(OSError) as error:
        with build_redaction(context('secret')._config):
            raise OSError(2, 'unavailable', 'synthetic-error-filename')
    assert 'synthetic-error-filename' not in str(error.value)


@pytest.mark.parametrize('secret', ['workspace-private<&"token', '9137'])
def test_metrics_workspace_build_redacts_every_published_artifact(project, monkeypatch, secret):
    import html

    import pandas as pd

    from trellum.components import RawHTML
    from trellum.metrics import invalidate_metrics_cache
    from trellum.metrics_report import MetricsReport

    monkeypatch.setenv('SCALAR_TEST_VALUE', secret)
    metrics = [{'name': f'metric_{index:02}', 'label': f'Metric {index:02} {secret}',
                'tags': [secret], 'agg': 'sum', 'column': 'value', 'dataset': 'daily',
                'description': f'Controlled metric description containing {secret}.'}
               for index in range(26)]
    metrics.append({'name': 'unbound', 'label': secret, 'description': secret})
    (project / 'metrics.yaml').write_text(json.dumps({'version': 1, 'datasets': {
        'daily': {'provider': 'fixture:daily', 'dimensions': ['segment'],
                  'time': {'column': 'date', 'grain': 'day'}}}, 'metrics': metrics}))
    invalidate_metrics_cache()

    def generate(self, ctx):
        value = ctx.read_text('input')
        ctx.metrics = lambda *args, **kwargs: pd.DataFrame({
            'date': ['2026-01-01', '2026-01-02'], 'segment': [value, 'public'],
            'value': [9137, 91378]})
        MetricsReport().generate(ctx)
        ctx.set_header(subtitle=value, meta={'source': value})
        ctx.add_section('Common content', [RawHTML(html=html.escape(value),
                        data_key='private_note', data={'token': value, 'public': 'kept'})])

    try:
        out = build(project, monkeypatch, generate)
    finally:
        invalidate_metrics_cache()

    variants = (secret, html.escape(secret), json.dumps(secret)[1:-1])
    documents = {}
    for path in out.rglob('*'):
        if not path.is_file():
            continue
        if path.suffix == '.json':
            documents[path.name] = json.loads(path.read_bytes())
            # Numeric secrets must be redacted as JSON values: filtering raw
            # serialized bytes would corrupt a larger number like 91378.
            if secret.isdigit():
                def check(value):
                    if isinstance(value, dict):
                        for key, child in value.items():
                            check(key)
                            check(child)
                    elif isinstance(value, list):
                        for child in value:
                            check(child)
                    elif isinstance(value, str):
                        assert secret not in value
                    else:
                        assert value != int(secret)
                check(documents[path.name])
                continue
        assert all(variant.encode() not in path.read_bytes() for variant in variants)

    marker = documents['_meta.json']['metrics_workspace']
    assert marker['pages'] == 2
    prefix = f'_metrics-workspace-{marker["build"]}'
    assert (out / 'index.html').read_bytes() == (out / f'{prefix}-page-1.html').read_bytes()
    catalog = documents[f'{prefix}-catalog.json']['metrics']
    assert len(catalog) == 27
    assert catalog[0]['category'] == '[REDACTED]'
    assert catalog[-1]['label'] == '[REDACTED]'
    for page in (1, 2):
        payload = documents[f'{prefix}-page-{page}.json']
        assert payload['private_note'] == {'token': '[REDACTED]', 'public': 'kept'}
        assert '[REDACTED]' in (out / f'{prefix}-page-{page}.html').read_text()
        assert 'public' in json.dumps(payload)
        assert 91378 in [cell for row in payload['_ds_daily']['_data'] for cell in row]


@pytest.mark.parametrize('secret', ['1', 'a', 'true'])
def test_common_secret_preserves_generated_html_and_browser_runtime(project, monkeypatch, secret):
    import html

    from playwright.sync_api import sync_playwright

    from trellum.components import KpiRow, RawHTML
    from trellum.rendering.cdn import copy_vendor_tree
    from trellum.testing.test_chart_header_summaries_browser import _route

    monkeypatch.setenv('SCALAR_TEST_VALUE', secret)
    def generate(self, ctx):
        value = ctx.read_text('input')
        ctx.add_section('Results', [KpiRow([{'label': value, 'value': 7}]),
            RawHTML(html='<h1>' + html.escape(value) + '</h1>',
                    data_key='note', data={'token': value})])
    out = build(project, monkeypatch, generate)
    data = json.loads((out / 'data.json').read_bytes())
    assert data['note']['token'] == '[REDACTED]'
    assert 'fw_c2' in data['components']
    assert data['components']['fw_c2']['label'] == '[REDACTED]'
    assert data['components']['fw_c2']['type'] == 'kpi'
    assert '<h1>[REDACTED]</h1>' in (out / 'index.html').read_text()
    copy_vendor_tree(out)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            _route(page, out)
            page.goto('http://scalar.test/index.html', wait_until='networkidle')
            page.wait_for_function('window._reportData && window._reportData.components')
            assert page.locator('.fw-raw h1').inner_text() == '[REDACTED]'
            assert '7' in page.locator('#fw_c2').inner_text()
            assert not errors
        finally:
            browser.close()


@pytest.mark.parametrize('secret', ['1', 'a', '{"token":"true","number":1}'])
def test_common_secret_workspace_routes_and_dictionary_codes_remain_valid(project, monkeypatch, secret):
    import pandas as pd
    from playwright.sync_api import sync_playwright

    from trellum.metrics import invalidate_metrics_cache
    from trellum.metrics_report import MetricsReport
    from trellum.rendering.cdn import copy_vendor_tree
    from trellum.testing.test_chart_header_summaries_browser import _route

    monkeypatch.setenv('SCALAR_TEST_VALUE', secret)
    value = 'true' if secret.startswith('{') else secret
    (project / 'metrics.yaml').write_text(json.dumps({'version': 1, 'datasets': {
        'daily': {'provider': 'fixture:daily', 'time': {'column': 'date', 'grain': 'day'}}},
        'metrics': [{'name': f'sales_{index:02}', 'label': value, 'agg': 'sum',
                     'column': 'value', 'dataset': 'daily'} for index in range(26)]}))
    invalidate_metrics_cache()
    def generate(self, ctx):
        ctx.metrics = lambda *args, **kwargs: pd.DataFrame({
            'date': ['2026-06-06', '2026-06-07'], 'value': [7, 8]})
        MetricsReport().generate(ctx)
    try:
        out = build(project, monkeypatch, generate, type='json' if secret.startswith('{') else 'secret', secret=True)
    finally:
        invalidate_metrics_cache()
    meta = json.loads((out / '_meta.json').read_bytes())
    marker = meta['metrics_workspace']
    assert marker['version'] == 1 and marker['pages'] == 2
    assert meta['schema_version'] == 2 and meta['last_status'] == 'success'
    validation = json.loads((out / '_validation.json').read_bytes())
    assert all(check['level'] in {'pass', 'info', 'warn', 'fail'} for check in validation['checks'])
    assert any(check['id'] == 'yaml-missing-required' for check in validation['checks'])
    assert all(type(count) is int for count in validation['summary'].values())
    catalog = json.loads((out / f'_metrics-workspace-{marker["build"]}-catalog.json').read_bytes())
    assert catalog['version'] == 1
    names = {f'sales_{index:02}' for index in range(26)}
    assert {item['id'] for item in meta['metrics_used']} == names
    assert set(json.loads((out / 'data.json').read_bytes())['_metrics']) == names
    assert {row['name'] for row in catalog['metrics']} == names
    assert all(row['label'] == '[REDACTED]' for row in catalog['metrics'])
    copy_vendor_tree(out)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            _route(page, out)
            page.goto('http://scalar.test/index.html', wait_until='networkidle')
            page.wait_for_function('window._reportData && window._reportData.components')
            assert page.evaluate('window._fwMetricsWorkspace.page') == 1
            assert page.locator('.fw-metric-block').count() == 25
            assert page.locator('.fw-kpi-value').all_text_contents() == ['15'] * 25
            page.get_by_role('link', name='Page 2', exact=True).click()
            page.wait_for_function('window._fwMetricsWorkspace.page === 2 && window._reportData')
            assert page.locator('.fw-metric-block').count() == 1
            assert page.locator('.fw-kpi-value').all_text_contents() == ['15']
            assert not errors
        finally:
            browser.close()


def test_short_secret_filters_real_cells_without_rewriting_dictionary_indexes(project, monkeypatch):
    from trellum.redaction import build_redaction
    from trellum.rendering.redaction import json_bytes

    monkeypatch.setenv('TRELLUM_SCALAR_SECRETS', '["1"]')
    with build_redaction({}):
        encoded = json_bytes({'components': {'fw_c1': {'type': 'kpi', 'value': 1}},
            '_ds_daily': {'_cols': ['segment', 'value'], '_dict': {'segment': ['public', '1']},
                          '_data': [[0, 1], [1, 7]]}}, kind='payload')
    data = json.loads(encoded)
    assert data['components']['fw_c1']['value'] == '[REDACTED]'
    assert data['_ds_daily']['_dict']['segment'] == ['public', '[REDACTED]']
    assert data['_ds_daily']['_data'] == [[0, '[REDACTED]'], [1, 7]]


def test_custom_markup_attributes_and_escaped_content_are_filtered(project, monkeypatch):
    from trellum.redaction import build_redaction
    from trellum.rendering.redaction import html_content

    monkeypatch.setenv('TRELLUM_SCALAR_SECRETS', '["synthetic-token", "private<&token"]')
    with build_redaction({}):
        output = html_content('<h1 class="synthetic-token" title=synthetic-token>'
                              'private&lt;&amp;token</h1><script>window.key="synthetic-token"</script>',
                              trusted_code=False)
    assert output == ('<h1 class="[REDACTED]" title="[REDACTED]">[REDACTED]</h1>'
                      '<script>window.key="[REDACTED]"</script>')


def test_custom_component_markup_and_assets_keep_secret_filtering(project, monkeypatch):
    from trellum.components.base import Component

    secret = 'synthetic-custom-component-secret'
    monkeypatch.setenv('SCALAR_TEST_VALUE', secret)
    class PluginComponent(Component):
        # Simulate a report/plugin implementation, not a bundled component.
        def render_html(self, ctx):
            return '<div id="' + ctx.next_id() + '"><script>window.componentKey="' + secret + '"</script></div>'

        @classmethod
        def client_js(cls):
            return 'window.assetKey="' + secret + '";'

        @classmethod
        def css(cls):
            return '.custom::after{content:"' + secret + '"}'

    # The tests are under trellum.testing, while real report methods have a
    # project module. Mark the implementation's provenance accordingly.
    PluginComponent.render_html.__module__ = 'report_plugin'
    PluginComponent.client_js.__func__.__module__ = 'report_plugin'
    PluginComponent.css.__func__.__module__ = 'report_plugin'
    def generate(self, ctx):
        ctx.add_section('Plugin', [PluginComponent(), '<script>window.rawKey="' + secret + '"</script>'])
    out = build(project, monkeypatch, generate)
    output = (out / 'index.html').read_text()
    assert secret not in output
    assert 'window.componentKey="[REDACTED]"' in output
    assert 'window.assetKey="[REDACTED]"' in output
    assert 'content:"[REDACTED]"' in output
    assert 'window.rawKey="[REDACTED]"' in output
