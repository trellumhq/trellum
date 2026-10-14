"""Real Gunicorn/PostgreSQL native and enhanced analytics browser checks.

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
        ReportSourceSummary.objects.create(report=rows[0], state="ready", quality="verified", observed_at=timezone.now(), attempt_at=timezone.now(), attempt_status="success")
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
    page.goto(server["origin"]+"/login?next="+server["path"])
    page.get_by_label("Email", exact=True).fill(server["users"][role])
    page.get_by_label("Password", exact=True).fill("Analytics-test-42")
    started = time.perf_counter()
    page.get_by_role("button", name="Continue", exact=True).click()
    page.wait_for_url("**"+server["path"])
    expect(page.locator("#analyticsTable tbody tr[data-name]")).to_have_count(25)
    expect(page.locator("#analyticsTable tbody tr[data-name]").first).to_be_visible()
    return time.perf_counter()-started


@pytest.mark.parametrize("engine", ("chromium", "webkit"))
def test_analytics_gunicorn_native_enhanced_devices_roles(analytics_server, engine):
    from playwright.sync_api import sync_playwright, expect
    server = analytics_server
    timings = []
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        try:
            for javascript in (False, True):
                for width in tuple(int(value) for value in os.environ.get("ANALYTICS_BROWSER_WIDTHS", "1440,390,720,195").split(",")):
                    context = browser.new_context(java_script_enabled=javascript, viewport={"width": width, "height": 900}, reduced_motion="reduce", color_scheme="dark" if width == 720 else "light")
                    page = context.new_page(); page.set_default_timeout(15000)
                    if javascript:
                        page.add_init_script("""window.addEventListener('click', () => {
                          const started = performance.now();
                          queueMicrotask(() => {
                            const status = document.querySelector('[data-console-navigation-native-status]');
                            if (status && status.textContent) sessionStorage.setItem('analyticsNavigationFeedback', JSON.stringify({text:status.textContent, milliseconds:performance.now()-started}));
                          });
                        });""")
                    errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
                    cold_first_rows = _login(page, server, "admin")
                    assert cold_first_rows <= {0:3, 20:4.5, 50:6.5}[server["delay"]]
                    started = time.perf_counter()
                    response = page.goto(server["origin"]+server["path"], wait_until="domcontentloaded")
                    expect(page.locator("#analyticsTable tbody tr[data-name]")).to_have_count(25)
                    elapsed = time.perf_counter()-started
                    assert elapsed <= {0:3, 20:4.5, 50:6.5}[server["delay"]]
                    assert len(response.body()) <= 160*1024
                    assert page.locator("*").count() <= 1500
                    page.screenshot(path=str(server["artifacts"] / f"{engine}-{width}-js{int(javascript)}.png"), full_page=False)
                    assert page.locator("body").evaluate("e => e.scrollWidth <= innerWidth"), page.evaluate("Array.from(document.querySelectorAll('body *')).filter(e => e.getBoundingClientRect().right > innerWidth+1).map(e => ({tag:e.tagName, cls:e.className, text:e.textContent.slice(0,60), parent:e.parentElement.className, right:e.getBoundingClientRect().right})).slice(0,30)")
                    assert page.get_by_text("Build observation unavailable", exact=True).count() > 0
                    timings.append(dict(engine=engine, width=width, javascript=javascript, cold_login_first_rows_seconds=cold_first_rows, first_rows_seconds=elapsed, html_bytes=len(response.body()), dom_nodes=page.locator("*").count()))
                    # Native search and sorting are equally usable on mobile cards.
                    page.get_by_label("Search report names", exact=True).fill("report-0999")
                    page.get_by_label("Search report names", exact=True).press("Enter")
                    expect(page.locator("#analyticsTable tbody tr[data-name]")).to_have_count(1)
                    expect(page.locator("#analyticsTable tbody")).to_contain_text("Report 0999")
                    page.get_by_role("link", name="7d", exact=True).click()
                    assert "q=report-0999" in page.url and "days=7" in page.url
                    page.get_by_label("Search report names", exact=True).fill("")
                    page.get_by_label("Sort by", exact=True).select_option("views")
                    page.get_by_label("Order", exact=True).select_option("desc")
                    page.get_by_role("button", name="Search", exact=True).click()
                    expect(page.locator("#analyticsTable tbody tr[data-name]").first).to_contain_text("Report 0999")
                    # Paging retains range/order and uses keyboard-operable links.
                    page.get_by_role("link", name="Next", exact=True).focus()
                    page.keyboard.press("Enter")
                    expect(page.locator(".analytics-pagination")).to_contain_text("Page 2 of 40")
                    if javascript:
                        feedback = json.loads(page.evaluate("sessionStorage.getItem('analyticsNavigationFeedback')"))
                        assert feedback["text"] == "Opening page…" and feedback["milliseconds"] < 100
                        timings[-1]["native_navigation_feedback"] = feedback
                    assert "sort=views" in page.url and "direction=desc" in page.url and "days=7" in page.url
                    page.get_by_label("Search report names", exact=True).fill("no-matching-report")
                    page.get_by_role("button", name="Search", exact=True).click()
                    expect(page.locator(".ui-empty")).to_contain_text("No reports match your search.")
                    page.goto(server["origin"]+server["empty"])
                    expect(page.locator(".ui-empty")).to_contain_text("No reports in this studio.")
                    assert not errors
                    context.close()
            for role, status in (("developer",200), ("viewer",403)):
                context = browser.new_context(); page = context.new_page()
                if role == "viewer":
                    # Login succeeds and the authoritative route denies the destination.
                    page.goto(server["origin"]+"/login?next="+server["path"])
                    page.get_by_label("Email", exact=True).fill(server["users"][role])
                    page.get_by_label("Password", exact=True).fill("Analytics-test-42")
                    page.get_by_role("button", name="Continue", exact=True).click()
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
    # No post-render fetch is necessary for analytics; record the full HTTP work window.
    assert analytics_work
    assert all(row["storage_count"] == 0 and row["sql_count"] <= 27 and row["response_bytes"] <= 160*1024 for row in analytics_work if row["status"] == 200)
    (server["artifacts"] / f"{engine}-timings.json").write_text(json.dumps({
        "source": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
        "browser": engine, "delay_ms": server["delay"], "timings": timings,
        "analytics_http_requests": len(analytics_work), "max_sql": max(row["sql_count"] for row in analytics_work),
        "storage": sum(row["storage_count"] for row in analytics_work),
    }, indent=2))
