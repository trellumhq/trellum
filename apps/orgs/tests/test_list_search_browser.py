"""List screens search by themselves: typing applies after a pause, with no Search button."""
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect, sync_playwright

from apps.orgs.models import OrgMembership

pytestmark = pytest.mark.django_db(transaction=True)


def _static(settings):
    def route(request_route):
        asset = settings.BASE_DIR / "static" / urlparse(request_route.request.url).path.split("/static/", 1)[1]
        if asset.is_file():
            request_route.fulfill(path=str(asset))
        else:
            request_route.continue_()
    return route


def test_org_members_search_applies_after_a_pause_without_a_button(live_server, login, org_admin, org, make_user, settings):
    for name in ("findme", "someone-else"):
        OrgMembership.objects.create(user=make_user(f"{name}@demo.example"), org=org, role="member")
    client = login(org_admin)
    host = live_server.url
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": host}])
        page = context.new_page()
        page.route("**/static/**", _static(settings))
        page.goto(f"{host}/orgs/{org.slug}/settings/members")
        rows = lambda email: page.locator("main tbody tr", has_text=email)  # noqa: E731
        search = page.get_by_role("searchbox", name="Search members by name or email…")
        expect(search).to_be_visible()
        # No Search button anywhere in the page, not even a hidden one.
        assert page.locator("form[role=search] button[type=submit]").count() == 0
        expect(rows("someone-else@demo.example")).to_have_count(1)
        search.fill("findme")
        # Still inside the pause: nothing reloaded yet.
        page.wait_for_timeout(150)
        assert "q=" not in page.url
        page.wait_for_url("**q=findme**", timeout=5000)
        expect(rows("findme@demo.example")).to_have_count(1)
        expect(rows("someone-else@demo.example")).to_have_count(0)
        expect(search).to_be_focused()  # typing resumes where it left off
        search.fill("")
        page.wait_for_function("!location.search.includes('findme')", timeout=5000)
        expect(rows("someone-else@demo.example")).to_have_count(1)
        assert "q=" not in page.url  # an emptied box leaves nothing in the address bar
        browser.close()
