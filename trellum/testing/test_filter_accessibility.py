"""Generated native labels and the real plugin focus targets in Chromium."""
from __future__ import annotations

import pandas as pd
import pytest
from playwright.sync_api import expect

from trellum.components import DataSource, FilterBar
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.testing.test_interactions import _playwright as _playwright
from trellum.testing.test_interactions import _ReportServer
from trellum.testing.test_live_date_range import date_server as date_server


@pytest.fixture(scope="module")
def filter_url(tmp_path_factory):
    output = tmp_path_factory.mktemp("filter-accessibility")
    df = pd.DataFrame({"day": pd.date_range("2026-10-01 12:00", periods=4, freq="h"),
                       "region": ["East", "West", "East", "West"],
                       "value": [1, 2, 3, 4], "text": ["alpha", "beta", "alpha", "beta"]})
    filters = [
        {"type": "date_range", "column": "day", "label": "Event date"},
        {"column": "region", "label": "Region"},
        {"column": "region", "label": "Single region", "multi": False},
        {"type": "text", "column": "text", "label": "Search text"},
        {"type": "slider", "column": "value", "label": "Value"},
        {"type": "toggle", "column": "region", "label": "Platform"},
        {"type": "flag", "column": "region", "label": "Flag", "flag_value": "East"},
    ]
    ctx = ReportContext(config={"name": "Filter accessibility"}, slug="filters", output_dir=str(output))
    for scope in ("first", "second"):
        ctx.set_scope(scope)
        ctx.add_section("", [DataSource("events", df), FilterBar("events", df, filters=filters)])
        ctx.add_section("Scoped", [DataSource("scoped", df), FilterBar("scoped", df, filters=filters)])
    render_report(ctx, str(output), auto_refresh=False)
    server = _ReportServer(str(output))
    yield server.url
    server.shutdown()


def _assert_native_names(page):
    # Browser label resolution, including hidden native selects after SlimSelect.
    missing = page.locator('.fw-filter-bar input, .fw-filter-bar select').evaluate_all("""els => els.filter(e =>
        !e.id || !e.name || !(e.labels.length || e.getAttribute('aria-labelledby'))).map(e => e.outerHTML)""")
    assert missing == []
    unassociated = page.locator('.fw-text-filter, select.fw-dropdown, .fw-date-input').evaluate_all(
        "els => els.filter(e => e.labels.length !== 1 || e.labels[0].htmlFor !== e.id).map(e => e.id)")
    assert unassociated == []
    ids = page.locator('[id^=fw_c]').evaluate_all("els => els.map(e => e.id)")
    assert len(ids) == len(set(ids)), 'Generated control/label IDs must be unique'
    broken = page.locator('[aria-labelledby]').evaluate_all(r"""els => els.flatMap(e =>
        e.getAttribute('aria-labelledby').split(/\s+/).filter(id => document.querySelectorAll('[id="' + id + '"]').length !== 1))""")
    assert broken == []


def test_native_plugin_names_and_keyboard(_playwright, filter_url):
    page = _playwright.new_page(viewport={"width": 1440, "height": 1000})
    try:
        page.goto(filter_url, wait_until="networkidle")
        _assert_native_names(page)
        bars = page.locator('.fw-filter-bar')
        assert bars.count() == 2
        for bar in bars.all():
            expect(bar.get_by_role('combobox', name='Region', exact=True)).to_have_count(1)
            expect(bar.get_by_role('combobox', name='Single region', exact=True)).to_have_count(1)
            expect(bar.get_by_role('textbox', name='Search text', exact=True)).to_have_count(1)
            expect(bar.get_by_role('group', name='Value', exact=True)).to_have_count(1)
            expect(bar.get_by_role('slider', name='Value minimum')).to_have_count(1)
            expect(bar.get_by_role('slider', name='Value maximum')).to_have_count(1)
        bar = bars.first
        # Native labels move focus, and text retains its Enter-to-commit contract.
        bar.locator('label', has_text='Search text').click()
        text = bar.get_by_role('textbox', name='Search text')
        expect(text).to_be_focused()
        text.fill('alpha')
        text.press('Enter')
        assert _rows(page) == 2
        page.goto(filter_url, wait_until='networkidle')
        for name in ('Region', 'Single region'):
            combo = bar.get_by_role('combobox', name=name, exact=True)
            select = bar.locator('select.fw-dropdown').nth(0 if name == 'Region' else 1)
            bar.locator('label[for="' + select.get_attribute('id') + '"]').click()
            expect(combo).to_be_focused()
            combo.press('ArrowDown')
            search = page.get_by_role('searchbox', name='Search ' + name, exact=True).first
            expect(search).to_be_focused()
            search.fill('West')
            content = page.locator('.ss-content[data-id="' + select.get_attribute('data-id') + '"]')
            expect(content.locator('.ss-option')).to_have_count(1)
            search.press('ArrowDown')
            search.press('Enter')
            expect(combo).to_contain_text('West')
            search.press('Escape')
            expect(combo).to_be_focused()
            page.wait_for_function("window._fwFilterEngine.getFiltered('events').length === 2")
            page.evaluate("""name => {
                const s = [...document.querySelectorAll('select.fw-dropdown')].find(s =>
                    document.getElementById(s.getAttribute('aria-labelledby')).textContent === name);
                s._fwSlimSelect.setSelected([]);
            }""", name)
        trigger = bar.get_by_role('button', name='Event date', exact=False)
        trigger.press('Enter')
        expect(bar.locator('.fw-date-input[data-edge="min"]')).to_be_visible()
        expect(bar.locator('.fw-date-input[data-edge="max"]')).to_be_visible()
        expect(bar.locator('.fw-date-input[data-edge="min"]')).to_have_accessible_name('Event date Start date')
        expect(bar.get_by_role('combobox', name='Event date Start time')).to_be_visible()
        expect(bar.get_by_role('combobox', name='Event date End time')).to_be_visible()
        bar.locator('.fw-date-input[data-edge="min"]').fill('2026-10-01')
        trigger.press('Escape')
        for group_name, button in [('Platform', 'West'), ('Flag', 'Only East')]:
            group = bar.get_by_role('group', name=group_name, exact=True)
            target = group.get_by_role('button', name=button, exact=True)
            target.focus()
            target.press('Space')
            expect(target).to_have_attribute('aria-pressed', 'true')
            expect(group.get_by_role('button', pressed=True)).to_have_count(1)
            group.get_by_role('button', name='All', exact=True).press('Enter')
        handle = bar.get_by_role('slider', name='Value maximum')
        handle.focus()
        handle.press('ArrowLeft')
        assert float(handle.get_attribute('aria-valuenow')) < 4
        assert _rows(page) < 4
        page.reload(wait_until='networkidle')
        _assert_native_names(page)
        original_ids = page.locator('.fw-filter-bar [id]').evaluate_all('els => els.map(e => e.id)')
        page.evaluate("window._currentScope = 'second'; window.renderAll()")
        page.wait_for_function("!!window._scopeCache.second")
        _assert_native_names(page)
        assert page.locator('.fw-filter-bar [id]').evaluate_all('els => els.map(e => e.id)') == original_ids
        expect(bar.get_by_role('combobox', name='Region', exact=True)).to_have_count(1)
    finally:
        page.close()


def _rows(page):
    return page.evaluate("window._fwFilterEngine.getFiltered('events').length")


@pytest.mark.heavy
def test_mobile_native_and_plugin_names(_playwright, filter_url):
    page = _playwright.new_page(viewport={"width": 390, "height": 844})
    try:
        page.goto(filter_url, wait_until='networkidle')
        _assert_native_names(page)
        combo = page.get_by_role('combobox', name='Region', exact=True).first
        combo.click()
        expect(page.get_by_role('searchbox', name='Search Region', exact=True).first).to_be_focused()
        page.keyboard.press('Escape')
        expect(combo).to_be_focused()
    finally:
        page.close()


def test_live_date_labels_survive_arming(_playwright, date_server):
    page = _playwright.new_page()
    try:
        page.goto(date_server)
        page.wait_for_selector(".fw-live-bar[data-live-state='ready']")
        _assert_native_names(page)
        page.get_by_role('button', name='Date', exact=False).first.click()
        field = page.locator('.fw-date-input[data-edge="min"]')
        expect(field).to_have_accessible_name('Date Start date')
        expect(field).to_be_enabled()
        expect(field).to_be_visible()
        field.fill('2026-10-30')
        field.press('Tab')
        expect(page.locator('.fw-date-trigger')).to_contain_text('Oct 30, 2026')
        expect(page.locator('.fw-date-trigger')).to_have_attribute('title', '2026-10-30 – 2026-11-02')
    finally:
        page.close()


def test_validator_reports_blank_filter_names():
    from trellum.validation.checks.filter_accessibility import _check_filter_labels
    from trellum.validation.result import ValidationResult

    df = pd.DataFrame({"region": ["East"]})
    result = ValidationResult(slug="filters")
    bar = FilterBar("events", df, filters=[{"column": "region", "label": " "},
                                         {"column": "region"}])
    _check_filter_labels([(bar, "Scoped")], result)
    assert [c.id for c in result.checks] == ["filter-label-empty"]
    assert result.checks[0].dataset_id == "events"


@pytest.mark.parametrize("value,group_name,button", [
    ("West", "Platform", "West"), ("only", "Flag", "Only East"),
    ("exclude", "Flag", "Exclude East"),
])
def test_url_restores_pressed_state(_playwright, filter_url, value, group_name, button):
    page = _playwright.new_page()
    try:
        page.goto(filter_url + "?events.region=" + value, wait_until="networkidle")
        group = page.locator('.fw-filter-bar').first.get_by_role('group', name=group_name, exact=True)
        expect(group.get_by_role('button', name=button, exact=True)).to_have_attribute('aria-pressed', 'true')
        expect(group.get_by_role('button', pressed=True)).to_have_count(1)
        _assert_native_names(page)
    finally:
        page.close()
