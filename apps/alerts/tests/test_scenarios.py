"""The scenario suite's own machinery (vault portal-agent plan §11, Layer 2):
the file is well-formed, the loop runs end to end on the fake client, the
mention matching and the exit code. The judgment itself is not tested here --
that is what running the command for real is for."""
from io import StringIO

import pytest
from django.conf import settings
from django.core import mail
from django.core.management import CommandError, call_command

from apps.alerts.management.commands import alert_scenarios
from apps.alerts.models import AlertRule, AlertRun
from apps.alerts.tests.conftest import decide
from apps.assistant.tests.test_message_sse import FakeClient

DEMO = settings.BASE_DIR / "trellum" / "demo"


def test_scenario_file_names_real_reports_in_order():
    spec = alert_scenarios.load_scenarios()
    ids = [s["id"] for s in spec["scenarios"]]
    assert ids == ["outage", "launch", "promo", "quiet", "retention", "novelty", "stale", "spend"]
    assert (spec["anchor"], spec["days"], spec["seed"]) == ("2026-08-28", 180, 42)
    seen = set()
    for s in spec["scenarios"]:
        assert (DEMO / "reports" / s["report"] / "report.yaml").is_file(), s["id"]
        assert s["decision"] in ("alert", "quiet")
        assert isinstance(s["after"], str) and len(s["after"]) == 10
        if s.get("reuses"):
            assert s["reuses"] in seen, "a reused rule must run first"
        else:
            assert s["before"] <= s["after"] <= s.get("evaluate_at", s["after"])
        seen.add(s["id"])


def test_mentions_match_case_insensitively_over_title_message_and_evidence():
    run = AlertRun(
        title="APAC DAU fell", message="Sessions fell with it; IAP revenue too.",
        evidence={"cited": ["dau 2026-07-28: 1,000 vs 2,400 the day before"]},
    )
    hits = alert_scenarios.mentions(
        run, ["apac", "Dau", "sessions", "2026-07-28", "iap_revenue", "EMEA", "refund_rate"]
    )
    assert hits == ["apac", "Dau", "sessions", "2026-07-28", "iap_revenue"]


@pytest.mark.django_db
class TestDryRun:
    @pytest.fixture(autouse=True)
    def no_warehouse_no_subprocess(self, monkeypatch, write_meta):
        """The loop without the expensive parts: a build is a fresh _meta.json."""
        monkeypatch.setattr(alert_scenarios, "ensure_warehouse", lambda studio, spec: None)
        monkeypatch.setattr(
            alert_scenarios, "build",
            lambda report, owner, now: write_meta(
                report.slug, last_status="success", last_run=f"{now}T04:00:00+00:00",
            ),
        )

    def run(self, org, studio, tmp_path, *only):
        out, buf = tmp_path / "results.md", StringIO()
        args = ["--dry", "--org", org.slug, "--studio", studio.slug, "--out", str(out)]
        for sid in only:
            args += ["--only", sid]
        call_command("alert_scenarios", *args, stdout=buf)
        return buf.getvalue(), out.read_text(encoding="utf-8")

    def test_reused_rule_runs_its_scenario_first_and_never_delivers(
        self, built, owner, assistant_config, org, studio_tree, tmp_path
    ):
        stdout, md = self.run(org, studio_tree, tmp_path, "novelty")

        assert "outage     alert     alert     alert     2/2" in stdout
        assert "novelty    quiet     quiet     -         0/0" in stdout
        assert "2 passed, 0 failed, 0 needs-knob" in stdout
        rule = AlertRule.objects.get()  # novelty reuses outage's rule
        assert rule.name == "scenario: outage" and rule.enabled is False
        assert rule.cooldown_hours == 0 and rule.last_alerted_at is None
        runs = AlertRun.objects.filter(rule=rule).order_by("started_at")
        # Each evaluation lives on its build's calendar: baseline, after, novelty.
        assert [f"{r.started_at:%Y-%m-%d}" for r in runs] == ["2026-07-27", "2026-07-30", "2026-07-31"]
        assert mail.outbox == []
        assert "**Canned alert**" in md and "Dry run: APAC, dau" in md

    def test_failed_scenario_without_note_exits_1(
        self, built, owner, assistant_config, org, studio_tree, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            alert_scenarios, "dry_client",
            lambda s: FakeClient([decide(False, title="Nothing", message="Flat.")] * 3, []),
        )
        with pytest.raises(CommandError, match="1 scenario\\(s\\) failed: outage"):
            self.run(org, studio_tree, tmp_path, "outage")

    def test_unpriced_runs_render_unavailable_cost_in_rows_and_aggregate(
        self, built, owner, assistant_config, org, studio_tree, tmp_path
    ):
        from apps.orgs.models import OrgAssistantConfig

        OrgAssistantConfig.objects.filter(org=org).update(model="claude-future-9")
        stdout, md = self.run(org, studio_tree, tmp_path, "outage")
        assert "unavailable" in stdout
        assert "Cost unavailable." in stdout
        assert "| unavailable |" in md

    def test_failed_scenario_with_note_is_informational_and_sees_only_its_report(
        self, built, owner, assistant_config, org, studio_tree, write_report, tmp_path, monkeypatch
    ):
        from apps.reports.scan import sync_studio_registry

        write_report("monetization")
        sync_studio_registry(studio_tree)
        calls = []
        monkeypatch.setattr(
            alert_scenarios, "dry_client",
            lambda s: FakeClient([decide(True, title="Spend up", message="Installs flat.")] * 3, calls),
        )
        stdout, _ = self.run(org, studio_tree, tmp_path, "spend")
        assert "spend      quiet     alert     alert     0/0" in stdout and "FAIL (info)" in stdout
        assert "0 passed, 0 failed, 1 needs-knob" in stdout
        # The built player-overview (another scenario's clock) is hidden from the
        # model, and the model's calendar is the build's, not the real one.
        catalog = "\n".join(b["text"] for b in calls[0]["system"])
        assert "monetization" in catalog and "player-overview" not in catalog
        assert "TODAY IS **2026-08-01**" in catalog and "Last build 2026-08-01 succeeded" in catalog
        assert "TODAY IS **2026-08-08**" in "\n".join(b["text"] for b in calls[-1]["system"])
