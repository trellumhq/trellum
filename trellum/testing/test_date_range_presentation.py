"""Date range presentation exercised in complete vendored framework reports."""
from __future__ import annotations

import importlib.util
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import pytest
from playwright.sync_api import expect, sync_playwright

from trellum.assets import load_js
from trellum.components.filters.date_range import DateRangeFilter
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def report_url(tmp_path_factory):
    output = tmp_path_factory.mktemp("date-presentation")
    ctx = ReportContext(config={"name": "Date range examples"}, slug="dates", output_dir=str(output))
    source = Path(__file__).parents[1] / "demo/reports/component-showcase/examples_dates.py"
    spec = importlib.util.spec_from_file_location("date_examples", source)
    examples = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(examples)
    examples.add_sections(ctx)
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    # Also exercise reload/share restoration with the ordinary single-bar
    # composition, separately from the heterogeneous multi-bar showcase.
    del ctx.sections[1:]
    render_report(ctx, str(output / "daily"), auto_refresh=False)
    copy_vendor_tree(output / "daily")

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(output)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _bar(page, name):
    return page.locator(f'.fw-filter-bar[data-fw-ds-id="showcase_dates_{name}"]')


def _open(page, url):
    page.goto(url)
    page.wait_for_function("window._fwFilterEngine && window._fwFilterEngine._state.showcase_dates_daily")
    page.wait_for_selector("#fwLoading", state="hidden")


def _state(page, name):
    return page.evaluate("""name => {
        const engine = window._fwFilterEngine, id = 'showcase_dates_' + name;
        return {rows: engine.getFiltered(id).length,
            range: Object.values(engine._state[id]).find(s => s.mode === 'range').value};
    }""", name)


def test_daily_native_edits_presets_unrelated_filter_and_url_restore(browser, report_url):
    page = browser.new_page(locale="en-US", timezone_id="America/Los_Angeles")
    try:
        _open(page, report_url)
        daily = _bar(page, "daily")
        assert _state(page, "daily") == {"rows": 8, "range": {"min": "2026-10-15", "max": "2026-10-22"}}
        expect(daily.locator('.fw-date-trigger-text')).to_have_text("Oct 15, 2026 – Oct 22, 2026")
        expect(daily.locator('.fw-date-trigger')).to_have_attribute("title", "2026-10-15 – 2026-10-22")
        daily.locator('.fw-date-trigger').click()
        daily.get_by_role("button", name="14D", exact=True).click()
        assert _state(page, "daily") == {"rows": 15, "range": {"min": "2026-10-08", "max": "2026-10-22"}}
        daily.get_by_role("button", name="7D", exact=True).click()
        daily.get_by_role("button", name="Organic", exact=True).click()
        assert _state(page, "daily")["rows"] == 4
        daily.locator('.fw-date-trigger').click()
        daily.get_by_label("Start date", exact=True).fill("2026-10-18")
        assert _state(page, "daily") == {"rows": 2, "range": {"min": "2026-10-18", "max": "2026-10-22"}}
        expect(daily.get_by_role("button", name="Organic", exact=True)).to_have_class("fw-toggle-btn active")
        daily.locator('.fw-date-preset[data-days="all"]').click()
        assert _state(page, "daily")["rows"] == 11
        # Explicit incoming links use the existing multi-bar keys and decode path.
        url = report_url + '?' + urlencode({"showcase_dates_daily.day": "2026-10-10..2026-10-14",
                                           "showcase_dates_daily.channel": "Paid"})
        _open(page, url)
        assert _state(page, "daily") == {"rows": 3, "range": {"min": "2026-10-10", "max": "2026-10-14"}}
        _open(page, report_url + 'daily/?' + urlencode({"day": "2026-10-10..2026-10-14", "channel": "Paid"}))
        page.reload()
        page.wait_for_function("window._fwFilterEngine && window._fwFilterEngine._state.showcase_dates_daily")
        assert _state(page, "daily")["rows"] == 3
        expect(_bar(page, "daily").get_by_label("Start date", exact=True)).to_have_value("2026-10-10")
        _open(page, report_url + '?' + urlencode({"showcase_dates_daily.day": "14D"}))
        assert _state(page, "daily")["rows"] == 15
    finally:
        page.close()


def test_hourly_native_pairing_exact_values_reference_disabled_and_empty(browser, report_url):
    page = browser.new_page(locale="en-GB", timezone_id="Pacific/Auckland")
    try:
        _open(page, report_url)
        hourly = _bar(page, "hourly")
        hourly.locator('.fw-date-trigger').click()
        assert hourly.locator('.fw-date-input').evaluate_all("els => els.map(e => e.dataset.edge)") == ["min", "max"]
        assert hourly.locator('.fw-date-hour').evaluate_all("els => els.map(e => e.dataset.edge)") == ["min-hour", "max-hour"]
        assert hourly.locator('.fw-date-endpoint').first.inner_text().splitlines()[:2] == ["Start date", "Start time"]
        assert hourly.locator('.fw-date-preset').evaluate_all("els => els.every(e => e.type === 'button')")
        assert hourly.get_by_label("Start time", exact=True).locator('option').count() == 48
        assert hourly.get_by_label("End time", exact=True).locator('option').last.get_attribute('value') == '23:30'
        expect(hourly.locator('.fw-date-trigger-text')).to_have_text("22 Oct 2026 08:00 – 22 Oct 2026 12:30")
        hourly.get_by_role('button', name='1H', exact=True).click()
        assert _state(page, "hourly") == {"rows": 3, "range": {"min": "2026-10-22 11:30", "max": "2026-10-22 12:30"}}
        hourly.get_by_label('Start time', exact=True).select_option('12:00')
        assert _state(page, "hourly")["rows"] == 2
        expect(hourly.locator('.fw-date-trigger')).to_have_attribute('title', '2026-10-22 12:00 – 2026-10-22 12:30')
        hourly.get_by_label('End time', exact=True).select_option('12:00')
        assert _state(page, "hourly")["rows"] == 1
        # UTC calendar formatting retains supplied clock/offset text verbatim.
        assert page.evaluate("v => window._fwFilterTypes.date_range._fmtDate(v)",
                             "2026-10-22T00:17:30+02:00") == "22 Oct 2026 00:17:30+02:00"
        older = _bar(page, "older")
        older.locator('.fw-date-trigger').click()
        expect(older.locator('.fw-date-reference')).to_have_text(
            "Presets use the latest recorded date: 22 Feb 2024. Records may have gaps.")
        expect(hourly.locator('.fw-date-trigger')).to_have_attribute('aria-expanded', 'false')
        unavailable = _bar(page, "unavailable")
        expect(unavailable.locator('.fw-date-trigger')).to_be_disabled()
        expect(unavailable.locator('.fw-date-unavailable')).to_be_visible()
        assert unavailable.locator('.fw-date-input:enabled, .fw-date-preset:enabled').count() == 0
        gaps = _bar(page, "gaps")
        gaps.locator('.fw-date-trigger').click()
        gaps.get_by_label('Start date', exact=True).fill('2026-10-10')
        gaps.get_by_label('End date', exact=True).fill('2026-10-10')
        assert _state(page, "gaps") == {"rows": 0, "range": {"min": "2026-10-10", "max": "2026-10-10"}}
        page.keyboard.press('Escape')
        page.get_by_role('link', name='10 October 2026 interval', exact=True).click()
        page.wait_for_selector('#fwLoading', state='hidden')
        assert _state(page, "gaps")["rows"] == 0
        assert page.evaluate('!!document.getElementById(decodeURIComponent(location.hash.slice(1)))')
    finally:
        page.close()


def _geometry(page):
    return page.locator('.fw-date-popover.open').evaluate("""p => {
        const r = p.getBoundingClientRect();
        return {left:r.left, right:r.right, top:r.top, bottom:r.bottom,
            width:document.documentElement.clientWidth, height:innerHeight,
            overflow:document.documentElement.scrollWidth > document.documentElement.clientWidth};
    }""")


def _assert_fits(page):
    box = _geometry(page)
    assert box['left'] >= 7 and box['right'] <= box['width'] - 7, box
    assert box['top'] >= 7 and box['bottom'] <= box['height'] - 7, box
    assert not box['overflow'], box


def _check_floating(page, report_url, phone=False):
    _open(page, report_url)
    page.locator('#fwThemeSelect').select_option('trellum dark' if phone else 'trellum light')
    hourly = _bar(page, 'hourly')
    trigger = hourly.locator('.fw-date-trigger')
    initial_height = trigger.bounding_box()['height']
    assert initial_height == 31  # Measured on the unchanged dependency baseline.
    trigger.click()
    _assert_fits(page)
    popover = hourly.locator('.fw-date-popover')
    assert trigger.get_attribute('aria-controls') == popover.get_attribute('id')
    expect(trigger).to_have_attribute('aria-expanded', 'true')
    hourly.get_by_role('button', name='1H', exact=True).focus()
    page.keyboard.press('Escape')
    expect(trigger).to_be_focused()
    expect(trigger).to_have_attribute('aria-expanded', 'false')
    trigger.click()
    if phone:
        for box in hourly.locator('.fw-date-input, .fw-date-hour, .fw-date-preset').all():
            assert box.bounding_box()['height'] >= 40
    artifacts = Path('/tmp/trellum-date-review')
    artifacts.mkdir(exist_ok=True)
    page.screenshot(path=str(artifacts / ('new-phone.png' if phone else 'new-desktop.png')))
    page.locator('.fw-section h2').first.click()
    expect(trigger).to_have_attribute('aria-expanded', 'false')
    # Real picker at each viewport edge in a 240px panel, including a low
    # viewport anchor. Only the containing report bar is repositioned.
    for side, y in [('left', 60), ('right', page.viewport_size['height'] - 65)]:
        hourly.evaluate("""(bar, pos) => {
            bar.style.cssText = 'position:fixed;z-index:500;width:240px;box-sizing:border-box;'
                + pos.side + ':0;top:' + pos.y + 'px;padding:0;margin:0';
        }""", {'side': side, 'y': y})
        trigger.click()
        _assert_fits(page)
        assert trigger.bounding_box()['height'] == initial_height
        page.keyboard.press('Escape')
    # Resizing an already open picker also reclamps it.
    trigger.click()
    page.set_viewport_size({'width': 320, 'height': 568})
    page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    _assert_fits(page)


def test_chromium_floating_keyboard_and_narrow_panel(browser, report_url):
    page = browser.new_page(viewport={'width': 1280, 'height': 900}, locale='en-US')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        _check_floating(page, report_url)
        assert errors == []
    finally:
        page.close()


@pytest.mark.heavy
def test_phone_date_fields_presets_floating_and_trigger_height(browser, report_url):
    page = browser.new_page(viewport={'width': 390, 'height': 844}, locale='en-US', is_mobile=True, has_touch=True)
    try:
        page.emulate_media(color_scheme='dark')
        _check_floating(page, report_url, phone=True)
    finally:
        page.close()


def test_irregular_native_options_and_config_contract_remain_exact(browser):
    plugin = DateRangeFilter()
    frame = pd.DataFrame({'day': pd.to_datetime(['2026-10-22 10:17', '2026-10-22 12:43'])})
    fc = plugin.build_config('dates', 'day', {}, frame)
    assert fc['min_date'] == '2026-10-22 10:17' and fc['max_date'] == '2026-10-22 12:43'
    assert plugin.build_config('strings', 'day', {}, frame.astype(str))['hourly'] is False
    page = browser.new_page(timezone_id='America/Los_Angeles')
    try:
        page.set_content(plugin.render_html('dates', 'day', 'Date', {}, frame))
        page.evaluate(load_js('components/date_range_filter.js'))
        result = page.evaluate("""fc => {
            const type = window._fwFilterTypes.date_range, calls = [];
            type.init(document.body, fc, 'dates', {urlLookup: () => undefined,
                setFilter: (...args) => calls.push(args), propagate: () => {}});
            return calls;
        }""", fc)
        assert result == [['dates', 'day', 'range', {'min': fc['min_date'], 'max': fc['max_date']}]]
        assert page.locator('.fw-date-hour').evaluate_all('els => els.map(e => e.value)') == ['00:00', '00:00']
        assert page.locator('.fw-date-trigger').get_attribute('title') == '2026-10-22 10:17 – 2026-10-22 12:43'
    finally:
        page.close()
