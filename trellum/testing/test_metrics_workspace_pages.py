"""Bounded generated pages retain complete claims and safe last-good publication."""
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from trellum.metrics import invalidate_metrics_cache
from trellum.metrics_report import MetricsReport
from trellum.metrics_workspace import owned_name
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext


def build(project, count, monkeypatch, unbound=0, unique_columns=False):
    monkeypatch.setattr('trellum.project._project_root_override', str(project))
    metrics = [{'name': f'metric_{i:04}', 'label': f'Metric {i:04}', 'agg': 'sum',
        'column': 'value', 'dataset': 'daily', 'description': 'Controlled representative metric for bounded workspace publication tests.'}
        for i in range(count)]
    metrics.extend({'name': f'unbound_{i:04}', 'label': f'Unbound {i:04}'} for i in range(unbound))
    if unique_columns:
        for index, metric in enumerate(metrics[:count]):
            metric['column'] = f'value_{index:04}'
    (project / 'metrics.yaml').write_text(yaml.safe_dump({'version': 1, 'datasets': {
        'daily': {'provider': 'fixture:daily', 'time': {'column': 'date', 'grain': 'day'}}}, 'metrics': metrics}))
    invalidate_metrics_cache()
    output = project / 'output'
    context = ReportContext({'name': 'Metrics', 'tags': ['metrics']}, 'metrics', str(output))
    data = {'date': ['2026-01-01', '2026-01-02'], 'value': [1, 2]}
    if unique_columns:
        data.update({f'value_{index:04}': [index, index + 1] for index in range(count)})
    context.metrics = lambda *args, **kwargs: pd.DataFrame(data)
    MetricsReport().generate(context)
    render_report(context, auto_refresh=False)
    return output


def test_bounded_pages_keep_complete_root_data_and_claims(tmp_path, monkeypatch):
    out = build(tmp_path, 56, monkeypatch, unbound=70)
    meta = json.loads((out / '_meta.json').read_bytes())
    marker = meta['metrics_workspace']
    assert marker['version'] == 1 and marker['pages'] == 3
    root = json.loads((out / 'data.json').read_bytes())
    assert len(root['_metrics']) == len(meta['metrics_used']) == 56
    assert len(next(value for value in root['components'].values() if value['type'] == 'table')['rows']) == 70
    catalog = json.loads(next(out.glob('*catalog.json')).read_bytes())['metrics']
    assert len(catalog) == 126 and catalog[-1]['name'] == 'unbound_0069'
    for number in range(1, 4):
        name = f'_metrics-workspace-{marker["build"]}-page-{number}'
        html = (out / (name + '.html')).read_text()
        payload = json.loads((out / (name + '.json')).read_bytes())
        assert html.count('fw-section fw-metric-block') <= 25
        assert html.count('<tr id="definition-') <= 25
        assert len(payload['_metrics']) <= 25
        assert len([c for c in payload['components'].values() if c['type'] == 'chart']) <= 25
        assert all(len(c['rows']) <= 25 and c['workspacePageSize'] == 25
                   for c in payload['components'].values() if c['type'] == 'table')
        assert payload['_metrics_workspace']['build'] == marker['build']
    assert 'metric_0055' not in json.loads((out / f'_metrics-workspace-{marker["build"]}-page-1.json').read_bytes())['_metrics']


def test_rebuild_removes_only_previous_owned_pages(tmp_path, monkeypatch):
    out = build(tmp_path, 56, monkeypatch)
    previous = json.loads((out / '_meta.json').read_bytes())['metrics_workspace']['files']
    (out / 'authored.html').write_text('Keep authored view')
    build(tmp_path, 20, monkeypatch)
    assert all(not (out / name).exists() for name in previous)
    assert (out / 'authored.html').read_text() == 'Keep authored view'
    assert json.loads((out / '_meta.json').read_bytes())['metrics_workspace']['pages'] == 1


def test_page_data_excludes_other_metric_series_but_keeps_exact_rows(tmp_path, monkeypatch):
    out = build(tmp_path, 56, monkeypatch, unique_columns=True)
    meta = json.loads((out / '_meta.json').read_bytes())['metrics_workspace']
    root = json.loads((out / 'data.json').read_bytes())['_ds_daily']
    page = json.loads((out / f'_metrics-workspace-{meta["build"]}-page-1.json').read_bytes())['_ds_daily']
    assert 'value_0055' in root['_cols'] and 'value_0055' not in page['_cols']
    assert page['_cols'] == ['date', *[f'value_{index:04}' for index in range(25)]]
    assert len(page['_data']) == len(root['_data']) == 2
    assert page['_data'][1][1:] == list(range(1, 26))


@pytest.mark.parametrize('phase', ['stage', 'publish'])
def test_failed_replacement_retains_last_good_roots_and_deep_pages(tmp_path, monkeypatch, phase):
    import trellum.metrics_workspace as workspace
    out = build(tmp_path, 56, monkeypatch)
    previous = {path.name: path.read_bytes() for path in out.iterdir() if path.is_file()}
    if phase == 'stage':
        monkeypatch.setattr(workspace, 'write_pages', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('stage failure')))
    else:
        original = workspace._replace
        failures = [False]
        def fail_index(path, content):
            if path.name == 'index.html' and not failures[0]:
                failures[0] = True
                raise RuntimeError('publish failure')
            return original(path, content)
        monkeypatch.setattr(workspace, '_replace', fail_index)
    with pytest.raises(RuntimeError):
        build(tmp_path, 20, monkeypatch)
    assert {path.name: path.read_bytes() for path in out.iterdir() if path.is_file()} == previous


def test_untrusted_manifest_cannot_delete_paths_or_symlink_targets(tmp_path):
    from trellum.metrics_workspace import collect_old_pages, gc_old_pages
    out = tmp_path / 'output'
    out.mkdir()
    safe = '_metrics-workspace-' + 'a' * 32 + '-page-99.html'
    target = tmp_path / 'outside'
    target.write_text('untouched')
    (out / safe).symlink_to(target)
    (out / '_meta.json').write_text(json.dumps({'metrics_workspace': {'files': [safe, '../outside', '/outside', 'authored.html']}}))
    assert collect_old_pages(out) == {safe}
    gc_old_pages(out, collect_old_pages(out), set())
    assert target.read_text() == 'untouched' and (out / safe).is_symlink()
    assert not owned_name('../' + safe)


def test_early_routing_precedes_bootstrap_and_encodes_untrusted_anchors(tmp_path, monkeypatch):
    from trellum.metrics_workspace import early_route
    out = build(tmp_path, 56, monkeypatch)
    html = (out / 'index.html').read_text()
    assert html.index('data-metrics-early-route') < html.index('window._fwMetricsWorkspace=')
    assert html.index('data-metrics-early-route') < html.index('<script src=')
    marker = json.loads((out / '_meta.json').read_bytes())['metrics_workspace']
    script = early_route([{'anchor': '</script><script>bad()\u2028\u2029', 'page': 3}], marker['build'], 1, 3)
    assert '</script><script>bad()' not in script
    assert '\\u003c/script>' in script and '\\u2028' in script and '\\u2029' in script
    assert 'window.stop();location.replace(url.href)' in script
    for bad in (True, 0, -1, 4, '3'):
        with pytest.raises(ValueError):
            early_route([{'anchor': 'metric-safe', 'page': bad}], marker['build'], 1, 3)


def test_staged_render_only_writes_inside_its_own_output_dir(tmp_path):
    """The build sandbox mounts only output/<slug> writable, never its parent."""
    import os

    from trellum.metrics_workspace import staged_render

    parent = tmp_path / 'output'
    out = parent / 'metrics'
    out.mkdir(parents=True)
    (out / '.metrics-stage-stale').mkdir()
    parent.chmod(0o555)
    try:
        if os.access(parent, os.W_OK):
            import pytest
            pytest.skip('cannot make the parent read-only (running as root)')

        def render(ctx, output_dir):
            (Path(output_dir) / 'index.html').write_text('<html></html>')
            (Path(output_dir) / '_meta.json').write_text('{}')

        staged_render(type('Ctx', (), {})(), out, render)
    finally:
        parent.chmod(0o755)
    assert (out / 'index.html').exists()
    assert sorted(p.name for p in out.iterdir() if p.name.startswith('.metrics-stage-')) == []
    assert sorted(p.name for p in parent.iterdir()) == ['metrics']
