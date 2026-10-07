"""Audit-log viewer (org admin) and /system (operator only)."""
import pytest

pytestmark = pytest.mark.django_db


class TestAuditViewer:
    def test_admin_sees_logged_actions(self, login, org_admin, org, member):
        c = login(org_admin)
        c.post(f"/orgs/{org.slug}/settings/members/{member.pk}/role", {"role": "admin"})
        html = c.get(f"/orgs/{org.slug}/settings/audit").content.decode()
        assert "member.set_role" in html
        assert org_admin.email in html

    def test_action_filter(self, login, org_admin, org, member):
        c = login(org_admin)
        c.post(f"/orgs/{org.slug}/settings/members/{member.pk}/role", {"role": "admin"})
        c.post(f"/orgs/{org.slug}/settings/invites", {"email": "x@demo.example", "org_role": "member"})
        html = c.get(f"/orgs/{org.slug}/settings/audit?action=member.invite").content.decode()
        tbody = html.split("<tbody>", 1)[1]  # the filter <select> lists all actions
        assert "member.invite" in tbody
        assert "member.set_role" not in tbody

    def test_member_cannot_open(self, login, member, org):
        assert login(member).get(f"/orgs/{org.slug}/settings/audit").status_code == 403

    def test_sidebar_links_audit(self, login, org_admin, org):
        html = login(org_admin).get(f"/orgs/{org.slug}/settings/members").content.decode()
        assert f"/orgs/{org.slug}/settings/audit" in html


class TestSystemPage:
    def test_operator_sees_checks(self, login, superuser):
        html = login(superuser).get("/system").content.decode()
        assert "database" in html
        assert "SECRET_ENCRYPTION_KEY" in html
        assert "worker" in html
        assert 'data-console-nav-group="operator"' in html
        assert 'href="/system"' in html and 'aria-current="page"' in html

    def test_upload_limit_is_editable(self, login, superuser):
        """The form field alone is not enough — /system renders its fields one
        by one, so a new one is invisible until the template lists it."""
        html = login(superuser).get("/system").content.decode()
        assert 'name="max_upload_mb"' in html

    def test_free_space_is_reported(self, login, superuser):
        html = login(superuser).get("/system").content.decode()
        assert "data volume free space" in html

    def test_hidden_from_non_operators(self, login, org_admin):
        assert login(org_admin).get("/system").status_code == 404

    def test_operator_flag_is_enough(self, login, make_user):
        """This page is the reason the operator flag exists. Gating it on
        is_superuser meant support staff could reach the operator console but
        not the health page it links to."""
        support = make_user("support@portal.local")
        support.is_operator_flag = True
        support.save(update_fields=["is_operator_flag"])
        assert login(support).get("/system").status_code == 200

    def test_operator_sees_the_menu_link(self, login, make_user):
        support = make_user("support2@portal.local")
        support.is_operator_flag = True
        support.save(update_fields=["is_operator_flag"])
        assert 'href="/system"' in login(support).get("/").content.decode()

    def test_new_organization_stays_superuser_only(self, login, make_user, superuser):
        """org_create is guarded by require_superuser; offering the link to an
        operator would only ever produce a 403."""
        support = make_user("support3@portal.local")
        support.is_operator_flag = True
        support.save(update_fields=["is_operator_flag"])
        assert '/orgs/new' not in login(support).get("/").content.decode()
        assert '/orgs/new' in login(superuser).get("/").content.decode()

    # ── the Releases section (internal planning ticket #066) ────────────────────────────────

    @staticmethod
    def _recorded(latest: str):
        from apps.core.models import OpsState

        import trellum_portal

        mine = trellum_portal.__version__
        return OpsState.record("update_check", latest=latest, releases=[
            {"tag": f"v{latest}", "name": f"v{latest}", "date": "2026-09-12",
             "excerpt": "Scheduled exports.", "url": "https://example.test/rel/latest"},
            {"tag": f"v{mine}", "name": f"v{mine}", "date": "2026-08-26",
             "excerpt": "First versioned release.", "url": "https://example.test/rel/mine"},
        ])

    def test_releases_section_is_absent_until_a_check_has_succeeded(self, login, superuser):
        assert "<h2>Releases</h2>" not in login(superuser).get("/system").content.decode()

    def test_releases_section_when_a_newer_release_exists(self, login, superuser):
        self._recorded("99.0.0")
        html = login(superuser).get("/system").content.decode()
        assert "<h2>Releases</h2>" in html
        assert "Trellum <strong>99.0.0</strong> is available" in html
        assert "Upgrade guide" in html and "install/upgrades/" in html
        assert "All releases" in html
        assert "this instance" in html and "Scheduled exports." in html
        assert 'href="https://example.test/rel/latest"' in html
        assert "TRELLUM_UPDATE_CHECK=false" in html
        assert "from api.github.com, once a day" in html

    def test_releases_section_when_up_to_date(self, login, superuser):
        import trellum_portal

        self._recorded(trellum_portal.__version__)
        html = login(superuser).get("/system").content.decode()
        assert "up to date" in html and "the latest release" in html
        assert "Upgrade guide" not in html

    def test_releases_section_when_this_instance_is_ahead(self, login, superuser, monkeypatch):
        """A dev build newer than the feed is neither up to date nor behind."""
        import trellum_portal

        monkeypatch.setattr(trellum_portal, "__version__", "0.3.0")
        self._recorded("0.2.0")
        html = login(superuser).get("/system").content.decode()
        assert "<h2>Releases</h2>" in html and "runs <code>0.3.0</code>." in html
        assert "up to date" not in html and "is available" not in html
        assert "Scheduled exports." in html and "this instance" in html

    def test_releases_row_does_not_open_the_page_to_org_admins(self, login, org_admin):
        self._recorded("99.0.0")
        assert login(org_admin).get("/system").status_code == 404


class TestAuditLogIsAppendOnly:
    """The audit-log documentation promises "written automatically; never editable".
    Add and change were blocked; delete — the one operation that destroys
    evidence — was not."""

    @pytest.mark.parametrize("perm", ["add", "change", "delete"])
    def test_admin_refuses_every_mutation(self, rf, superuser, perm):
        from django.contrib import admin as django_admin

        from apps.core.models import AuditLog

        model_admin = django_admin.site._registry[AuditLog]
        request = rf.get("/admin/core/auditlog/")
        request.user = superuser
        assert getattr(model_admin, f"has_{perm}_permission")(request) is False

    def test_menu_link_only_for_operator(self, login, superuser, org_admin, org):
        assert "/system" in login(superuser).get("/").content.decode()
        c = login(org_admin)
        assert 'href="/system"' not in c.get("/").content.decode()

    def test_doctor_command_shares_checks(self):
        from apps.core.health import run_checks

        labels = {c["label"] for c in run_checks()}
        assert {"database", "SECRET_ENCRYPTION_KEY", "worker"} <= labels


class TestSystemPolicyPanel:
    def test_instance_settings_still_render(self, login, superuser):
        html = login(superuser).get("/system").content.decode()
        assert "Instance settings" in html
