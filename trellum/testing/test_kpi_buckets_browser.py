"""Genuine native reports, plus explicitly separate metadata fault probes."""

from __future__ import annotations

import base64
import csv
import dataclasses
import importlib.util
import json
import mimetypes
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from trellum.assets import load_js
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_calendar_coverage import ROOT, example
from trellum.themes import THEME_REGISTRY
from trellum.validation import validate_report
from trellum.validation.checks.calendar_coverage import _check_calendar_coverage
from trellum.validation.result import ValidationResult
from trellum.validation.walk import _all_components

BASELINE = "529c253509d86840b4a9647374c67bf89865046a"


def build(output, historical=False):
    output.mkdir(parents=True, exist_ok=True)
    m = example()
    if historical:
        source = subprocess.check_output(
            ["git", "show", f"{BASELINE}:trellum/components/charts/bar.py"], cwd=ROOT
        )
        path = output / "historical_bar.py"
        path.write_bytes(source)
        spec = importlib.util.spec_from_file_location("kpi_historical_bar", path)
        old = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = old
        spec.loader.exec_module(old)

        def bar(*args, **kwargs):
            kwargs.pop("bucket_context", None)
            return old.BarChart(*args, **kwargs)

        m.BarChart = bar
    ctx = ReportContext(
        config={
            "name": "Native KPI bucket calendar",
            "studio": "shared",
            "category": "Test",
            "description": "Deterministic acquisition cohorts by platform and daily weekly monthly quarterly UTC calendar buckets, with eight native additive and ratio trends.",
            "theme": "trellum light",
        },
        slug="kpi-buckets",
        output_dir=str(output),
        theme=THEME_REGISTRY["trellum light"],
    )
    m.add_sections(ctx)
    details = compute_details(ctx)
    native = validate_report(ctx, details=details)
    own = ValidationResult(ctx.slug)
    _check_calendar_coverage(_all_components(ctx), own)
    assert native.summary["fail"] == own.summary["fail"] == 0, [
        dataclasses.asdict(c) for c in native.checks + own.checks
    ]
    assert native.summary["warn"] == 0, native.to_dict()
    assert "calendar_coverage" in native.groups_run
    native.write(str(output))
    (output / "coverage-validation.json").write_text(json.dumps(dataclasses.asdict(own), indent=2))
    (output / "native-details.json").write_text(json.dumps(details, indent=2, default=str))
    render_report(ctx, str(output), auto_refresh=False, validation=native, details=details)
    copy_vendor_tree(output)
    if historical:
        html = (output / "index.html").read_text()
        html = html.replace(
            load_js("components/chart_base.js"),
            subprocess.check_output(
                ["git", "show", f"{BASELINE}:trellum/static/js/components/chart_base.js"], cwd=ROOT
            ).decode(),
        )
        (output / "index.html").write_text(html)
    return ctx


def open_report(page, output):
    def serve(route):
        path = output / unquote(urlparse(route.request.url).path).lstrip("/")
        route.fulfill(
            body=path.read_bytes(),
            content_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
        ) if path.is_file() else route.fulfill(status=404, body="missing fixture")

    page.route("**/*", serve)
    page.goto("http://kpi.test/index.html", wait_until="networkidle")
    page.evaluate("document.fonts.ready")


def visible(page, selector):
    return page.locator(selector).filter(visible=True)


def reveal(page):
    for canvas in visible(page, "canvas").all():
        canvas.scroll_into_view_if_needed()
        page.wait_for_function("id=>!!window._chartInstances[id]", arg=canvas.get_attribute("id"))
    page.evaluate("Object.values(window._chartInstances).forEach(c=>{c.stop();c.update('none')})")


def choose(page, grain=None, horizon=None, variant="on"):
    for kind, value in (
        ("grain", grain.title() if grain else None),
        ("horizon", f"D{horizon}" if horizon else None),
    ):
        if value:
            page.locator(f'[data-toggle-id="kpi_buckets_{variant}_{kind}"] button').get_by_text(
                value, exact=True
            ).click()
    reveal(page)


def states(page):
    ids = visible(page, "canvas").evaluate_all("els=>els.map(e=>e.id)")
    return page.evaluate(
        "ids=>ids.map(id=>{const c=window._chartInstances[id];return {id,title:c.canvas.dataset.fwTitle,height:c.height,width:c.width,labels:c.data.labels,data:c.data.datasets.map(d=>d.data),calendar:c.canvas._fwCalendarCoverage||null}})",
        ids,
    )


def note(page):
    el = visible(page, ".fw-calendar-coverage")
    el.scroll_into_view_if_needed()
    page.wait_for_function(
        "id=>document.getElementById(id)?._fwCoverageState?.ready", arg=el.get_attribute("id")
    )
    return el


def test_native_diagnostics_are_recorded(tmp_path):
    build(tmp_path)
    assert (tmp_path / "coverage-validation.json").is_file()
    assert len(json.loads((tmp_path / "data.json").read_text())["components"]) > 192


def exercise(tmp_path, width):
    from PIL import Image, ImageChops
    from playwright.sync_api import sync_playwright

    artifacts = Path(os.environ.get("KPI_BUCKET_ARTIFACTS", str(tmp_path))) / str(width)
    output = artifacts / "native"
    baseline = artifacts / "historical"
    build(output)
    build(baseline, True)
    evidence = artifacts / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": width, "height": 900}, device_scale_factor=1, accept_downloads=True
        )
        old = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        open_report(page, output)
        open_report(old, baseline)
        reveal(page)
        reveal(old)
        off_pixels = visible(page, "canvas").evaluate_all("els=>els.map(e=>e.toDataURL())")
        off = states(page)
        historic = states(old)
        assert [(s['labels'], s['data'], s['height'], s['width']) for s in off] == [
            (s['labels'], s['data'], s['height'], s['width']) for s in historic
        ]
        for i, (a, b) in enumerate(
            zip(visible(page, "canvas").all(), visible(old, "canvas").all())
        ):
            a.screenshot(path=str(evidence / f"default-{i}.png"))
            b.screenshot(path=str(evidence / f"historical-{i}.png"))
            assert (
                ImageChops.difference(
                    Image.open(evidence / f"default-{i}.png").convert("RGB"),
                    Image.open(evidence / f"historical-{i}.png").convert("RGB"),
                ).getbbox()
                is None
            )
        page.get_by_role("tab", name="Calendar context", exact=True).click()
        reveal(page)
        note(page)
        on = states(page)
        assert [(s['labels'], s['data'], s['height'], s['width']) for s in off] == [
            (s['labels'], s['data'], s['height'], s['width']) for s in on
        ]
        for i, (a, s) in enumerate(zip(visible(page, "canvas").all(), on)):
            a.screenshot(path=str(evidence / f"context-{i}.png"))
            assert a.evaluate("e=>e.toDataURL()") == off_pixels[i]
        oracle = {
            "daily": (104, 0, 1, 1, 1, 1),
            "weekly": (16, 2, 3, 3, 7, 7),
            "monthly": (4, 2, 19, 23, 30, 30),
            "quarterly": (2, 2, 19, 85, 91, 92),
        }
        for grain in oracle:
            for horizon in (1, 7, 30):
                choose(page, grain, horizon)
                el = note(page)
                s = el.evaluate("e=>e._fwCoverageState")
                b = s['buckets']
                count, partial, first, last, full_first, full_last = oracle[grain]
                assert (
                    len(b),
                    sum(x['partial'] for x in b),
                    b[0]['selected'],
                    b[-1]['selected'],
                    b[0]['days'],
                    b[-1]['days'],
                ) == (count, partial, first, last, full_first, full_last)
                assert sum(x['observed'] for x in b) == 104 and sum(x['missing'] for x in b) == 0
                assert all(x['mature'] == x['selected'] for x in b)
                chart_states = states(page)
                assert len(chart_states) == 8
                for chart in chart_states:
                    assert chart['calendar'] == s
                revenue = {1: 20, 7: 30, 30: 60}[horizon]
                retained = {1: 16, 7: 12, 30: 8}[horizon]
                deposits = {1: 3, 7: 5, 30: 7}[horizon]
                for chart, multiplier in zip(
                    chart_states,
                    (50, 40, 1.25, revenue, revenue / 50, retained / 40, revenue / 40, deposits),
                ):
                    expected = [
                        multiplier if i in (2, 4, 5, 6) else x['selected'] * multiplier
                        for x in b
                        for i in [chart_states.index(chart)]
                    ]
                    assert chart['data'][0] == pytest.approx(expected)
        choose(page, "weekly", 7)
        chart = states(page)[0]
        cid = chart['id']
        canvas = page.locator('#' + cid)
        canvas.scroll_into_view_if_needed()
        point = page.evaluate(
            "id=>{const c=window._chartInstances[id],p=c.getDatasetMeta(0).data[0].getCenterPoint(),r=c.canvas.getBoundingClientRect();return {x:r.x+p.x,y:r.y+p.y}}",
            cid,
        )
        page.mouse.move(point['x'], point['y'])
        page.wait_for_selector('.fw-tt-title:visible')
        text = page.locator('.fw-tt-title:visible').inner_text()
        assert (
            '2026-06-08 to 2026-06-14' in text
            and '2026-06-12 to 2026-06-14' in text
            and '3/7' in text
        )
        assert page.locator('.fw-calendar-tooltip').evaluate(
            'el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight}'
        )
        assert '$150' in page.locator('.fw-calendar-tooltip .fw-tt-line').inner_text()
        assert page.evaluate(
            "Chart.defaults.interaction.mode==='nearest' && Chart.defaults.interaction.intersect===false"
        )
        page.screenshot(path=str(evidence / f"hover-{width}.png"))
        # Actual native CSV button; ratios retain raw fraction units.
        for i, expected in ((0, 150), (2, 1.25), (4, 0.6)):
            chart = states(page)[i]
            button = page.locator(f'[data-csv-chart="{chart["id"]}"]')
            button.scroll_into_view_if_needed()
            with page.expect_download() as dl:
                button.click()
            path = evidence / f"weekly-{i}.csv"
            dl.value.save_as(path)
            csv_rows = list(csv.reader(path.read_text().splitlines()))
            assert len(csv_rows) == 17 and csv_rows[1][0] == '2026-06-08'
            assert float(csv_rows[1][1]) == pytest.approx(expected)
        # Native date-range controls and platform toggle, with public state confirmation.
        page.locator('.fw-date-trigger').click()
        date_inputs = page.locator('input[type="date"]')
        assert date_inputs.count() == 2
        for control in date_inputs.all():
            control.fill('2026-07-09')
            control.dispatch_event('change')
        page.locator('.fw-date-trigger').click()
        reveal(page)
        s = note(page).evaluate('e=>e._fwCoverageState')
        assert len(s['buckets']) == 1
        b = s['buckets'][0]
        assert (b['start'], b['end'], b['selected'], b['days']) == (
            '2026-07-06',
            '2026-07-12',
            1,
            7,
        )
        for grain, days in (("monthly", 31), ("quarterly", 92)):
            choose(page, grain)
            b = note(page).evaluate('e=>e._fwCoverageState.buckets[0]')
            assert b['selected'] == 1 and b['days'] == days
        platform = (
            page.locator('[data-fw-kind="FilterBar"]') if False else page.locator('.fw-filter-bar')
        )
        platform.get_by_text('iOS', exact=True).click()
        reveal(page)
        assert states(page)[0]['data'][0] == [20]
        assert note(page).evaluate('e=>e._fwCoverageState.buckets[0].observed') == 1
        choose(page, 'daily', 30)
        assert [x['data'][0][0] for x in states(page)] == pytest.approx(
            [20, 10, 2, 30, 1.5, 0.2, 3, 3]
        )
        platform.get_by_text('Android', exact=True).click()
        reveal(page)
        assert [x['data'][0][0] for x in states(page)] == pytest.approx(
            [30, 30, 1, 30, 1, 0.2, 1, 4]
        )
        platform.get_by_text('iOS', exact=True).click()
        choose(page, 'weekly', 7)
        restored = page.url
        page.reload(wait_until='networkidle')
        reveal(page)
        # Report-local toggles are restored by native session state; URL restores filters.
        page.get_by_role('tab', name='Calendar context', exact=True).click()
        choose(page, 'weekly', 7)
        assert states(page)[0]['data'][0] == [20]
        # Separate native source fault probes. These do not repair historic chart sums.
        probe = page.evaluate("""() => {
            const e=window._fwFilterEngine,d=Object.values(window._reportData.components).find(c=>c.bucket_context).bucket_context;
            e.clearAllFilters(d.dataset_id); const raw=e.getFiltered(d.dataset_id).map(r=>({...r}));
            const out={}; function calc(key,rs){e.init(d.dataset_id,rs);out[key]=window._fwCalendarCoverage.compute(d);}
            calc('removed',raw.filter(r=>r.day!=='2026-07-09'));
            calc('duplicate',raw.concat({...raw[0]}));
            calc('missingMetric',raw.map((r,i)=>i===0?{...r,cost:null}:r));
            calc('zero',raw.map((r,i)=>i===0?{...r,cost:0}:r));
            calc('restored',raw); return out;
        }""")
        assert sum(b['missing'] for b in probe['removed']['buckets']) == 1
        assert sum(b['observed'] for b in probe['duplicate']['buckets']) == 104
        assert sum(b['inputMissing'] for b in probe['missingMetric']['buckets']) == 1
        assert sum(b['inputMissing'] for b in probe['zero']['buckets']) == 0
        # Restore native controls and source together after deliberate fault probes.
        page.reload(wait_until='networkidle')
        reveal(page)
        page.get_by_role('tab', name='Calendar context', exact=True).click()
        choose(page, 'weekly', 7)
        # Re-render, light/dark and native reveal; subscriptions stay bounded.
        counts = []
        for theme in ('trellum dark', 'trellum light', 'trellum dark'):
            page.evaluate("theme=>{window.switchTheme(theme);window.renderAll()}", theme)
            reveal(page)
            note(page)
            counts.append(
                page.evaluate(
                    "Object.values(window._fwFilterEngine._subscribers).reduce((n,a)=>n+a.length,0)"
                )
            )
            page.screenshot(
                path=str(evidence / f"native-{width}-{theme.split()[1]}.png"), full_page=True
            )
        assert max(counts) == min(counts)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        # Native section snapshot includes the visible shared note, not hover dates.
        section = note(page).locator(
            'xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," fw-section ")][1]'
        )
        assert section.count() == 1
        section.scroll_into_view_if_needed()
        reveal(page)
        capture = section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
        (evidence / f'native-section-{width}.png').write_bytes(
            base64.b64decode(capture['image']['data_base64'])
        )
        for kind in ('PNG', 'PDF'):
            with page.expect_download() as download:
                page.evaluate('kind=>fw["export"+kind]()', kind)
            download.value.save_as(evidence / f'native-report-{width}.{kind.lower()}')
        lifecycle = page.evaluate('''() => {
            const e=window._fwFilterEngine,canvas=Array.from(document.querySelectorAll('canvas')).find(el=>el.offsetWidth&&window._reportData.components[el.id]?.bucket_context);
            const cfg=window._reportData.components[canvas.id],d=cfg.bucket_context,raw=e.getFilteredExcluding(d.dataset_id,['day','platform']).map(r=>({...r}));
            e.removeDataset(d.dataset_id);
            const missing=canvas._fwCalendarCoverage;
            e.init(d.dataset_id,raw);e.setFilter(d.dataset_id,'probe','platform','equals','iOS');
            const value=window._chartInstances[canvas.id].data.datasets[0].data[0];
            const replacement=canvas.cloneNode(true);canvas.replaceWith(replacement);window._fwRenderers.chart(replacement.id,cfg);
            delete cfg.bucket_context;window._fwRenderers.chart(replacement.id,cfg);
            const removed=!replacement._fwCalendarCoverage&&!replacement._fwCalendarDispose;
            cfg.bucket_context=d;window._fwRenderers.chart(replacement.id,cfg);
            const adopted=!!replacement._fwCalendarCoverage;
            const el=Array.from(document.querySelectorAll('.fw-calendar-coverage')).find(e=>e.offsetWidth);
            const nc=window._reportData.components[el.id];delete window._reportData.components[el.id];
            window._fwCalendarCoverage.reconcile(window._reportData.components);
            const noteRemoved=!el._fwCoverageDispose&&!el._fwCoverageState&&!el.textContent;
            window._reportData.components[el.id]=nc;window._fwRenderers.calendar_coverage(el.id,nc);
            return {missing:missing.status,value,removed,adopted,noteRemoved};
        }''')
        assert lifecycle == {
            'missing': 'unavailable',
            'value': 60,
            'removed': True,
            'adopted': True,
            'noteRemoved': True,
        }
        (evidence / 'lifecycle.json').write_text(json.dumps(lifecycle, indent=2))
        (evidence / 'fault-probes.json').write_text(json.dumps(probe, indent=2))
        (evidence / 'inspection.json').write_text(
            json.dumps(
                {
                    'viewport': width,
                    'url': restored,
                    'handler_counts': counts,
                    'hover': text,
                    'errors': errors,
                },
                indent=2,
            )
        )
        assert not errors
        old.close()
        page.close()
        browser.close()
    print(f'Native KPI artifacts: {evidence}')


def test_kpi_buckets_chromium_desktop(tmp_path):
    exercise(tmp_path, 1280)


@pytest.mark.heavy
def test_kpi_buckets_chromium_phone_resources(tmp_path):
    exercise(tmp_path, 390)


def test_native_parent_scope_reentry_and_source_readiness(tmp_path):
    """A second genuine report checks one optional scope, not a renderer harness."""
    from playwright.sync_api import sync_playwright

    from trellum.components import BarChart, DataSource, FilterBar, ScopedDataSource

    m = example()
    frame = m.facts()
    output = tmp_path / "scoped-native"
    output.mkdir()
    ctx = ReportContext(
        config={
            "name": "Scoped calendar example",
            "studio": "shared",
            "category": "Test",
            "description": "A shared acquisition snapshot with independent native scoped platform filters and UTC weekly calendar coverage.",
            "theme": "trellum light",
        },
        slug="scoped-calendar",
        output_dir=str(output),
        theme=THEME_REGISTRY["trellum light"],
    )
    for key in ("First", "Second"):
        ctx.set_scope(key)
        source = m.SOURCE + "_" + key.lower()
        ctx.add_section(
            "Original dates",
            [
                DataSource(source, frame),
                FilterBar(source, frame, filters=[{"column": "day", "type": "date_range"}]),
            ],
        )
        ds = "kpi_scope_" + key.lower()
        coverage = dataclasses.replace(m.declaration(frame, "weekly", 7), dataset_id=ds)
        ctx.add_section(
            key + " scoped calendar",
            [
                ScopedDataSource(ds, parent=source),
                FilterBar(ds, frame, filters=[{"column": "platform", "type": "toggle"}]),
                coverage,
                BarChart(
                    frame,
                    "weekly_bucket",
                    "cost",
                    "Scoped cost",
                    dataset_id=ds,
                    bucket_context=coverage,
                ),
            ],
        )
    details = compute_details(ctx)
    validation = validate_report(ctx, details=details)
    own = ValidationResult(ctx.slug)
    _check_calendar_coverage(_all_components(ctx), own)
    assert validation.summary['warn'] == validation.summary['fail'] == own.summary['fail'] == 0, (
        validation.checks
    )
    validation.write(str(output))
    (output / "coverage-validation.json").write_text(json.dumps(dataclasses.asdict(own), indent=2))
    render_report(ctx, str(output), auto_refresh=False, validation=validation, details=details)
    copy_vendor_tree(output)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        open_report(page, output)
        reveal(page)
        assert states(page)[0]['data'][0][0] == 150
        visible(page, '.fw-filter-bar').get_by_text('iOS', exact=True).click()
        reveal(page)
        assert states(page)[0]['data'][0][0] == 60
        page.locator('[data-toggle-id="__scope__"] button').get_by_text(
            'Second', exact=True
        ).click()
        reveal(page)
        assert page.evaluate('window._currentScope') == 'Second'
        assert states(page)[0]['data'][0][0] == 150
        visible(page, '.fw-filter-bar').get_by_text('Android', exact=True).click()
        reveal(page)
        assert states(page)[0]['data'][0][0] == 90
        page.locator('[data-toggle-id="__scope__"] button').get_by_text('First', exact=True).click()
        reveal(page)
        assert states(page)[0]['data'][0][0] == 60
        # Parent's native daily range intersects the scoped platform population.
        page.locator('.fw-date-trigger:visible').click()
        for control in visible(page, 'input[type=date]').all():
            control.fill('2026-07-09')
            control.dispatch_event('change')
        page.locator('.fw-date-trigger:visible').click()
        reveal(page)
        assert states(page)[0]['data'][0] == [20]
        assert note(page).evaluate('el=>el._fwCoverageState.buckets[0].selected') == 1
        # Structural replacement of the parent must not strand a cached child.
        result = page.evaluate('''() => {
            const e=window._fwFilterEngine,source='showcase_kpi_bucket_facts_first';
            const columnar=e._columnar[source];
            const raw=e.getFilteredExcluding(source,['day']).map(r=>({...r,cost:r.cost+1}));
            e.init(source,raw);
            const canvas=Array.from(document.querySelectorAll('canvas')).find(c=>c.offsetWidth);
            const cost=window._chartInstances[canvas.id].data.datasets[0].data[0];
            const note=Array.from(document.querySelectorAll('.fw-calendar-coverage')).find(el=>el.offsetWidth);
            const active=window._scopeCache[window._currentScope],nc=active[note.id];
            active[note.id]={...nc,coverage:{...nc.coverage,caption:'Replacement coverage'}};
            window._fwCalendarCoverage.reconcile(active);
            const invalidated=!note._fwCoverageState&&note.dataset.coverageReady==='false';
            window._fwRenderers.calendar_coverage(note.id,active[note.id]);
            const adopted=note.textContent.includes('Replacement coverage');
            e.removeDataset(source);
            const error=window._fwCalendarCoverage.captureError(document.querySelector('.fw-container'),window._scopeCache[window._currentScope]);
            return {cost,error,invalidated,adopted};
        }''')
        assert result['cost'] == 21 and 'resolving' in result['error']
        assert result['invalidated'] and result['adopted']
        assert not errors
        page.close()
        browser.close()
