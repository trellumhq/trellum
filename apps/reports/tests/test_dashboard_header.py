"""The dashboard has ONE header.

It used to render the global shell and then a second solid-accent bar
underneath, which duplicated logout, duplicated the studio name, and used a
different rail and type scale. The controls moved into the shell; these tests
keep the second bar from coming back.
"""
import re

import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


def _dashboard_html(client, login, org_admin, org, studio) -> str:
    login(org_admin)
    url = reverse("studio-dashboard", args=[org.slug, studio.slug])
    return client.get(url).content.decode()


def test_no_second_header_bar():
    html = (settings.BASE_DIR / "templates" / "portal" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "portal-top-bar" not in html
    assert "portal-top-inner" not in html


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


def test_category_row_does_not_hide_its_own_scrollbar():
    # The row used to be overflow-x:auto with the scrollbar suppressed, so a
    # studio with 20+ categories hid most of them off the right edge with no
    # affordance at all. Tabs that do not fit now go into the More menu.
    css = (settings.BASE_DIR / "static" / "portal.css").read_text(encoding="utf-8")
    folder_inner = css.split(".folder-inner {", 1)[1].split("}", 1)[0]
    assert "overflow-x: auto" not in folder_inner
    assert ".folder-inner::-webkit-scrollbar" not in css


def test_overflow_menu_exists_and_hides_filtered_items():
    js = (settings.BASE_DIR / "static" / "portal.js").read_text(encoding="utf-8")
    css = (settings.BASE_DIR / "static" / "portal.css").read_text(encoding="utf-8")
    assert "function splitTabs" in js, "the fit calculation is gone"
    assert "function filterMoreMenu" in js
    # columnGap: the row is a flex container with a gap that offsetWidth does
    # not include; leaving it out overfills the row by gap x tabs.
    assert "columnGap" in js, "splitTabs must account for the flex gap"
    # A UA [hidden] rule loses to an author display:flex, so the filter would
    # compute the right answer and still paint every item.
    assert ".folder-menu-item[hidden]" in css


def test_studio_pills_cannot_overflow_unreachably():
    css = (settings.BASE_DIR / "static" / "portal.css").read_text(encoding="utf-8")
    studio_inner = css.split(".studio-inner {", 1)[1].split("}", 1)[0]
    assert "flex-wrap: wrap" in studio_inner


def test_sticky_offsets_have_no_magic_numbers():
    # The old chain hardcoded 52px and 94px for bars whose real heights had
    # drifted. Offsets now derive from tokens.
    css = (settings.BASE_DIR / "static" / "portal.css").read_text(encoding="utf-8")
    for rule in re.findall(r"position:\s*sticky;\s*top:\s*([^;]+);", css):
        assert not re.search(r"\+\s*\d+px", rule), f"magic sticky offset: {rule.strip()}"


def test_folder_inner_can_shrink_for_split_tabs():
    # A flex item's default min-width is auto, which floors it at full tab
    # content width -- #folderTabs would never actually shrink for anything
    # placed beside it in the rail, so splitTabs's clientWidth read would
    # never see a narrower bar and never move tabs into the More menu.
    css = (settings.BASE_DIR / "static" / "portal.css").read_text(encoding="utf-8")
    folder_inner = css.split(".folder-inner {", 1)[1].split("}", 1)[0]
    assert "min-width: 0" in folder_inner


def test_catalog_rows_are_fluid():
    # Catalog controls and report content use the full console content area.
    css = (settings.BASE_DIR / "static" / "portal.css").read_text(encoding="utf-8")
    rail_pattern = "max-width: none; margin: 0"
    for selector in (".folder-rail {", ".studio-inner {", ".portal-body-inner {"):
        block = css.split(selector, 1)[1].split("}", 1)[0]
        assert rail_pattern in block, f"{selector} is not fluid"
    # .folder-bar itself stays full-bleed, like .studio-bar -- only the inner
    # wrapper is capped, so the sticky background/border still spans edge to
    # edge under the centered content.
    folder_bar = css.split(".folder-bar {", 1)[1].split("}", 1)[0]
    assert "max-width" not in folder_bar
