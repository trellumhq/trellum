"""History restoration against rendered portal pages and the real browser scripts."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from django.conf import settings

from apps.core import roles


def _run_restore(*, page_html, page_path, row_slug, report, registry_headers, engine, surface):
    rows = [report]
    registry_calls = []
    status_calls = []
    history = {
        "aggregates": {"runs": 1, "success_rate": 1, "window_days": 90},
        "history": [{"started_at": "2026-10-10T12:00:00Z", "trigger": "manual", "status": "success"}],
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path.endswith("/api/registry"):
                registry_calls.append(None)
                body, content_type = json.dumps({"reports": rows}).encode(), "application/json"
            elif path.endswith("/api/system/status"):
                status_calls.append(None)
                body, content_type = b'{"running":{},"queue":[]}', "application/json"
            elif path.endswith("/api/system/git/status"):
                body, content_type = b'{"configured":false}', "application/json"
            elif path.endswith("/api/system/run-stats"):
                body, content_type = b'{"stats":{}}', "application/json"
            elif path.endswith("/api/me/favorites"):
                body, content_type = b'{"favorites":[]}', "application/json"
            elif path.endswith("/api/my-subscriptions"):
                body, content_type = b'{"reports":{}}', "application/json"
            elif path.endswith(f"/api/reports/{row_slug}/status"):
                body, content_type = json.dumps(history).encode(), "application/json"
            elif path.endswith(f"/api/reports/{row_slug}/log"):
                body, content_type = b'{"status":"success","stdout":"old log"}', "application/json"
            elif path == page_path:
                body, content_type = page_html.encode(), "text/html"
            elif path == "/away":
                body, content_type = b"<!doctype html><title>Away</title>", "text/html"
            elif path.startswith("/static/"):
                target = Path(settings.BASE_DIR) / "static" / path.removeprefix("/static/")
                if not target.is_file():
                    self.send_error(404)
                    return
                body = target.read_bytes()
                content_type = "application/javascript" if target.suffix == ".js" else "text/css"
            elif path.startswith("/_vendor/"):
                body, content_type = b"", "text/css"
            elif path.endswith("/api/assistant/widget.js"):
                body, content_type = b"", "application/javascript"
            elif "/api/" in path:
                body, content_type = b"{}", "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            if path.endswith("/api/registry"):
                for name, value in registry_headers.items():
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = getattr(playwright, engine).launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()
            page.add_init_script(
                "window.__pageshows=[]; addEventListener('pageshow', e => __pageshows.push(e.persisted))"
            )
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                page.goto(base + page_path)
                selector = f'[data-{"ops-row" if surface == "operations" else "slug"}="{row_slug}"]'
                page.locator(selector).wait_for()
                original_row = page.locator(selector)
                assert report["name"] in original_row.inner_text()
                page.goto(base + "/away")
                rows[0] = {**rows[0], "name": "Updated Player Overview",
                           "last_run": "2026-10-11T12:00:00Z"}
                page.go_back()
                page.locator(selector).wait_for()
                try:
                    page.wait_for_function(
                        "args => document.querySelector(args.selector)?.innerText.includes(args.name)",
                        arg={"selector": selector, "name": "Updated Player Overview"},
                        timeout=5000,
                    )
                except PlaywrightTimeoutError:
                    pass
                state = {
                    "pageshows": page.evaluate("window.__pageshows"),
                    "registry_requests": len(registry_calls),
                    "status_requests": len(status_calls),
                    "registry_headers": registry_headers,
                }
                artifact_dir = settings.BASE_DIR.parents[1] / ".artifacts" / "portal-third-batch" / "navigation"
                artifact_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=artifact_dir / f"{surface}-history-{engine}.png", full_page=True)
                (artifact_dir / f"{surface}-history-{engine}.json").write_text(
                    json.dumps(state, indent=2), encoding="utf-8"
                )
                assert "Updated Player Overview" in page.locator(selector).inner_text(), state
                assert state["registry_requests"] >= 2, state
            finally:
                context.close()
                browser.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def developer(make_user, org, studio_tree, grant_studio):
    user = make_user("dev@history.example", org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
    return user


@pytest.mark.django_db
@pytest.mark.parametrize("engine", ("chromium", "webkit"))
def test_catalog_refreshes_current_registry_after_real_history_restore(
    engine, login, developer, org, studio_tree, report_row
):
    page_path = f"/s/{org.slug}/{studio_tree.slug}/"
    response = login(developer).get(page_path)
    assert response.status_code == 200
    registry_response = login(developer).get(page_path + "api/registry")
    assert registry_response.status_code == 200
    registry_headers = {
        name: registry_response.headers[name]
        for name in ("Cache-Control", "Vary", "ETag", "Last-Modified", "Expires")
        if name in registry_response.headers
    }
    report = next(
        item for item in registry_response.json()["reports"]
        if item["slug"] == report_row.slug
    )
    _run_restore(
        page_html=response.content.decode(), page_path=page_path, row_slug=report_row.slug,
        report=report, registry_headers=registry_headers, engine=engine, surface="catalog",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("engine", ("chromium", "webkit"))
def test_operations_restores_current_registry_after_real_history_navigation(
    engine, login, developer, org, studio_tree, report_row
):
    page_path = f"/s/{org.slug}/{studio_tree.slug}/operations"
    response = login(developer).get(page_path)
    assert response.status_code == 200
    registry_response = login(developer).get(f"/s/{org.slug}/{studio_tree.slug}/api/registry")
    assert registry_response.status_code == 200
    registry_headers = {
        name: registry_response.headers[name]
        for name in ("Cache-Control", "Vary", "ETag", "Last-Modified", "Expires")
        if name in registry_response.headers
    }
    report = next(
        item for item in registry_response.json()["reports"]
        if item["slug"] == report_row.slug
    )
    _run_restore(
        page_html=response.content.decode(), page_path=page_path, row_slug=report_row.slug,
        report=report, registry_headers=registry_headers, engine=engine, surface="operations",
    )
