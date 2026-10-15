"""Real Gunicorn/PostgreSQL analytics browser checks for a studio beyond the browser-side threshold.

Uses an owned pytest database and subprocess, with PBKDF2 authentication.
Artifacts are opt-in through ANALYTICS_BROWSER_ARTIFACTS; no external service.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import quote
from urllib.request import urlopen

import pytest
from django.conf import settings

from apps.core.tests.browser_login import reach_password_step, sign_in

pytestmark = pytest.mark.django_db


@pytest.fixture(scope="module", params=(0, 20, 50))
def analytics_server(request, django_db_setup, django_db_blocker, tmp_path_factory):
    from django.contrib.auth.hashers import PBKDF2PasswordHasher
    from django.utils import timezone
    from apps.accounts.models import User
    from apps.core import roles
    from apps.orgs.models import Organization, OrgMembership
    from apps.studios.models import Studio, StudioMembership
    from apps.reports.models import Report, ReportSourceSummary, ReportViewDaily
    tmp = tmp_path_factory.mktemp(f"analytics-{request.param}")
    artifacts = Path(os.environ.get("ANALYTICS_BROWSER_ARTIFACTS", tmp)) / str(request.param)
    artifacts.mkdir(parents=True, exist_ok=True)
    with django_db_blocker.unblock():
        org = Organization.objects.create(slug=f"analytics-{request.param}", name="Analytics browser")
        studio = Studio.objects.create(org=org, slug="reports", name="Reports")
        empty = Studio.objects.create(org=org, slug="empty", name="Empty")
        users = {}
        for role in (roles.ADMIN, roles.DEVELOPER, roles.VIEWER):
            user = User.objects.create(email=f"{role}-{request.param}@analytics.example", password=PBKDF2PasswordHasher().encode("Analytics-test-42", PBKDF2PasswordHasher().salt()))
            OrgMembership.objects.create(org=org, user=user)
            for target in (studio, empty):
                StudioMembership.objects.create(studio=target, user=user, role=role)
            users[role] = user.email
        rows = Report.objects.bulk_create([Report(studio=studio, slug=f"report-{i:04}", name=f"Report {i:04}" if i != 1 else "A long report name "*15) for i in range(1000)])
        ReportViewDaily.objects.create(report=rows[-1], date=timezone.now().date(), views=100)
        ReportSourceSummary.objects.create(report=rows[0], attempt_at=timezone.now(), attempt_status="success")
    db = settings.DATABASES["default"]
    database_url = f"postgres://{quote(db['USER'])}:{quote(db['PASSWORD'])}@{db['HOST']}:{db['PORT']}/{db['NAME']}"
    (tmp / "analytics_browser_settings.py").write_text(
        "from trellum_portal.settings.test import *\n"
        "WHITENOISE_USE_FINDERS=True\n"
        "PASSWORD_HASHERS=['django.contrib.auth.hashers.PBKDF2PasswordHasher']\n"
        "MIDDLEWARE=['apps.core.tests.portal_e2e.instrumentation.MeasureRequests']+MIDDLEWARE\n"
        f"DATA_DIR={str(tmp / 'data')!r}\n"
    )
    (tmp / "analytics_browser_wsgi.py").write_text("from django.core.wsgi import get_wsgi_application\napplication=get_wsgi_application()\n")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "analytics_browser_settings", "DATABASE_URL": database_url,
           "PYTHONPATH": str(tmp)+os.pathsep+str(settings.BASE_DIR), "PORTAL_E2E_WORK_LOG": str(artifacts / "work.jsonl"),
           "PORTAL_E2E_SQL_DELAY_MS": str(request.param), "PORTAL_E2E_SOURCE_DIGEST": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}
    log = open(artifacts / "gunicorn.log", "w")
    process = subprocess.Popen([sys.executable, "-m", "gunicorn", "analytics_browser_wsgi:application", "--bind", f"127.0.0.1:{port}", "--workers", "1", "--threads", "2"], env=env, stdout=log, stderr=log)
    origin = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if urlopen(origin+"/login", timeout=1).status == 200:
                    break
            except Exception:
                if process.poll() is not None:
                    raise RuntimeError((artifacts / "gunicorn.log").read_text())
                time.sleep(.1)
        else:
            raise RuntimeError("Gunicorn did not start")
        yield dict(origin=origin, path=f"/s/{org.slug}/{studio.slug}/analytics", empty=f"/s/{org.slug}/{empty.slug}/analytics", users=users, artifacts=artifacts, delay=request.param)
    finally:
        process.terminate(); process.wait(timeout=15); log.close()
        with django_db_blocker.unblock():
            org.delete()
            User.objects.filter(email__in=users.values()).delete()


def _login(page, server, role):
    from playwright.sync_api import expect
    reach_password_step(page, server["origin"], server["users"][role], server["path"])
    page.get_by_label("Password", exact=True).fill("Analytics-test-42")
    started = time.perf_counter()
    page.get_by_role("button", name="Log in", exact=True).click()
    page.wait_for_url("**"+server["path"])
    expect(page.locator("#analyticsTable tbody tr[data-name]")).to_have_count(200)
    expect(page.locator("#analyticsTable tbody tr[data-name]").first).to_be_visible()
    return time.perf_counter()-started


@pytest.mark.parametrize("engine", ("chromium", "webkit"))
def test_analytics_gunicorn_server_side_mode_devices_roles(analytics_server, engine):
    from playwright.sync_api import sync_playwright, expect
    server = analytics_server
    timings = []
    rows = "#analyticsTable tbody tr[data-name]"
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        try:
            for width in tuple(int(value) for value in os.environ.get("ANALYTICS_BROWSER_WIDTHS", "1440,390,720,195").split(",")):
                context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce", color_scheme="dark" if width == 720 else "light")
                page = context.new_page(); page.set_default_timeout(15000)
                errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
                cold_first_rows = _login(page, server, "admin")
                assert cold_first_rows <= {0:3, 20:4.5, 50:6.5}[server["delay"]]
                started = time.perf_counter()
                response = page.goto(server["origin"]+server["path"], wait_until="domcontentloaded")
                expect(page.locator(rows)).to_have_count(200)
                elapsed = time.perf_counter()-started
                assert elapsed <= {0:3, 20:4.5, 50:6.5}[server["delay"]]
                assert len(response.body()) <= 700*1024
                page.screenshot(path=str(server["artifacts"] / f"{engine}-{width}.png"), full_page=False)
                # The page's own visible content fits (the shell header is not this page's layout).
                overflow = page.evaluate("Array.from(document.querySelectorAll('main *')).filter(e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && r.right > innerWidth + 1 && !e.closest('thead'); }).map(e => ({tag: e.tagName, cls: e.className, right: e.getBoundingClientRect().right})).slice(0, 20)")
                assert not overflow, overflow
                # Main's controls only; no selects, no Search button.
                assert page.locator("main select, main form, main button").count() == 0
                expect(page.locator(".ui-pager")).to_contain_text("1–200 of 1000")
                assert page.get_by_text("unavailable").count() == 0
                timings.append(dict(engine=engine, width=width, cold_login_first_rows_seconds=cold_first_rows, first_rows_seconds=elapsed, html_bytes=len(response.body()), dom_nodes=page.locator("*").count()))
                # The filter box searches the whole studio after a pause; a header sorts it.
                page.locator("#analyticsSearch").fill("report-0999")
                expect(page.locator(rows)).to_have_count(1)
                expect(page.locator("#analyticsTable tbody")).to_contain_text("Report 0999")
                assert "q=report-0999" in page.url
                page.locator("#analyticsSearch").fill("")
                expect(page.locator(rows)).to_have_count(200)
                sortable = width >= 768  # phones show cards: no sortable headers, as before
                if sortable:
                    page.locator('th[data-sort="views"]').click()
                    page.locator('th[data-sort="views"]').click()
                    expect(page.locator(rows).first).to_contain_text("Report 0999")
                    assert "sort=views" in page.url and "direction=desc" in page.url
                # The pager keeps the sort.
                page.get_by_role("link", name="Next →", exact=True).click()
                expect(page.locator(".ui-pager")).to_contain_text("201–400 of 1000")
                assert "page=2" in page.url and (not sortable or "sort=views" in page.url)
                page.goto(server["origin"]+server["empty"])
                expect(page.locator(".ui-empty")).to_contain_text("No views recorded yet.")
                assert not errors
                context.close()
            for role, status in (("developer",200), ("viewer",403)):
                context = browser.new_context(); page = context.new_page()
                if role == "viewer":
                    # Login succeeds and the authoritative route denies the destination.
                    sign_in(page, server["origin"], server["users"][role], "Analytics-test-42", server["path"])
                    page.wait_for_url("**"+server["path"])
                else:
                    _login(page, server, role)
                response = page.goto(server["origin"]+server["path"])
                assert response.status == status
                context.close()
        finally:
            browser.close()
    work = [json.loads(line) for line in (server["artifacts"] / "work.jsonl").read_text().splitlines()]
    analytics_work = [row for row in work if row["route"] == "report-analytics" or row["path"].endswith("/analytics")]
    assert analytics_work
    assert all(row["storage_count"] == 0 and row["sql_count"] <= 27 and row["response_bytes"] <= 700*1024 for row in analytics_work if row["status"] == 200)
    (server["artifacts"] / f"{engine}-timings.json").write_text(json.dumps({
        "browser": engine, "delay_ms": server["delay"], "timings": timings,
        "analytics_http_requests": len(analytics_work), "max_sql": max(row["sql_count"] for row in analytics_work),
        "storage": sum(row["storage_count"] for row in analytics_work),
    }, indent=2))
