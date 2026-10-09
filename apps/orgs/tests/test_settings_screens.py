"""Org settings screens rebuilt against the design system: the badge
vocabulary, the group-grant summary strip, copy-to-clipboard, guarded
destructive actions and the settings-hub redirect."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Invitation
from apps.core import roles

pytestmark = pytest.mark.django_db


@pytest.fixture
def html(login, org_admin):
    def _get(path):
        resp = login(org_admin).get(path)
        assert resp.status_code == 200
        return resp.content.decode()

    return _get


class TestGroupsList:
    def test_badges_for_every_grant_kind(self, html, org, studio, make_group):
        make_group(
            "Demo BA",
            org_role=roles.ORG_ADMIN,
            default_studio_role=roles.VIEWER,
            grants=[(studio, roles.DEVELOPER)],
        )
        page = html(f"/orgs/{org.slug}/settings/groups")
        assert 'class="ui-table"' in page
        assert '<span class="ui-badge accent">org admin</span>' in page
        assert f'<span class="ui-badge info">all studios: {roles.VIEWER}</span>' in page
        assert '<td data-label="Overrides">1 studio</td>' in page
        # No legacy badge system on the rebuilt screen.
        assert 'class="pill' not in page

    def test_group_name_links_to_detail(self, html, org, make_group):
        group = make_group("Devs")
        page = html(f"/orgs/{org.slug}/settings/groups")
        assert f'href="/orgs/{org.slug}/settings/groups/{group.pk}"' in page

    def test_empty_state_offers_the_cta(self, html, org):
        page = html(f"/orgs/{org.slug}/settings/groups")
        assert "ui-empty" in page
        assert "+ New group" in page


class TestGroupDetailSummaryStrip:
    def test_strip_shows_every_grant_kind(self, html, org, studio, make_group):
        group = make_group(
            "Demo BA",
            org_role=roles.ORG_ADMIN,
            default_studio_role=roles.VIEWER,
            grants=[(studio, roles.DEVELOPER)],
        )
        page = html(f"/orgs/{org.slug}/settings/groups/{group.pk}")
        start = page.index("Access summary")
        summary = page[start : page.index("<h2>Group settings", start)]
        assert "organization admin" in summary
        assert f"all studios: {roles.VIEWER}" in summary
        assert f"{studio.slug}: {roles.DEVELOPER}" in summary
        assert "Access is additive" in summary

    def test_strip_warns_when_the_group_grants_nothing(self, html, org, make_group):
        group = make_group("Empty")
        page = html(f"/orgs/{org.slug}/settings/groups/{group.pk}")
        start = page.index("Access summary")
        summary = page[start : page.index("<h2>Group settings", start)]
        assert '<span class="ui-badge warn">No access yet</span>' in summary

    def test_destructive_buttons_are_guarded(
        self, html, org, studio, member, make_group, attach_group
    ):
        group = make_group("Devs", grants=[(studio, roles.DEVELOPER)])
        attach_group(member, group)
        overview = html(f"/orgs/{org.slug}/settings/groups/{group.pk}")
        assert "Danger zone" in overview
        assert (
            f'action="/orgs/{org.slug}/settings/groups/{group.pk}/delete"' in overview
        )
        assert "return confirm('Delete group" in overview

        members = html(f"/orgs/{org.slug}/settings/groups/{group.pk}?tab=members")
        assert 'name="action" value="remove_user"' in members
        assert "return confirm('Remove " in members


class TestInvitesScreen:
    @pytest.fixture
    def invite(self, org, org_admin):
        return Invitation.objects.create(
            org=org, email="newbie@demo.example", org_role=roles.ORG_MEMBER,
            invited_by=org_admin,
        )

    def test_pending_row_has_a_copy_button(self, html, org, invite):
        page = html(f"/orgs/{org.slug}/settings/invites")
        assert "data-ui-copy" in page
        assert "ui-copy-done" in page  # the inline "copied ✓" feedback
        assert 'class="ui-table"' in page

    def test_new_invite_link_panel_has_a_copy_button(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/settings/invites",
            {"email": "fresh@demo.example", "org_role": roles.ORG_MEMBER},
        )
        page = resp.content.decode()
        assert "data-ui-copy" in page
        assert "/invite/" in page

    def test_expired_invite_gets_a_warn_badge(self, html, org, invite):
        invite.expires_at = timezone.now() - timedelta(days=1)
        invite.save(update_fields=["expires_at"])
        page = html(f"/orgs/{org.slug}/settings/invites")
        assert '<span class="ui-badge warn"' in page
        assert ">expired</span>" in page

    def test_revoke_is_guarded(self, html, org, invite):
        page = html(f"/orgs/{org.slug}/settings/invites")
        assert "return confirm(" in page
        assert f"/settings/invites/{invite.pk}/revoke" in page

    def test_empty_state(self, html, org):
        assert "ui-empty" in html(f"/orgs/{org.slug}/settings/invites")


class TestSectionedForms:
    def test_sso_sections_and_check_rows(self, html, org):
        page = html(f"/orgs/{org.slug}/settings/sso")
        for heading in ("App registration", "Access rules", "Group mapping"):
            assert heading in page
        assert 'class="ui-check-row"' in page
        assert "display:flex;gap:8px;align-items:center" not in page
        assert "data-ui-copy" in page  # redirect URI is copyable

    def test_assistant_sections_and_check_rows(self, html, org):
        page = html(f"/orgs/{org.slug}/settings/assistant")
        assert "Provider" in page and "Budgets" in page
        assert "AI settings" in page
        assert "shared provider and model for report chats and alert evaluations" in page
        assert "Enable AI for chats and alerts in this organization" in page
        assert "strictly read-only" not in page
        assert 'href="/orgs/demo/settings/assistant"' in page
        assert '<span class="tl-console-label">AI settings</span>' in page
        assert 'AI Assistant' not in page
        assert 'class="ui-check-row"' in page
        assert "display:flex;gap:8px;align-items:center" not in page


class TestSettingsHub:
    def test_org_settings_redirects_to_members(self, login, org_admin, org):
        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/")
        assert resp.status_code == 302
        assert resp.url == f"/orgs/{org.slug}/settings/members"
