"""apps/reports/snapshot.py: PNG/PDF rendering of a built report's output
directory, driven through an ephemeral local HTTP server + headless Chromium.

Chromium is not guaranteed to be present in every environment that runs this
suite, so the render tests detect that up front and skip cleanly rather than
failing CI over a missing browser binary. The failure-path tests (missing
entry file, Playwright absent) never touch a browser and always run.
"""
from __future__ import annotations

import http.server
import os
import sys
import threading
import types
import urllib.error
import urllib.request

import pytest

from apps.reports.snapshot import (
    RenderResult,
    _CHROME_HIDE_CSS,
    _EXPAND_COLLAPSED_JS,
    _make_handler,
    render_report,
)

_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "snapshot_report")


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


_HAS_CHROMIUM = _chromium_available()
_skip_no_chromium = pytest.mark.skipif(
    not _HAS_CHROMIUM, reason="playwright/chromium not available in this environment"
)


class TestRenderReport:
    @_skip_no_chromium
    def test_renders_fixture_to_png(self):
        result = render_report(_FIXTURE_DIR, want_pdf=False)
        assert result.error == ""
        assert isinstance(result.png, bytes)
        assert len(result.png) > 0
        assert result.pdf is None  # want_pdf=False
        assert result.truncated is False

    @_skip_no_chromium
    def test_renders_pdf_when_requested(self):
        result = render_report(_FIXTURE_DIR, want_pdf=True)
        assert result.error == ""
        assert isinstance(result.png, bytes)
        assert isinstance(result.pdf, bytes)
        assert len(result.pdf) > 0

    def test_missing_entry_file_returns_error_not_exception(self, tmp_path):
        result = render_report(tmp_path)
        assert isinstance(result, RenderResult)
        assert result.png is None
        assert result.pdf is None
        assert result.error != ""
        assert "not found" in result.error

    def test_playwright_absent_returns_error_not_exception(self, tmp_path, monkeypatch):
        (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
        # sys.modules[name] = None is the standard way to force the next
        # `import name` to raise ImportError without actually uninstalling
        # the package -- render_report imports playwright.sync_api lazily,
        # inside the function body, specifically so this path is reachable.
        monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
        monkeypatch.setitem(sys.modules, "playwright", None)

        result = render_report(tmp_path)

        assert result.png is None
        assert result.pdf is None
        assert "playwright" in result.error.lower()

    def test_import_of_module_succeeds_without_playwright(self, monkeypatch):
        """The module itself must import cleanly even when Playwright is
        missing entirely -- only render_report's lazy import should need it,
        so other code (e.g. the notify layer holding a RenderResult) never
        has to carry the dependency."""
        monkeypatch.setitem(sys.modules, "playwright", None)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
        monkeypatch.delitem(sys.modules, "apps.reports.snapshot", raising=False)

        import importlib

        mod = importlib.import_module("apps.reports.snapshot")
        assert hasattr(mod, "render_report")
        assert hasattr(mod, "RenderResult")


class TestChromeHiding:
    """The portal's own chrome (back-nav, export/theme controls, the AI assistant,
    the Email & Alerts button) must not appear in a mailed snapshot -- see
    ``_CHROME_HIDE_CSS``. Exercised with a fake Playwright module rather than
    real Chromium: a unit assertion that the hiding CSS is injected before
    the screenshot is taken is the pragmatic contract here (real Chromium is
    not guaranteed present in every environment that runs this suite, and
    the fixture report carries none of the framework's own chrome markup to
    screenshot-diff against)."""

    def _render_with_fake_page(self, tmp_path, monkeypatch, *, html="<html><body></body></html>"):
        """Shared fake Playwright rig: records call order and every
        evaluate()/add_style_tag() script/content so tests can assert not
        just *that* something ran, but *what* and *when*."""
        (tmp_path / "index.html").write_text(html, encoding="utf-8")
        calls = {"style_tags": [], "order": [], "evaluated": []}

        class _FakePage:
            def goto(self, *a, **k):
                calls["order"].append("goto")

            def add_style_tag(self, content=None, **k):
                calls["style_tags"].append(content)
                calls["order"].append("add_style_tag")

            def wait_for_timeout(self, ms):
                calls["order"].append("wait_for_timeout")

            def evaluate(self, script):
                calls["order"].append("evaluate")
                calls["evaluated"].append(script)
                return 900

            def set_viewport_size(self, size):
                pass

            def screenshot(self, **k):
                calls["order"].append("screenshot")
                return b"PNGDATA"

            def pdf(self, **k):
                return b"PDFDATA"

        class _FakeBrowser:
            def new_page(self, **k):
                return _FakePage()

            def close(self):
                pass

        class _FakeChromium:
            def launch(self, **k):
                return _FakeBrowser()

        class _FakePlaywrightCtx:
            def __enter__(self):
                return types.SimpleNamespace(chromium=_FakeChromium())

            def __exit__(self, *a):
                return False

        fake_sync_api = types.ModuleType("playwright.sync_api")
        fake_sync_api.sync_playwright = lambda: _FakePlaywrightCtx()
        monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
        monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

        result = render_report(tmp_path, want_pdf=False)
        return result, calls

    def test_hide_css_injected_before_screenshot(self, tmp_path, monkeypatch):
        result, calls = self._render_with_fake_page(tmp_path, monkeypatch)

        assert result.error == ""
        assert calls["style_tags"] == [_CHROME_HIDE_CSS]
        # Injected before the height is measured and the screenshot taken --
        # a style tag added after either would leave the hidden elements'
        # last-known geometry (or their pixels) in the capture.
        assert calls["order"].index("add_style_tag") < calls["order"].index("evaluate")
        assert calls["order"].index("add_style_tag") < calls["order"].index("screenshot")
        for selector in (
            ".fw-nav-group", ".fw-export-wrap", ".fw-theme-select", ".fw-help-wrap",
            "#assistantAsk", "#assistantPill", "#assistantPanel", "#fwDeliveryBtn",
            "#fwOptionsWrap",
        ):
            assert selector in _CHROME_HIDE_CSS


class TestCollapsedSectionExpansion:
    """Tables/charts inside a default-collapsed Section never leave the
    framework's lazy-render queue unless the section is expanded first (see
    _EXPAND_COLLAPSED_JS's docstring in snapshot.py for the mechanism) --
    exercised the same way as chrome-hiding above: assert the script runs,
    and runs early enough to matter."""

    def test_expand_collapsed_js_evaluated_before_height_and_screenshot(
        self, tmp_path, monkeypatch
    ):
        result, calls = TestChromeHiding()._render_with_fake_page(tmp_path, monkeypatch)

        assert result.error == ""
        assert _EXPAND_COLLAPSED_JS in calls["evaluated"]
        expand_idx = calls["evaluated"].index(_EXPAND_COLLAPSED_JS)
        # It's the first evaluate() call -- run before the page's scrollHeight
        # is read, so a section expanding changes the height this module
        # measures rather than being missed by it.
        assert expand_idx == 0
        assert calls["order"].index("add_style_tag") < calls["order"].index("evaluate")
        assert calls["order"].index("evaluate") < calls["order"].index("screenshot")

    def test_expand_collapsed_js_targets_the_fw_collapsed_class(self):
        assert "fw-collapsible" in _EXPAND_COLLAPSED_JS
        assert "fw-collapsed" in _EXPAND_COLLAPSED_JS
        assert "classList.remove" in _EXPAND_COLLAPSED_JS


class TestVendorServing:
    def test_vendor_assets_served_by_ephemeral_server(self, tmp_path):
        """Built reports reference vendor libraries (chart.js etc.) by
        absolute /_vendor/... path, outside the report's own output
        directory. Without this the ephemeral server 404s them and every
        chart in a real report renders blank."""
        (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
        handler_cls = _make_handler(tmp_path)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/_vendor/inter.css", timeout=5
            ) as resp:
                assert resp.status == 200
                assert len(resp.read()) > 0
        finally:
            server.shutdown()
            server.server_close()

    def test_unknown_vendor_asset_404s_not_serves_report_dir(self, tmp_path):
        (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
        handler_cls = _make_handler(tmp_path)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/_vendor/does-not-exist.js", timeout=5
                )
            assert exc_info.value.code == 404
        finally:
            server.shutdown()
            server.server_close()
