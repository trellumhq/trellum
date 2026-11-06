"""Synthetic policy settings checks; no source connection or provider calls."""
import pytest
from apps.assistant.models import SourceReadGrant
from apps.datasources.models import DataSource
from apps.orgs.models import PermissionGroup

pytestmark = pytest.mark.django_db


@pytest.fixture
def grant_editor(login, org_admin, studio_tree):
    client = login(org_admin)
    source = DataSource.objects.create(studio=studio_tree, name="warehouse", type="postgres", config={"host": "db.invalid"}, credentials={"password": "never-show-this"})
    path = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/settings/assistant-sources"
    return client, source, path


def test_save_explicit_principals_revision_and_audit(grant_editor, viewer):
    from apps.core.models import AuditLog
    client, source, path = grant_editor
    response = client.get(path)
    assert response.status_code == 200
    assert b"never-show-this" not in response.content
    assert b"data-settings-save" in response.content
    data = {"datasource": source.pk, "revision": 0, "relations": "analytics.sales", "users": [viewer.pk], "enabled": "on"}
    saved = client.post(path, data, HTTP_X_TRELLUM_FORM="1")
    assert saved.status_code == 200, saved.content
    grant = SourceReadGrant.objects.get(datasource=source)
    assert grant.enabled and grant.revision == 1 and list(grant.users.all()) == [viewer]
    assert AuditLog.objects.filter(action="assistant.source_grant.update").exists()
    assert client.post(path, data, HTTP_X_TRELLUM_FORM="1").status_code == 409
    data.update(revision=1, enabled="")
    assert client.post(path, data, HTTP_X_TRELLUM_FORM="1").status_code == 200
    grant.refresh_from_db()
    assert not grant.enabled and grant.revision == 2


def test_tenant_scope_relations_and_no_default_access(grant_editor, other_org, make_user):
    client, source, path = grant_editor
    outsider = make_user("outside@example.invalid", org=other_org)
    group = PermissionGroup.objects.create(org=other_org, name="Other")
    for extra in ({}, {"users": [outsider.pk]}, {"groups": [group.pk]}, {"relations": "public.a\npg_catalog.pg_class"}, {"relations": "public.a\npublic.a"}):
        data = {"datasource": source.pk, "revision": 0, "relations": "public.a", "enabled": "on", **extra}
        response = client.post(path, data, HTTP_X_TRELLUM_FORM="1")
        assert response.status_code == 400, response.content
    assert not SourceReadGrant.objects.exists()
    assert client.post(path, {"datasource": source.pk, "revision": 0, "relations": "public.a"}, HTTP_X_TRELLUM_FORM="1").status_code == 200
    assert not SourceReadGrant.objects.get().users.exists()


def test_admin_only_choices_and_unsupported_empty(grant_editor, login, viewer, studio_tree):
    client, source, path = grant_editor
    source.config = {"ssh_host": "bastion.invalid"}
    source.save()
    assert b"No supported sources" in client.get(path).content
    assert not client.get(path + "/sources?q=warehouse").json()["results"]
    assert client.post(path, {"datasource": source.pk, "revision": 0, "relations": "public.a"}, HTTP_X_TRELLUM_FORM="1").status_code == 400
    for suffix in ("", "/sources", "/principals"):
        assert login(viewer).get(path + suffix).status_code == 403


def test_choice_search_is_org_scoped(grant_editor, viewer, other_org, make_user):
    client, source, path = grant_editor
    outside = make_user("outside@example.invalid", org=other_org)
    payload = client.get(path + f"/principals?q=view&selected_people={outside.pk}").json()
    assert viewer.pk in [row["id"] for row in payload["members"]]
    assert not payload["selected"]["members"]


def test_revoke_can_remove_a_user_who_left_organization(grant_editor, viewer):
    from apps.orgs.models import OrgMembership
    client, source, path = grant_editor
    grant = SourceReadGrant.objects.create(studio=source.studio, datasource=source, enabled=True, relations=['analytics.sales'])
    grant.users.add(viewer)
    OrgMembership.objects.filter(user=viewer, org=source.studio.org).delete()
    response = client.post(path, {'datasource': source.pk, 'revision': 1, 'relations': 'analytics.sales'}, HTTP_X_TRELLUM_FORM='1')
    assert response.status_code == 200, response.content
    grant.refresh_from_db()
    assert not grant.enabled and not grant.users.exists() and grant.revision == 2


@pytest.mark.heavy
def test_large_choices_are_bounded_and_use_remote_search(grant_editor, django_assert_max_num_queries):
    from django.contrib.auth import get_user_model
    from apps.orgs.models import OrgMembership
    client, source, path = grant_editor
    users = get_user_model().objects.bulk_create([get_user_model()(email=f'person-{i}@example.invalid') for i in range(510)])
    OrgMembership.objects.bulk_create([OrgMembership(user=u, org=source.studio.org) for u in users])
    with django_assert_max_num_queries(40):
        html = client.get(path).content
    assert b'data-recipient-source=' in html
    assert html.count(b'type="checkbox" name="users"') <= 500
    payload = client.get(path + '/principals?q=person-').json()
    assert len(payload['members']) == 25
    assert payload['pagination']['members']['has_next']
    response = client.post(path, {'datasource': source.pk, 'revision': 0, 'relations': 'analytics.sales', 'users': [u.pk for u in users]}, HTTP_X_TRELLUM_FORM='1')
    assert response.status_code == 400
    assert not SourceReadGrant.objects.exists()
