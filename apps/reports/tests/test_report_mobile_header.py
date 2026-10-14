"""Chromium phone layout of the console shell bar and hosted report chrome."""
from __future__ import annotations

import pytest
from django.conf import settings

STATIC = settings.BASE_DIR / "static"
FW_CSS = settings.BASE_DIR / "trellum" / "static" / "css" / "components"


def _read(path):
    return path.read_text(encoding="utf-8")


def _page() -> str:
    css = "\n".join(
        _read(p)
        for p in (
            STATIC / "console-shell.css",
            FW_CSS / "header.css",
            FW_CSS / "report_metadata.css",
        )
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>:root{{--bg-header:#0f766e;--bg-card:#181b1f;--border-color:#30363d}}{css}</style></head>
<body class="tl-console-title-known">
<div class="tl-console-shell" data-console-shell><header class="tl-console-header">
 <button class="tl-console-drawer-button" aria-label="Open navigation">M</button>
 <span class="tl-console-page-title">Player Overview</span><span class="tl-console-header-spacer"></span>
 <a class="tl-console-health-alert" href="/system" aria-label="Degraded"><svg viewBox="0 0 20 20"><path d="M10 2.5l8 14H2z"/></svg><span class="tl-console-health-text">Degraded</span></a>
 <button class="tl-console-search-button" data-console-search-toggle aria-label="Search">S</button>
 <a class="tl-console-docs-link" href="/docs" aria-label="Documentation"><svg viewBox="0 0 20 20"><path d="M4 3.5h8"/></svg><span class="tl-console-docs-label">Docs</span></a>
 <details class="tl-console-assistant-picker"><summary aria-label="AI assistant"><svg viewBox="0 0 16 16" width="16" height="16"><path d="M8 0l8 8-8 8z"/></svg><span class="tl-console-assistant-label">AI assistant</span></summary></details>
</header></div>
<div class="fw-report-chrome"><div class="fw-header"><div class="fw-header-left">Report</div>
 <div class="fw-header-right"></div></div>
 <div class="fw-report-metadata"><span class="fw-freshness visible fresh"><span class="fw-fresh-dot"></span>Updated 12m ago</span></div></div>
<div class="fw-anno-bar"><span class="fw-anno-label">Annotations:</span>
 <span class="fw-anno-toggle">Releases</span><span class="fw-anno-toggle">Incidents</span>
 <span class="fw-anno-toggle">Campaigns</span><span class="fw-anno-toggle">A/B Tests</span></div>
<script>{_read(STATIC / "report_menu.js")}</script></body></html>"""


def test_phone_header_is_compact_and_consistent():
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        pytest.skip("Playwright not installed")
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as exc:
            pytest.skip(f"chromium unavailable: {exc}")
        try:
            page = browser.new_page(viewport={"width": 412, "height": 915})
            page.route(
                "http://console.test/**",
                lambda route: route.fulfill(content_type="text/html", body=_page()),
            )
            page.goto("http://console.test/s/demo/casino/r/player-overview/index.html?display=console")
            page.evaluate(
                "window.__reportMenu.register({id:'share',label:'Share',onSelect(){}})"
            )
            page.wait_for_selector(".rmw-mobile-options")
            box = lambda sel: page.locator(sel).first.bounding_box()  # noqa: E731

            # Freshness and the labelled options/monitor actions share one row.
            fresh, options, monitor = (
                box(".fw-freshness"), box(".rmw-mobile-options"), box(".rmw-monitor-link"),
            )
            assert abs(fresh["y"] + fresh["height"] / 2 - (options["y"] + options["height"] / 2)) < 8
            assert fresh["x"] < options["x"] < monitor["x"]
            assert monitor["x"] + monitor["width"] <= 412
            assert page.locator(".rmw-mobile-options").get_attribute("aria-label") == "Report options"
            chrome = box(".fw-report-chrome")
            assert chrome["height"] <= 64

            # Header icon buttons use one neutral bordered 44px treatment.
            styles = page.evaluate(
                """() => ['.tl-console-drawer-button','.tl-console-health-alert',
                '.tl-console-search-button','.tl-console-docs-link',
                '.tl-console-assistant-picker > summary'].map(s => {
                  const e = document.querySelector(s), c = getComputedStyle(e), b = e.getBoundingClientRect();
                  return [c.borderTopWidth, c.borderTopColor, c.backgroundColor, Math.round(b.width), Math.round(b.height)];
                })"""
            )
            assert len({tuple(s) for s in styles}) == 1, styles

            # Annotation chips stay on one row and scroll sideways.
            tops = page.eval_on_selector_all(
                ".fw-anno-bar > *", "els => els.map(e => {const b = e.getBoundingClientRect(); return Math.round(b.top + b.height / 2);})"
            )
            assert max(tops) - min(tops) <= 2, tops
            assert page.evaluate("document.documentElement.scrollWidth") <= 412
        finally:
            browser.close()
