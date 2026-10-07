"""Management feature availability and authorization tests."""
from __future__ import annotations

import pytest
from django.conf import settings as django_settings
from django.contrib import admin as dj_admin
from django.test import override_settings

from apps.accounts import sso
from apps.accounts.adapters import admission
from apps.accounts.models import Invitation
from apps.accounts.forms import create_user_account
from apps.accounts.views import _apply_invitation
from apps.core import roles
from apps.core.audit import audit
from apps.core.models import AuditLog
from apps.core.permissions import effective_roles
from apps.orgs import domains, quotas
from apps.orgs.models import OrgSSOConfig, PermissionGroup

pytestmark = pytest.mark.django_db

FEATURE_PAGES = (
    "/orgs/{org}/settings/groups",
    "/orgs/{org}/settings/sso",
    "/orgs/{org}/settings/security",
    "/orgs/{org}/settings/audit",
)


class TestFeatureAvailability:
    @pytest.mark.parametrize("path", FEATURE_PAGES)
    def test_management_pages_are_available_to_org_admins(
        self, client, login, org_admin, org, path
    ):
        login(org_admin)
        assert client.get(path.format(org=org.slug)).status_code == 200

    def test_group_grants_widen_access(self, org, studio, make_user, make_group, attach_group):
        user = make_user("grouped@example.com", org=org)
        group = make_group("Analysts", grants=[(studio, roles.DEVELOPER)])
        attach_group(user, group)
        assert effective_roles(user, org).role_for(studio) == roles.DEVELOPER

    def test_audit_trail_is_written(self, org, org_admin, rf):
        request = rf.post("/anything")
        request.user = org_admin
        request.org = org
        audit(request, "test.action", target=org)
        assert AuditLog.objects.filter(org=org, action="test.action").exists()


class TestAdminPermissions:
    @staticmethod
    def _request(rf, superuser):
        request = rf.get("/admin/")
        request.user = superuser
        return request

    @pytest.mark.parametrize("model", [PermissionGroup, OrgSSOConfig])
    def test_configuration_models_are_editable(self, rf, superuser, model):
        model_admin = dj_admin.site._registry[model]
        request = self._request(rf, superuser)
        assert model_admin.has_view_permission(request) is True
        assert model_admin.has_add_permission(request) is True
        assert model_admin.has_change_permission(request) is True
        assert model_admin.has_delete_permission(request) is True

    def test_audit_log_remains_immutable(self, rf, superuser):
        model_admin = dj_admin.site._registry[AuditLog]
        request = self._request(rf, superuser)
        assert model_admin.has_view_permission(request) is True
        assert model_admin.has_add_permission(request) is False
        assert model_admin.has_change_permission(request) is False
        assert model_admin.has_delete_permission(request) is False


class TestOperationalPolicy:
    def test_defaults(self):
        assert django_settings.TRELLUM_ORG_SELF_SIGNUP is False
        assert django_settings.TRELLUM_QUOTAS_ENABLED is False
        assert django_settings.TRELLUM_IMPERSONATION_ENABLED is True
        assert django_settings.TRELLUM_SSO_DOMAIN_VERIFICATION is True

    def test_operator_values_drive_policy(self, member, org):
        from apps.orgs.views import may_create_org

        with override_settings(
            TRELLUM_ORG_SELF_SIGNUP=True,
            TRELLUM_QUOTAS_ENABLED=True,
            TRELLUM_IMPERSONATION_ENABLED=False,
            TRELLUM_SSO_DOMAIN_VERIFICATION=False,
        ):
            assert may_create_org(member) is True
            assert django_settings.TRELLUM_QUOTAS_ENABLED is True
            assert django_settings.TRELLUM_IMPERSONATION_ENABLED is False
            assert domains.enforcement_enabled() is False
            assert quotas.limits_for(org) == {}


class TestAccountGrowth:
    def test_two_orgs_and_six_users_across_invite_oidc_and_ldap(
        self, org, other_org, make_user
    ):
        admin_a = make_user("admin-a@example.com", org=org, org_role=roles.ORG_ADMIN)
        admin_b = make_user("admin-b@example.com", org=other_org, org_role=roles.ORG_ADMIN)

        for target_org, email, inviter in (
            (org, "invited-a@example.com", admin_a),
            (other_org, "invited-b@example.com", admin_b),
        ):
            invitation = Invitation.objects.create(
                org=target_org, email=email, invited_by=inviter
            )
            user = create_user_account(email, email.split("@")[0], "pw-Str0ng-pw")
            _apply_invitation(invitation, user)

        for target_org, email, method in (
            (org, "oidc@example.com", OrgSSOConfig.AUTH_OIDC),
            (other_org, "ldap@example.com", OrgSSOConfig.AUTH_LDAP),
        ):
            cfg = OrgSSOConfig.objects.create(
                org=target_org,
                enabled=True,
                auth_method=method,
                auto_provision=True,
            )
            user, refusal = admission(cfg, email)
            assert user is None and refusal is None
            created = create_user_account(email, email.split("@")[0], "pw-Str0ng-pw")
            sso.provision_membership(created, cfg)

        assert org.memberships.count() == 3
        assert other_org.memberships.count() == 3
