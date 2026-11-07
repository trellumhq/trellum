"""Dense event labels remain readable without losing their event details."""
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from trellum.assets import STATIC_ROOT, load_css, load_js
from trellum.demo.tools.make_fixtures import build_fact_daily, write_events_yaml


@pytest.fixture
def annotation_page(tmp_path):
    from playwright.sync_api import sync_playwright

    anchor = date(2026, 6, 30)
    dates = pd.date_range(end=anchor, periods=100)
    daily = build_fact_daily(dates, anchor, np.random.default_rng(42))
    values = daily.groupby('event_date', observed=True).dau.sum()
    event_file = tmp_path / 'events.yaml'
    write_events_yaml(str(event_file), anchor)
    events = [e for e in yaml.safe_load(event_file.read_text())['events']
              if e['studio'] == 'nova-play']
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        page.set_content('''<style>body{font:14px sans-serif;color:#222;background:white}
            .fw-chart-container{position:relative;height:320px}li{overflow-wrap:anywhere}</style>
            <main class="fw-container"><h2>Daily active users</h2>
            <div class="fw-chart-container"><canvas id="chart"></canvas></div></main>''')
        for vendor in ['chart.umd.min.js', 'chartjs-plugin-annotation.min.js']:
            page.add_script_tag(content=(STATIC_ROOT / 'vendor' / vendor).read_text())
        page.evaluate('''events => {
            window._reportData = {_events: events}; window._chartInstances = {};
            window._fwFontPx = () => 12; window._lsGet = () => null;
            window._lsSet = () => {};
        }''', events)
        page.add_script_tag(content=load_js('runtime/annotations.js'))
        page.evaluate('''data => {
            window.chart = new Chart(document.getElementById('chart'), {
                type:'line', data:{labels:data.labels, datasets:[{label:'DAU',data:data.values,
                    borderColor:'#477a9b',pointRadius:0}]},
                options:{animation:false,maintainAspectRatio:false,
                    plugins:{legend:{display:false},annotation:{annotations:
                        _buildAnnotations(_reportData._events, data.labels)}}}
            }); window._chartInstances.chart = chart;
        }''', {'labels': list(values.index), 'values': values.tolist()})
        yield page
        browser.close()


def _labels(page):
    return page.evaluate('''() => Chart.registry.getPlugin('annotation').getAnnotations(chart)
        .filter(e => e.label && e.label.options.display).map(e => ({
            x:e.label.x,y:e.label.y,w:e.label.width,h:e.label.height,
            text:e.label.options.content}))''')


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_dense_labels_do_not_overlap(annotation_page, width):
    page = annotation_page
    page.set_viewport_size({'width': width, 'height': 800})
    page.evaluate('chart.resize(); chart.update("none")')
    artifact = Path('.data-test/annotations')
    artifact.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(artifact / f'chart-{width}.png'))
    labels = _labels(page)
    overlaps = [(a['text'], b['text']) for i, a in enumerate(labels) for b in labels[i+1:]
                if a['x'] < b['x'] + b['w'] and b['x'] < a['x'] + a['w']
                and a['y'] < b['y'] + b['h'] and b['y'] < a['y'] + a['h']]
    assert not overlaps, overlaps
    assert page.evaluate("Chart.registry.getPlugin('annotation').getAnnotations(chart).length") == 12
    area = page.evaluate('({left:chart.chartArea.left,right:chart.chartArea.right})')
    assert labels
    assert all(a['x'] >= area['left'] and a['x'] + a['w'] <= area['right'] for a in labels)
    assert page.locator('.fw-annotation-details li').count() == 12
    page.keyboard.press('Tab')
    assert page.locator('summary').evaluate('(el) => el === document.activeElement')
    page.keyboard.press('Enter')
    assert page.locator('details').evaluate('(el) => el.open')
    text = page.locator('details').inner_text()
    for event in page.evaluate('_reportData._events'):
        assert event['label'] in text
        assert event['date'] in text
        if event.get('end_date'):
            assert event['end_date'] in text
    page.screenshot(path=str(artifact / f'details-{width}.png'))


def test_annotation_details_track_groups_dates_scope_and_destroy(annotation_page):
    page = annotation_page
    original = page.evaluate('Object.keys(chart.options.plugins.annotation.annotations).length')
    assert original == 12
    labels_before = _labels(page)
    page.set_viewport_size({'width': 1800, 'height': 800})
    page.evaluate('chart.resize(); chart.update("none")')
    page.set_viewport_size({'width': 1280, 'height': 800})
    page.evaluate('chart.resize(); chart.update("none")')
    assert [label['text'] for label in _labels(page)] == [label['text'] for label in labels_before]
    page.evaluate('''() => {
        _annoVisible.campaign = false;
        _refreshAllAnnotations();
    }''')
    assert page.locator('details').inner_text().find('coin sale') == -1
    assert page.locator('details li').count() == 7
    page.evaluate('''() => {
        _annoVisible.campaign = true;
        _refreshAllAnnotations();
    }''')
    assert page.locator('details li').count() == 12
    # A narrow date window retains overlapping durations, with original dates
    # in the details even though their bands are clipped to the selected range.
    page.evaluate('''() => {
        chart.data.labels = chart.data.labels.slice(-4);
        chart.data.datasets[0].data = chart.data.datasets[0].data.slice(-4);
        _refreshAllAnnotations();
    }''')
    assert len(_labels(page)) >= 1
    assert page.locator('details li').count() == 2
    assert '2026-06-25' in page.locator('details').text_content()
    page.evaluate('''() => { window._currentScope = 'northwind'; _refreshAllAnnotations(); }''')
    assert page.locator('details').count() == 0
    assert page.evaluate('Object.keys(chart.options.plugins.annotation.annotations).length') == 0
    page.evaluate('''() => {
        window._currentScope = 'nova-play';
        _reportData._events = [{date:'2026-06-28',label:'A single release',type:'release'},
            {date:'2026-06-28',label:'<b>Same day incident</b>',type:'incident'}];
        _refreshAllAnnotations();
    }''')
    assert len(_labels(page)) == 1
    assert page.locator('details li').count() == 2
    assert page.locator('details b').count() == 0
    page.evaluate('''() => { _annoVisible.incident = false; _refreshAllAnnotations(); }''')
    assert len(_labels(page)) == 1
    assert page.locator('details').count() == 0
    page.evaluate('''() => { _annoVisible.incident = true; _refreshAllAnnotations(); }''')
    assert page.locator('details li').count() == 2
    page.evaluate('chart.destroy()')
    assert page.locator('details').count() == 0


def test_crowded_event_details_in_image_and_pdf_export(annotation_page):
    page = annotation_page
    for vendor in ['html2canvas.min.js', 'jspdf.umd.min.js']:
        page.add_script_tag(content=(STATIC_ROOT / 'vendor' / vendor).read_text())
    page.add_script_tag(content=load_js('runtime/export.js'))
    page.evaluate('''() => {
        var capture = html2canvas;
        window.exportClones = [];
        window.html2canvas = (el, options) => {
            var onclone = options.onclone;
            options.onclone = async (doc, root) => {
                await onclone(doc, root);
                exportClones.push({open:doc.querySelector('details').open,
                    events:doc.querySelectorAll('details li').length});
            };
            return capture(el, options);
        };
    }''')
    artifact = Path('.data-test/annotations')
    artifact.mkdir(parents=True, exist_ok=True)
    for name, command, magic in [('report.png', '_exportPNG()', b'\x89PNG'),
                                  ('report.pdf', '_exportPDF()', b'%PDF')]:
        with page.expect_download() as download:
            page.evaluate(command)
        target = artifact / name
        download.value.save_as(str(target))
        assert target.read_bytes().startswith(magic)
        assert not page.locator('details').evaluate('(el) => el.open')
    assert page.evaluate('exportClones') == [{'open': True, 'events': 12}] * 2
    # Per-chart canvas downloads retain all bands/markers and clean labels.
    image = page.evaluate('chart.toBase64Image()')
    assert image.startswith('data:image/png;base64,')


@pytest.mark.parametrize('mode', ['grouped', 'adaptive'])
def test_owned_events_skip_full_label_fallback(annotation_page, mode):
    page = annotation_page
    page.add_style_tag(content=load_css('components/adaptive_events.css'))
    page.evaluate('''mode => {
        const labels=chart.data.labels, datasets=chart.data.datasets;chart.destroy();
        const dated=[], annotations=_buildAnnotations(_reportData._events,labels,dated);
        window.chart=new Chart(document.getElementById('chart'),{
            type:'line',data:{labels,datasets},options:{animation:false,maintainAspectRatio:false,
                plugins:{legend:{display:false},annotation:{annotations}}},
            plugins:[_fwLineEventLabelsPlugin(null,dated,mode)]});
        _chartInstances.chart=chart;_refreshAllAnnotations();
    }''', mode)
    assert not _labels(page)
    assert page.locator('.fw-annotation-details').count() == 0
    assert page.evaluate('chart._fwDatedEvents.length') == 12
    owner = '.fw-event-details' if mode == 'grouped' else '.fw-adaptive-events'
    assert page.locator(owner).count() == 1
    page.evaluate('chart.destroy()')
    assert page.locator(owner).count() == 0
