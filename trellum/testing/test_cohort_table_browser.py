"""Real generated reports, vendored runtime, native filtering and captures."""
import base64
import csv
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import pytest

from trellum.components import DataSource, FilterBar, Panel, ScopedDataSource, TabGroup
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.testing.test_cohort_table import diagnostic, examples, make


@pytest.fixture(scope='module')
def native_report(tmp_path_factory):
    root = tmp_path_factory.mktemp('native-cohort-report')
    ctx = ReportContext({'name': 'Cohort snapshots', 'studio': 'test', 'category': 'Components',
                         'description': 'Independent supplied observations and forecasts with explicit cohort states.'},
                        'cohort-snapshots', str(root))
    examples.add_sections(ctx)
    sparse = examples.cohort_frame().iloc[:3].copy().astype(object)
    sparse['cohort'] = ['<b>same month, first</b>', '<b>same month, first</b>', '__proto__']
    sparse['day'] = [120, 7, 30]
    sparse['observed'] = [.5, 0, -0.23456789012345]
    ctx.add_section('Cohort sparse identities', [
        Panel([make(sparse, 'Sparse identities', static=True,
                    milestone={'label': 'Sampled recovery', 'threshold': 1})])])
    ctx.add_section('Cohort scoped reveal', [
        DataSource('cohort_reveal_base', examples.cohort_frame()),
        ScopedDataSource('cohort_reveal_scope', parent='cohort_reveal_base'),
        FilterBar('cohort_reveal_scope', examples.cohort_frame(), filters=[{'column': 'platform', 'type': 'toggle'}]),
        TabGroup(tabs=[{'label': 'First', 'content': [make(examples.cohort_frame(), 'Reveal first', dataset_id='cohort_reveal_scope')]},
                       {'label': 'Second', 'content': [make(examples.cohort_frame(), 'Reveal second', dataset_id='cohort_reveal_scope')]}],
                 id='cohort_reveal_tabs')])
    ctx.add_section('Cohort empty snapshot', [make(examples.cohort_frame().iloc[:0], 'Empty snapshot', static=True)])
    sampled = pd.DataFrame([
        {'cohort': cohort, 'month': '2026-01', 'day': day,
         'observed': value, 'state': 'observed' if value is not None else 'not_mature',
         'forecast': forecast, 'reason': ''}
        for cohort, pairs in [
            ('Nonmonotone', [(None, 1.4), (1.2, 1.8), (.5, None), (1.3, None)]),
            ('Forecast', [(.2, 2.0), (None, .8), (None, 1.1), (None, 1.6)]),
        ]
        for day, (value, forecast) in zip([7, 30, 60, 90], pairs)
    ])
    ctx.add_section('Cohort sampled milestones', [
        make(sampled, 'Sampled milestones', static=True, forecast='forecast',
             forecast_metadata=examples.FORECAST, milestone={'threshold': 1, 'label': 'First displayed'})])
    details, result = diagnostic(ctx)
    assert result.summary['warn'] == result.summary['fail'] == 0, result.to_dict()
    (root / 'diagnostics.json').write_text(json.dumps({'details': details, 'validation': result.to_dict()}, indent=2))
    render_report(ctx, str(root), auto_refresh=False, validation=result, details=details)
    copy_vendor_tree(root)
    return root


def _open(browser, root, width, errors, warnings):
    page = browser.new_page(viewport={'width': width, 'height': 1000}, device_scale_factor=1)
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: warnings.append(message.text) if message.type in ('error', 'warning') else None)
    def serve(route):
        path = root / unquote(urlparse(route.request.url).path).lstrip('/')
        if path.is_file():
            route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
        else:
            route.fulfill(status=404, body='Missing test asset')
    page.route('http://cohort.test/**', serve)
    page.goto('http://cohort.test/index.html', wait_until='networkidle')
    return page


def _render(page, locator):
    locator.scroll_into_view_if_needed()
    locator.locator('.fw-cohort-content').wait_for(state='visible')
    page.wait_for_function('el => el.querySelector("table,.fw-cohort-empty,.fw-cohort-error") !== null', arg=locator.element_handle())


def _csv(page, table):
    with page.expect_download() as info:
        table.get_by_role('button', name='Download CSV', exact=True).click()
    with open(info.value.path(), encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_cohort_states_filters_lifecycle_and_captures(native_report, width):
    from playwright.sync_api import expect, sync_playwright
    evidence = Path(os.environ.get('TRELLUM_COHORT_EVIDENCE_DIR', '/tmp/trellum-cohort-evidence'))
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'native-report-path.txt').write_text(str(native_report))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        errors, warnings = [], []
        page = _open(browser, native_report, width, errors, warnings)
        try:
            tables = page.locator('.fw-cohort')
            first, milestone, observed = tables.nth(0), tables.nth(1), tables.nth(2)
            _render(page, first)
            expect(first.locator('tbody tr th')).to_have_text(['D7', 'D30', 'D60', 'D90', 'D120', 'D180'])
            expect(first.locator('thead tr').first.locator('th')).to_have_text(['Horizon', '2025', '2026'])
            assert first.locator('tbody td[data-state="observed"]').count() == 13
            assert first.locator('tbody td[data-state="forecast"]').count() == 15
            assert first.locator('tbody td[data-state="unavailable"]').count() == 1
            assert first.locator('tbody td[data-state="not_mature"]').count() == 1
            assert first.locator('tbody tr').first.locator('td').nth(1).inner_text() == '0.00%'
            assert first.locator('tfoot').count() == 0
            expect(first.locator('caption')).to_contain_text('Supplied cells · milestone off')
            rows = _csv(page, first)
            assert len(rows) == 30
            assert rows[0]['observed'] == '0.34' and rows[0]['forecast'] == '0.37'
            assert rows[6]['observed'] == '0'
            assert rows[14]['observed'] == rows[14]['forecast'] == ''
            assert rows[14]['observed_state'] == rows[14]['display_state'] == 'unavailable'
            assert rows[-1]['forecast_model'] == examples.FORECAST['model']
            assert rows[-1]['population_source_time'] == examples.POPULATION['source_time']
            # Every real source record, including raw precision, is exported.
            for source, exported in zip(examples.cohort_frame().to_dict('records'), rows):
                assert exported['cohort'] == source['cohort']
                if pd.notna(source['forecast']):
                    assert float(exported['forecast']) == source['forecast']
            first.locator('summary').click()
            expect(first.locator('.fw-cohort-details')).to_contain_text('Observed: 0.34 · Forecast: 0.37')
            first.locator('summary').focus()
            cid = first.get_attribute('id')
            page.evaluate('id => _fwRenderers.cohort_table(id, _reportData.components[id])', cid)
            # Re-entry replaces its own subscription rather than duplicating it.
            assert page.evaluate("id => _fwFilterEngine._subscribers.showcase_cohorts_supplied.filter(s=>s.id===id+'_cohorts').length", cid) == 1
            assert first.locator('details').get_attribute('open') is not None
            assert first.locator('summary').evaluate('e=>e===document.activeElement')
            filterbar = page.locator('#sec-cohort-tables-—-supplied-snapshot-and-forecasts .fw-filter-bar')
            filterbar.get_by_role('button', name='iOS', exact=True).click()
            expect(first.locator('tbody tr').first.locator('td')).to_have_count(2)
            assert len(_csv(page, first)) == 12
            assert first.locator('details').get_attribute('open') is not None
            filterbar.get_by_role('button', name='Web', exact=True).click()
            expect(first.locator('.fw-cohort-empty')).to_have_text('No cohorts match the current selection.')
            assert first.get_by_role('button', name='Download CSV').is_disabled()
            filterbar.get_by_role('button', name='All', exact=True).click()
            expect(first.locator('tbody td')).to_have_count(30)
            # Programmatic partial selections, even on a dimension absent from
            # FilterBar controls, cannot be claimed as valid forecast scope.
            page.evaluate("_fwFilterEngine.setFilter('showcase_cohorts_supplied', 'injected', 'day', 'equals', 7)")
            expect(first.locator('.fw-cohort-error')).to_contain_text('slice precomputed')
            assert first.locator('table').count() == 0
            page.evaluate("_fwFilterEngine.clearFilter('showcase_cohorts_supplied', 'injected')")
            expect(first.locator('tbody td')).to_have_count(30)
            page.evaluate("id => { const subs=_fwFilterEngine._subscribers.showcase_cohorts_supplied; const sub=subs.find(s=>s.id===id+'_cohorts'); sub.fn(_fwFilterEngine.getFiltered('showcase_cohorts_supplied').slice(0,1)); }", cid)
            expect(first.locator('.fw-cohort-error')).to_contain_text('Partial cohort')
            page.evaluate("_fwFilterEngine.notify('showcase_cohorts_supplied')")
            expect(first.locator('tbody td')).to_have_count(30)
            page.evaluate("id => { const sub=_fwFilterEngine._subscribers.showcase_cohorts_supplied.find(s=>s.id===id+'_cohorts'); const rows=_fwFilterEngine.getFiltered('showcase_cohorts_supplied').map(r=>({...r})); rows[0].forecast=.99; sub.fn(rows); }", cid)
            expect(first.locator('.fw-cohort-error')).to_contain_text('source fields changed')
            page.evaluate("_fwFilterEngine.notify('showcase_cohorts_supplied')")
            page.evaluate("id => { const sub=_fwFilterEngine._subscribers.showcase_cohorts_supplied.find(s=>s.id===id+'_cohorts'); const rows=_fwFilterEngine.getFiltered('showcase_cohorts_supplied'); sub.fn([...rows,rows[0]]); }", cid)
            expect(first.locator('.fw-cohort-error')).to_contain_text('duplicate cells')
            page.evaluate("_fwFilterEngine.notify('showcase_cohorts_supplied')")
            for mutate in ("delete rows[1].forecast", "rows[1].forecast=NaN", "rows[1]=null"):
                page.evaluate("([id,mutate]) => { const sub=_fwFilterEngine._subscribers.showcase_cohorts_supplied.find(s=>s.id===id+'_cohorts'); const rows=_fwFilterEngine.getFiltered('showcase_cohorts_supplied').map(r=>({...r})); eval(mutate); sub.fn(rows); }", [cid,mutate])
                expect(first.locator('.fw-cohort-error')).to_contain_text('source fields changed')
                page.evaluate("_fwFilterEngine.notify('showcase_cohorts_supplied')")
            # Runtime live ancestry rejection restores once the injected live hook is removed.
            page.evaluate("_fwFilterEngine._liveDatasets.showcase_cohorts_supplied={}; _fwFilterEngine.notify('showcase_cohorts_supplied')")
            expect(first.locator('.fw-cohort-error')).to_contain_text('LiveDataSource')
            page.evaluate("delete _fwFilterEngine._liveDatasets.showcase_cohorts_supplied; _fwFilterEngine.notify('showcase_cohorts_supplied')")
            _render(page, milestone)
            expect(milestone.locator('tfoot td').first).to_have_text('D90 · Observed')
            expect(milestone.locator('.fw-cohort-note')).to_contain_text('first displayed horizon')
            _render(page, observed)
            assert observed.locator('[data-state="forecast"]').count() == 0
            assert observed.locator('[data-state="not_mature"]').count() == 16
            assert all(r['forecast'] == '' for r in _csv(page, observed))
            sparse = tables.nth(3)
            _render(page, sparse)
            expect(sparse.locator('thead .fw-cohort-identity')).to_have_text(['<b>same month, first</b>', '__proto__'])
            assert sparse.locator('thead b').count() == 0
            assert sparse.locator('[data-state="no_record"]').count() == 3
            assert [r['horizon'] for r in _csv(page, sparse)] == ['7', '120', '30']
            reveal = page.locator('#sec-cohort-scoped-reveal')
            reveal.scroll_into_view_if_needed()
            reveal.get_by_role('tab', name='Second', exact=True).click()
            second = page.locator('.fw-cohort[data-fw-title="Reveal second"]')
            _render(page, second)
            reveal.get_by_role('button', name='iOS', exact=True).click()
            expect(second.locator('tbody td')).to_have_count(12)
            reveal.get_by_role('tab', name='First', exact=True).click()
            exposed = page.locator('.fw-cohort[data-fw-title="Reveal first"]')
            _render(page, exposed)
            expect(exposed.locator('tbody td')).to_have_count(12)
            remove_id = second.get_attribute('id')
            page.evaluate('id => document.getElementById(id).remove()', remove_id)
            page.wait_for_function("id => !_fwFilterEngine._subscribers.cohort_reveal_scope.some(s=>s.id===id+'_cohorts')", arg=remove_id)
            empty = page.locator('.fw-cohort[data-fw-title="Empty snapshot"]')
            _render(page, empty)
            expect(empty.locator('.fw-cohort-empty')).to_contain_text('No cohorts match')
            sampled = page.locator('.fw-cohort[data-fw-title="Sampled milestones"]')
            _render(page, sampled)
            expect(sampled.locator('tfoot td')).to_have_text(['D30 · Observed', 'D60 · Forecast F'])
            # Ordinary PivotTable is an unchanged, real native comparison.
            pivot = page.locator('[data-fw-kind="PivotTable"]')
            pivot.scroll_into_view_if_needed()
            pivot.locator('tbody tr').first.wait_for()
            assert pivot.locator('tfoot').count() == 1
            assert page.locator('.fw-pivot-controls select').count() == 4
            assert page.locator('.fw-pivot-controls select').first.evaluate('el=>Array.from(el.selectedOptions).map(o=>o.value)') == ['horizon_label']
            assert pivot.get_attribute('data-freeze') == 'false'
            for theme in ('light', 'dark'):
                page.evaluate('theme => switchTheme(theme)', theme)
                page.wait_for_function("!document.getAnimations().some(a=>a.playState==='running' && a.effect?.getComputedTiming().endTime !== Infinity)")
                _render(page, first)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'document overflow'
                # Inspect local scroll and retained first label at phone and in
                # a deliberately narrow native Panel on desktop.
                assert first.locator('thead tr').nth(1).locator('th').first.evaluate('e=>getComputedStyle(e).position') != 'sticky'
                local = first.locator('.fw-cohort-scroll')
                if width == 390:
                    assert local.evaluate('e=>e.scrollWidth>e.clientWidth')
                    before = first.locator('tbody th').first.bounding_box()['x']
                    local.evaluate('e=>e.scrollLeft=e.scrollWidth')
                    assert abs(first.locator('tbody th').first.bounding_box()['x'] - before) < 1
                    local.evaluate('e=>e.scrollLeft=0')
                first.screenshot(path=str(evidence / f'native-{width}-{theme}-cohort.png'))
                _render(page, observed)
                observed.screenshot(path=str(evidence / f'native-{width}-{theme}-observed.png'))
                pivot.scroll_into_view_if_needed()
                page.locator('#sec-cohort-tables-—-existing-pivottable-alternative').screenshot(path=str(evidence / f'native-{width}-{theme}-pivot.png'))
                sparse.evaluate('e=>e.parentElement.style.maxWidth="280px"')
                _render(page, sparse)
                assert sparse.evaluate('e=>e.scrollWidth<=e.clientWidth')
                assert sparse.locator('.fw-cohort-scroll').evaluate('e=>e.scrollWidth>e.clientWidth')
                sparse.screenshot(path=str(evidence / f'native-{width}-{theme}-narrow.png'))
                _render(page, milestone)
                # Capture the actual current filtered native section including
                # population, states, milestone and forecast provenance.
                filterbar.get_by_role('button', name='iOS', exact=True).click()
                section = page.locator('#sec-cohort-tables-—-supplied-snapshot-and-forecasts')
                section.scroll_into_view_if_needed()
                assert first.locator('details ul').evaluate('el=>getComputedStyle(el).display') == 'none'
                section.screenshot(path=str(evidence / f'native-{width}-{theme}-section.screen.png'))
                payload = section.evaluate('el => fw.captureElementSnapshotForAnalysis(el)')
                assert payload['format'] == 'trellum-analysis-capture'
                assert payload['source']['filters']['showcase_cohorts_supplied']
                (evidence / f'native-{width}-{theme}-section.png').write_bytes(base64.b64decode(payload['image']['data_base64']))
                (evidence / f'native-{width}-{theme}-section-source.json').write_text(json.dumps(payload['source'], indent=2))
                # Native report PNG/PDF use the same vendored export contract.
                for fmt in ('PNG', 'PDF'):
                    with page.expect_download() as info:
                        page.evaluate("fmt=>fw['export'+fmt]()", fmt)
                    path = evidence / f'native-{width}-{theme}-report.{fmt.lower()}'
                    info.value.save_as(path)
                    assert path.stat().st_size > 1000
                    if fmt == 'PDF':
                        assert path.read_bytes().startswith(b'%PDF-')
                filterbar.get_by_role('button', name='All', exact=True).click()
            assert not errors, errors
            assert not warnings, warnings
        finally:
            page.close()
            browser.close()
