"""The studio Alerts page (internal planning ticket #147): role gating, the form, the run log
and Test now. The evaluator itself is covered in test_evaluator.py; here it
runs behind the scripted ``decide`` client."""
import pytest
from django.core import mail

from apps.alerts.models import AlertRule, AlertRun
from apps.alerts.tests.conftest import decide
from apps.core import roles
from apps.core.models import AuditLog
from apps.orgs.models import PermissionGroupGrant

pytestmark = pytest.mark.django_db


@pytest.fixture
def prefix(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}/alerts"


@pytest.fixture
def viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("viewer@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


def _main(resp) -> str:
    """The page body without the console shell (which carries its own forms)."""
    return resp.content.decode().split("<main", 1)[1]


def _form(rule, **overrides):
    data = {
        "name": "Revenue watch", "report": rule.report.pk, "instructions": "Tell me if revenue drops.",
        "trigger": "after_build", "freq": "daily", "send_hour": 8, "send_minute": 0,
        "weekday": 0, "month_day": 1, "timezone": "UTC", "recipient_roles": ["developers"],
        "cooldown_hours": 24, "enabled": "on",
    }
    data.update(overrides)
    return data


class TestGating:
    def test_selected_viewer_cannot_list_or_open_an_unassigned_rule(
        self, login, member, rule, prefix, studio_tree, make_group, attach_group, settings
    ):
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        group = make_group("Selected", grants=[(studio_tree, roles.VIEWER)])
        grant = group.grants.get()
        grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
        grant.save(update_fields=["viewer_scope"])
        attach_group(member, group)

        client = login(member)
        assert rule.name not in _main(client.get(prefix))
        assert client.get(f"{prefix}/{rule.pk}").status_code == 404

    def test_viewer_gets_the_page_read_only(self, login, viewer, rule, prefix):
        c = login(viewer)
        page = c.get(prefix)
        assert page.status_code == 200
        html = _main(page)
        assert rule.name in html and "Run log" in html
        assert "New alert" not in html and "/edit" not in html and "<form" not in html
        assert f'href="{prefix}"' in page.content.decode()  # the studio navigation carries the tab
        detail = _main(c.get(f"{prefix}/{rule.pk}"))
        assert "Test now" not in detail and "data-test-now" not in detail
        assert c.get(f"{prefix}/new").status_code == 403
        assert c.post(f"{prefix}/new", _form(rule)).status_code == 403
        assert c.post(f"{prefix}/{rule.pk}/test").status_code == 403
        assert c.post(f"{prefix}/{rule.pk}/delete").status_code == 403
        assert c.post(f"{prefix}/{rule.pk}/toggle").status_code == 403
        assert AlertRule.objects.filter(pk=rule.pk).exists()

    def test_non_member_gets_404(self, login, make_user, org, other_org, rule, prefix):
        no_grant = make_user("nograent@demo.example", org=org)
        assert login(no_grant).get(prefix).status_code == 404
        stranger = make_user("x@rival.example", org=other_org)
        assert login(stranger).get(f"{prefix}/{rule.pk}").status_code == 404

    def test_rule_from_another_studio_is_404(self, login, owner, rule, studio2, org, grant_studio):
        grant_studio(owner, studio2, roles.DEVELOPER)
        assert login(owner).get(f"/s/{org.slug}/{studio2.slug}/alerts/{rule.pk}").status_code == 404


class TestForm:
    def test_developer_creates_a_rule(self, login, owner, rule, prefix, viewer):
        c = login(owner)
        resp = c.post(f"{prefix}/new", _form(
            rule, name="DAU watch", trigger="schedule", freq="hourly", send_minute=15,
            recipient_roles=["admins", "developers"], recipients=[viewer.pk], cooldown_hours=6,
        ))
        assert resp.status_code == 302 and resp["Location"] == prefix
        made = AlertRule.objects.get(name="DAU watch")
        assert made.studio == rule.studio and made.created_by == owner
        assert made.trigger == "schedule" and made.freq == "hourly" and made.send_minute == 15
        assert made.recipient_roles == ["admins", "developers"]
        assert list(made.recipients.all()) == [viewer] and made.cooldown_hours == 6
        row = AuditLog.objects.get(action="alert.create")
        assert row.metadata["rule_id"] == made.pk and row.actor == owner
        html = c.get(prefix).content.decode()
        assert "DAU watch" in html and "Hourly at :15 UTC" in html
        assert "All admins + All developers + 1 person" in html

    def test_edit_and_toggle_audit_updates(self, login, owner, rule, prefix):
        c = login(owner)
        resp = c.post(f"{prefix}/{rule.pk}/edit", _form(rule, name="Renamed", instructions=""))
        assert resp.status_code == 302
        rule.refresh_from_db()
        assert rule.name == "Renamed" and rule.instructions == "" and rule.created_by == owner
        c.post(f"{prefix}/{rule.pk}/toggle")
        rule.refresh_from_db()
        assert rule.enabled is False
        rows = AuditLog.objects.filter(action="alert.update").order_by("pk")
        assert [r.metadata["rule_id"] for r in rows] == [rule.pk, rule.pk]
        assert rows[1].metadata["enabled"] is False
        assert "Disabled" in c.get(prefix).content.decode()

    def test_delete(self, login, owner, rule, prefix):
        AlertRun.objects.create(rule=rule, decision="quiet", title="x")
        resp = login(owner).post(f"{prefix}/{rule.pk}/delete")
        assert resp.status_code == 302
        assert not AlertRule.objects.filter(pk=rule.pk).exists()
        assert AuditLog.objects.get(action="alert.delete").metadata["rule_id"] == rule.pk

    def test_schedule_validation_errors_surface(self, login, owner, rule, prefix):
        resp = login(owner).post(f"{prefix}/new", _form(
            rule, name="Bad", trigger="schedule", send_hour=25, timezone="Mars/Olympus",
        ))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "Must be 0-23." in html and "Unknown IANA timezone" in html
        assert not AlertRule.objects.filter(name="Bad").exists()

    def test_at_least_one_recipient(self, login, owner, rule, prefix):
        resp = login(owner).post(f"{prefix}/new", _form(rule, name="Nobody", recipient_roles=[]))
        assert resp.status_code == 200
        assert "Select at least one recipient" in resp.content.decode()

    def test_form_lists_only_this_studio(self, login, owner, rule, prefix, make_group, other_org):
        make_group("Finance")
        make_group("Rival", org_=other_org)
        html = login(owner).get(f"{prefix}/new").content.decode()
        assert "Finance" in html and "Rival" not in html
        assert "Tell me if anything looks off" in html
        assert f'value="{rule.report.pk}"' in html

    def test_groups_field_absent_without_groups(self, login, owner, prefix, rule):
        html = login(owner).get(f"{prefix}/new").content.decode()
        assert "Permission groups" not in html and "Everyone in Casino Studio" in html


class TestRunLog:
    def test_renders_quiet_and_alert_runs(self, login, viewer, rule, prefix):
        AlertRun.objects.create(
            rule=rule, decision="quiet", title="Nothing unusual",
            message="Revenue moved within its weekly range.",
            evidence={"cited": ["gross_revenue 2026-09-07: 980 vs 1000"]},
        )
        AlertRun.objects.create(
            rule=rule, decision="alert", title="Revenue fell", message="Down 30%.",
            evidence={"cited": ["gross_revenue 2026-09-08: 700 vs 1000"]},
            delivered_to=["a@demo.example", "b@demo.example"], cost_usd="0.0105",
        )
        AlertRun.objects.create(rule=rule, status="error", error="RuntimeError: disk on fire")
        html = login(viewer).get(f"{prefix}/{rule.pk}").content.decode()
        assert "Why it stayed quiet:" in html and "Revenue moved within its weekly range." in html
        assert "gross_revenue 2026-09-07: 980 vs 1000" in html
        assert "Revenue fell" in html and "Delivered to a@demo.example, b@demo.example" in html
        assert "$0.0105" in html and "Nothing sent" in html
        assert "disk on fire" in html
        assert html.index("disk on fire") < html.index("Revenue fell") < html.index("Nothing unusual")


class TestTestNow:
    def test_dry_run_records_and_sends_nothing(self, login, owner, rule, prefix, fake_llm):
        fake_llm(decide(True))
        c = login(owner)
        resp = c.post(f"{prefix}/{rule.pk}/test")
        run = AlertRun.objects.get(rule=rule)
        assert resp.status_code == 302 and resp["Location"] == f"{prefix}/{rule.pk}?run={run.pk}"
        assert run.decision == AlertRun.DECISION_ALERT and run.delivered_to == []
        assert mail.outbox == []
        rule.refresh_from_db()
        assert rule.last_alerted_at is None and rule.last_run_at is not None
        html = c.get(resp["Location"]).content.decode()
        assert "Test run — nothing was sent." in html
        assert f'class="al-run is-new" id="run-{run.pk}"' in html
        assert "Revenue is down 30% on the week." in html

    def test_unavailable_assistant_is_a_message_not_a_500(
        self, login, owner, rule, prefix, assistant_config, fake_llm
    ):
        assistant_config.enabled = False
        assistant_config.save()
        calls = fake_llm(decide(True))
        c = login(owner)
        resp = c.post(f"{prefix}/{rule.pk}/test")
        assert resp.status_code == 302
        html = c.get(resp["Location"]).content.decode()
        assert "The AI assistant is disabled for this organization." in html
        assert calls == [] and not AlertRun.objects.filter(rule=rule).exists()
