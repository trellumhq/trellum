"""Administrator settings preserve delivery policy and compatibility boundaries."""
import pytest
from django.test import Client, RequestFactory
from django.http import Http404

from apps.accounts.models import ApiKey
from apps.core import roles
from apps.core.models import AuditLog, InstanceConfig
from apps.orgs.models import OrgMembership
from apps.reports.deliveries import schedule_defaults

pytestmark = pytest.mark.django_db
ENHANCED = {"HTTP_X_TRELLUM_FORM": "1", "HTTP_ACCEPT": "application/json"}


def org_url(org):
    return f"/orgs/{org.slug}/settings/deliveries"


def test_policy_settings_visible_in_standard_navigation(login, org_admin, org, report_row):
    response = login(org_admin).get(org_url(org))
    assert response.status_code == 200
    assert response.context["console_page_title"] == "Delivery settings"
    assert f'href="{org_url(org)}" aria-current="page"' in response.content.decode()
    listing = login(org_admin).get(f"/s/{org.slug}/{report_row.studio.slug}/deliveries")
    assert b"Manage deliveries" not in listing.content
    assert b"delivery-policy-form" not in listing.content
    assert b"delivery-type-tabs" not in listing.content


@pytest.mark.parametrize("role", [roles.VIEWER, roles.DEVELOPER, None])
def test_org_policy_requires_org_admin(role, login, org, make_user, studio, grant_studio):
    actor = make_user("policy-member@example.test", org=org)
    if role:
        grant_studio(actor, studio, role)
    client = login(actor)
    assert client.get(org_url(org)).status_code == 403
    assert client.post(org_url(org), {"deliveries_paused": "on"}, **ENHANCED).status_code == 403
    org.refresh_from_db()
    assert not org.deliveries_paused
    assert not AuditLog.objects.filter(action="notify.delivery_policy_update").exists()


def test_group_org_admin_can_save(login, member, org, make_group, attach_group):
    attach_group(member, make_group("Policy administrators", org_role=roles.ORG_ADMIN))
    response = login(member).post(org_url(org), {"deliveries_paused": "on"}, **ENHANCED)
    assert response.status_code == 200
    org.refresh_from_db()
    assert org.deliveries_paused


def test_support_operator_cannot_edit_instance_policy(login, member):
    member.is_operator_flag = True
    member.save(update_fields=["is_operator_flag"])
    client = login(member)
    response = client.get("/instance/settings")
    assert response.status_code == 200
    assert b"delivery-policy-form" not in response.content
    assert client.post("/instance/settings", {"action": "delivery_policy", "deliveries_paused": "on"}, **ENHANCED).status_code == 404
    assert not InstanceConfig.load().deliveries_paused


@pytest.mark.parametrize("scope", [ApiKey.READ, ApiKey.READ_WRITE])
def test_policy_settings_reject_api_keys(client, org_admin, org, scope):
    _, secret = ApiKey.mint(user=org_admin, org=org, name="Policy", scopes=scope)
    headers = {"HTTP_AUTHORIZATION": f"Bearer {secret}"}
    for path in ("/instance/settings", org_url(org), "/deliveries"):
        assert client.get(path, **headers).status_code == 403
        assert client.post(path, {"action": "delivery_policy", "scope": "instance", "deliveries_paused": "on"}, **headers).status_code == 403
    assert not InstanceConfig.load().deliveries_paused
    org.refresh_from_db()
    assert not org.deliveries_paused


def test_direct_policy_guard_rejects_foreign_key_and_instance_key(org_admin, org, other_org, superuser):
    from types import SimpleNamespace
    from apps.core.views import instance_settings
    from apps.orgs.views import delivery_settings

    request = RequestFactory().post(org_url(other_org), {"deliveries_paused": "on"})
    request.user = org_admin
    request.api_key = SimpleNamespace(org_id=org.pk, can_write=True)
    with pytest.raises(Http404):
        delivery_settings(request, org_slug=other_org.slug)
    request.user = superuser
    request.POST = request.POST.copy()
    request.POST["action"] = "delivery_policy"
    with pytest.raises(Http404):
        instance_settings(request)


@pytest.mark.parametrize("enhanced", [False, True])
def test_instance_and_org_saves_inherit_and_bump_only_pause_changes(login, superuser, org_admin, org, enhanced):
    headers = ENHANCED if enhanced else {}
    instance_body = {"action": "delivery_policy", "deliveries_paused": "on",
                     "delivery_default_enabled": "false", "delivery_default_timezone": "Europe/Amsterdam"}
    response = login(superuser).post("/instance/settings", instance_body, **headers)
    assert response.status_code == (200 if enhanced else 302)
    if enhanced:
        assert "redirect" not in response.json()
        assert response.json()["values"]["delivery_default_enabled"] == "false"
    else:
        assert response.url == "/instance/settings#scheduled-deliveries"
    instance = InstanceConfig.load()
    assert instance.delivery_generation == 1
    assert schedule_defaults(org) == {"enabled": False, "timezone": "Europe/Amsterdam"}
    client = login(org_admin)
    assert client.post(org_url(org), {"delivery_default_enabled": "true", "delivery_default_timezone": "UTC"}, **headers).status_code == (200 if enhanced else 302)
    org.refresh_from_db()
    assert schedule_defaults(org) == {"enabled": True, "timezone": "UTC"}
    assert client.post(org_url(org), {"delivery_default_enabled": "", "delivery_default_timezone": ""}, **headers).status_code == (200 if enhanced else 302)
    org.refresh_from_db()
    assert org.delivery_default_enabled is None and org.delivery_default_timezone is None
    assert org.delivery_generation is None
    for paused, generation in (("on", 1), ("on", 1), ("", 2)):
        client.post(org_url(org), {"deliveries_paused": paused}, **headers)
        org.refresh_from_db()
        assert org.delivery_generation == generation
    assert AuditLog.objects.filter(action="notify.delivery_policy_update").count() == 6
    log = AuditLog.objects.filter(action="notify.delivery_policy_update", org=org).latest("pk")
    assert log.metadata["before"]["deliveries_paused"] is True
    assert log.metadata["after"]["deliveries_paused"] is False


@pytest.mark.parametrize("scope", ["instance", "org"])
@pytest.mark.parametrize("enhanced", [False, True])
def test_invalid_policy_keeps_draft_and_changes_nothing(scope, enhanced, login, superuser, org_admin, org):
    instance = InstanceConfig.load()
    target, path, actor = (instance, "/instance/settings", superuser) if scope == "instance" else (org, org_url(org), org_admin)
    body = {"action": "delivery_policy", "deliveries_paused": "on", "delivery_default_enabled": "false", "delivery_default_timezone": "Wrong/Zone"}
    response = login(actor).post(path, body, **(ENHANCED if enhanced else {}))
    if enhanced:
        assert response.status_code == 400
        assert "delivery_default_timezone" in response.json()["errors"]
    else:
        assert response.status_code == 200
        form = response.context["delivery_policy_form"]
        assert form.is_bound and form["delivery_default_timezone"].value() == "Wrong/Zone"
        assert form["deliveries_paused"].value() is True
        assert f'action="{path}"' in response.content.decode()
    target.refresh_from_db()
    assert not target.deliveries_paused
    assert target.delivery_generation is None
    assert target.delivery_default_enabled is None
    assert not AuditLog.objects.filter(action="notify.delivery_policy_update").exists()


def test_org_isolation_and_legacy_redirects_are_authorized(login, org_admin, superuser, org, other_org):
    client = login(org_admin)
    assert client.get(org_url(other_org)).status_code == 404
    assert client.post(org_url(other_org), {"deliveries_paused": "on"}, **ENHANCED).status_code == 404
    assert client.get("/deliveries").url == org_url(org)
    assert client.get(f"/deliveries?org={other_org.slug}").status_code == 404
    assert client.get("/deliveries?org=https://outside.example").status_code == 404
    assert client.get("/deliveries?return_to=https://outside.example").url == org_url(org)
    OrgMembership.objects.create(user=org_admin, org=other_org, role=roles.ORG_ADMIN)
    session = client.session
    session["active_org"] = other_org.slug
    session.save()
    assert client.get("/deliveries").url == org_url(other_org)
    assert client.get(f"/deliveries?org={org.slug}").url == org_url(org)
    assert login(superuser).get("/deliveries").url == "/instance/settings#scheduled-deliveries"
    assert login(superuser).get(f"/deliveries?org={org.slug}").url == org_url(org)


def test_policy_native_csrf_and_legacy_validation_canonical_action(org_admin, login, org):
    client = Client(enforce_csrf_checks=True)
    client.force_login(org_admin)
    client.get(org_url(org))
    body = {"deliveries_paused": "on"}
    assert client.post(org_url(org), body).status_code == 403
    assert client.post(org_url(org), body, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).url == org_url(org)
    response = login(org_admin).post("/deliveries", {"scope": org.pk, "delivery_default_timezone": "Wrong/Zone"})
    assert response.status_code == 200
    assert f'action="{org_url(org)}"' in response.content.decode()
