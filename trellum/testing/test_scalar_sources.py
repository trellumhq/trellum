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
