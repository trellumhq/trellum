"""The dashboard has ONE header.

It used to render the global shell and then a second solid-accent bar
underneath, which duplicated logout, duplicated the studio name, and used a
different rail and type scale. The controls moved into the shell; these tests
keep the second bar from coming back.
"""
import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


def _dashboard_html(client, login, org_admin, org, studio) -> str:
    login(org_admin)
    url = reverse("studio-dashboard", args=[org.slug, studio.slug])
    return client.get(url).content.decode()


def test_dashboard_uses_shared_page_heading(client, login, org_admin, org, studio):
    html = _dashboard_html(client, login, org_admin, org, studio)
    assert '<header class="page-head">' in html
    assert f"Studio · {studio.name}" in html
    assert "portal-page-head" not in html


def test_dashboard_controls_live_in_the_shell(client, login, org_admin, org, studio):
    html = _dashboard_html(client, login, org_admin, org, studio)
    shell = html.split('<header class="tl-console-header"', 1)[1].split("</header>", 1)[0]
    # portal.js looks #searchInput up by id and several call sites are unguarded,
    # so it must exist. Search belongs to the page it searches: the field sits in
    # the catalog's folder bar, not in the one header. #portalThemeSelect and
    # #errorLogBtn moved out (ui-consistency-punchlist.md item 3,
    # studio-theming-design.md §8): the report-theme picker is superseded by
    # the per-viewer Appearance select (also in the shell, but unconditional
    # and rendered from the "theme_registry" context var, not the id
    # "portalThemeSelect"); the error log moved into the dashboard's own
    # summary bar (static/portal.js renderSummary), beside the other
    # surface-specific controls.
    assert 'id="searchInput"' not in shell
    assert "tl-console-search" not in shell and "data-console-search" not in html
    rail = html.split('<div class="folder-rail">', 1)[1].split("</label>", 1)[0]
    assert rail.index('id="folderTabs"') < rail.index('id="searchInput"')
    assert 'type="search"' in rail and "data-page-search" in rail
    assert 'placeholder="Search reports…"' in rail
    assert html.index('id="searchInput"') < html.index('id="catalogToolbar"')
    assert "portalThemeSelect" not in shell
    assert "errorLogBtn" not in shell


def test_error_log_button_lives_in_the_summary_bar(client, login, org_admin, org, studio):
    # Not the shell (it cannot work on pages that never render the
    # dashboard's client-state) -- portal.js builds it into renderSummary.
    js = (settings.BASE_DIR / "static" / "portal.js").read_text(encoding="utf-8")
    assert 'id="errorLogBtn"' in js
    assert 'id="errorLogBadge"' in js


def test_dashboard_summary_omits_health_counts():
    js = (settings.BASE_DIR / "static" / "portal.js").read_text(encoding="utf-8")
    summary = js.split("function renderSummary", 1)[1].split(
        "/* ── URL state", 1
    )[0]
    assert " healthy" not in summary
    assert " errors" not in summary
    assert " not run" not in summary


def test_view_toggle_left_the_shell_for_the_summary_bar(
    client, login, org_admin, org, studio
):
    # Cards/List is dashboard-content state, so the toggle renders in the
    # summary bar (portal.js renderSummary, [data-setview] delegate) — and
    # Operations/Chat are gone from the toggle entirely: Operations is a page
    # in the studio tab bar, the chat view mode is deleted.
    html = _dashboard_html(client, login, org_admin, org, studio)
    shell = html.split('<header class="tl-console-header"', 1)[1].split("</header>", 1)[0]
    for dead_id in ("viewCards", "viewList", "viewOps", "viewChat"):
        assert f'id="{dead_id}"' not in shell, f"#{dead_id} is back in the shell"
    assert "view-toggle" not in shell
    js = (settings.BASE_DIR / "static" / "portal.js").read_text(encoding="utf-8")
    assert 'data-setview="cards"' in js
    assert 'data-setview="list"' in js
    assert "viewToggleHtml" in js


def test_exactly_one_logout_affordance(client, login, org_admin, org, studio):
    html = _dashboard_html(client, login, org_admin, org, studio)
    assert html.count('action="/logout"') == 1
    assert "portalLogout" not in html


@pytest.mark.parametrize("remembered", (False, True))
def test_management_pages_have_no_dashboard_tools(client, login, org_admin, org, studio, remembered):
    # The tool cluster is dashboard-only; no other page sprouts a search box in
    # the header, with or without a remembered studio (which used to add a
    # "Search reports" box that only navigated to the catalog).
    login(org_admin)
    if remembered:
        client.get(f"/s/{org.slug}/{studio.slug}/")
    for path in ("/", f"/orgs/{org.slug}/settings/invites", f"/orgs/{org.slug}/settings/members",
                 f"/s/{org.slug}/{studio.slug}/settings/members"):
        html = client.get(path, {"org": org.slug} if path == "/" else {}).content.decode()
        header = html.split('<header class="tl-console-header"', 1)[1].split("</header>", 1)[0]
        assert 'id="searchInput"' not in html, path
        assert "tl-console-search" not in html, path
        assert "data-console-search" not in header, path
        assert "\u2315" not in header and "⌕" not in header, path
        # Only the jump control, and only where a studio is in view.
        assert ("data-console-jump" in header) is (remembered or "/s/" in path), path


def test_own_search_fields_are_marked_for_the_shortcut(client, login, org_admin, org, studio):
    # Ctrl+K focuses the field carrying this marker; pages with none open the jump list.
    login(org_admin)
    base = f"/s/{org.slug}/{studio.slug}"
    for path in (f"/orgs/{org.slug}/settings/invites", f"/orgs/{org.slug}/settings/members"):
        assert "data-page-search" in client.get(path).content.decode(), path
    assert "data-page-search" not in client.get(f"{base}/settings/members").content.decode()
    # Fields that render only with data are marked in their templates, and read "Search ...".
    for template, marker in (
        ("reports/metrics.html", 'id="metSearch" class="ui-input" data-page-search'),
        ("reports/analytics.html", 'id="analyticsSearch" class="ui-input analytics-search" data-page-search'),
        ("reports/my_deliveries.html", 'name="q" data-page-search'),
        ("operator/logs.html", 'name="q" type="search" data-page-search'),
    ):
        source = (settings.BASE_DIR / "templates" / template).read_text(encoding="utf-8")
        assert marker in source, template
        assert "Filter metrics" not in source and "Filter reports" not in source, template
