"""The cross-organization operator console: who may reach it, and what the
tier above Organization can actually do."""
import pytest
from django.contrib.auth import get_user_model

from apps.orgs.models import OrgQuota

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def operator(db):
    return User.objects.create_user(
        email="ops@portal.local", password="pw-Str0ng-pw", is_operator_flag=True
    )


class TestAccess:
    @pytest.mark.parametrize(
        "url", ["/operator/", "/operator/orgs/"]
    )
    def test_anonymous_is_sent_to_login(self, client, url):
        resp = client.get(url)
        assert resp.status_code in (302, 401)

    @pytest.mark.parametrize("url", ["/operator/", "/operator/orgs/"])
    def test_ordinary_member_gets_404_not_403(self, login, member, url):
        """The console's existence is not advertised."""
        assert login(member).get(url).status_code == 404

    def test_org_admin_is_still_not_an_operator(self, login, org_admin):
        assert login(org_admin).get("/operator/").status_code == 404

    def test_operator_flag_grants_access(self, login, operator):
        assert login(operator).get("/operator/").status_code == 200

    def test_superuser_implies_operator(self, login, superuser):
        assert login(superuser).get("/operator/").status_code == 200

    def test_operator_flag_does_not_grant_django_admin(self, login, operator):
        """The point of a separate flag: support staff without the database."""
        resp = login(operator).get("/admin/", follow=False)
        assert resp.status_code in (302, 403)
        assert operator.is_staff is False


class TestFleet:
    def test_navigation_has_only_the_fixed_console_pages(self):
        from apps.operator import nav

        assert [(item.label, item.url_name) for item in nav.items()] == [
            ("Fleet", "operator-fleet"),
            ("Organizations", "operator-orgs"),
        ]

    def test_shows_workers_and_queue(self, login, operator, report_row):
        from apps.runner.models import Run, WorkerHeartbeat

        WorkerHeartbeat.objects.create(worker_id="r1", role="runner", max_concurrent=3)
        Run.objects.create(
            report=report_row, studio=report_row.studio, slug=report_row.slug
        )
        body = login(operator).get("/operator/").content.decode()
        assert "r1" in body
        assert "Fleet" in body


class TestOrgList:
    def test_lists_every_org_regardless_of_membership(
        self, login, operator, org, other_org
    ):
        body = login(operator).get("/operator/orgs/").content.decode()
        assert org.slug in body
        assert other_org.slug in body


class TestSuspend:
    def test_suspending_hides_the_org_from_its_members(
        self, client, login, operator, org, member, studio
    ):
        login(operator).post(
            f"/operator/orgs/{org.slug}/active", {"is_active": "0"}
        )
        org.refresh_from_db()
        assert org.is_active is False

        # The existing decorators already key on is_active, so members 404.
        client.logout()
        resp = login(member).get(f"/orgs/{org.slug}/settings/members")
        assert resp.status_code == 404

    def test_restoring_brings_it_back(self, login, operator, org):
        c = login(operator)
        c.post(f"/operator/orgs/{org.slug}/active", {"is_active": "0"})
        c.post(f"/operator/orgs/{org.slug}/active", {"is_active": "1"})
        org.refresh_from_db()
        assert org.is_active is True

    def test_action_is_audited(self, login, operator, org):
        from apps.core.models import AuditLog

        login(operator).post(f"/operator/orgs/{org.slug}/active", {"is_active": "0"})
        assert AuditLog.objects.filter(action="operator.org.active", org=org).exists()


class TestQuotaEditing:
    def test_saving_creates_the_row(self, login, operator, org):
        login(operator).post(
            f"/operator/orgs/{org.slug}/quota",
            {
                "max_concurrent_runs": "4",
                "monthly_build_minutes": "600",
                "max_studios": "0",
                "max_reports": "0",
                "max_memory_mb": "4096",
                "notes": "pilot",
            },
        )
        quota = OrgQuota.objects.get(org=org)
        assert quota.max_concurrent_runs == 4
        assert quota.monthly_build_minutes == 600
        assert quota.notes == "pilot"

    def test_storage_cap_is_editable_and_usage_is_shown(
        self, login, operator, org, studio_tree
    ):
        """Every quota needs a field to set it AND a row showing usage — a cap
        you cannot see yourself approaching is not much of a cap."""
        from apps.datasources.models import DataSource

        DataSource.objects.create(
            studio=studio_tree, name="extract", type="file",
            config={"upload": True, "path": "data-sources/files/extract.csv"},
        )
        target = studio_tree.project_root / "data-sources/files/extract.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"z" * (2 * 1024 * 1024))

        login(operator).post(
            f"/operator/orgs/{org.slug}/quota", {"max_storage_mb": "4096"}
        )
        assert OrgQuota.objects.get(org=org).max_storage_mb == 4096
        html = login(operator).get(f"/operator/orgs/{org.slug}/").content.decode()
        assert 'name="max_storage_mb"' in html
        assert "2.0 MB" in html

    def test_non_numeric_is_rejected_without_saving(self, login, operator, org):
        login(operator).post(
            f"/operator/orgs/{org.slug}/quota", {"max_concurrent_runs": "lots"}
        )
        assert OrgQuota.objects.filter(org=org).first().max_concurrent_runs == 0

    def test_negative_is_clamped_to_unlimited(self, login, operator, org):
        login(operator).post(
            f"/operator/orgs/{org.slug}/quota", {"max_concurrent_runs": "-5"}
        )
        assert OrgQuota.objects.get(org=org).max_concurrent_runs == 0


class TestStudioPool:
    """Pool placement is an operator decision: it picks which runners may build
    the studio, so it is about fleet capacity, not tenant configuration."""

    def test_operator_can_move_a_studio(self, login, operator, org, studio):
        login(operator).post(
            f"/operator/orgs/{org.slug}/studios/{studio.slug}/pool", {"pool": "large"}
        )
        studio.refresh_from_db()
        assert studio.pool == "large"

    def test_unknown_pool_is_refused(self, login, operator, org, studio):
        login(operator).post(
            f"/operator/orgs/{org.slug}/studios/{studio.slug}/pool", {"pool": "enormous"}
        )
        studio.refresh_from_db()
        assert studio.pool == "standard"

    def test_move_is_audited(self, login, operator, org, studio):
        from apps.core.models import AuditLog

        login(operator).post(
            f"/operator/orgs/{org.slug}/studios/{studio.slug}/pool", {"pool": "small"}
        )
        assert AuditLog.objects.filter(action="operator.studio.pool").exists()

    def test_queued_runs_keep_their_original_pool(
        self, login, operator, org, report_row
    ):
        """The pool is copied onto the run at enqueue, so moving a studio must
        not strand work that is already waiting."""
        from apps.runner.models import Run
        from apps.runner.services import enqueue

        enqueue(report_row)
        assert Run.objects.get().pool == "standard"

        login(operator).post(
            f"/operator/orgs/{org.slug}/studios/{report_row.studio.slug}/pool",
            {"pool": "large"},
        )
        assert Run.objects.get().pool == "standard"

    def test_ordinary_admin_cannot_move_pools(self, login, org_admin, org, studio):
        resp = login(org_admin).post(
            f"/operator/orgs/{org.slug}/studios/{studio.slug}/pool", {"pool": "large"}
        )
        assert resp.status_code == 404
        studio.refresh_from_db()
        assert studio.pool == "standard"


class TestImpersonation:
    @pytest.fixture(autouse=True)
    def _enabled(self, settings):
        settings.TRELLUM_IMPERSONATION_ENABLED = True

    def test_operator_can_act_as_a_member(self, login, operator, org, member):
        c = login(operator)
        c.post(f"/operator/orgs/{org.slug}/impersonate/{member.pk}")
        assert c.session["_auth_user_id"] == str(member.pk)
        assert c.session["impersonator_id"] == operator.pk

    def test_both_ends_are_audited(self, login, operator, org, member):
        from apps.core.models import AuditLog

        c = login(operator)
        c.post(f"/operator/orgs/{org.slug}/impersonate/{member.pk}")
        c.post("/operator/impersonate/stop")

        actions = set(AuditLog.objects.values_list("action", flat=True))
        assert "operator.impersonate.start" in actions
        assert "operator.impersonate.stop" in actions

    def test_the_operator_is_named_on_every_row_they_cause(
        self, login, operator, org, member, studio, grant_studio
    ):
        """The trail must never say the customer did it when an operator did.

        `actor` stays the impersonated user — the action really did carry their
        permissions — so the operator's identity has to live in its own column
        or it is simply absent.
        """
        from apps.core import roles
        from apps.core.models import AuditLog

        grant_studio(member, studio, roles.DEVELOPER)
        c = login(operator)
        c.post(f"/operator/orgs/{org.slug}/impersonate/{member.pk}")
        # Any ordinary audited action, taken while wearing the borrowed account.
        c.post(f"/s/{org.slug}/{studio.slug}/api/system/cache/clear")

        # The operator's own sign-in is an auth.* row by them, before any
        # impersonation existed; the subject here is the borrowed-account action.
        rows = AuditLog.objects.exclude(
            action__startswith="operator.impersonate"
        ).exclude(action__startswith="auth.")
        assert rows.exists(), "expected an ordinary audited action to compare"
        for row in rows:
            assert row.actor_id == member.pk
            assert row.impersonator_id == operator.pk

    def test_the_start_row_itself_names_the_operator(
        self, login, operator, org, member
    ):
        """The row that opens the window used to be the worst offender: start()
        calls login() before the audit, so `actor` is already the target."""
        from apps.core.models import AuditLog

        c = login(operator)
        c.post(f"/operator/orgs/{org.slug}/impersonate/{member.pk}")

        row = AuditLog.objects.get(action="operator.impersonate.start")
        assert row.impersonator_id == operator.pk

    def test_ordinary_actions_carry_no_impersonator(self, login, operator, org):
        """Guards the column from being set for everyone."""
        from apps.core.models import AuditLog

        c = login(operator)
        c.post(f"/operator/orgs/{org.slug}/quota", {"max_studios": "3"})

        rows = AuditLog.objects.all()
        assert rows.exists()
        assert all(row.impersonator_id is None for row in rows)

    def test_stopping_returns_to_the_operator(self, login, operator, org, member):
        c = login(operator)
        c.post(f"/operator/orgs/{org.slug}/impersonate/{member.pk}")
        c.post("/operator/impersonate/stop")
        assert c.session["_auth_user_id"] == str(operator.pk)
        assert "impersonator_id" not in c.session

    def test_stopping_after_the_operator_vanished_logs_the_session_out(
        self, login, operator, org, member
    ):
        c = login(operator)
        c.post(f"/operator/orgs/{org.slug}/impersonate/{member.pk}")
        type(operator).objects.filter(pk=operator.pk).delete()
        c.post("/operator/impersonate/stop")
        assert "_auth_user_id" not in c.session
        assert "impersonator_id" not in c.session

    def test_expiry_after_the_operator_vanished_logs_the_session_out(
        self, login, operator, org, member, settings
    ):
        settings.TRELLUM_IMPERSONATION_MINUTES = 0
        c = login(operator)
        c.post(f"/operator/orgs/{org.slug}/impersonate/{member.pk}")
        type(operator).objects.filter(pk=operator.pk).delete()
        c.get("/")
        assert "_auth_user_id" not in c.session

    def test_cannot_impersonate_another_operator(self, login, operator, org, make_user):
        """Sideways escalation: a compromised support account must not reach a
        colleague's privileges."""
        colleague = make_user("ops2@portal.local", org=org)
        colleague.is_operator_flag = True
        colleague.save(update_fields=["is_operator_flag"])

        c = login(operator)
        c.post(f"/operator/orgs/{org.slug}/impersonate/{colleague.pk}")
        assert c.session["_auth_user_id"] == str(operator.pk)

    def test_the_banner_offers_a_way_out(self, login, operator, org, member):
        c = login(operator)
        c.post(f"/operator/orgs/{org.slug}/impersonate/{member.pk}")
        body = c.get("/").content.decode()
        assert "Acting as" in body
        assert "/operator/impersonate/stop" in body

    def test_console_link_disappears_while_impersonating(
        self, login, operator, org, member
    ):
        c = login(operator)
        c.post(f"/operator/orgs/{org.slug}/impersonate/{member.pk}")
        assert "Operator console" not in c.get("/").content.decode()

    def test_expired_session_reverts_on_the_next_request(
        self, login, operator, org, member, settings
    ):
        settings.TRELLUM_IMPERSONATION_MINUTES = 0  # expires immediately
        c = login(operator)
        c.post(f"/operator/orgs/{org.slug}/impersonate/{member.pk}")

        c.get("/")  # middleware notices the deadline has passed
        assert c.session["_auth_user_id"] == str(operator.pk)

    def test_operator_setting_disables_it(self, login, operator, org, member, settings):
        settings.TRELLUM_IMPERSONATION_ENABLED = False
        c = login(operator)
        resp = c.post(f"/operator/orgs/{org.slug}/impersonate/{member.pk}")
        assert resp.status_code == 302
        assert c.session["_auth_user_id"] == str(operator.pk)

    def test_ordinary_user_cannot_start_one(self, login, member, org, make_user):
        victim = make_user("victim@demo.example", org=org)
        c = login(member)
        assert (
            c.post(f"/operator/orgs/{org.slug}/impersonate/{victim.pk}").status_code
            == 404
        )
