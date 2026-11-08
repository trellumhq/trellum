"""Real multi-scope dropdowns must not clone enhanced controls or old owners."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest
from playwright.sync_api import expect

from trellum.components import DataSource, FilterBar, KpiRow, LineChart
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_interactions import _playwright as _playwright
from trellum.testing.test_interactions import _ReportServer
from trellum.validation import validate_report


@pytest.fixture(scope="module")
def scope_report(tmp_path_factory):
    output = tmp_path_factory.mktemp("scope-filter-lifecycle")
    ctx = ReportContext(
        config={
            "name": "Scope dropdown lifecycle",
            "annotations": False,
            "description": "Compare daily synthetic values for two independent game scopes, with source and campaign filters controlling a total and a daily trend. Four fixed observations per game exercise dropdown ownership and dependent choices.",
            "studio": "Demo",
            "category": "Testing",
        },
        slug="scope-filters",
        output_dir=str(output),
    )
    for scope, label, factor in [("game_a", "Game A", 1), ("game_b", "Game B", 10)]:
        frame = pd.DataFrame(
            {
                "day": pd.date_range("2026-11-01", periods=4),
                "source": [label + " Search"] * 2 + [label + " Social"] * 2,
                "campaign": [
                    label + " " + name
                    for name in ("Search launch", "Search return", "Social launch", "Social return")
                ],
                "value": [n * factor for n in (1, 2, 3, 4)],
            }
        )
        ctx.set_scope(scope, label)
        ds = scope + "_facts"
        ctx.add_section(
            "",
            [
                DataSource(ds, frame),
                FilterBar(
                    ds,
                    frame,
                    filters=[
                        {"column": "source", "label": "Source"},
                        {"column": "campaign", "label": "Campaign", "depends_on": "source"},
                    ],
                ),
            ],
        )
        ctx.add_section(
            "Results",
            [
                KpiRow([{"label": "Total", "column": "value", "agg": "sum"}], dataset_id=ds),
                LineChart(frame, "day", "value", "Selected values", dataset_id=ds),
            ],
        )
    result = validate_report(ctx, details=compute_details(ctx))
    assert result.summary["fail"] == result.summary["warn"] == 0, [
        (c.id, c.message) for c in result.checks if c.level in ("fail", "warn")
    ]
    render_report(ctx, str(output), auto_refresh=False)
    server = _ReportServer(str(output))
    yield server.url, output
    server.shutdown()


def _popup(page, select):
    return page.locator(f'.ss-content[data-id="{select.get_attribute("data-id")}"]')


def _assert_dropdowns(page, label):
    bar = page.locator('.fw-filter-bar').first
    assert bar.locator('select.fw-dropdown').count() == 2
    assert bar.locator('.ss-main').count() == 2
    assert page.locator('.ss-content').count() == 2 + page.locator('#reference').count()
    for name, suffixes in [
        ("Source", ["Search", "Social"]),
        ("Campaign", ["Search launch", "Search return", "Social launch", "Social return"]),
    ]:
        select = bar.locator('select.fw-dropdown').nth(0 if name == "Source" else 1)
        expected = [label + " " + suffix for suffix in suffixes]
        assert select.locator('option').evaluate_all('es=>es.map(e=>e.value)') == expected
        combo = bar.locator(f'.ss-main[data-id="{select.get_attribute("data-id")}"]')
        expect(combo).to_be_visible()
        expect(combo).to_have_accessible_name(name)
        combo.click()
        content = _popup(page, select)
        expect(content).to_be_visible()
        assert content.locator('.ss-option').all_text_contents() == expected
        expect(content.get_by_role('searchbox', name='Search ' + name)).to_be_focused()
        page.keyboard.press('Escape')
        expect(combo).to_be_focused()
    return bar


def _choose(page, name, option):
    bar = page.locator('.fw-filter-bar').first
    bar.get_by_role('combobox', name=name, exact=True).click()
    select = bar.locator('select.fw-dropdown').nth(0 if name == "Source" else 1)
    _popup(page, select).get_by_role('option', name=option, exact=True).click()
    page.keyboard.press('Escape')


def _assert_selection_filters_current_data(page, label, factor):
    bar = page.locator('.fw-filter-bar').first
    _choose(page, "Source", label + " Search")
    campaign = bar.locator('select.fw-dropdown').nth(1)
    expect(campaign.locator('option')).to_have_count(2)
    assert campaign.locator('option').evaluate_all('es=>es.map(e=>e.value)') == [
        label + " Search launch",
        label + " Search return",
    ]
    expect(page.locator('.fw-kpi-value').first).to_have_text(str(3 * factor))
    _choose(page, "Campaign", label + " Search launch")
    expect(page.locator('.fw-kpi-value').first).to_have_text(str(factor))
    assert page.evaluate(
        "Object.values(_chartInstances).map(c=>c.data.datasets.map(d=>d.data))"
    ) == [[[factor]]]
    # Use the native Clear actions, including after the bar has been replaced.
    for select in [campaign, bar.locator('select.fw-dropdown').first]:
        fid = select.get_attribute('data-filter-id')
        bar.locator(f'.fw-filter-action[data-filter-id="{fid}"][data-action="clear-all"]').click()
    expect(page.locator('.fw-kpi-value').first).to_have_text(str(10 * factor))


def _switch(page, scope):
    page.locator(f'[data-scope-key="{scope}"]').click()
    page.wait_for_function(
        'scope=>window._currentScope===scope && document.querySelector("select.fw-dropdown").options[0].value.startsWith(scope === "game_a" ? "Game A" : "Game B")',
        arg=scope,
    )


def _cascade_counts(page):
    return page.evaluate(
        "Object.fromEntries(Object.entries(_fwFilterEngine._subscribers).map(([id,rows])=>[id,rows.filter(r=>r.id.startsWith('cascade_')).length]))"
    )


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_scope_switch_replaces_dropdown_widgets_and_cascades(_playwright, scope_report, width):
    url, output = scope_report
    evidence = Path(os.environ.get('TRELLUM_SCOPE_EVIDENCE', str(output)))
    evidence.mkdir(parents=True, exist_ok=True)
    page = _playwright.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(url, wait_until='networkidle')
        _assert_dropdowns(page, "Game A")
        # An independent SlimSelect must survive scoped teardown untouched.
        page.evaluate("""() => {
            const select = document.createElement('select');
            select.id = 'reference';
            select.setAttribute('aria-label', 'Reference');
            select.innerHTML = '<option>Shared reference</option>';
            document.body.appendChild(select);
            window.savedReference = new SlimSelect({select});
        }""")
        reference = page.locator('#reference')
        reference_id = reference.get_attribute('data-id')
        _assert_selection_filters_current_data(page, "Game A", 1)
        for scope, label, factor in [
            ("game_b", "Game B", 10),
            ("game_a", "Game A", 1),
            ("game_b", "Game B", 10),
        ]:
            # Replacement while the old popup is open must remove its portal.
            page.locator('.fw-filter-bar').first.get_by_role(
                'combobox', name='Source', exact=True
            ).click()
            _switch(page, scope)
            _assert_dropdowns(page, label)
            expect(page.locator('.fw-kpi-value').first).to_have_text(str(10 * factor))
            subscribers = _cascade_counts(page)
            assert subscribers.get(scope + '_facts') == 1
            assert all(n == 0 for key, n in subscribers.items() if key != scope + '_facts')
            assert reference.get_attribute('data-id') == reference_id
            assert page.evaluate(
                "savedReference.render.main.main.isConnected && savedReference.render.content.main.isConnected"
            )
            _assert_selection_filters_current_data(page, label, factor)
        # Same-scope redraws keep the current widget instead of rebuilding it.
        first = page.locator('select.fw-dropdown').first
        widget_id = first.get_attribute('data-id')
        page.evaluate('window.renderAll()')
        _assert_dropdowns(page, "Game B")
        assert first.get_attribute('data-id') == widget_id
        page.evaluate("Object.values(_chartInstances).forEach(chart=>chart.update('none'))")
        page.locator('.fw-filter-bar').first.get_by_role(
            'combobox', name='Source', exact=True
        ).click()
        expect(_popup(page, first).get_by_role('searchbox', name='Search Source')).to_be_focused()
        page.screenshot(path=str(evidence / f'scope-game_b-{width}.png'), animations='disabled')
        assert errors == []
    finally:
        page.close()


def test_late_readiness_cannot_revive_disposed_scope(_playwright, scope_report):
    page = _playwright.new_page()
    try:
        page.goto(scope_report[0], wait_until='networkidle')
        # Hold only the dropdown's readiness callback, simulating slow hydration.
        page.evaluate("""() => {
            window.lateDropdownReady = [];
            const dropdown = _fwFilterTypes.dropdown;
            const wire = dropdown.wireControls;
            dropdown.wireControls = function(el, cfg, ds, helpers) {
                const ready = _fwFilterEngine.onReady;
                if (ds === 'game_b_facts') _fwFilterEngine.onReady = (id, fn) => lateDropdownReady.push(fn);
                try { wire.call(this, el, cfg, ds, helpers); }
                finally { _fwFilterEngine.onReady = ready; }
            };
        }""")
        _switch(page, 'game_b')
        assert page.evaluate('lateDropdownReady.length') == 1
        page.evaluate(
            "window.oldDropdown = document.querySelector('select.fw-dropdown')._fwSlimSelect"
        )
        _switch(page, 'game_a')
        page.evaluate('lateDropdownReady.forEach(fn=>fn())')
        assert _cascade_counts(page)['game_b_facts'] == 0
        assert page.evaluate('oldDropdown.render.main.main.isConnected') is False
        assert page.evaluate('oldDropdown.render.content.main.isConnected') is False
        _assert_dropdowns(page, "Game A")
        _assert_selection_filters_current_data(page, "Game A", 1)
    finally:
        page.close()
