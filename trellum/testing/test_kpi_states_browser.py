"""Painted KPI declarations, filter restoration and real-baseline geometry."""
import importlib.util
import json
import mimetypes
import subprocess
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlparse

import pandas as pd
import pytest

from trellum.components import DataSource, FilterBar, KpiCard, KpiRow
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext

BASELINE = "e0fa5a3d59fe58a8a91b5d3fc557515da20d2ac4"
EVIDENCE = Path("/tmp/trellum-kpi-evidence")


def context(output):
    return ReportContext(config={"name": "KPI states", "studio": "test", "category": "Components",
        "description": "Synthetic revenue availability, declared cohort observations and paired-period comparisons by case, cohort and region."},
        slug="kpi-states", output_dir=str(output))


def add_geometry(ctx, baseline=False):
    ctx.add_section("Geometry ordinary", [KpiRow([
        KpiCard(label="Ordinary", value=123),
        KpiCard(label="Centered", value=0, alignment="center"),
        KpiCard(label="Currency", value=12, format="currency"),
    ], static=True)])
    comparison = {} if baseline else {"comparison": {"measure": "percentage_points",
        "current": {"value": .22, "unit": "fraction"}, "baseline": {"value": 20, "unit": "percent"}}}
    ctx.add_section("Geometry comparison", [KpiRow([
        KpiCard(label="Conversion", value=.22, format="percent", sub="Declared paired periods",
                **({"delta": "+2.0 pp"} if baseline else comparison)) for _ in range(3)
    ], static=True)])
    ctx.add_section("Geometry cohort", [KpiRow([
        KpiCard(label="Final D7", value=.08 if baseline else None, format="percent",
                **({"forecast": .15, "sub": "Cohort age: 3 days"} if baseline else {
                    "maturity": {"state": "immature", "age": 3, "horizon": 7,
                                 "observed": {"value": 8, "unit": "percent"},
                                 "forecast": {"value": .15, "unit": "fraction"}}})) for _ in range(3)
    ], static=True)])


def build_reports(root):
    final = root / "final"
    final.mkdir()
    ctx = context(final)
    add_geometry(ctx)
    path = Path(__file__).parents[1] / "demo/reports/component-showcase/examples_kpi_states.py"
    spec = importlib.util.spec_from_file_location("kpi_examples_browser", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.add_sections(ctx)
    legacy = pd.DataFrame([
        {"case": "Valid", "current": 110, "change": 10, "baseline": 100, "pct": .1, "cash": 12},
        {"case": "Zero", "current": 480, "change": 480, "baseline": 0, "pct": 0, "cash": 0},
        {"case": "Unavailable", "current": 500, "change": None, "baseline": None, "pct": None, "cash": None},
        {"case": "Signed", "current": -50, "change": 50, "baseline": -100, "pct": -5, "cash": -2},
        {"case": "Overflow", "current": 1, "change": 1, "baseline": 1e308, "pct": 0, "cash": 0},
        {"case": "Overflow", "current": 1, "change": 1, "baseline": 1e308, "pct": 0, "cash": 0},
    ])
    ctx.add_section("Legacy unit contracts", [
        DataSource("kpi_legacy_states", legacy),
        FilterBar("kpi_legacy_states", legacy, filters=[{"column": "case", "label": "Legacy case", "type": "toggle"}]),
        KpiRow([
            {"label": "Sum percent", "agg": "sum", "column": "current", "delta_col": "pct"},
            {"label": "Ratio percent", "agg": "sum", "column": "current", "delta_col": "change",
             "delta_agg": "ratio", "delta_denominator": "baseline"},
            {"label": "Currency delta", "agg": "sum", "column": "current", "delta_col": "cash", "delta_format": "currency"},
            {"label": "Neutral zero", "agg": "sum", "column": "current", "delta_col": "pct"},
            {"label": "Missing constant", "value": None, "format": "currency"},
            {"label": "Fraction? legacy scale", "value": .08, "format": "percent"},
            KpiCard(label="Supplied override", value=.22, format="percent", delta="Authored +2 pp", delta_direction="down",
                    comparison={"measure": "percentage_points", "value": 99}),
        ], dataset_id="kpi_legacy_states"),
        KpiRow([
            {"label": "Reducer overrides unused constant", "value": None, "agg": "sum", "column": "current",
             "availability": {"required_columns": ["current"], "empty": "unavailable"}},
            {"label": "Finite denominator", "agg": "ratio", "numerator": "change", "denominator": "baseline",
             "availability": {"required_columns": ["change", "baseline"], "empty": "unavailable",
                              "ratio_denominator": "nonzero"}},
        ], dataset_id="kpi_legacy_states"),
    ])
    empty = pd.DataFrame(columns=["amount"])
    ctx.add_section("Registered empty population", [
        DataSource("kpi_empty_states", empty),
        FilterBar("kpi_empty_states", empty, filters=[]),
        KpiRow([
            {"label": "Unknown empty revenue", "agg": "sum", "column": "amount", "format": "currency",
             "availability": {"required_columns": ["amount"], "empty": "unavailable"}},
            {"label": "Deliberate empty zero", "agg": "sum", "column": "amount",
             "availability": {"required_columns": ["amount"], "empty": "zero"}},
            {"label": "Empty count", "agg": "count"},
        ], dataset_id="kpi_empty_states"),
    ])
    from trellum.reporting.diagnostics import compute_details
    from trellum.validation import validate_report
    validation = validate_report(ctx, details=compute_details(ctx))
    assert not [(c.id, c.message) for c in validation.checks if c.level in {"warn", "fail"}]
    render_report(ctx, str(final), auto_refresh=False)
    copy_vendor_tree(root)

    # Source archives and shallow CI clones may omit the historical object.
    # Keep all runtime assertions there; compare actual assets when available.
    available = subprocess.run(["git", "cat-file", "-e", BASELINE], capture_output=True).returncode == 0
    if not available:
        return False
    baseline = root / "baseline"
    baseline.mkdir()
    before = context(baseline)
    add_geometry(before, baseline=True)
    # These are the actual accepted-baseline assets, not a recreated renderer.
    def asset(path):
        return subprocess.check_output(["git", "show", f"{BASELINE}:trellum/static/{path}"], text=True)
    card_js, row_js, css = asset("js/components/kpi_card.js"), asset("js/components/kpi_row.js"), asset("css/components/kpi_card.css")
    with patch.object(KpiCard, "client_js", return_value=card_js), \
         patch.object(KpiRow, "client_js", return_value=card_js + "\n" + row_js), \
         patch.object(KpiCard, "css", return_value=css), patch.object(KpiRow, "css", return_value=css):
        render_report(before, str(baseline), auto_refresh=False)
    return True


def section(page, title):
    return page.locator(".fw-section").filter(has=page.get_by_role("heading", name=title, exact=True))


def value(card):
    return card.locator(".fw-kpi-value").inner_text()


def geometry(page):
    return page.locator('.fw-section').filter(has=page.get_by_role('heading', name='Geometry', exact=False)).evaluate_all("""sections => sections.map(s =>
        Array.from(s.querySelectorAll('.fw-kpi-card')).map(c => {
            const b=c.getBoundingClientRect(), v=c.querySelector('.fw-kpi-value');
            return {height:b.height,width:b.width,x:b.x,font:getComputedStyle(v).fontSize,
                align:getComputedStyle(c).textAlign};
        }))""")


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_declared_kpi_states_paint_filter_and_geometry(tmp_path, width):
    from playwright.sync_api import expect, sync_playwright

    baseline_available = build_reports(tmp_path)
    EVIDENCE.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 950})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        def route(request):
            url = urlparse(request.request.url)
            path = tmp_path / unquote(url.path).lstrip("/")
            if url.hostname == "kpi.test" and path.is_file():
                request.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")
            else:
                request.abort()
        page.route("**/*", route)
        if baseline_available:
            page.goto("http://kpi.test/baseline/index.html", wait_until="networkidle")
            expect(page.locator('.fw-kpi-value').first).to_have_text('123')
            page.evaluate("document.fonts.ready")
            before = geometry(page)
            page.screenshot(animations="disabled", path=str(EVIDENCE / f"baseline-{width}.png"), full_page=True)
        page.goto("http://kpi.test/final/index.html", wait_until="networkidle")
        expect(page.locator('.fw-kpi-value').first).to_have_text('123')
        page.evaluate("document.fonts.ready")
        after = geometry(page)
        if baseline_available:
            assert after == before
        (EVIDENCE / f"geometry-{width}.json").write_text(json.dumps({
            "baseline_commit": BASELINE if baseline_available else None,
            "baseline": before if baseline_available else None, "final": after,
        }, indent=2))
        assert after[0][0]["font"] == "24px"
        assert after[0][1]["align"] == "center"
        assert section(page, 'Geometry comparison').locator('.fw-kpi-card').first.locator('div').evaluate_all(
            "nodes => nodes.map(n => n.className)") == [
                'fw-kpi-label', 'fw-kpi-value', 'fw-kpi-delta up fw-kpi-declared-comparison', 'fw-kpi-sub']
        assert page.locator('.fw-kpi-age').first.inner_text() == 'Age 3d · <7d'
        page.screenshot(animations="disabled", path=str(EVIDENCE / f"final-{width}.png"), full_page=True)

        inputs = section(page, "KPI states — supplied observation completeness")
        grid = inputs.locator('.fw-kpi-grid')
        cards = grid.locator('.fw-kpi-card')
        inputs.get_by_role('button', name='Complete', exact=True).click()
        expect(cards.nth(0).locator('.fw-kpi-value')).to_have_text('$120.00')
        assert value(cards.nth(1)) == '$120.00'
        assert inputs.locator('.fw-kpi-state-note:visible').count() == (0 if width == 390 else 1)
        ordinary_height = cards.nth(0).bounding_box()['height']
        inputs.get_by_role('button', name='Measured zero', exact=True).click()
        expect(cards.nth(1).locator('.fw-kpi-value')).to_have_text('$0.00')
        assert value(cards.nth(2)) == '0.0%'
        inputs.get_by_role('button', name='Missing', exact=True).click()
        expect(cards.nth(1).locator('.fw-kpi-value')).to_have_text('—')
        assert value(cards.nth(0)) == '$0.00'  # Legacy subtotal remains.
        assert value(cards.nth(3)) == '1'
        assert cards.nth(0).bounding_box()['height'] == ordinary_height
        note_id = cards.nth(1).get_attribute('aria-describedby')
        assert inputs.locator('#' + note_id).inner_text().startswith('Complete revenue:')
        assert inputs.locator('#' + note_id).evaluate("n => !n.closest('.fw-kpi-grid')")
        inputs.screenshot(animations="disabled", path=str(EVIDENCE / f"missing-{width}.png"))
        inputs.get_by_role('button', name='Partial', exact=True).click()
        assert value(cards.nth(0)) == '$75.00'
        assert value(cards.nth(1)) == '—'
        inputs.screenshot(animations="disabled", path=str(EVIDENCE / f"partial-{width}.png"))
        inputs.get_by_role('button', name='Zero denominator', exact=True).click()
        assert value(cards.nth(1)) == '$42.00'
        assert value(cards.nth(2)) == '—'
        expect(inputs.locator('.fw-kpi-state-note').filter(has_text='ROAS:')).to_have_text('ROAS: zero denominator')
        inputs.screenshot(animations="disabled", path=str(EVIDENCE / f"zero-denominator-{width}.png"))
        if width == 390:
            assert grid.locator('.fw-kpi-card:visible').count() == 4
            inputs.get_by_role('button', name='+ 2 more').click()
            assert grid.locator('.fw-kpi-card:visible').count() == 6
            assert value(cards.nth(4)) == '—'
            assert value(cards.nth(5)) == '8.00%'
            assert inputs.locator('.fw-kpi-state-note:visible').count() == 2
        # Genuine no-match predicate, then restoration through the real engine.
        page.evaluate("window._fwFilterEngine.setFilter('showcase_kpi_states','none','observation','in',['absent'])")
        assert value(cards.nth(3)) == '0.00'
        assert value(cards.nth(1)) == '—'
        inputs.screenshot(animations="disabled", path=str(EVIDENCE / f"no-matches-{width}.png"))
        page.evaluate("window._fwFilterEngine.clearFilter('showcase_kpi_states','none')")
        inputs.get_by_role('button', name='Complete', exact=True).click()
        expect(cards.nth(1).locator('.fw-kpi-value')).to_have_text('$120.00')
        assert not cards.nth(1).get_attribute('aria-describedby')
        # URL state is restored by the real FilterBar.
        restored = page.url
        page.reload(wait_until='networkidle')
        inputs = section(page, "KPI states — supplied observation completeness")
        expect(inputs.locator('.fw-kpi-value').nth(1)).to_have_text('$120.00')
        assert page.url == restored

        cohorts = section(page, "KPI states — declared cohort age and horizon")
        cohorts.get_by_role('button', name='Young', exact=True).click()
        cohort_cards = cohorts.locator('.fw-kpi-card')
        expect(cohort_cards.nth(0).locator('.fw-kpi-value')).to_contain_text('Age 3d · <7d')
        assert cohort_cards.nth(0).locator('.fw-kpi-forecast').inner_text() == 'Observed D3 · 8.00%'
        assert cohort_cards.nth(0).locator('.fw-kpi-sub').inner_text() == 'Forecast D7 · 15.00%'
        assert cohort_cards.nth(1).locator('.fw-kpi-sub').count() == 0
        cohorts.screenshot(animations="disabled", path=str(EVIDENCE / f"immature-{width}.png"))
        cohorts.get_by_role('button', name='Mature', exact=True).click()
        assert value(cohort_cards.nth(0)) == '14.00%'
        cohorts.get_by_role('button', name='All', exact=True).click()
        assert value(cohort_cards.nth(0)) == '—'
        assert cohorts.locator('.fw-kpi-state-note').count() == 2
        cohorts.screenshot(animations="disabled", path=str(EVIDENCE / f"mixed-cohort-{width}.png"))

        periods = section(page, 'KPI states — weighted paired-period comparisons')
        periods.locator('.ss-main').click()
        page.locator('.ss-content:visible .ss-option').filter(has_text='North').click()
        periods.get_by_role('heading').click()
        expect(periods.locator('.fw-kpi-value').first).to_have_text('24.00%')
        assert periods.locator('.fw-kpi-change').nth(0).inner_text() == '+60.0%'
        assert periods.locator('.fw-kpi-change').nth(1).inner_text() == '+9.0 pp'
        assert periods.locator('.fw-kpi-change').nth(2).inner_text() == '— · zero baseline'
        assert periods.locator('.fw-kpi-value').nth(2).inner_text() == '$480.00'
        periods.screenshot(animations="disabled", path=str(EVIDENCE / f"comparison-zero-{width}.png"))
        periods.locator('.ss-main').click()
        page.locator('.ss-content:visible .ss-option').filter(has_text='South').click()
        periods.get_by_role('heading').click()
        expect(periods.locator('.fw-kpi-value').first).to_have_text('22.00%')
        assert periods.locator('.fw-kpi-change').nth(0).inner_text() == '+10.0%'
        assert periods.locator('.fw-kpi-change').nth(1).inner_text() == '+2.0 pp'
        periods.screenshot(animations="disabled", path=str(EVIDENCE / f"comparison-weighted-{width}.png"))
        periods.get_by_role('link', name='Clear', exact=True).click()
        assert periods.locator('select').evaluate('el => el._fwSlimSelect.getSelected()') == []
        periods.locator('.ss-main').click()
        page.locator('.ss-content:visible .ss-option').filter(has_text='Unknown baseline').click()
        periods.get_by_role('heading').click()
        assert periods.locator('select').evaluate('el => el._fwSlimSelect.getSelected()') == ['Unknown baseline']
        expect(periods.locator('.fw-kpi-change').first).to_have_text('— · baseline unavailable')
        expect(periods.locator('.fw-kpi-value').first).to_have_text('30.00%')
        periods.screenshot(animations="disabled", path=str(EVIDENCE / f"comparison-unavailable-{width}.png"))
        snapshots = section(page, 'KPI states — independent supplied values')
        standalone = snapshots.locator('.fw-kpi-card').last
        assert standalone.evaluate("el => !el.closest('.fw-kpi-grid') && el.nextElementSibling.id === el.getAttribute('aria-describedby')")
        favorable = snapshots.locator('.fw-kpi-card').nth(4).locator('.fw-kpi-delta')
        assert 'up' in favorable.get_attribute('class')
        assert favorable.inner_text() == '-10.0%'
        assert snapshots.locator('.fw-kpi-card').nth(3).locator('.fw-kpi-delta').get_attribute('class') == 'fw-kpi-delta fw-kpi-declared-comparison'

        empty = section(page, 'Registered empty population')
        assert empty.locator('.fw-kpi-value').all_inner_texts() == ['—', '0.00', '0.00']
        empty.screenshot(animations="disabled", path=str(EVIDENCE / f"registered-empty-{width}.png"))
        legacy = section(page, 'Legacy unit contracts')
        legacy.get_by_role('button', name='Valid', exact=True).click()
        changes = legacy.locator('.fw-kpi-change')
        assert changes.all_inner_texts() == ['+0.1%', '+10.0%', '+$12.00', '+0.1%', 'Authored +2 pp']
        legacy_height = legacy.locator('.fw-kpi-card').nth(1).bounding_box()['height']
        assert legacy.locator('.fw-kpi-value').nth(4).inner_text() == '—'
        assert legacy.locator('.fw-kpi-value').nth(5).inner_text() == '0.1%'
        assert legacy.locator('.fw-kpi-delta').last.get_attribute('class').endswith('down fw-kpi-declared-comparison')
        legacy.get_by_role('button', name='Zero', exact=True).click()
        assert changes.nth(1).inner_text() == '— · zero baseline'
        assert 'up' not in legacy.locator('.fw-kpi-delta').nth(0).get_attribute('class')
        legacy.get_by_role('button', name='Unavailable', exact=True).click()
        assert changes.nth(1).inner_text() == '— · baseline unavailable'
        assert legacy.locator('.fw-kpi-card').nth(1).bounding_box()['height'] == legacy_height
        legacy.get_by_role('button', name='Signed', exact=True).click()
        assert changes.nth(1).inner_text() == '+50.0%'  # Legacy abs baseline parity.
        legacy.get_by_role('button', name='Overflow', exact=True).click()
        assert changes.nth(1).inner_text() == '— · baseline unavailable'
        assert legacy.locator('.fw-kpi-grid').last.locator('.fw-kpi-value').all_inner_texts() == ['2', '—']
        assert legacy.locator('.fw-kpi-state-note').last.inner_text() == 'Finite denominator: denominator unavailable'

        # Safe text, long wrapping notes, short comparison line with full accessible text.
        page.evaluate("""() => {
            const card = document.querySelector('.fw-kpi-card');
            const description = document.createElement('p');
            description.id = 'authored-metric-description'; description.textContent = 'Supplied metric definition';
            document.body.appendChild(description);
            card.setAttribute('aria-describedby', description.id);
            window._fwRenderers.kpi(card.id, {label:'<b>Metric</b>',value:12,
                availability:{state:'unavailable',reason:'<img src=x onerror=alert(1)> ' + 'long context '.repeat(25)},
                comparison:{measure:'relative_percent',current:{value:12,unit:'number'},baseline:{value:null,unit:'number'},reason:'Unavailable ' + 'context '.repeat(30)}});
        }""")
        first = page.locator('.fw-kpi-card').first
        assert first.locator('img,b').count() == 0
        assert page.locator('.fw-kpi-state-note').first.locator('img').count() == 0
        assert first.locator('.fw-kpi-delta').evaluate('n => n.getBoundingClientRect().height') < 25
        assert len(first.locator('.fw-kpi-delta').get_attribute('title')) > 100
        assert first.get_attribute('aria-describedby').split() == ['authored-metric-description', first.get_attribute('id') + '_state_note']
        page.evaluate("id => window._fwRenderers.kpi(id, {label:'Restored', value:12})", first.get_attribute('id'))
        assert first.get_attribute('aria-describedby') == 'authored-metric-description'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        # Theme changes and repeated renders never duplicate external notes.
        count = page.locator('.fw-kpi-state-note').count()
        for theme in ('trellum dark', 'trellum light'):
            page.locator('#fwThemeSelect').select_option(theme)
            expect(page.locator('html')).to_have_attribute('data-theme', theme)
            page.evaluate('window.renderAll()')
            cohorts.get_by_role('button', name='Young', exact=True).click()
            assert cohort_cards.first.locator('.fw-kpi-sub').evaluate("""el => {
                const probe = document.createElement('span'); probe.style.color = 'var(--text-warning)';
                el.appendChild(probe); const expected = getComputedStyle(probe).color; probe.remove();
                return getComputedStyle(el).color === expected;
            }""")
            page.mouse.move(0, 0)
            page.evaluate('window.scrollTo(0,0)')
            # Card theme transitions must finish before recording the actual paint.
            page.wait_for_function("""() => {
                const probe=document.createElement('i'); probe.style.backgroundColor='var(--bg-card)';
                document.body.appendChild(probe);
                const target=getComputedStyle(probe).backgroundColor; probe.remove();
                return getComputedStyle(document.querySelector('.fw-kpi-card')).backgroundColor===target;
            }""")
            page.screenshot(animations="disabled", path=str(EVIDENCE / f"{theme.replace(' ', '-')}-{width}.png"), full_page=True)
        assert page.locator('.fw-kpi-state-note').count() <= count
        note_ids = page.locator('.fw-kpi-state-note').evaluate_all('nodes => nodes.map(n => n.id)')
        assert len(note_ids) == len(set(note_ids))
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        browser.close()
