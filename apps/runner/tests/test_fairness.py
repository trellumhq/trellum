"""Fair scheduling across organizations, pools, and per-org quotas.

The bug this replaces: the claim loop ordered globally by (priority,
created_at), so one org calling `run all` on 200 reports pushed every other
tenant behind them — and `priority` comes from tenant-authored report.yaml, so
a tenant could promote itself to the front of everyone else's queue.
"""
import pytest

from apps.orgs.models import Organization, OrgQuota
from apps.reports.models import Report
from apps.reports.scan import sync_studio_registry
from apps.runner.models import Run
from apps.runner.services import next_run_ids
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


@pytest.fixture
def make_org_with_runs(data_dir):
    """An org with its own studio and N queued runs."""

    def _make(slug, n_runs, *, pool="standard", priority=99):
        org = Organization.objects.create(slug=slug, name=slug.title())
        studio = Studio.objects.create(
            org=org, slug=f"{slug}-studio", name="S", pool=pool
        )
        studio.ensure_dirs()
        import yaml

        runs = []
        for i in range(n_runs):
            rslug = f"r{i}"
            rdir = studio.reports_dir / rslug
            rdir.mkdir(parents=True, exist_ok=True)
            (rdir / "report.yaml").write_text(
                yaml.safe_dump({"slug": rslug, "name": rslug}), encoding="utf-8"
            )
            (rdir / "generator.py").write_text("# stub\n", encoding="utf-8")
        sync_studio_registry(studio)
        for report in Report.objects.filter(studio=studio):
            report.priority = priority
            report.save(update_fields=["priority"])
            runs.append(
                Run.objects.create(
                    report=report,
                    studio=studio,
                    slug=report.slug,
                    pool=pool,
                    priority=priority,
                )
            )
        return org, studio, runs

    return _make


def _orgs_of(run_ids):
    return [
        r.studio.org.slug
        for r in Run.objects.filter(pk__in=run_ids).select_related("studio__org")
    ]


class TestFairnessAcrossOrgs:
    def test_a_flooding_org_does_not_starve_a_quiet_one(self, make_org_with_runs):
        make_org_with_runs("flooder", 50)
        make_org_with_runs("quiet", 1)

        ids = next_run_ids(limit=2)
        orgs = set(_orgs_of(ids))

        assert "quiet" in orgs, "the quiet org was starved behind the flood"

    def test_org_with_nothing_running_is_scheduled_first(self, make_org_with_runs):
        busy_org, busy_studio, busy_runs = make_org_with_runs("busy", 3)
        idle_org, _, _ = make_org_with_runs("idle", 3)
        # 'busy' already occupies two runner slots.
        Run.objects.filter(pk__in=[busy_runs[0].pk, busy_runs[1].pk]).update(
            status=Run.RUNNING
        )

        first = next_run_ids(limit=1)
        assert _orgs_of(first) == ["idle"]

    def test_ties_break_on_who_waited_longest(self, make_org_with_runs):
        _, _, early = make_org_with_runs("early", 1)
        _, _, late = make_org_with_runs("late", 1)
        from django.utils import timezone

        Run.objects.filter(pk=early[0].pk).update(
            created_at=timezone.now() - timezone.timedelta(hours=2)
        )

        assert _orgs_of(next_run_ids(limit=1)) == ["early"]

    def test_tenant_priority_reorders_its_own_queue(self, make_org_with_runs):
        org, studio, runs = make_org_with_runs("tenant", 3)
        # Tenant-authored report.yaml ranks its last report first.
        Run.objects.filter(pk=runs[2].pk).update(priority=1)

        tenant_ids = [
            pk
            for pk in next_run_ids(limit=10)
            if Run.objects.get(pk=pk).studio.org.slug == "tenant"
        ]
        assert tenant_ids[0] == runs[2].pk

    def test_tenant_priority_cannot_jump_another_orgs_queue(self, make_org_with_runs):
        """The escalation the old global ordering allowed: priority comes from
        tenant-authored report.yaml, so a 1 must not outrank another tenant."""
        _, _, greedy = make_org_with_runs("greedy", 2)
        Run.objects.filter(pk__in=[r.pk for r in greedy]).update(priority=1)
        make_org_with_runs("victim", 2, priority=99)

        # Both orgs are idle, so the fair schedule alternates between them
        # regardless of the priority the greedy tenant assigned itself.
        assert set(_orgs_of(next_run_ids(limit=2))) == {"greedy", "victim"}

    def test_empty_queue_returns_nothing(self, db):
        assert next_run_ids() == []

    def test_stop_requested_runs_are_not_offered(self, make_org_with_runs):
        _, _, runs = make_org_with_runs("a", 1)
        Run.objects.filter(pk=runs[0].pk).update(stop_requested=True)
        assert next_run_ids() == []


class TestPools:
    def test_runner_only_sees_its_pools(self, make_org_with_runs):
        make_org_with_runs("bigco", 2, pool="large")
        make_org_with_runs("smallco", 2, pool="small")

        assert set(_orgs_of(next_run_ids(pools=["large"]))) == {"bigco"}
        assert set(_orgs_of(next_run_ids(pools=["small"]))) == {"smallco"}

    def test_no_pool_filter_serves_everything(self, make_org_with_runs):
        make_org_with_runs("bigco", 1, pool="large")
        make_org_with_runs("smallco", 1, pool="small")

        assert set(_orgs_of(next_run_ids(pools=[]))) == {"bigco", "smallco"}

    def test_run_pool_is_copied_from_the_studio_at_enqueue(self, make_org_with_runs):
        """Moving a studio between pools must not strand queued work."""
        from apps.runner.services import enqueue

        org, studio, _ = make_org_with_runs("x", 1, pool="large")
        report = Report.objects.filter(studio=studio).first()
        Run.objects.all().delete()

        enqueue(report)
        assert Run.objects.get().pool == "large"


class TestQuotas:
    @pytest.fixture(autouse=True)
    def _enforce(self, settings):
        settings.TRELLUM_QUOTAS_ENABLED = True

    def test_org_at_its_concurrency_cap_yields_to_others(self, make_org_with_runs):
        capped_org, _, capped_runs = make_org_with_runs("capped", 3)
        OrgQuota.objects.create(org=capped_org, max_concurrent_runs=1)
        Run.objects.filter(pk=capped_runs[0].pk).update(status=Run.RUNNING)
        make_org_with_runs("other", 1)

        assert set(_orgs_of(next_run_ids(limit=5))) == {"other"}

    def test_under_its_cap_the_org_still_runs(self, make_org_with_runs):
        org, _, _ = make_org_with_runs("fine", 2)
        OrgQuota.objects.create(org=org, max_concurrent_runs=5)
        assert _orgs_of(next_run_ids(limit=1)) == ["fine"]

    def test_zero_cap_means_unlimited(self, make_org_with_runs):
        org, _, runs = make_org_with_runs("unlimited", 3)
        OrgQuota.objects.create(org=org, max_concurrent_runs=0)
        Run.objects.filter(pk=runs[0].pk).update(status=Run.RUNNING)
        assert _orgs_of(next_run_ids(limit=1)) == ["unlimited"]

    def test_quotas_are_ignored_when_operator_policy_is_off(
        self, make_org_with_runs, settings
    ):
        """A self-hosted install must not silently acquire limits."""
        settings.TRELLUM_QUOTAS_ENABLED = False
        org, _, runs = make_org_with_runs("capped", 3)
        OrgQuota.objects.create(org=org, max_concurrent_runs=1)
        Run.objects.filter(pk=runs[0].pk).update(status=Run.RUNNING)

        assert _orgs_of(next_run_ids(limit=1)) == ["capped"]


class TestQuotaMeasurement:
    @pytest.fixture(autouse=True)
    def _enforce(self, settings):
        settings.TRELLUM_QUOTAS_ENABLED = True

    def test_build_minutes_sum_finished_runs_this_month(self, make_org_with_runs):
        from django.utils import timezone

        from apps.orgs import quotas

        org, _, runs = make_org_with_runs("metered", 2)
        now = timezone.now()
        Run.objects.filter(pk=runs[0].pk).update(
            status=Run.SUCCESS,
            started_at=now - timezone.timedelta(minutes=10),
            finished_at=now,
        )
        Run.objects.filter(pk=runs[1].pk).update(
            status=Run.SUCCESS,
            started_at=now - timezone.timedelta(minutes=5),
            finished_at=now,
        )
        assert quotas.build_minutes_this_month(org) == pytest.approx(15.0, abs=0.1)

    def test_exhausted_month_is_a_fatal_stop(self, make_org_with_runs):
        """Fatal, not waitable: next month is not a reason to keep a run queued."""
        from django.utils import timezone

        from apps.orgs import quotas

        org, _, runs = make_org_with_runs("spent", 2)
        OrgQuota.objects.create(org=org, monthly_build_minutes=5)
        now = timezone.now()
        Run.objects.filter(pk=runs[0].pk).update(
            status=Run.SUCCESS,
            started_at=now - timezone.timedelta(minutes=30),
            finished_at=now,
        )
        decision = quotas.check_run_allowed(org)
        assert decision.allowed is False
        assert decision.fatal is True
        assert "monthly build-minute quota" in decision.reason

    def test_concurrency_block_is_waitable_not_fatal(self, make_org_with_runs):
        """It clears itself when a sibling finishes, so the run must stay queued."""
        from apps.orgs import quotas

        org, _, runs = make_org_with_runs("busy", 3)
        OrgQuota.objects.create(org=org, max_concurrent_runs=1)
        Run.objects.filter(pk=runs[0].pk).update(status=Run.RUNNING)

        decision = quotas.check_run_allowed(org)
        assert decision.allowed is False
        assert decision.fatal is False

    def test_oversized_build_is_fatal(self, make_org_with_runs):
        from apps.orgs import quotas

        org, _, _ = make_org_with_runs("capped", 1)
        OrgQuota.objects.create(org=org, max_memory_mb=1024)

        assert quotas.check_run_allowed(org, memory_mb=512).allowed is True
        decision = quotas.check_run_allowed(org, memory_mb=4096)
        assert decision.allowed is False
        assert decision.fatal is True
        assert "4096 MB" in decision.reason

    def test_no_quota_row_means_no_limits(self, make_org_with_runs):
        from apps.orgs import quotas

        org, _, _ = make_org_with_runs("free", 1)
        assert quotas.check_run_allowed(org, memory_mb=999_999).allowed is True
