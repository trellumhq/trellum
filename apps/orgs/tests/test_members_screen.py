"""The Members reference screen: effective access with source attribution."""
import re

import pytest

from apps.core import roles

pytestmark = pytest.mark.django_db


def _row_for(html: str, email: str) -> str:
    # Search inside the table body: the shell user-menu also carries an email.
    start = html.index(email, html.index("<tbody>"))
    return html[start : html.index("</tr>", start)]


class TestEffectiveAccessColumn:
    def test_direct_role_shown_as_direct(self, login, org_admin, org, member, studio, grant_studio):
        grant_studio(member, studio, roles.DEVELOPER)
        html = login(org_admin).get(f"/orgs/{org.slug}/settings/members").content.decode()
        row = _row_for(html, member.email)
        assert f"{studio.slug}: developer" in row
        assert "via direct" in row

    def test_group_role_shown_with_group_source(
        self, login, org_admin, org, member, studio, make_group, attach_group
    ):
        attach_group(member, make_group("BAs", default_studio_role=roles.VIEWER))
        html = login(org_admin).get(f"/orgs/{org.slug}/settings/members").content.decode()
        row = _row_for(html, member.email)
        assert f"{studio.slug}: viewer" in row
        assert "via group" in row

    def test_max_wins_direct_over_weaker_group(
        self, login, org_admin, org, member, studio, make_group, attach_group, grant_studio
    ):
        grant_studio(member, studio, roles.ADMIN)
        attach_group(member, make_group("Viewers", grants=[(studio, roles.VIEWER)]))
        row = _row_for(
            login(org_admin).get(f"/orgs/{org.slug}/settings/members").content.decode(),
            member.email,
        )
        assert f"{studio.slug}: admin" in row
        assert "via direct" in row

    def test_org_admin_attributed(self, login, org_admin, org, studio):
        row = _row_for(
            login(org_admin).get(f"/orgs/{org.slug}/settings/members").content.decode(),
            org_admin.email,
        )
        assert f"{studio.slug}: admin" in row
        assert "via org admin" in row

    def test_no_access_badge_for_plain_member(self, login, org_admin, org, member, studio):
        row = _row_for(
            login(org_admin).get(f"/orgs/{org.slug}/settings/members").content.decode(),
            member.email,
        )
        assert "no access" in row

    def test_group_pills_link_to_group_detail(
        self, login, org_admin, org, member, make_group, attach_group
    ):
        group = make_group("Demo BA")
        attach_group(member, group)
        row = _row_for(
            login(org_admin).get(f"/orgs/{org.slug}/settings/members").content.decode(),
            member.email,
        )
        assert f"/orgs/{org.slug}/settings/groups/{group.pk}" in row

    def test_org_role_select_has_no_auto_submit(self, login, org_admin, org, member):
        html = login(org_admin).get(f"/orgs/{org.slug}/settings/members").content.decode()
        # Scoped to the page content: the shell's own theme select
        # (unconditional on every page, studio-theming-design.md §8) submits
        # inline on change by design -- a different, deliberate control.
        main = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.DOTALL)
        assert main is not None
        main = main.group(1)
        assert "this.form.submit()" not in main  # explicit Save buttons only
