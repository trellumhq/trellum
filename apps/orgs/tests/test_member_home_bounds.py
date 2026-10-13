"""Bounded member access and visibility-scoped home health hints."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.orgs.models import OrgMembership
from apps.reports.models import Report
from apps.runner.models import Run
from apps.studios.models import Studio, StudioMembership

pytestmark = pytest.mark.django_db


def test_member_summary_and_editor_preserve_explicit_roles(login, org_admin, org, make_user):
    target = make_user("bounded@example.test")
    OrgMembership.objects.create(user=target, org=org, role="member")
    studios = [Studio.objects.create(org=org, slug=f"bounded{i:02}", name=f"Bounded {i}") for i in range(30)]
    StudioMembership.objects.bulk_create([StudioMembership(user=target, studio=s, role="viewer") for s in studios])
    client = login(org_admin)
    response = client.get(reverse("org-members", args=[org.slug]), {"q": "bounded@example"})
    row = response.context["member_rows"][0]
    assert len(row["effective"]) == 3
    assert row["effective_count"] == 30
    assert all(item["source"] == "direct" for item in row["effective"])
    assert b'id="studio-role-' not in response.content
    editor = reverse("org-member-studios", args=[org.slug, target.pk])
    response = client.get(editor)
    assert len(response.context["rows"]) == 25
    path = reverse("org-member-studio-role", args=[org.slug, target.pk])
    response = client.post(path, {"studio_id": studios[0].pk, "role": "developer", "return_to": "studios", "q": "Bounded", "page": "2"}, HTTP_X_TRELLUM_FORM="1")
    assert response.json()["refresh"] == [f"member-studio-effective-{studios[0].pk}"]
    assert StudioMembership.objects.get(user=target, studio=studios[-1]).role == "viewer"
    response = client.post(path, {"studio_id": studios[0].pk, "role": "", "return_to": "studios", "q": "Bounded", "page": "2"})
    assert response.status_code == 302 and response.url.startswith(editor + "?")
    assert "q=Bounded" in response.url and "page=2" in response.url
    assert StudioMembership.objects.get(user=target, studio=studios[-1]).role == "viewer"


def test_home_pages_before_health_and_joins_actual_report_studio(login, org_admin, org):
    studios = [Studio.objects.create(org=org, slug=f"card{i:02}", name=f"Card {i}") for i in range(30)]
    public = Report.objects.create(studio=studios[0], slug="public", audience="studio")
    Report.objects.create(studio=studios[0], slug="private", audience="private")
    terminal = Run.objects.create(report=public, studio=studios[-1], slug="public", status="success", created_at=timezone.now() - timedelta(hours=1))
    Run.objects.create(report=public, studio=studios[0], slug="public", status="running")
    absent = Report.objects.create(studio=studios[0], slug="absent", present_in_scan=False)
    Run.objects.create(report=absent, studio=studios[0], slug="absent", status="error")
    client = login(org_admin)
    response = client.get("/", {"org": org.slug})
    assert len(response.context["cards"]) == 24
    first = next(c for c in response.context["cards"] if c["studio"].pk == studios[0].pk)
    assert first["last_run"].pk == terminal.pk and first["report_count"] == 2
    response = client.get("/", {"org": org.slug, "q": "card29"})
    assert [c["studio"].pk for c in response.context["cards"]] == [studios[-1].pk]


def test_member_groups_summary_and_full_tab_are_bounded(login, org_admin, org, make_user):
    from apps.orgs.models import PermissionGroup, PermissionGroupMembership

    target = make_user("many-groups@example.test")
    OrgMembership.objects.create(user=target, org=org, role="member")
    groups = PermissionGroup.objects.bulk_create([PermissionGroup(org=org, name=f"Group {i:03}", default_studio_role="viewer") for i in range(100)])
    PermissionGroupMembership.objects.bulk_create([PermissionGroupMembership(user=target, group=group) for group in groups])
    client = login(org_admin)
    response = client.get(reverse("org-members", args=[org.slug]), {"q": "many-groups"})
    row = response.context["member_rows"][0]
    assert len(row["groups"]) == 3 and row["group_count"] == 100
    editor = reverse("org-member-studios", args=[org.slug, target.pk])
    response = client.get(editor, {"tab": "groups", "group_page": 4})
    assert len(response.context["group_page"].object_list) == 25
    assert response.context["group_total"] == 100
    assert groups[-1].name.encode() in response.content
    assert b'id="studio-role-' not in response.content
    response = client.get(editor, {"tab": "groups", "group_q": "099"})
    assert response.context["group_total"] == 1
    assert PermissionGroupMembership.objects.filter(user=target, group__org=org).count() == 100
