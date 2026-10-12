"""Rendered geometry and field association checks for operator/org settings forms."""

import re
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from apps.accounts.tests.test_setup_wizard import walk_to_operator
from apps.core.tests.test_email_connections_browser import _rendered_page
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import Report

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.django_db


def _theme(html, theme):
    return re.sub(r"<html(?=[ >])", f'<html data-theme="{theme}"', html, count=1)


def _assert_layout(page, page_name, html, width, theme):
    page.set_viewport_size({"width": width, "height": 900})
    page.set_content(_theme(_rendered_page(html), theme), wait_until="domcontentloaded")
    if page_name in {"sso", "invites", "audit", "group-access", "group-effective"}:
        assert page.evaluate("Boolean(window.TrellumChoices && window.TrellumRecipients)"), page_name
        page.evaluate("TrellumChoices.init(document); TrellumRecipients.init(document)")
    if page_name == "group-access":
        assert page.locator("[data-recipient-picker]").count() == 1
        assert page.evaluate("TrellumRecipients.init(document).length") == 1
        assert page.locator(".recipient-picker").count() == 1
        assert page.locator("[data-recipient-picker] input[type=checkbox]").count() == 1
        assert page.locator(".recipient-picker .ui-picker-trigger").is_enabled()
    if page_name in {"sso", "invites", "group-access"}:
        assert page.locator(".recipient-picker").count() == 1, page_name
    if page_name in {"audit", "group-effective"}:
        assert page.locator(".ui-choice-picker").count() == 1, page_name
    # The normal theme boot may apply an OS/default preference after parsing.
    # Pin both console and document themes for each explicit visual scenario.
    page.evaluate("""theme => {
        document.documentElement.dataset.theme = theme;
        document.querySelectorAll('[data-console-shell]').forEach(root => {
            root.dataset.consoleTheme = theme;
        });
    }""", theme)
    assert page.locator("html").get_attribute("data-theme") == theme
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), page_name

    for rail in page.locator(".ui-form-rail").all():
        box = rail.bounding_box()
        if box and box["width"]:
            assert box["width"] <= 761, (page_name, box)
            if width > 900:
                center_delta = rail.evaluate(
                    "el => { const p = el.parentElement.getBoundingClientRect(); "
                    "const r = el.getBoundingClientRect(); "
                    "return Math.abs((r.left + r.width / 2) - (p.left + p.width / 2)); }"
                )
                assert center_delta < 3, (page_name, box, center_delta)

    for row in page.locator(".ui-form-row").all():
        box = row.bounding_box()
        if not box or not box["width"]:
            continue
        columns = row.evaluate(
            "el => getComputedStyle(el).gridTemplateColumns.split(' ').length"
        )
        assert columns == (1 if width < 768 else 2), (page_name, width, columns)

    labels_without_controls = page.locator('label[for]:not([for=""])').evaluate_all(
        "labels => labels.filter(label => !document.getElementById(label.htmlFor)).map(label => label.htmlFor)"
    )
    assert not labels_without_controls, (page_name, labels_without_controls)


@pytest.mark.parametrize("width", (390, 1440))
@pytest.mark.parametrize("theme", ("light", "dark"))
def test_rendered_settings_forms_fit_and_keep_operational_tables_wide(
    width,
    theme,
    login,
    org_admin,
    org,
    superuser,
    make_group,
    member,
    attach_group,
    studio,
    client,
    settings,
):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"
    org_client = login(org_admin)
    group = make_group("Layout checks", grants=[(studio, "viewer")])
    attach_group(member, group)
    grant = PermissionGroupGrant.objects.get(group=group, studio=studio)
    grant.viewer_scope = "selected"
    grant.save(update_fields=["viewer_scope"])
    Report.objects.create(studio=studio, slug="layout-check", name="Layout check report")
    pages = {
        "assistant": org_client.get(f"/orgs/{org.slug}/settings/assistant"),
        "sso": org_client.get(f"/orgs/{org.slug}/settings/sso"),
        "invites": org_client.get(f"/orgs/{org.slug}/settings/invites"),
        "audit": org_client.get(f"/orgs/{org.slug}/settings/audit"),
        "group-new": org_client.get(f"/orgs/{org.slug}/settings/groups/new"),
        "group-detail": org_client.get(f"/orgs/{org.slug}/settings/groups/{group.pk}"),
        "group-access": org_client.get(f"/orgs/{org.slug}/settings/groups/{group.pk}?tab=access"),
        "group-effective": org_client.get(f"/orgs/{org.slug}/settings/groups/{group.pk}?tab=effective"),
    }
    operator_client = login(superuser)
    pages["instance-settings"] = operator_client.get("/instance/settings")
    pages["email-connection"] = operator_client.get("/system/email-connections/new")
    walk_to_operator(client)
    pages["setup-settings"] = client.get("/setup/settings")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        for page_name, response in pages.items():
            assert response.status_code == 200, page_name
            if page_name == "group-access":
                assert response.context["selected_block_reason"] is None
            html = response.content.decode()
            if page_name == "assistant":
                script = (ROOT / "static" / "assistant-settings.js").read_text(encoding="utf-8")
                html = re.sub(
                    r'<script src="[^"]*assistant-settings\.js"(?: defer)?></script>',
                    lambda match, script=script: f"<script>{script}</script>",
                    html,
                )
            _assert_layout(page, page_name, html, width, theme)
            if page_name == "sso" and width > 900:
                # Domain ownership is an operational table and must stay outside the form rail.
                assert page.locator("table.ui-table").first.bounding_box()["width"] > 761
            if page_name == "instance-settings" and width > 900:
                assert page.locator("#email-connections table").count() == 0 or page.locator(
                    "#email-connections table"
                ).first.bounding_box()["width"] > 761
            if width in (390, 1440):
                output = ROOT / ".artifacts" / "portal-settings-layout"
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=output / f"{page_name}-{width}-{theme}.png", full_page=True)
        browser.close()
