"""Painted status roles, author overrides and native theme/filter lifecycle."""
import json
import os
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from trellum.rendering.html_builder import render_report
from trellum.testing.test_status_text import ROLES, report
from trellum.themes import THEME_REGISTRY, Theme

# Actual dependency-baseline geometry (24cd1cf), same genuine module report.
BASELINE_CARDS = {1280: [124] * 6, 390: [118, 118, 118, 118, 138, 138]}
GEOMETRY = """() => [...document.querySelectorAll('.fw-kpi-grid,.fw-kpi-card,.fw-kpi-value,.fw-kpi-label,.fw-kpi-delta,.fw-kpi-change,.fw-kpi-forecast,.fw-kpi-sub,.fw-table-scroll,.fw-table td,.fw-table th')].map(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {tag:e.className, x:r.x,y:r.y,width:r.width,height:r.height,font:s.fontSize,line:s.lineHeight,padding:s.padding,display:s.display}})"""

# Resolve actual computed alpha backgrounds, including the 12% color-mix pill
# and transparent striped table cells over their report panel. Keep unrounded
# luminance/ratios; no threshold rounding and no assumed white canvas.
PAINT = """el => {
    function rgba(value) {
        let m=value.match(/^color\\(srgb (.*)\\)$/);
        if(m) {const parts=m[1].split(' / ');return [...parts[0].split(' ').map(Number),parts[1]===undefined?1:Number(parts[1])];}
        const a=value.match(/[\\d.]+/g).map(Number);
        return [a[0]/255,a[1]/255,a[2]/255,a.length>3?a[3]:1];
    }
    function over(f,b) {return f.slice(0,3).map((c,i)=>c*f[3]+b[i]*(1-f[3])).concat(1);}
    function background(e) {
        const parents=[];for(;e;e=e.parentElement)parents.unshift(e);
        return parents.reduce((b,p)=>over(rgba(getComputedStyle(p).backgroundColor),b),[1,1,1,1]);
    }
    function luminance(c) {return c.slice(0,3).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);}
    const style=getComputedStyle(el),fg=rgba(style.color),bg=background(el);
    const a=luminance(over(fg,bg)),b=luminance(bg);
    return {color:style.color,background:style.backgroundColor,composited:bg,
            ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05),text:el.textContent};
}"""


def _route(page, output):
    vendor = Path(__file__).parents[1] / 'static/vendor'

    def fulfill(route):
        url = urlsplit(route.request.url)
        assert url.hostname == 'status.test', route.request.url
        path = unquote(url.path).lstrip('/')
        file = vendor / path[len('_vendor/'):] if path.startswith('_vendor/') else output / path
        if file.is_file():
            route.fulfill(path=str(file))
        else:
            route.fulfill(status=404, body='missing')

    page.route('**/*', fulfill)


def _ready(page):
    page.goto('http://status.test/index.html', wait_until='networkidle')
    _reveal(page)


def _reveal(page):
    page.locator('.fw-kpi-value').last.wait_for()
    for table in page.locator('.fw-table-scroll').all():
        table.scroll_into_view_if_needed()
        table.locator('tbody tr').first.wait_for()
    page.evaluate('scrollTo(0,0)')


def _color(page, variable):
    return page.locator('.fw-kpi-grid').evaluate("""(el,v) => {
        const p=document.createElement('span');p.style.color='var(--'+v+')';el.append(p);
        const c=getComputedStyle(p).color;p.remove();return c;
    }""", variable)


def _export(page, cid):
    page.locator(f'[data-csv-target="{cid}"]').click()
    return page.evaluate('csvCalls.at(-1)')


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_status_text_real_themes_paint_geometry_and_lifecycle(tmp_path, monkeypatch, width):
    from playwright.sync_api import sync_playwright

    builtins = dict(THEME_REGISTRY)
    legacy = Theme(bg_main='#111315', bg_card='#181b1f', bg_card_hover='#23272d',
                   text_main='#f3f4f6', text_muted='#a7adb8', accent_green='#00c48c',
                   accent_red='#f87171', accent_yellow='#ffd166')
    custom = replace(legacy, text_positive='#72e0b2', text_negative='#ffacac',
                     text_warning='#fce192', text_context='#c6cbd3')
    monkeypatch.setitem(THEME_REGISTRY, 'legacy custom dark', legacy)
    monkeypatch.setitem(THEME_REGISTRY, 'custom roles', custom)
    render_report(report(tmp_path), auto_refresh=False)
    data = json.loads((tmp_path / 'data.json').read_text())
    tables = [cid for cid, cfg in data['components'].items() if cfg['type'] == 'table']
    evidence = os.environ.get('TRELLUM_STATUS_EVIDENCE')
    evidence = Path(evidence) if evidence else None
    if evidence:
        evidence.mkdir(parents=True, exist_ok=True)
    measurements = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        _route(page, tmp_path)
        _ready(page)
        assert page.locator('html').get_attribute('data-theme') == 'trellum light'
        assert page.locator('#fwThemeSelect').input_value() == 'trellum light'
        page.evaluate('window.csvCalls=[];window._fwDownloadCsv=(columns,rows)=>csvCalls.push({columns,rows})')
        cards = page.locator('.fw-kpi-card')
        assert [c.bounding_box()['height'] for c in cards.all()] == BASELINE_CARDS[width]
        positions = [c.bounding_box() for c in cards.all()]
        assert len({p['y'] for p in positions}) == (1 if width == 1280 else 3)
        assert cards.first.locator('.fw-kpi-value').evaluate('e=>getComputedStyle(e).fontSize') == '24px'
        assert cards.first.locator('.fw-kpi-label').evaluate('e=>getComputedStyle(e).fontSize') == '12px'
        assert page.locator('.fw-kpi-change').first.bounding_box()['height'] == 15
        page.evaluate('scrollTo(0,0)')
        if evidence and (evidence / f'baseline-{width}-geometry.json').exists():
            baseline = json.loads((evidence / f'baseline-{width}-geometry.json').read_text())
            assert page.evaluate(GEOMETRY) == baseline

        for name in builtins:
            page.select_option('#fwThemeSelect', name)
            assert page.locator('html').get_attribute('data-theme') == name
            assert page.locator('#fwThemeSelect').input_value() == name
            # Every consumed KPI role must follow the selected theme, including
            # two improvements with opposite numeric signs and no-direction text.
            positive, negative, warning, context = [_color(page, r.replace('_', '-')) for r in ROLES]
            assert cards.nth(0).locator('.fw-kpi-delta').evaluate('e=>getComputedStyle(e).color') == positive
            assert cards.nth(1).locator('.fw-kpi-delta').evaluate('e=>getComputedStyle(e).color') == positive
            assert cards.nth(2).locator('.fw-kpi-delta').evaluate('e=>getComputedStyle(e).color') == negative
            assert cards.nth(3).locator('.fw-kpi-delta').evaluate('e=>getComputedStyle(e).color') == context
            assert cards.nth(4).locator('.fw-kpi-forecast').evaluate('e=>getComputedStyle(e).color') == warning
            assert cards.nth(5).locator('.fw-kpi-delta').get_attribute('class') == 'fw-kpi-delta'
            for card in cards.all():
                # Hover settles its real CSS transition before sampling.
                page.mouse.move(0, 0)
                page.wait_for_timeout(220)
                for hover in (False, True):
                    if hover:
                        card.hover()
                        page.wait_for_timeout(220)
                    for text in card.locator('.fw-kpi-delta,.fw-kpi-change,.fw-kpi-forecast,.fw-kpi-sub').all():
                        paint = text.evaluate(PAINT)
                        assert paint['ratio'] >= 4.6, (name, hover, paint)
                        measurements.append({'theme': name, 'hover': hover, **paint})
            for cid in tables[:2]:
                table = page.locator(f'#{cid}')
                table.scroll_into_view_if_needed()
                for row in table.locator('tbody tr').all():
                    for hover in (False, True):
                        page.mouse.move(0, 0)
                        if hover:
                            row.hover()
                        # Legacy delta cells carry their auto-rule on td;
                        # declared deltas carry a span and neutral outcome below.
                        cell = row.locator('td').last
                        text = cell.locator('span').first if cid == tables[0] else cell
                        for target in [text, *cell.locator('.fw-comparison-outcome').all()]:
                            paint = target.evaluate(PAINT)
                            assert paint['ratio'] >= 4.6, (name, cid, hover, paint)
                            measurements.append({'theme': name, 'hover': hover, **paint})
                assert table.locator('tbody tr').first.locator('td').first.evaluate('e=>getComputedStyle(e).color') == _color(page, 'text-main')
                delta = table.locator('tbody tr').first.locator('td').last
                target = delta.locator('span').first if cid == tables[0] else delta
                assert target.evaluate('e=>getComputedStyle(e).color') == positive
                if cid == tables[0]:
                    assert delta.locator('.fw-comparison-outcome').evaluate('e=>getComputedStyle(e).color') == context
            for note in page.locator('.fw-table-note').all():
                paint = note.evaluate(PAINT)
                assert paint['ratio'] >= 4.6, (name, paint)
            if evidence and name in ('trellum light', 'trellum dark'):
                page.evaluate('scrollTo(0,0)')
                page.screenshot(path=str(evidence / f'changed-{width}-{name.replace(" ", "-")}.png'), full_page=True)
                if width == 390:
                    table = page.locator(f'#{tables[0]}')
                    table.evaluate('e=>e.scrollLeft=e.scrollWidth')
                    table.screenshot(path=str(evidence / f'changed-{width}-{name.replace(" ", "-")}-outcomes.png'))
                    table.evaluate('e=>e.scrollLeft=0')

        declared = page.locator(f'#{tables[0]} tbody tr')
        assert declared.nth(0).locator('td').last.inner_text() == '+12.0%\nImproved'
        assert declared.nth(1).locator('td').last.inner_text() == '-8.0%\nImproved'
        assert declared.nth(2).locator('td').last.inner_text() == '+2.0 pp\nWorsened'
        assert declared.nth(3).locator('td').last.inner_text() == '0.0 pp\nUnchanged'
        assert declared.nth(4).locator('td').last.inner_text() == '—\nNo prior period'
        assert _export(page, tables[0])['rows'][0] == ['Revenue', 24800, .12]
        assert _export(page, tables[0])['rows'][1] == ['Cost per install', 2.35, -.08]
        assert _export(page, tables[0])['rows'][4] == ['New market', 735, '']
        assert _export(page, tables[1])['rows'] == [['Orders', 410, 35], ['Returns', 18, -4]]
        for name in ('legacy custom dark', 'custom roles'):
            page.select_option('#fwThemeSelect', name)
            assert cards.first.locator('.fw-kpi-delta').evaluate('e=>getComputedStyle(e).color') == _color(page, 'text-positive')
            expected = _color(page, 'accent-green') if name == 'legacy custom dark' else 'rgb(114, 224, 178)'
            assert cards.first.locator('.fw-kpi-delta').evaluate('e=>getComputedStyle(e).color') == expected
            for selector, role in [('.fw-kpi-delta.up', 'text-positive'),
                                   ('.fw-kpi-delta.down', 'text-negative'),
                                   ('.fw-kpi-forecast', 'text-warning'),
                                   ('.fw-kpi-sub', 'text-context')]:
                assert page.locator(selector).first.evaluate('e=>getComputedStyle(e).color') == _color(page, role)
            explicit = page.locator(f'#{tables[2]} tbody tr')
            assert explicit.first.locator('td').last.evaluate('e=>getComputedStyle(e).color') == _color(page, 'accent-green')
            assert explicit.last.locator('td').last.evaluate('e=>getComputedStyle(e).color') == 'rgb(177, 69, 123)'
            # Full native renderer re-entry keeps role references and words.
            for cid in tables:
                page.evaluate('([id,cfg])=>window._fwRenderers.table(id,cfg)', [cid, data['components'][cid]])
            card_id = cards.nth(1).get_attribute('id')
            page.evaluate('([id,cfg])=>window._fwRenderers.kpi(id,cfg)', [card_id, data['components'][card_id]])
            assert cards.nth(1).locator('.fw-kpi-change').inner_text() == '−8% · Improved'
            assert cards.nth(1).locator('.fw-kpi-delta').evaluate('e=>getComputedStyle(e).color') == expected
            page.reload(wait_until='networkidle')
            _reveal(page)
            cards.first.locator('.fw-kpi-delta').wait_for()
            assert page.locator('#fwThemeSelect').input_value() == name
            assert cards.first.locator('.fw-kpi-delta').evaluate('e=>getComputedStyle(e).color') == expected
            page.evaluate('window.csvCalls=[];window._fwDownloadCsv=(columns,rows)=>csvCalls.push({columns,rows})')
        # Hosts with no role CSS still resolve the nested legacy fallbacks.
        page.add_style_tag(content=':root {--text-positive:initial;--text-negative:initial;--text-warning:initial;--text-context:initial;}')
        for selector, legacy_role in [('.fw-kpi-delta.up', 'accent-green'),
                                      ('.fw-kpi-delta.down', 'accent-red'),
                                      ('.fw-kpi-forecast', 'accent-yellow'),
                                      ('.fw-kpi-sub', 'text-muted')]:
            assert page.locator(selector).first.evaluate('e=>getComputedStyle(e).color') == _color(page, legacy_role)
        assert page.locator(f'#{tables[0]} tbody tr').first.locator('td').last.locator('span').first.evaluate('e=>getComputedStyle(e).color') == _color(page, 'accent-green')
        page.get_by_role('button', name='Context', exact=True).click()
        assert page.locator(f'#{tables[0]} tbody tr').count() == 2
        assert _export(page, tables[0])['rows'] == [['Retention', .31, 0], ['New market', 735, '']]
        page.select_option('#fwThemeSelect', 'trellum dark')
        page.reload(wait_until='networkidle')
        assert page.locator('html').get_attribute('data-theme') == 'trellum dark'
        assert page.locator('#fwThemeSelect').input_value() == 'trellum dark'
        cards.first.locator('.fw-kpi-delta').wait_for()
        assert cards.first.locator('.fw-kpi-delta').evaluate('e=>getComputedStyle(e).color') == _color(page, 'text-positive')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if evidence:
            (evidence / f'contrast-{width}.json').write_text(json.dumps(measurements, indent=2))
        assert errors == []
        browser.close()
