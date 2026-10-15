"""Authorized bounded choices and native browsing preserve explicit drafts."""
import pytest
from django.urls import reverse

from apps.orgs.models import OrgMembership
from apps.studios.models import StudioMembership

pytestmark = pytest.mark.django_db


def test_search_endpoint_answers_one_bounded_set_labelled_by_email(login, org_admin, studio, org, make_user):
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
    assert filtered["results"][0]["label"] == users[-1].email and not filtered["results"][0]["hint"]
    StudioMembership.objects.create(user=users[-1], studio=studio, role="viewer")
    data = client.get(api, {"selected": users[-1].pk, "q": "choice29"}).json()
    assert data["selected"] is None and data["results"] == []
    assert client.post(api, {}).status_code == 405


def test_add_member_picker_lists_everyone_inline_like_a_plain_select(login, org_admin, studio, org, make_user):
    users = [make_user(f"inline{i:02}@example.test") for i in range(60)]
    OrgMembership.objects.bulk_create([OrgMembership(user=u, org=org, role="member") for u in users])
    html = login(org_admin).get(reverse("studio-members", args=[org.slug, studio.slug])).content.decode()
    assert all(f">{u.email}</option>" in html for u in users)
    assert "data-choice-source" not in html and "Choose a person" not in html
    assert "Browse all" not in html and "Previous" not in html


def test_add_member_picker_searches_only_beyond_the_inline_limit(login, org_admin, studio, org, make_user, monkeypatch):
    import apps.core.choice_requests as module
    monkeypatch.setattr(module, "INLINE_CHOICE_LIMIT", 30)
    users = [make_user(f"huge{i:02}@example.test") for i in range(40)]
    OrgMembership.objects.bulk_create([OrgMembership(user=u, org=org, role="member") for u in users])
    html = login(org_admin).get(reverse("studio-members", args=[org.slug, studio.slug])).content.decode()
    assert f'data-choice-source="/s/{org.slug}/{studio.slug}/api/member-choices"' in html
    assert html.count("@example.test</option>") <= 26
    assert "Browse all" not in html and "Previous" not in html and "Next" not in html


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
