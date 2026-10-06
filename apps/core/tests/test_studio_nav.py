"""The studio sidebar (templates/_console_nav.html): one navigation for the
whole studio and for console-mounted reports.

Reports · Operations · Report Analytics · Metrics · Experiments · Annotations, then
the four direct studio settings links —
every studio-scoped management page carries it in the global shell, and the
active link is the page you are on. Links are pages with URLs,
never JS view modes; gates mirror the pages they lead to (Analytics
developer/admin, studio settings admin-only) and never relax them.
"""
import re

import pytest

from apps.core import roles

pytestmark = pytest.mark.django_db


@pytest.fixture
def viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("viewer@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


@pytest.fixture
def developer(make_user, org, studio_tree, grant_studio):
    user = make_user("dev@demo.example", org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
    return user


@pytest.fixture
def studio_admin(make_user, org, studio_tree, grant_studio):
    user = make_user("studioadmin@demo.example", org=org)
    grant_studio(user, studio_tree, roles.ADMIN)
    return user


@pytest.fixture
def prefix(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}"


def _tab_bar(html: str) -> str:
    assert 'class="tl-console-nav"' in html, "no console navigation on the page"
    return html.split('class="tl-console-nav"', 1)[1].split("</nav>", 1)[0]


def _active_tab(html: str) -> str:
    """Label of the one tab marked aria-current."""
    labels = re.findall(
        r'<a class="tl-console-nav-link[^"]* active[^"]*"[^>]*aria-current="page"[^>]*>.*?'
        r'<span class="tl-console-label">([^<]+)',
        _tab_bar(html),
        re.DOTALL,
    )
    assert len(labels) == 1, f"expected exactly one active tab, got {labels}"
    return labels[0].strip()


class TestTabBarOnEveryStudioPage:
    """Each adopted page: 200, tab bar present, correct tab active."""

    def test_dashboard_reports_tab(self, login, viewer, prefix, report_row):
        html = login(viewer).get(f"{prefix}/").content.decode()
        assert _active_tab(html) == "Reports"

    def test_operations_page(self, login, viewer, prefix, report_row):
        resp = login(viewer).get(f"{prefix}/operations")
        assert resp.status_code == 200
        html = resp.content.decode()
        assert _active_tab(html) == "Operations"
        # The template boots portal.js into the ops surface via the flag,
        # not a query param.
        assert '"initial_view": "ops"' in html

    def test_analytics_page(self, login, developer, prefix, report_row):
        resp = login(developer).get(f"{prefix}/analytics")
        assert resp.status_code == 200
        assert _active_tab(resp.content.decode()) == "Report Analytics"

    def test_experiments_page(self, login, viewer, prefix):
        resp = login(viewer).get(f"{prefix}/experiments")
        assert resp.status_code == 200
        assert _active_tab(resp.content.decode()) == "Experiments"

    def test_annotations_page(self, login, viewer, prefix):
        resp = login(viewer).get(f"{prefix}/annotations")
        assert resp.status_code == 200
        assert _active_tab(resp.content.decode()) == "Annotations"

    def test_settings_pages(self, login, studio_admin, prefix):
        for path, label in (
            ("/settings/repo", "Repository"),
            ("/settings/datasources", "Data sources"),
            ("/settings/members", "Members"),
            ("/settings/appearance", "Report theme"),
        ):
            resp = login(studio_admin).get(f"{prefix}{path}")
            assert resp.status_code == 200, path
            assert _active_tab(resp.content.decode()) == label, path

    def test_org_pages_replace_studio_navigation(self, login, org_admin, org):
        html = login(org_admin).get("/", {"org": org.slug}).content.decode()
        nav = _tab_bar(html)
        assert "Studios" in nav
        assert "/operations" not in nav


class TestOperationsUrl:
    """?view=ops / ?view=health were the client-side view-mode params;
    Operations is a page now and old deep links redirect to it."""

    @pytest.mark.parametrize("legacy", ["ops", "health"])
    def test_old_view_param_redirects(self, login, viewer, prefix, report_row, legacy):
        resp = login(viewer).get(f"{prefix}/", {"view": legacy})
        assert resp.status_code == 302
        assert resp.url == f"{prefix}/operations"

    def test_redirect_keeps_other_params(self, login, viewer, prefix, report_row):
        resp = login(viewer).get(f"{prefix}/", {"view": "ops", "tab": "Marketing"})
        assert resp.status_code == 302
        assert resp.url == f"{prefix}/operations?tab=Marketing"

    def test_list_view_param_still_serves_the_dashboard(
        self, login, viewer, prefix, report_row
    ):
        # ?view=list is launcher state portal.js reads client-side — it must
        # not bounce.
        assert login(viewer).get(f"{prefix}/", {"view": "list"}).status_code == 200


class TestScopeGuard:
    """The Studio settings group is studio-admin only — exactly the permission gate
    the settings pages themselves have. Nobody below admin gets the tab, and
    the Analytics tab mirrors its page's developer floor."""

    def test_viewer_sees_no_settings_tab(self, login, viewer, prefix, report_row):
        tabs = _tab_bar(login(viewer).get(f"{prefix}/").content.decode())
        assert "/settings/" not in tabs
        assert "Studio settings" not in tabs
        assert "/analytics" not in tabs

    def test_developer_sees_no_settings_tab(self, login, developer, prefix, report_row):
        tabs = _tab_bar(login(developer).get(f"{prefix}/").content.decode())
        assert "/settings/" not in tabs
        assert "Studio settings" not in tabs
        assert "/analytics" in tabs  # but Analytics is theirs

    def test_studio_admin_sees_the_settings_tab(
        self, login, studio_admin, prefix, report_row
    ):
        tabs = _tab_bar(login(studio_admin).get(f"{prefix}/").content.decode())
        assert f"{prefix}/settings/repo" in tabs

    def test_org_admin_sees_the_settings_tab(self, login, org_admin, prefix, report_row):
        # Org admins are implicit studio admins (effective_roles.role_for).
        tabs = _tab_bar(login(org_admin).get(f"{prefix}/").content.decode())
        assert f"{prefix}/settings/repo" in tabs


class TestFeatureTabs:
    """Experiments and annotations are ordinary studio tabs."""

    def test_tabs_have_no_tier_chips(self, login, viewer, prefix, report_row):
        tabs = _tab_bar(login(viewer).get(f"{prefix}/").content.decode())
        assert f"{prefix}/experiments" in tabs
        assert f"{prefix}/annotations" in tabs
        assert "◆" not in tabs
