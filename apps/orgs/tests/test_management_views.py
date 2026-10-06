"""Org management: studios, members, groups — and the audit trail."""
import pytest

from apps.core import roles
from apps.core.models import AuditLog
from apps.orgs.models import (
    Organization,
    OrgMembership,
    PermissionGroup,
    PermissionGroupGrant,
    PermissionGroupMembership,
)
from apps.studios.models import Studio, StudioMembership

pytestmark = pytest.mark.django_db


class TestStudioLifecycle:
    def test_create_studio(self, login, org_admin, org, settings, tmp_path):
        settings.DATA_DIR = tmp_path
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/studios/new",
            {"slug": "casino", "name": "Casino", "description": "slots"},
        )
        assert resp.status_code == 302
        studio = Studio.objects.get(org=org, slug="casino")
        # Directory skeleton was created on the data volume.
        assert (studio.reports_dir).is_dir()
        assert (studio.output_dir).is_dir()
        assert AuditLog.objects.filter(action="studio.create", org=org).exists()

    def test_duplicate_slug_rejected(self, login, org_admin, org, studio):
        login(org_admin).post(
            f"/orgs/{org.slug}/studios/new", {"slug": studio.slug, "name": "Again"}
        )
        assert Studio.objects.filter(org=org, slug=studio.slug).count() == 1

    def test_delete_requires_slug_confirmation(self, login, org_admin, org, studio):
        c = login(org_admin)
        c.post(f"/orgs/{org.slug}/studios/{studio.slug}/delete", {"confirm_slug": "wrong"})
        assert Studio.objects.filter(pk=studio.pk).exists()
        c.post(f"/orgs/{org.slug}/studios/{studio.slug}/delete", {"confirm_slug": studio.slug})
        assert not Studio.objects.filter(pk=studio.pk).exists()

    def test_member_cannot_create_studio(self, login, member, org):
        resp = login(member).post(
            f"/orgs/{org.slug}/studios/new", {"slug": "nope", "name": "Nope"}
        )
        assert resp.status_code == 403


class TestOrgLifecycle:
    """Organization deletion — the exit a one-person org needs, and the
    emitter of the org.delete audit rows the orphan sweep dates trees by."""

    def test_member_cannot_delete_the_org(self, login, member, org):
        resp = login(member).post(f"/orgs/{org.slug}/delete", {"confirm_slug": org.slug})
        assert resp.status_code == 403
        assert Organization.objects.filter(pk=org.pk).exists()

    def test_delete_requires_slug_confirmation(self, login, org_admin, org):
        login(org_admin).post(f"/orgs/{org.slug}/delete", {"confirm_slug": "wrong"})
        assert Organization.objects.filter(pk=org.pk).exists()

    def test_delete_takes_rows_and_leaves_files(
        self, login, org_admin, org, studio, settings, tmp_path
    ):
        settings.DATA_DIR = tmp_path
        output = studio.output_dir
        output.mkdir(parents=True, exist_ok=True)
        (output / "index.html").write_text("built")

        resp = login(org_admin).post(f"/orgs/{org.slug}/delete", {"confirm_slug": org.slug})
        assert resp.status_code == 302

        assert not Organization.objects.filter(pk=org.pk).exists()
        assert not Studio.objects.filter(pk=studio.pk).exists()
        # The admin's account survives the org; only the membership went.
        org_admin.refresh_from_db()
        assert org_admin.erased_at is None
        # Files stay for the orphan sweep, dated by the audit row written
        # before the delete -- whose slug metadata must therefore exist and
        # whose org FK is NULL because the org it named is gone.
        assert (output / "index.html").exists()
        row = AuditLog.objects.get(action="org.delete")
        assert row.metadata.get("slug") == org.slug
        assert row.org_id is None
        assert row.actor_id == org_admin.pk

    def test_sole_admin_can_delete_org_then_their_own_account(
        self, login, org_admin, org
    ):
        """The full exit: the guard that blocks a sole admin's self-deletion
        stops applying once the organization itself is gone."""
        c = login(org_admin)
        c.post(f"/account/delete", {"confirm_email": org_admin.email, "password": "pw-Str0ng-pw"})
        org_admin.refresh_from_db()
        assert org_admin.erased_at is None  # blocked: sole admin

        c.post(f"/orgs/{org.slug}/delete", {"confirm_slug": org.slug})
        c.post(f"/account/delete", {"confirm_email": org_admin.email, "password": "pw-Str0ng-pw"})
        org_admin.refresh_from_db()
        assert org_admin.erased_at is not None


class TestSlugImmutability:
    def test_org_slug_frozen(self, org):
        org.slug = "renamed"
        with pytest.raises(ValueError):
            org.save()

    def test_studio_slug_frozen(self, studio):
        studio.slug = "renamed"
        with pytest.raises(ValueError):
            studio.save()

    def test_display_name_still_editable(self, org):
        org.name = "New Name"
        org.save()
        assert Organization.objects.get(pk=org.pk).name == "New Name"


class TestMembers:
    def test_change_role(self, login, org_admin, org, member):
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/members/{member.pk}/role", {"role": "admin"}
        )
        assert OrgMembership.objects.get(user=member, org=org).role == "admin"

    def test_last_admin_protected(self, login, org_admin, org):
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/members/{org_admin.pk}/role", {"role": "member"}
        )
        assert OrgMembership.objects.get(user=org_admin, org=org).role == "admin"

    def test_remove_member_cleans_up(
        self, login, org_admin, org, member, studio, grant_studio, make_group, attach_group
    ):
        grant_studio(member, studio, roles.VIEWER)
        attach_group(member, make_group("G"))
        login(org_admin).post(f"/orgs/{org.slug}/settings/members/{member.pk}/remove")
        assert not OrgMembership.objects.filter(user=member, org=org).exists()
        assert not StudioMembership.objects.filter(user=member).exists()
        assert not PermissionGroupMembership.objects.filter(user=member).exists()

    def test_cannot_remove_self(self, login, org_admin, org):
        login(org_admin).post(f"/orgs/{org.slug}/settings/members/{org_admin.pk}/remove")
        assert OrgMembership.objects.filter(user=org_admin, org=org).exists()

    def test_set_studio_role_from_org_page(self, login, org_admin, org, member, studio):
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/members/{member.pk}/studio-role",
            {"studio_id": studio.pk, "role": "developer"},
        )
        assert StudioMembership.objects.get(user=member, studio=studio).role == "developer"


class TestGroups:
    def test_create_group(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/settings/groups",
            {"name": "Demo BA", "description": "", "org_role": "", "default_studio_role": "viewer"},
        )
        assert resp.status_code == 302
        group = PermissionGroup.objects.get(org=org, name="Demo BA")
        assert group.default_studio_role == "viewer"
        assert AuditLog.objects.filter(action="group.create").exists()

    def test_duplicate_name_rejected(self, login, org_admin, org, make_group):
        make_group("Demo BA")
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/settings/groups",
            {"name": "Demo BA", "description": "", "org_role": "", "default_studio_role": ""},
        )
        assert resp.status_code == 200  # re-rendered with error
        assert PermissionGroup.objects.filter(org=org, name="Demo BA").count() == 1

    def test_grant_and_ungrant(self, login, org_admin, org, studio, make_group):
        group = make_group("Devs")
        url = f"/orgs/{org.slug}/settings/groups/{group.pk}"
        c = login(org_admin)
        c.post(url, {"action": "grant", "studio_id": studio.pk, "role": "developer"})
        assert PermissionGroupGrant.objects.filter(
            group=group, studio=studio, role="developer"
        ).exists()
        c.post(url, {"action": "ungrant", "studio_id": studio.pk})
        assert not PermissionGroupGrant.objects.filter(group=group).exists()

    def test_add_and_remove_user(self, login, org_admin, org, member, make_group):
        group = make_group("Devs")
        url = f"/orgs/{org.slug}/settings/groups/{group.pk}"
        c = login(org_admin)
        c.post(url, {"action": "add_user", "user_id": member.pk})
        assert PermissionGroupMembership.objects.filter(user=member, group=group).exists()
        c.post(url, {"action": "remove_user", "user_id": member.pk})
        assert not PermissionGroupMembership.objects.filter(user=member, group=group).exists()

    def test_cannot_add_user_from_other_org(self, login, org_admin, org, make_user, make_group):
        stranger = make_user("stranger@else.com")  # not an org member
        group = make_group("Devs")
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/groups/{group.pk}",
            {"action": "add_user", "user_id": stranger.pk},
        )
        assert not PermissionGroupMembership.objects.filter(user=stranger).exists()

    def test_delete_group_drops_roles(
        self, login, org_admin, member, org, studio, make_group, attach_group
    ):
        from apps.core.permissions import effective_roles

        group = make_group("Admins", org_role=roles.ORG_ADMIN)
        attach_group(member, group)
        assert effective_roles(member, org).is_org_admin
        login(org_admin).post(f"/orgs/{org.slug}/settings/groups/{group.pk}/delete")
        assert not effective_roles(member, org).is_org_admin


class TestStudioMembersPage:
    def test_studio_admin_manages_members(
        self, login, member, make_user, org, studio, grant_studio
    ):
        grant_studio(member, studio, roles.ADMIN)
        colleague = make_user("colleague@demo.example", org=org)
        c = login(member)
        c.post(
            f"/s/{org.slug}/{studio.slug}/settings/members/set",
            {"user_id": colleague.pk, "role": "viewer"},
        )
        assert StudioMembership.objects.filter(
            user=colleague, studio=studio, role="viewer"
        ).exists()
        c.post(
            f"/s/{org.slug}/{studio.slug}/settings/members/set",
            {"user_id": colleague.pk, "role": ""},
        )
        assert not StudioMembership.objects.filter(user=colleague, studio=studio).exists()


class TestAuditTrail:
    def test_mutations_leave_audit_rows(self, login, org_admin, org, member, settings, tmp_path):
        settings.DATA_DIR = tmp_path
        c = login(org_admin)
        c.post(f"/orgs/{org.slug}/studios/new", {"slug": "s1", "name": "S1"})
        c.post(f"/orgs/{org.slug}/settings/members/{member.pk}/role", {"role": "admin"})
        c.post(
            f"/orgs/{org.slug}/settings/invites", {"email": "i@demo.example", "org_role": "member"}
        )
        actions = set(AuditLog.objects.values_list("action", flat=True))
        assert {"studio.create", "member.set_role", "member.invite"} <= actions
        # Every row for an org action carries the org and the actor.
        # auth.* rows are excluded deliberately: signing in is instance-level,
        # not org-scoped, so those legitimately have no org.
        for row in AuditLog.objects.exclude(action__startswith="auth."):
            assert row.org_id == org.pk
            assert row.actor_id == org_admin.pk
