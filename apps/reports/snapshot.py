"""Render a built report's output directory to a PNG (and optionally a PDF).

A built report is a static directory (HTML entry file, ``data.json``, chunked
data files, assets); the entry file's own JS fetches ``data.json`` and renders
charts client-side. A plain ``file://`` load leaves the page blank because
``fetch()`` is refused from the file origin, so this module spins up a
throwaway localhost HTTP server rooted at the output directory -- the same
trick the framework's own screenshot tests use (see
``trellum/testing/runner.py::_take_screenshot`` and
``trellum/testing/test_interactions.py::_ReportServer``) -- and points
headless Chromium at it.

Vendor libraries (chart.js and friends) are referenced by built reports as
absolute ``/_vendor/...`` paths and live outside any single report's output
directory, so a plain static file server 404s them and every chart silently
vanishes. The request handler below delegates ``/_vendor/*`` to the
framework's ``serve_vendor_request``, exactly like ``apps/reports/vendor.py``
does for the live portal (that function's contract is a
``BaseHTTPRequestHandler``-shaped object, which ``http.server`` handlers
already satisfy -- no shim needed here).

Ready-wait: reports carry no explicit "done" DOM marker to poll for, so this
copies the wait the framework's own Playwright suites settled on rather than
inventing a new one: ``goto(..., wait_until="networkidle")`` so the initial
``data.json``/chunk fetches have finished, then a fixed settle so Chart.js's
post-fetch repaint (triggered by a DOM update, not a network event) has time
to finish before the screenshot is taken.

Playwright is only imported inside ``render_report`` so that importing this
module never requires it to be installed -- callers that merely hold a
``RenderResult`` around (e.g. the delivery/notify layer) must not be forced to
carry a Chromium dependency they never invoke.
"""
from __future__ import annotations

import http.server
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Matches the framework's own settle time after networkidle (see
# trellum/testing/runner.py, trellum/testing/test_interactions.py) --
# Chart.js finishes painting shortly after the DOM update that follows the
# data fetch, which networkidle alone does not wait for.
_POST_NETWORKIDLE_SETTLE_MS = 2000
# 552 CSS px = exactly the width the delivery mail displays the snapshot at,
# so the report renders its narrow/responsive layout with text at natural
# size instead of a half-scale desktop layout that no amount of bitmap
# resolution can un-blur. _DEVICE_SCALE = 3 was chosen empirically (live
# A/B of 2x/3x/4x mails): 3x clearly survives fractional display-scaling
# downscales (Windows 125/150%, phone DPR 3) better than 2x, while 4x
# produces an image large enough that mail viewers fall back to
# low-quality/bilinear scaling and actually look worse despite the extra
# pixels.
_VIEWPORT_WIDTH = 552
_DEVICE_SCALE = 3

#: Portal-injected chrome that has no place in an emailed snapshot -- the
#: screenshot should show the report, not the app around it. Selectors, and
#: where each comes from:
#:   .fw-nav-group    the back-to-portal breadcrumb + reload button, injected
#:                     via extensions.nav_html (apps.runner.executor
#:                     .breadcrumb_nav_html) -- see trellum/rendering/
#:                     html_builder.py::_inject_nav and trellum/components/
#:                     header.py::ReportHeader.render_html.
#:   .fw-export-wrap  the framework's own Export (PNG/PDF) button + menu,
#:   .fw-theme-select likewise the theme-switcher select -- both rendered by
#:                     ReportHeader.render_html regardless of host; neither
#:                     means anything inside a still image already emailed.
#:   .fw-help-wrap    the framework's "Slow?" performance-hint button
#:                     (#fwHelpBtn) + its dropdown (#fwHelpMenu), also
#:                     rendered by ReportHeader.render_html
#:                     (trellum/components/header.py) -- a live-page
#:                     troubleshooting affordance for browser-extension
#:                     overhead, meaningless on a static emailed image.
#:   #assistantAsk    the AI assistant's openers (header button on desktop,
#:   #assistantPill    bottom pill on a phone) and its panel, injected via
#:   #assistantPanel   extensions.scripts (static/assistant.js self-mounts
#:                     all three).
#:   #fwDeliveryBtn   dead selector, kept harmlessly: report_delivery.js used
#:                     to mount its own "Email & alerts" header button under
#:                     this id before the report-page controls were
#:                     consolidated into one Options menu (below) -- no
#:                     build after that change ever emits this id again, but
#:                     a report snapshotted before the change on disk still
#:                     might, so the selector stays rather than risk it.
#:   #fwOptionsWrap   the consolidated "Options" menu (static/report_menu.js)
#:                     -- Email delivery/Share/Activity/Export, all under one
#:                     button+dropdown now; none of it means anything inside
#:                     a still image already emailed.
_CHROME_HIDE_CSS = (
    ".fw-nav-group, .fw-export-wrap, .fw-theme-select, .fw-help-wrap, "
    "#assistantAsk, #assistantPill, #assistantPanel, #fwDeliveryBtn, #fwOptionsWrap "
    "{ display: none !important; }"
)

#: Expands every collapsed Section (``Section(collapsible=True,
#: default_collapsed=True)`` / the ``collapsible`` + ``default_collapsed``
#: section dict shape -- see trellum/components/layout.py::Section and
#: trellum/rendering/html_builder.py::_render_section_list) before the
#: snapshot is captured.
#:
#: Framework tables/charts are lazy: components not flagged
#: ``_PRIORITY_TYPES`` (data_table, chart, pivot, ...) defer their first
#: render to an IntersectionObserver (trellum/rendering/html_builder.py
#: ``_renderComps``/``_lazyObserver``), which checks `el.closest('.fw-collapsed')`
#: and, if the element sits inside a collapsed section, only renders it once
#: the section is expanded and the element scrolls into view. A collapsed
#: section's body is not `display:none` -- it is
#: `.fw-collapsible.fw-collapsed .fw-collapsible-body { max-height:0;
#: opacity:0 }` (same file) -- so its table/chart placeholders never gain a
#: non-zero intersection with the viewport and never render, no matter how
#: tall the screenshot viewport is made. Removing the `fw-collapsed` class
#: (exactly what a reader's click on `.fw-collapsible-toggle` does -- see
#: the click handler right next to `_render_section_list` in the same file)
#: un-clips the body so the observer can see -- and then render -- what is
#: inside once the viewport covers it, which `render_report` already
#: arranges by resizing to `full_height` below.
_EXPAND_COLLAPSED_JS = (
    "document.querySelectorAll('.fw-collapsible.fw-collapsed').forEach("
    "function(el){ el.classList.remove('fw-collapsed'); });"
)

#: NOT handled here, on purpose: a table's ``max_rows`` cutoff (default 200
#: -- trellum/components/tables.py::DataTable.max_rows) is a separate kind
#: of truncation from the collapsed-section case above, and there is no
#: supported hook to lift it from outside the framework:
#:   - Static tables (no ``dataset_id``) bake ``df.head(max_rows)`` into the
#:     registered config at *render* time (``DataTable.render_html``, same
#:     file) -- rows past the cutoff are never serialized into data.json or
#:     the page at all, so there is nothing for page.evaluate to reveal.
#:   - Live tables (``dataset_id`` set) do hold the full dataset client-side
#:     and slice to ``cfg.maxRows`` only when drawing
#:     (``_fwRenderers['table']``'s ``_drawTable``, same file), but that
#:     ``cfg`` object is a closure-local variable inside the IIFE built by
#:     ``trellum/rendering/html_builder.py`` -- never assigned to
#:     ``window`` -- so there is no public identifier to reach in and bump.
#: Both are the framework choosing not to expose a re-render-with-more-rows
#: affordance (the UI's own answer is "Showing N of M rows -- CSV download
#: includes all M"). Working around that from the host would mean either
#: reimplementing the renderer against private internals (fragile, breaks
#: silently on the next framework bump) or patching the framework itself,
#: which this workstream does not do. Left as-is; a snapshot table shows the
#: same N rows a live viewer sees on first load.


@dataclass
class RenderResult:
    """Outcome of a single ``render_report`` call.

    ``error`` is the empty string on success; any non-empty value means both
    ``png`` and ``pdf`` are ``None`` and the caller should log-and-skip rather
    than fail the whole delivery run over one bad report.
    """

    png: Optional[bytes]
    pdf: Optional[bytes]
    error: str = ""
    truncated: bool = False


def _make_handler(output_dir: Path):
    """Build a request handler rooted at ``output_dir`` that also answers
    ``/_vendor/*`` requests, so charts render the same way they do in the
    live portal."""
    from trellum.rendering.cdn import serve_vendor_request

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(output_dir), **kwargs)

        def log_message(self, *args):  # noqa: A003 - stdlib override signature
            pass  # this runs in a background worker thread; keep it quiet

        def do_GET(self):
            if serve_vendor_request(self):
                return
            super().do_GET()

    return _Handler


def render_report(
    output_dir,
    *,
    want_pdf: bool = True,
    max_height_px: int = 6000,
    timeout_s: int = 60,
) -> RenderResult:
    """Render ``output_dir`` (a built report's output directory) to PNG bytes,
    and optionally PDF bytes.

    Never raises. Every failure mode -- a missing entry file, Playwright not
    installed, Chromium not installed, a navigation timeout, a browser crash
    -- comes back as ``RenderResult(None, None, error=...)`` instead of an
    exception, because the caller drives an alert/delivery pipeline where one
    bad report must not take down the rest of the run.
    """
    output_dir = Path(output_dir)

    from apps.reports.scan import _get_html_entry

    entry = _get_html_entry(str(output_dir))
    entry_path = output_dir / entry
    if not entry_path.is_file():
        return RenderResult(None, None, error=f"report entry file not found: {entry_path}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return RenderResult(None, None, error="playwright is not installed")

    server: http.server.ThreadingHTTPServer | None = None
    try:
        handler_cls = _make_handler(output_dir)
        # Port 0 -> OS-assigned free port. Several renders can run
        # concurrently (one per report in a scheduled delivery batch), so a
        # fixed port would collide between them.
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        url = f"http://127.0.0.1:{port}/{entry}"

        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as exc:  # noqa: BLE001 - surfaced via RenderResult, not raised
                return RenderResult(None, None, error=f"chromium unavailable: {exc}")

            try:
                page = browser.new_page(
                    viewport={"width": _VIEWPORT_WIDTH, "height": 900},
                    device_scale_factor=_DEVICE_SCALE,
                )
                page.goto(url, wait_until="networkidle", timeout=timeout_s * 1000)
                # Before anything is measured or captured: hides the portal's
                # own chrome (back-nav, export/theme controls, the "Slow?"
                # hint, the AI assistant, this workstream's own email button) so the
                # mailed image is the report, not the app around it. See
                # _CHROME_HIDE_CSS.
                page.add_style_tag(content=_CHROME_HIDE_CSS)
                # Also before anything is measured: un-collapse any
                # default-collapsed sections so their tables/charts leave the
                # framework's lazy-render queue (see _EXPAND_COLLAPSED_JS) and
                # actually paint before the height below is read. Ordered
                # ahead of the settle wait so that wait covers this reflow
                # too, not just the post-networkidle chart repaint it was
                # originally sized for.
                page.evaluate(_EXPAND_COLLAPSED_JS)
                page.wait_for_timeout(_POST_NETWORKIDLE_SETTLE_MS)

                full_height = page.evaluate("document.documentElement.scrollHeight")
                truncated = full_height > max_height_px
                if truncated:
                    # Clip by bounding the viewport rather than passing a
                    # screenshot `clip` rect -- Playwright rejects clip
                    # together with full_page, and a bounded viewport keeps
                    # the same full_page code path for both cases.
                    page.set_viewport_size({"width": _VIEWPORT_WIDTH, "height": max_height_px})
                    page.wait_for_timeout(300)
                    png = page.screenshot(full_page=False)
                else:
                    # Growing the viewport to the full page height brings any
                    # still-lazy content below the original 900px fold within
                    # the IntersectionObserver's view -- give it a moment to
                    # render before the screenshot (mirrors the 300ms wait
                    # the truncated branch above already relies on).
                    page.set_viewport_size({"width": _VIEWPORT_WIDTH, "height": full_height})
                    page.wait_for_timeout(300)
                    png = page.screenshot(full_page=True)

                pdf = None
                if want_pdf:
                    try:
                        pdf = page.pdf(format="A4", print_background=True)
                    except Exception:  # noqa: BLE001 - PDF is best-effort, PNG is the contract
                        pdf = None

                browser.close()
                return RenderResult(png=png, pdf=pdf, error="", truncated=truncated)
            except Exception as exc:  # noqa: BLE001 - navigation/screenshot failure
                try:
                    browser.close()
                except Exception:  # noqa: BLE001 - already failing, don't mask the real error
                    pass
                return RenderResult(None, None, error=f"render failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - server startup or unexpected failure
        return RenderResult(None, None, error=f"snapshot failed: {exc}")
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
