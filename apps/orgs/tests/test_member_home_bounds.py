"""Invisible bounds on the org members table and the org home grid.

The screens look exactly like they always did; these tests pin the query
optimisations and the (far-above-realistic) caps behind them.
"""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.orgs.models import OrgMembership
from apps.reports.models import Report
from apps.runner.models import Run
from apps.studios.models import Studio, StudioMembership

pytestmark = pytest.mark.django_db


def test_member_row_lists_every_studio_chip_and_role_form(login, org_admin, org, make_user):
    target = make_user("bounded@example.test")
    OrgMembership.objects.create(user=target, org=org, role="member")
    studios = [Studio.objects.create(org=org, slug=f"bounded{i:02}", name=f"Bounded {i}") for i in range(30)]
    StudioMembership.objects.bulk_create([StudioMembership(user=target, studio=s, role="viewer") for s in studios])
    client = login(org_admin)
    response = client.get(reverse("org-members", args=[org.slug]), {"q": "bounded@example"})
    row = response.context["member_rows"][0]
    assert len(row["effective"]) == 30
    assert len(row["studio_editor"]) == 30
    assert all(item["source"] == "direct" for item in row["effective"])
    html = response.content.decode()
    assert html.count(f'id="studio-role-{target.pk}-') == 30
    assert "Manage studio access" not in html and "View all" not in html


def test_member_row_lists_every_group_chip(login, org_admin, org, make_user):
    from apps.orgs.models import PermissionGroup, PermissionGroupMembership

    target = make_user("many-groups@example.test")
    OrgMembership.objects.create(user=target, org=org, role="member")
    groups = PermissionGroup.objects.bulk_create([PermissionGroup(org=org, name=f"Group {i:03}", default_studio_role="viewer") for i in range(12)])
    PermissionGroupMembership.objects.bulk_create([PermissionGroupMembership(user=target, group=group) for group in groups])
    response = login(org_admin).get(reverse("org-members", args=[org.slug]), {"q": "many-groups"})
    row = response.context["member_rows"][0]
    assert [g.pk for g in row["groups"]] == [g.pk for g in groups]
    assert "View all" not in response.content.decode()


def test_member_row_caps_studio_lists_and_links_to_full_page_only_when_huge(login, org_admin, org, make_user, monkeypatch):
    monkeypatch.setattr("apps.orgs.views.STUDIO_LIST_CAP", 5)
    target = make_user("huge@example.test")
    OrgMembership.objects.create(user=target, org=org, role="member")
    studios = [Studio.objects.create(org=org, slug=f"huge{i:02}", name=f"Huge {i}") for i in range(6)]
    StudioMembership.objects.bulk_create([StudioMembership(user=target, studio=s, role="viewer") for s in studios])
    client = login(org_admin)
    path = reverse("org-members", args=[org.slug])
    response = client.get(path, {"q": "huge@example"})
    row = response.context["member_rows"][0]
    assert len(row["effective"]) == 5 and len(row["studio_editor"]) == 5
    assert reverse("org-member-studios", args=[org.slug, target.pk]) in response.content.decode()
    monkeypatch.setattr("apps.orgs.views.STUDIO_LIST_CAP", 6)
    response = client.get(path, {"q": "huge@example"})
    assert len(response.context["member_rows"][0]["effective"]) == 6
    assert reverse("org-member-studios", args=[org.slug, target.pk]) not in response.content.decode()


def test_full_member_studio_page_stays_a_paged_route(login, org_admin, org, make_user):
    from apps.orgs.models import PermissionGroup, PermissionGroupMembership

    target = make_user("editor@example.test")
    OrgMembership.objects.create(user=target, org=org, role="member")
    studios = [Studio.objects.create(org=org, slug=f"edit{i:02}", name=f"Edit {i}") for i in range(30)]
    StudioMembership.objects.bulk_create([StudioMembership(user=target, studio=s, role="viewer") for s in studios])
    client = login(org_admin)
    editor = reverse("org-member-studios", args=[org.slug, target.pk])
    assert len(client.get(editor).context["rows"]) == 25
    path = reverse("org-member-studio-role", args=[org.slug, target.pk])
    response = client.post(path, {"studio_id": studios[0].pk, "role": "developer", "return_to": "studios", "q": "Edit", "page": "2"}, HTTP_X_TRELLUM_FORM="1")
    assert response.json()["refresh"] == [f"member-studio-effective-{studios[0].pk}"]
    response = client.post(path, {"studio_id": studios[0].pk, "role": "", "return_to": "studios", "q": "Edit", "page": "2"})
    assert response.status_code == 302 and response.url.startswith(editor + "?")
    assert "q=Edit" in response.url and "page=2" in response.url
    assert StudioMembership.objects.get(user=target, studio=studios[-1]).role == "viewer"
    groups = PermissionGroup.objects.bulk_create([PermissionGroup(org=org, name=f"Group {i:03}", default_studio_role="viewer") for i in range(30)])
    PermissionGroupMembership.objects.bulk_create([PermissionGroupMembership(user=target, group=group) for group in groups])
    response = client.get(editor, {"tab": "groups", "group_page": 2})
    assert len(response.context["group_page"].object_list) == 5 and response.context["group_total"] == 30
    assert client.get(editor, {"tab": "groups", "group_q": "029"}).context["group_total"] == 1


def test_home_lists_every_studio_and_joins_actual_report_studio(login, org_admin, org):
    studios = [Studio.objects.create(org=org, slug=f"card{i:02}", name=f"Card {i}") for i in range(30)]
    public = Report.objects.create(studio=studios[0], slug="public", audience="studio")
    Report.objects.create(studio=studios[0], slug="private", audience="private")
    terminal = Run.objects.create(report=public, studio=studios[-1], slug="public", status="success", created_at=timezone.now() - timedelta(hours=1))
    Run.objects.create(report=public, studio=studios[0], slug="public", status="running")
    absent = Report.objects.create(studio=studios[0], slug="absent", present_in_scan=False)
    Run.objects.create(report=absent, studio=studios[0], slug="absent", status="error")
    client = login(org_admin)
    response = client.get("/", {"org": org.slug})
    assert len(response.context["cards"]) == 30
    first = next(c for c in response.context["cards"] if c["studio"].pk == studios[0].pk)
    assert first["last_run"].pk == terminal.pk and first["report_count"] == 2
    html = response.content.decode()
    assert "Search studios" not in html and "ui-pager" not in html


def test_home_pages_only_beyond_the_studio_cap(login, org_admin, org, monkeypatch):
    monkeypatch.setattr("apps.orgs.views.STUDIO_LIST_CAP", 5)
    for i in range(6):
        Studio.objects.create(org=org, slug=f"cap{i:02}", name=f"Cap {i}")
    client = login(org_admin)
    response = client.get("/", {"org": org.slug})
    assert len(response.context["cards"]) == 5 and 'class="ui-pager"' in response.content.decode()
    assert len(client.get("/", {"org": org.slug, "page": 2}).context["cards"]) == 1
