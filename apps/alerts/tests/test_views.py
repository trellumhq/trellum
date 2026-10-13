"""The studio Alerts page (internal planning ticket #147): role gating, the form, the run log
and Test now. The evaluator itself is covered in test_evaluator.py; here it
runs behind the scripted ``decide`` client."""
import pytest
from django.core import mail
from django.utils import timezone

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
        assert "New alert" not in html and "/edit" not in html and 'method="post"' not in html
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
    @pytest.mark.parametrize(("freq", "weekday", "month_day"), [
        ("daily", 0, 1), ("weekdays", 0, 1), ("weekly", 4, 1), ("monthly", 0, 28),
    ])
    def test_native_schedule_time_keeps_minute_precision(self, login, owner, rule, prefix, freq, weekday, month_day):
        response = login(owner).post(f"{prefix}/new", _form(
            rule, name=f"Minute schedule {freq}", trigger="schedule", freq=freq,
            send_time="17:43", weekday=weekday, month_day=month_day,
        ))
        assert response.status_code == 302
        saved = AlertRule.objects.get(name=f"Minute schedule {freq}")
        assert (saved.freq, saved.send_hour, saved.send_minute) == (freq, 17, 43)
        assert saved.weekday == weekday and saved.month_day == month_day

    def test_hourly_schedule_uses_explicit_minute_past_hour(self, login, owner, rule, prefix):
        response = login(owner).post(f"{prefix}/new", _form(
            rule, name="Hourly minute schedule", trigger="schedule", freq="hourly",
            send_minute=0, send_time="08:00", hourly_minute=37,
        ))
        assert response.status_code == 302
        saved = AlertRule.objects.get(name="Hourly minute schedule")
        assert saved.freq == "hourly" and saved.send_minute == 37

    def test_midnight_time_is_preserved(self, login, owner, rule, prefix):
        response = login(owner).post(f"{prefix}/new", _form(
            rule, name="Midnight schedule", trigger="schedule", freq="daily", send_time="00:05",
        ))
        assert response.status_code == 302
        saved = AlertRule.objects.get(name="Midnight schedule")
        assert (saved.send_hour, saved.send_minute) == (0, 5)

    def test_blank_new_clock_value_does_not_fall_back_to_legacy_integer_fields(self, login, owner, rule, prefix):
        response = login(owner).post(f"{prefix}/new", _form(
            rule, trigger="schedule", freq="daily", send_time="", send_hour=8, send_minute=0,
        ))
        assert response.status_code == 200
        assert "Enter a local time." in response.content.decode()

    def test_saved_timezone_alias_remains_an_available_choice(self, login, owner, rule, prefix, monkeypatch):
        from apps.alerts import forms

        # Seed a persisted legacy alias even when this host's tzdata no longer
        # ships the corresponding backward link for ZoneInfo validation.
        AlertRule.objects.filter(pk=rule.pk).update(timezone="US/Pacific")
        rule.refresh_from_db()
        zones = forms.available_timezones()
        monkeypatch.setattr(forms, "available_timezones", lambda: zones - {"US/Pacific"})
        response = login(owner).get(f"{prefix}/{rule.pk}/edit")
        assert response.status_code == 200
        assert '<option value="US/Pacific" selected' in response.content.decode()

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

    def test_edit_form_keeps_saved_timezone_without_browser_default(self, login, owner, prefix, rule):
        rule.timezone = "Europe/London"
        rule.save(update_fields=["timezone"])
        html = login(owner).get(f"{prefix}/{rule.pk}/edit").content.decode()
        assert 'name="timezone"' in html and 'value="Europe/London"' in html
        assert "data-browser-timezone-default" not in html


class TestRunLog:
    def test_run_link_selects_older_page_and_ignores_foreign_run(self, login, viewer, rule, prefix):
        from datetime import timedelta

        now = timezone.now()
        old = AlertRun.objects.create(rule=rule, started_at=now - timedelta(days=40), title="Target old run")
        for offset in range(30):
            AlertRun.objects.create(rule=rule, started_at=now - timedelta(days=offset), title=f"Run {offset}")
        foreign_rule = AlertRule.objects.create(
            org=rule.org, studio=rule.studio, report=rule.report,
            name="Other rule", created_by=rule.created_by,
        )
        foreign = AlertRun.objects.create(rule=foreign_rule, title="Foreign secret")
        client = login(viewer)
        response = client.get(f"{prefix}/{rule.pk}?run={old.pk}#run-{old.pk}")
        assert response.status_code == 200
        assert response.context["page"].number == 2
        assert old in response.context["page"]
        assert f'class="al-run is-new" id="run-{old.pk}"' in response.content.decode()
        unrelated = client.get(f"{prefix}/{rule.pk}?run={foreign.pk}")
        assert unrelated.status_code == 200 and "Foreign secret" not in unrelated.content.decode()

    def test_null_legacy_run_renders_explicit_uncertainty(self, login, viewer, rule, prefix):
        AlertRun.objects.create(
            rule=rule, decision="alert", delivered_to=["historical@test.example"],
            execution_mode=None, history_namespace=None, delivery_status=None,
            failed_to=None, budget_warning_sent=None,
        )
        html = _main(login(viewer).get(f"{prefix}/{rule.pk}"))
        assert ">Legacy: mode unknown</span>" in html
        assert "Legacy: delivery uncertain" in html
        assert "Historical recipients historical@test.example" in html
        assert "Accepted for delivery to historical@test.example" not in html
        assert "None" not in html

    def test_list_keeps_live_status_beside_live_timestamp_after_newer_test(self, login, viewer, rule, prefix):
        live_at = timezone.now()
        AlertRun.objects.create(rule=rule, execution_mode="live", decision="quiet", started_at=live_at)
        AlertRule.objects.filter(pk=rule.pk).update(last_run_at=live_at)
        AlertRun.objects.create(rule=rule, execution_mode="test", decision="alert")
        response = login(viewer).get(prefix)
        row = response.context["rows"][0]
        assert row["last"] == "quiet" and row["rule"].last_run_at == live_at

    def test_durable_execution_and_delivery_labels(self, login, viewer, rule, prefix):
        for mode, status, delivery in [
            ("test", "ok", "not_attempted"), ("live", "ok", "not_attempted"),
            ("live", "skipped_cooldown", "not_attempted"), ("live", "ok", "accepted"),
            ("live", "ok", "partial"), ("live", "ok", "failed"),
            ("live", "ok", "no_recipients"),
        ]:
            AlertRun.objects.create(rule=rule, execution_mode=mode, status=status,
                                    delivery_status=delivery, decision="alert" if delivery != "not_attempted" else "quiet")
        legacy = AlertRun.objects.create(rule=rule, decision="quiet")
        # A highlight URL may point to a live run; it cannot turn it into a test.
        html = login(viewer).get(f"{prefix}/{rule.pk}?run={legacy.pk}").content.decode()
        for label in ("Test", "Evaluated", "Suppressed", "Accepted for delivery", "Partial", "Failed",
                      "No eligible recipients", "Legacy: mode unknown", "Legacy: delivery uncertain"):
            assert f'>{label}</span>' in html or label in html
        assert html.count('>Test</span>') == 1
        assert "does not confirm inbox receipt" in html

    def test_renders_unknown_cost_as_unavailable(self, login, viewer, rule, prefix):
        AlertRun.objects.create(rule=rule, status="ok", decision="quiet", cost_usd=None)
        html = login(viewer).get(f"{prefix}/{rule.pk}").content.decode()
        assert "Cost unavailable" in html

    def test_renders_quiet_and_alert_runs(self, login, viewer, rule, prefix):
        started_at = timezone.now()  # equal clocks must still render newest first
        AlertRun.objects.create(
            rule=rule, decision="quiet", title="Nothing unusual", started_at=started_at,
            message="Revenue moved within its weekly range.",
            evidence={"cited": ["gross_revenue 2026-09-07: 980 vs 1000"]},
        )
        AlertRun.objects.create(
            rule=rule, decision="alert", title="Revenue fell", message="Down 30%.", started_at=started_at,
            evidence={"cited": ["gross_revenue 2026-09-08: 700 vs 1000"]},
            delivered_to=["a@demo.example", "b@demo.example"], cost_usd="0.0105",
        )
        AlertRun.objects.create(rule=rule, status="error", error="RuntimeError: disk on fire", started_at=started_at)
        html = login(viewer).get(f"{prefix}/{rule.pk}").content.decode()
        assert "Why it stayed quiet:" in html and "Revenue moved within its weekly range." in html
        assert "gross_revenue 2026-09-07: 980 vs 1000" in html
        assert "Revenue fell" in html and "Historical recipients a@demo.example, b@demo.example" in html
        assert "$0.0105" in html and "Legacy: delivery uncertain" in html
        assert "disk on fire" in html
        assert html.index("disk on fire") < html.index("Revenue fell") < html.index("Nothing unusual")


class TestTestNow:
    def test_test_now_bills_requesting_developer_not_rule_owner(self, login, make_user, grant_studio,
                                                               rule, prefix, fake_llm):
        editor = make_user("editor@demo.example", org=rule.org)
        grant_studio(editor, rule.studio, roles.DEVELOPER)
        fake_llm(decide(True))
        response = login(editor).post(f"{prefix}/{rule.pk}/test")
        run = AlertRun.objects.get(rule=rule, execution_mode=AlertRun.MODE_TEST)
        assert response.status_code == 302 and response["Location"].endswith(f"?run={run.pk}")
        assert run.history_namespace.startswith(f"editor:{editor.pk}:")
        assert run.cost_usd is not None and mail.outbox == []

    def test_dry_run_records_and_sends_nothing(self, login, owner, rule, prefix, fake_llm):
        fake_llm(decide(True))
        c = login(owner)
        resp = c.post(f"{prefix}/{rule.pk}/test")
        run = AlertRun.objects.get(rule=rule)
        assert resp.status_code == 302 and resp["Location"] == f"{prefix}/{rule.pk}?run={run.pk}"
        assert run.decision == AlertRun.DECISION_ALERT and run.delivered_to == []
        assert mail.outbox == []
        rule.refresh_from_db()
        assert rule.last_alerted_at is None and rule.last_test_at is not None
        assert rule.last_run_at is None
        html = c.get(resp["Location"]).content.decode()
        assert "Test run — nothing was sent." in html
        assert f'class="al-run is-new" id="run-{run.pk}"' in html
        assert "Revenue is down 30% on the week." in html
        # Provenance survives an ordinary revisit without the highlight query.
        assert '>Test</span>' in c.get(f"{prefix}/{rule.pk}").content.decode()

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
