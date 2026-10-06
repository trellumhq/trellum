"""List screens are bounded, searchable, and linkable.

Members, groups, studios, invites and data sources rendered every row they
had. The Members screen was the worst of them: its per-row work is
O(members x studios), with a nested form per pair.
"""
import pytest
from django.urls import reverse

from apps.orgs.views import PAGE_SIZE

pytestmark = pytest.mark.django_db


LIST_URLS = ["org-members", "org-groups", "org-studios", "org-invites"]


@pytest.mark.parametrize("url_name", LIST_URLS)
def test_list_screens_offer_search(client, login, org_admin, org, url_name):
    html = login(org_admin).get(reverse(url_name, args=[org.slug])).content.decode()
    assert 'name="q"' in html, f"{url_name} has no search box"


def test_members_are_paginated(client, login, org_admin, org, make_user):
    from apps.orgs.models import OrgMembership

    for i in range(PAGE_SIZE + 5):
        OrgMembership.objects.create(
            user=make_user(f"bulk{i}@demo.example"), org=org, role="member"
        )
    html = login(org_admin).get(reverse("org-members", args=[org.slug])).content.decode()
    assert html.count('name="role"') <= PAGE_SIZE + 1, "every member rendered on one page"
    assert "ui-pager" in html, "no pager on an over-length member list"

    # Page 2 exists and is different.
    page2 = login(org_admin).get(
        reverse("org-members", args=[org.slug]), {"page": 2}
    ).content.decode()
    assert "ui-pager" in page2
    assert page2 != html


def test_member_search_filters_and_is_linkable(client, login, org_admin, org, make_user):
    from apps.orgs.models import OrgMembership

    needle = make_user("findme@demo.example")
    OrgMembership.objects.create(user=needle, org=org, role="member")
    other = make_user("someone-else@demo.example")
    OrgMembership.objects.create(user=other, org=org, role="member")

    resp = login(org_admin).get(reverse("org-members", args=[org.slug]), {"q": "findme"})
    html = resp.content.decode()
    assert "findme@demo.example" in html
    assert "someone-else@demo.example" not in html


def test_search_and_paging_do_not_drop_each_other(client, login, org_admin, org, make_user):
    # Paging used to be built by hand per screen; a pager that forgets the
    # active filter silently shows the wrong rows.
    from apps.orgs.models import OrgMembership

    for i in range(PAGE_SIZE + 5):
        OrgMembership.objects.create(
            user=make_user(f"needle{i}@demo.example"), org=org, role="member"
        )
    html = login(org_admin).get(
        reverse("org-members", args=[org.slug]), {"q": "needle"}
    ).content.decode()
    if "ui-pager" in html and "page=" in html:
        assert "q=needle" in html, "the pager drops the active search"


class TestOperatorOrgCreation:
    def test_hub_no_longer_carries_the_create_form(self, client, login, superuser, org):
        html = login(superuser).get("/", {"org": org.slug}).content.decode()
        assert "Create an organization" not in html
        assert 'action="/orgs/create"' not in html

    def test_operator_page_renders_for_a_superuser(self, client, login, superuser):
        html = login(superuser).get(reverse("org-new")).content.decode()
        assert 'action="/orgs/create"' in html

    def test_operator_page_is_denied_to_an_org_admin(self, client, login, org_admin):
        resp = login(org_admin).get(reverse("org-new"))
        assert resp.status_code in (403, 404)


class TestMemberEditFeedback:
    """Changing a role used to be completely silent and reset the view."""

    def test_org_role_change_reports_success(self, client, login, org_admin, org, member):
        c = login(org_admin)
        resp = c.post(
            f"/orgs/{org.slug}/settings/members/{member.pk}/role",
            {"role": "admin"},
            follow=True,
        )
        text = resp.content.decode()
        assert member.email in text and "organization admin" in text

    def test_studio_role_change_reports_success(
        self, client, login, org_admin, org, member, studio
    ):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/settings/members/{member.pk}/studio-role",
            {"studio_id": studio.pk, "role": "developer"},
            follow=True,
        )
        assert "is now developer in" in resp.content.decode()

    def test_role_change_keeps_the_active_search(
        self, client, login, org_admin, org, member, studio
    ):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/settings/members/{member.pk}/studio-role",
            {"studio_id": studio.pk, "role": "viewer", "q": "member", "page": "1"},
        )
        # page=1 is the default and is dropped; the filter must survive.
        assert resp["Location"].endswith("?q=member")


def test_no_match_search_explains_itself(client, login, org_admin, org):
    html = login(org_admin).get(
        f"/orgs/{org.slug}/settings/members", {"q": "zzz-nobody"}
    ).content.decode()
    assert "No member matches" in html
    assert "Clear search" in html
