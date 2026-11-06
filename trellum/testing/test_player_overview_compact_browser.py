"""Actual business generator, native assets/controls and baseline visual parity."""
from __future__ import annotations

import base64
import csv
import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from trellum.testing.test_player_overview_compact import TRENDS, player_builds  # noqa: F401


def _serve(route, outputs, missing):
    url = urlparse(route.request.url)
    parts = Path(unquote(url.path).lstrip("/")).parts
    if url.hostname == "player.test" and parts and parts[0] == "_vendor":
        path = outputs["after"] / Path(*parts)
        if path.is_file():
            route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0]
                          or "application/octet-stream")
            return
    if url.hostname == "player.test" and parts and parts[0] in outputs:
        path = outputs[parts[0]] / Path(*parts[1:])
        if path.is_file():
            route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0]
                          or "application/octet-stream")
            return
    missing.append(route.request.url)
    route.fulfill(status=404, body="Missing fixture asset")


def _hydrate(page):
    # The genuine report lazily renders below-fold charts; visit all canvases.
    for canvas in page.locator("canvas").all():
        if canvas.is_visible():
            canvas.scroll_into_view_if_needed()
            page.wait_for_function("id => !!Chart.getChart(document.getElementById(id))",
                                   arg=canvas.get_attribute("id"))
    page.wait_for_function("fw.filterEngine.getFiltered('cohorts').length === 144")
    page.wait_for_function("Object.values(Chart.instances).every(chart => !Chart.animator.running(chart))")


def _kpis(page):
    return page.locator(".fw-kpi-grid").evaluate_all("""grids => grids.map(grid => {
        const origin = grid.getBoundingClientRect();
        return Array.from(grid.querySelectorAll('.fw-kpi-card')).map(card => {
            const box = card.getBoundingClientRect();
            return {text: card.innerText, x: box.x - origin.x, y: box.y - origin.y,
                    width: box.width, height: box.height};
        });
    })""")


def _chart_snapshot(page, excluded=()):
    return page.locator("canvas").evaluate_all("""(canvases, excluded) => {
        const result = {};
        canvases.forEach(canvas => {
            const chart = Chart.getChart(canvas);
            if (!chart || excluded.includes(canvas.dataset.fwTitle)) return;
            result[canvas.dataset.fwTitle || canvas.id] = {
                height: canvas.getBoundingClientRect().height,
                type: chart.config.type,
                labels: chart.data.labels,
                datasets: chart.data.datasets.map(ds => ({label: ds.label,
                    data: ds.data.map(v => v && v.feature ?
                        {country: v.feature.properties.name, value: v.value} : v)}))
            };
        });
        return result;
    }""", list(excluded))


def _selected(frames, platform=None, start="2026-10-07", end="2026-11-06"):
    df = frames["main"]
    return df[(df.event_date >= start) & (df.event_date <= end)
              & (True if platform is None else df.platform == platform)]


def _check_trends(page, selected):
    expected = selected.groupby("event_date", sort=True).sum(numeric_only=True)
    for column, title, fmt in TRENDS:
        canvas = page.locator(f'#sec-daily-trends canvas[data-fw-title="{title}"]')
        canvas.scroll_into_view_if_needed()
        chart_id = canvas.get_attribute("id")
        page.wait_for_function(
            "([id, values]) => { const data = window._chartInstances[id]?.data.datasets[0].data;"
            " return data?.length === values.length && data.every((v,i) => Math.abs(v-values[i]) < 1e-7); }", arg=[chart_id, expected[column].tolist()])
        state = page.evaluate("""id => {
            const canvas = document.getElementById(id), chart = window._chartInstances[id];
            const summary = document.querySelector('[data-chart-summary="' + id + '"]');
            return {labels: chart.data.labels, data: chart.data.datasets[0].data,
                    height: canvas.getBoundingClientRect().height,
                    boxHeight: canvas.closest('.fw-chart-container').getBoundingClientRect().height,
                    summary: summary.querySelector('.fw-chart-summary-value').textContent,
                    label: summary.querySelector('.fw-chart-summary-label').textContent,
                    title: canvas.closest('.fw-grid-item').querySelector('.fw-chart-title').textContent};
        }""", chart_id)
        assert state["labels"] == expected.index.tolist()
        assert state["data"] == pytest.approx(expected[column].tolist())
        assert state["height"] == state["boxHeight"] == 160
        assert state["label"] == "Daily avg" and state["title"] == title
        # Independently calculate the statistic; use the native formatter only
        # for its public compact number/currency presentation.
        mean = float(expected[column].mean())
        formatted = page.evaluate("([fmt, n]) => fw.getFormatter(fmt)(n)", [fmt, mean])
        assert state["summary"] == formatted


def _dates(page, start=None, end=None, preset=None, dataset="main"):
    bar = page.locator(f'.fw-filter-bar[data-fw-ds-id="{dataset}"]')
    bar.locator(".fw-date-trigger").click()
    if preset:
        bar.get_by_role("button", name=preset, exact=True).click()
    else:
        fields = bar.locator(".fw-date-input")
        fields.nth(0).fill(start)
        fields.nth(0).dispatch_event("change")
        fields.nth(1).fill(end)
        fields.nth(1).dispatch_event("change")
    page.keyboard.press("Escape")


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_player_overview_native_controls_values_geometry_and_assets(player_builds, width):
    from playwright.sync_api import expect, sync_playwright

    builds, frames = player_builds
    outputs = {name: build["output"] for name, build in builds.items()}
    snapshots = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for variant in ("before", "after"):
                page = browser.new_page(viewport={"width": width, "height": 900})
                errors, missing = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda msg: errors.append(msg.text)
                        if msg.type in ("error", "warning") else None)
                page.route("**/*", lambda route: _serve(route, outputs, missing))
                page.goto(f"http://player.test/{variant}/index.html", wait_until="networkidle")
                page.evaluate("document.fonts.ready")
                page.wait_for_function("document.querySelector('#sec-overview .fw-kpi-value')")
                assert page.locator("#sec-overview .fw-kpi-card").count() == 8
                for title in ("Engagement", "Revenue"):
                    section = page.locator(f"#sec-{title.lower()}")
                    assert ("collapsed" in (section.get_attribute("class") or "")) is (variant == "after")
                _hydrate(page)
                page.evaluate("scrollTo(0, 0)")
                page.screenshot(path=str(outputs[variant] / f"top-{width}.png"))
                page.screenshot(path=str(outputs[variant] / f"default-{width}.png"), full_page=True)
                # All eight cards retain their original page position too.
                headline = page.locator("#sec-overview .fw-kpi-grid").bounding_box()
                states = {"default": _kpis(page), "headline": headline}
                if variant == "after":
                    _check_trends(page, _selected(frames))
                    grid = page.locator("#sec-daily-trends .fw-grid")
                    assert grid.evaluate("el => getComputedStyle(el).gridTemplateColumns.split(' ').length") == (
                        3 if width == 1280 else 1)
                    # A wrapped title must leave the canvas fixed at 160px.
                    title = grid.locator(".fw-chart-title").first
                    original = title.inner_text()
                    title.evaluate("el => el.textContent += ' — selected daily activity across all titles and countries'")
                    assert grid.locator("canvas").first.bounding_box()["height"] == 160
                    title.evaluate("(el, text) => el.textContent = text", original)
                    page.locator("#sec-daily-trends").screenshot(
                        path=str(outputs[variant] / f"trends-{width}.png"))
                # Open the real detailed sections; all original chart instances
                # and their native options remain reachable after disclosure.
                if variant == "after":
                    for title in ("Engagement", "Revenue"):
                        page.locator(f"#sec-{title.lower()} .fw-collapsible-toggle").click()
                _hydrate(page)
                states["charts"] = _chart_snapshot(page, [title for _, title, _ in TRENDS])
                assert page.locator("#rmMap").is_visible()
                assert page.locator("#psVenn").is_visible()
                assert "28" in page.locator("#psStats").inner_text()
                bar = page.locator('.fw-filter-bar[data-fw-ds-id="main"]')
                bar.get_by_role("button", name="ios", exact=True).click()
                page.wait_for_function("fw.filterEngine.getFiltered('main').every(r => r.platform === 'ios')")
                states["ios"] = _kpis(page)
                states["ios_charts"] = _chart_snapshot(page, [title for _, title, _ in TRENDS])
                if variant == "after":
                    _check_trends(page, _selected(frames, "ios"))
                _dates(page, "2026-10-24", "2026-11-02")
                page.wait_for_function("fw.filterEngine.getFiltered('main').length === 80")
                states["narrow"] = _kpis(page)
                states["narrow_charts"] = _chart_snapshot(page, [title for _, title, _ in TRENDS])
                if variant == "after":
                    _check_trends(page, _selected(frames, "ios", "2026-10-24", "2026-11-02"))
                    # Download the actual native chart CSV under both controls.
                    canvas = page.locator('#sec-daily-trends canvas[data-fw-title="IAP Revenue"]')
                    with page.expect_download() as download:
                        page.locator(f'[data-csv-chart="{canvas.get_attribute("id")}"]').click()
                    path = outputs[variant] / f"iap-selected-{width}.csv"
                    download.value.save_as(path)
                    with path.open(newline="", encoding="utf-8-sig") as file:
                        rows = list(csv.reader(file))
                    expected = _selected(frames, "ios", "2026-10-24", "2026-11-02").groupby(
                        "event_date").iap_revenue.sum()
                    assert len(rows) == 11 and rows[0] == ["label", "iap_revenue"]
                    assert [r[0] for r in rows[1:]] == expected.index.tolist()
                    assert [float(r[1]) for r in rows[1:]] == pytest.approx(expected.tolist())
                for theme in ("trellum light", "trellum dark"):
                    page.locator("#fwThemeSelect").select_option(theme)
                    expect(page.locator("html")).to_have_attribute("data-theme", theme)
                    assert _kpis(page) == states["narrow"]
                    if variant == "after":
                        _check_trends(page, _selected(frames, "ios", "2026-10-24", "2026-11-02"))
                        page.locator("#sec-daily-trends").screenshot(
                            path=str(outputs[variant] / f"trends-{width}-{theme.replace(' ', '-')}.png"))
                bar.get_by_role("button", name="All", exact=True).first.click()
                _dates(page, preset="30D")
                page.wait_for_function("fw.filterEngine.getFiltered('main').length === 496")
                assert _kpis(page) == states["default"]
                # Section filters remain independently scoped. Retention's
                # supplied D1/D7 cards stay static even under its own filters.
                retention = page.locator('.fw-filter-bar[data-fw-ds-id="cohorts"]')
                retention.get_by_role("button", name="ios", exact=True).click()
                page.wait_for_function("fw.filterEngine.getFiltered('cohorts').length === 72")
                states["retention_ios"] = _kpis(page)
                assert states["retention_ios"][2][2:] == states["default"][2][2:]
                retention.get_by_role("button", name="All", exact=True).first.click()
                segments = page.locator('.fw-filter-bar[data-fw-ds-id="segments"]')
                segments.get_by_role("button", name="ios", exact=True).click()
                expect(page.locator("#psStats")).to_contain_text("14")
                states["venn_ios"] = page.locator("#psStats").inner_text()
                segments.get_by_role("button", name="All", exact=True).click()
                assert _kpis(page) == states["default"]
                page.evaluate("scrollTo(0, 0)")
                page.screenshot(path=str(outputs[variant] / f"expanded-{width}.png"), full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert errors == [] and missing == []
                snapshots[variant] = states
                page.close()
            assert snapshots["before"] == snapshots["after"]
            (outputs["after"] / f"parity-{width}.json").write_text(json.dumps(snapshots, indent=2))
        finally:
            browser.close()


@pytest.mark.heavy
def test_player_overview_native_section_capture_and_embed_png_pdf(player_builds):
    from playwright.sync_api import sync_playwright

    builds, _ = player_builds
    outputs = {name: build["output"] for name, build in builds.items()}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors, missing = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.route("**/*", lambda route: _serve(route, outputs, missing))
            page.goto("http://player.test/after/index.html", wait_until="networkidle")
            for canvas in page.locator("#sec-daily-trends canvas").all():
                canvas.scroll_into_view_if_needed()
                page.wait_for_function("id => !!window._chartInstances[id]", arg=canvas.get_attribute("id"))
            snapshot = page.locator("#sec-daily-trends").evaluate(
                "el => fw.captureElementSnapshotForAnalysis(el)")
            assert snapshot["source"]["component_id"] == "sec-daily-trends"
            (outputs["after"] / "daily-trends-section.png").write_bytes(
                base64.b64decode(snapshot["image"]["data_base64"]))
            # The existing ?only= embed has its own layout contract (including
            # omitted titles). Exercise it separately from the section capture.
            page.goto("http://player.test/after/index.html?only=sec-daily-trends", wait_until="networkidle")
            assert page.locator("body").get_attribute("class").find("fw-only") >= 0
            for canvas in page.locator("#sec-daily-trends canvas").all():
                canvas.scroll_into_view_if_needed()
                page.wait_for_function("id => !!window._chartInstances[id]", arg=canvas.get_attribute("id"))
            for extension, prefix in (("png", b"\x89PNG"), ("pdf", b"%PDF")):
                with page.expect_download(timeout=60000) as download:
                    page.evaluate(f"fw.export{extension.upper()}()")
                path = outputs["after"] / f"daily-trends-embed.{extension}"
                download.value.save_as(path)
                assert path.read_bytes().startswith(prefix) and path.stat().st_size > 10000
            assert missing == errors == []
        finally:
            browser.close()
