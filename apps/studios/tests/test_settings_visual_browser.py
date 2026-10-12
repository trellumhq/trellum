"""Rendered desktop and phone checks for ordinary studio settings screens."""
from pathlib import Path

import pytest
from django.test import Client
from playwright.sync_api import sync_playwright

from apps.studios.models import StudioRepo

ROOT = Path(__file__).resolve().parents[3]
SCREENSHOTS = ROOT / "artifacts" / "studio-settings"


@pytest.mark.django_db
def test_studio_settings_render_without_overflow_in_both_themes(
    org, org_admin, studio_tree
):
    StudioRepo.objects.create(studio=studio_tree, repo_url="https://example.test/reports.git")
    client = Client()
    client.force_login(org_admin)
    screens = {
        "repository": client.get(f"/s/{org.slug}/{studio_tree.slug}/settings/repo"),
        "members": client.get(f"/s/{org.slug}/{studio_tree.slug}/settings/members"),
        "appearance": client.get(f"/s/{org.slug}/{studio_tree.slug}/settings/appearance"),
    }
    assert all(response.status_code == 200 for response in screens.values())
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)

        def serve(route):
            path = ROOT / "static" / route.request.url.rsplit("/static/", 1)[-1]
            if path.is_file():
                route.fulfill(status=200, body=path.read_bytes())
            else:
                route.fulfill(status=404, body="")

        page.route("http://portal.test/**", lambda route: route.fulfill(status=200, body=""))
        page.route("http://portal.test/static/**", serve)
        page.goto("http://portal.test/")

        for name, response in screens.items():
            for width, height in ((1440, 1000), (390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                for theme in ("light", "dark"):
                    page.evaluate("theme => { localStorage.setItem('mgmt-theme', theme); document.documentElement.dataset.theme = theme; }", theme)
                    page.set_content(response.content.decode(), wait_until="load")
                    page.add_style_tag(content="*,*::before,*::after{animation:none!important;transition:none!important}")
                    page.evaluate("document.querySelector('[data-console-shell]')?.setAttribute('data-console-theme', document.documentElement.dataset.theme)")
                    page.wait_for_timeout(50)
                    dimensions = page.evaluate("({width: innerWidth, scroll: document.documentElement.scrollWidth})")
                    assert dimensions["scroll"] <= dimensions["width"], (name, width, theme, dimensions)
                    page.screenshot(path=str(SCREENSHOTS / f"{name}-{width}-{theme}.png"), full_page=True)
                    if name == "members":
                        page.locator(".ui-choice-picker .ui-picker-trigger").click()
                        popup = page.locator(".ui-picker-popup")
                        assert popup.is_visible()
                        assert popup.bounding_box()["x"] >= 0
                        assert popup.bounding_box()["x"] + popup.bounding_box()["width"] <= width
        browser.close()
