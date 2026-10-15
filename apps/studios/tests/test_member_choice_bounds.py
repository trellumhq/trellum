"""Studio members screen lists everyone; bounds behind it are invisible."""
import pytest
from django.urls import reverse

from apps.orgs.models import OrgMembership
from apps.studios.models import StudioMembership

pytestmark = pytest.mark.django_db


def test_choices_api_search_offpage_selection_and_browse_route(login, org_admin, studio, org, make_user):
    users = [make_user(f"choice{i:02}@example.test") for i in range(30)]
    OrgMembership.objects.bulk_create([OrgMembership(user=u, org=org, role="member") for u in users])
    client = login(org_admin)
    api = reverse("studio-member-choices-api", args=[org.slug, studio.slug])
    response = client.get(api, {"selected": users[-1].pk})
    data = response.json()
    assert len(data["results"]) == 25 and data["selected"]["value"] == str(users[-1].pk)
    assert response.headers["Cache-Control"] == "private, no-store"
    filtered = client.get(api, {"q": "choice29"}).json()
    assert [item["value"] for item in filtered["results"]] == [str(users[-1].pk)]
    native = reverse("studio-member-choices", args=[org.slug, studio.slug])
    response = client.post(native, {"user_id": users[-1].pk, "role": "developer", "page": "2", "q": "choice"})
    assert response.status_code == 200
    assert response.context["selected"].user_id == users[-1].pk
    assert response.context["role"] == "developer" and response.context["page"].number == 2
    assert not StudioMembership.objects.filter(user__in=users, studio=studio).exists()
    invalid = client.post(native, {"user_id": users[-1].pk, "role": "bad"})
    assert invalid.status_code == 400 and invalid.context["role"] == "bad"
    # Even an unknown browse action never enters the setter.
    assert client.post(native, {"user_id": users[-1].pk, "role": "admin", "action": "save"}).status_code == 200
    assert not StudioMembership.objects.filter(user=users[-1], studio=studio).exists()
    StudioMembership.objects.create(user=users[-1], studio=studio, role="viewer")
    data = client.get(api, {"selected": users[-1].pk, "q": "choice29"}).json()
    assert data["selected"] is None and data["results"] == []
    assert client.post(api, {}).status_code == 405


def test_members_screen_lists_everyone_without_toolbar_pager_or_browse(login, org_admin, studio, org, make_user):
    users = [make_user(f"direct{i:02}@example.test") for i in range(55)]
    OrgMembership.objects.bulk_create([OrgMembership(user=u, org=org, role="member") for u in users])
    StudioMembership.objects.bulk_create([StudioMembership(user=u, studio=studio, role="viewer") for u in users[:54]])
    client = login(org_admin)
    path = reverse("studio-members", args=[org.slug, studio.slug])
    response = client.get(path)
    assert len(response.context["rows"]) == 54
    html = response.content.decode()
    for gone in ("Search direct members", "ui-pager", "settings/members/choices", "Choose a person", "Add member"):
        assert gone not in html
    assert " people</button>" not in html
    assert 'type="submit">Add</button>' in html
    assert "api/member-choices" not in html and "direct members." not in html
    assert [m.user_id for m in response.context["org_users"] if m.user_id == users[-1].pk] == [users[-1].pk]
    setter = f"/s/{org.slug}/{studio.slug}/settings/members/set"
    assert client.post(setter, {"user_id": "bad", "role": "admin"}).status_code == 404


def test_members_screen_caps_rows_and_switches_picker_to_server_search(login, org_admin, studio, org, make_user, monkeypatch):
    users = [make_user(f"cap{i:02}@example.test") for i in range(12)]
    OrgMembership.objects.bulk_create([OrgMembership(user=u, org=org, role="member") for u in users])
    StudioMembership.objects.bulk_create([StudioMembership(user=u, studio=studio, role="viewer") for u in users[:6]])
    eligible = OrgMembership.objects.filter(org=org).exclude(user__studio_memberships__studio=studio).count()
    client = login(org_admin)
    path = reverse("studio-members", args=[org.slug, studio.slug])
    monkeypatch.setattr("apps.studios.views.MEMBER_LIST_CAP", 5)
    response = client.get(path)
    html = response.content.decode()
    assert len(response.context["rows"]) == 5 and "Showing the first 5 direct members." in html
    assert len(response.context["org_users"]) == 5 and "api/member-choices" in html
    # At exactly the cap nothing is truncated and the picker searches client-side.
    monkeypatch.setattr("apps.studios.views.MEMBER_LIST_CAP", max(eligible, 6))
    response = client.get(path)
    html = response.content.decode()
    assert len(response.context["rows"]) == 6 and len(response.context["org_users"]) == eligible
    assert "direct members." not in html and "api/member-choices" not in html
