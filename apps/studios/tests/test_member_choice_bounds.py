"""Authorized bounded choices and native browsing preserve explicit drafts."""
import pytest
from django.urls import reverse

from apps.orgs.models import OrgMembership
from apps.studios.models import StudioMembership

pytestmark = pytest.mark.django_db


def test_choices_search_offpage_selection_and_native_browse(login, org_admin, studio, org, make_user):
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


def test_direct_members_list_is_paged_and_person_ids_validate(login, org_admin, studio, org, make_user):
    users = [make_user(f"direct{i:02}@example.test") for i in range(55)]
    OrgMembership.objects.bulk_create([OrgMembership(user=u, org=org, role="member") for u in users])
    StudioMembership.objects.bulk_create([StudioMembership(user=u, studio=studio, role="viewer") for u in users])
    client = login(org_admin)
    path = reverse("studio-members", args=[org.slug, studio.slug])
    response = client.get(path)
    assert len(response.context["rows"]) == 50
    assert client.get(path, {"q": "direct54"}).context["total"] == 1
    setter = f"/s/{org.slug}/{studio.slug}/settings/members/set"
    assert client.post(setter, {"user_id": "bad", "role": "admin"}).status_code == 404
