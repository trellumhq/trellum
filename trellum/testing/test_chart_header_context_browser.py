"""Vendored Chromium checks for optional chart header context."""
from __future__ import annotations

import importlib.util
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from trellum.components import ComboChart, DoughnutChart, LineChart, StackedBar
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext


def _build(output: Path) -> None:
    module_path = (Path(__file__).resolve().parents[1] / "demo" / "reports"
                   / "component-showcase" / "examples_chart_context.py")
    spec = importlib.util.spec_from_file_location("examples_chart_context", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext(
        config={"name": "Chart header context", "studio": "test", "category": "Test",
                "description": "Optional authored descriptions on chart panels."},
        slug="chart-header-context", output_dir=str(output),
    )
    module.add_sections(ctx)
    daily = ctx.sections[0]["components"][0].df
    ctx.add_section("Plot height and controls remain independent", [
        LineChart(daily, "day", "orders", "No context · same plot", dataset_id="showcase_header_context_daily"),
        LineChart(daily, "day", "orders", "Context · same plot", dataset_id="showcase_header_context_daily",
                  header_context="Orders; sum of matching completed orders"),
    ])
    ctx.add_section("Escaped header text", [
        DoughnutChart(daily.groupby("channel", as_index=False)["revenue"].sum(),
                      "channel", "revenue", "Literal text", static=True,
                      header_context='<img src=x onerror="window.contextXss=true"> & "quotes"'),
    ])
    ctx.add_section("Renderer descriptions remain associated", [
        ComboChart(daily, "day", ["revenue"], ["orders"], "Combo ownership",
                   static=True, header_context="USD and order counts"),
        StackedBar(daily, "day", ["revenue"], "Stack ownership", static=True,
                   show_totals=True, header_context="USD; supplied revenue"),
    ])
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_chart_header_context_paint_filter_theme_and_geometry(tmp_path, width):
    from playwright.sync_api import sync_playwright

    _build(tmp_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)

            def serve(route):
                path = tmp_path / unquote(urlparse(route.request.url).path).lstrip("/")
                if path.is_file():
                    route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0]
                                  or "application/octet-stream")
                else:
                    route.fulfill(status=404, body="Missing test asset")

            page.route("**/*", serve)
            page.goto("http://chart-context.test/index.html", wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            for canvas in page.locator("canvas").all():
                canvas.scroll_into_view_if_needed()
                page.wait_for_function("id => !!window._chartInstances[id]", arg=canvas.get_attribute("id"))

            title = page.get_by_text("Completed orders · filtered", exact=True).last
            container = title.evaluate_handle("el => {let n=el.closest('.fw-chart-head') || el;"
                                               "n=n.nextElementSibling; while(n && !n.matches('.fw-chart-container'))"
                                               "n=n.nextElementSibling; return n;}")
            chart_id = container.evaluate("el => el.querySelector('canvas').id")
            context = page.locator(f"#{chart_id}-header-context")
            assert context.text_content() == "Orders; sum of matching completed orders"
            assert page.locator(f"#{chart_id}").get_attribute("aria-describedby").split().count(
                f"{chart_id}-header-context") == 1
            assert context.evaluate("el => getComputedStyle(el).display !== 'none'")
            assert page.evaluate("id => document.getElementById(id).toDataURL().length > 1000", chart_id)

            # Supplied and omitted options own identical plot geometry.
            no_context = page.get_by_text("No context · same plot", exact=True).last.evaluate_handle(
                "el => {let n=el.closest('.fw-chart-head') || el; n=n.nextElementSibling;"
                "while(n && !n.matches('.fw-chart-container')) n=n.nextElementSibling; return n;}")
            pair_heights = [no_context.evaluate("el => el.getBoundingClientRect().height"),
                            container.evaluate("el => el.getBoundingClientRect().height")]
            expected_height = 220
            assert pair_heights == [expected_height, expected_height]
            assert page.locator("html").evaluate("el => el.scrollWidth <= innerWidth")
            omitted_title_context = page.get_by_text(
                "Orders; summary and authored context", exact=True).last
            assert omitted_title_context.is_visible()
            hidden_title_grid = page.locator('[data-show-titles="false"]').filter(
                has=omitted_title_context)
            assert hidden_title_grid.count() == 1
            assert hidden_title_grid.locator(".fw-chart-title").is_hidden()
            assert hidden_title_grid.locator(".fw-chart-summary").is_visible()

            # Real plotted values and the existing summary change on native filtering;
            # authored context remains literal and stable.
            before = page.evaluate("id => window._chartInstances[id].data.datasets[0].data.slice()", chart_id)
            summary = page.locator(f"[data-chart-summary='{chart_id}'] .fw-chart-summary-value")
            before_summary = summary.text_content()
            page.get_by_role("button", name="Partner", exact=True).click()
            page.wait_for_function("id => window._chartInstances[id].data.datasets[0].data[0] === 52", arg=chart_id)
            after = page.evaluate("id => window._chartInstances[id].data.datasets[0].data.slice()", chart_id)
            assert after != before and summary.text_content() != before_summary
            assert context.text_content() == "Orders; sum of matching completed orders"

            # Escaped authored markup remains text; a titleless doughnut keeps context.
            doughnut_context = page.locator(".fw-doughnut .fw-chart-header-context").first
            assert doughnut_context.is_visible()
            assert page.locator(".fw-doughnut .fw-chart-header-context img").count() == 0
            literal = page.locator(".fw-doughnut .fw-chart-header-context").nth(1)
            assert literal.text_content() == '<img src=x onerror="window.contextXss=true"> & "quotes"'
            assert page.evaluate("window.contextXss !== true")
            assert page.locator(".fw-doughnut canvas").evaluate_all(
                "els => els.every(el => el.getAttribute('aria-describedby').includes(el.id + '-header-context'))")
            assert doughnut_context.evaluate("el => el.scrollWidth <= el.clientWidth")

            for title, owned_context in (("Combo ownership", "combo-context"),
                                         ("Stack ownership", "stack-label-context")):
                canvas = page.locator(f"canvas[data-fw-title='{title}']")
                chart_canvas_id = canvas.get_attribute("id")
                described = canvas.get_attribute("aria-describedby").split()
                assert described.count(f"{chart_canvas_id}-header-context") == 1
                assert any(token.endswith(owned_context) for token in described), described

            # Theme redraws and a full render keep one context node and preserve descriptions.
            select = page.locator("#fwThemeSelect")
            for theme in ("trellum dark", "trellum light"):
                select.select_option(theme)
                page.locator(f"#{chart_id}").scroll_into_view_if_needed()
                page.wait_for_function("id => !!window._chartInstances[id]", arg=chart_id)
                assert page.locator(f"#{chart_id}-header-context").count() == 1
                assert page.locator(f"#{chart_id}").get_attribute("aria-describedby").split().count(
                    f"{chart_id}-header-context") == 1
            page.evaluate("window.renderAll()")
            page.wait_for_function("id => !!window._chartInstances[id]", arg=chart_id)
            for canvas in page.locator("canvas").all():
                canvas.scroll_into_view_if_needed()
                page.wait_for_function("id => !!window._chartInstances[id]", arg=canvas.get_attribute("id"))
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(100)
            assert page.locator(f"#{chart_id}-header-context").count() == 1
            for title, owned_context in (("Combo ownership", "combo-context"),
                                         ("Stack ownership", "stack-label-context")):
                canvas = page.locator(f"canvas[data-fw-title='{title}']")
                chart_canvas_id = canvas.get_attribute("id")
                described = canvas.get_attribute("aria-describedby").split()
                assert described.count(f"{chart_canvas_id}-header-context") == 1
                assert any(token.endswith(owned_context) for token in described), described
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

            evidence = Path("/tmp") / f"trellum-chart-context-{'desktop' if width > 600 else 'phone'}.png"
            page.screenshot(path=str(evidence), full_page=True)
            assert errors == [], errors
        finally:
            browser.close()
