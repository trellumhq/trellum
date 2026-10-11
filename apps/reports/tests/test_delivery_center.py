"""v1.2 delivery center: templates/reports/my_deliveries.html now expands
each schedule row inline (edit/delete/enable/disable/sample, wired by
static/report_delivery.js) instead of linking out to the report's drawer,
and drops the alert-subscription section entirely in favor of one
informational line. This file only exercises the server-rendered template
-- the inline JS behavior (expand/collapse, live PATCH-style updates) is
covered by hand/browser testing, not here."""
import json
from pathlib import Path

import pytest
from django.conf import settings

from apps.core import roles

pytestmark = pytest.mark.django_db


@pytest.fixture
def viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("view@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


@pytest.fixture
def other_viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("view2@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


@pytest.fixture
def prefix(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}"


class TestDeliveryCenterPage:
    def test_requires_auth(self, client):
        resp = client.get("/me/deliveries")
        assert resp.status_code in (302, 401)

    def test_empty_state_renders(self, login, viewer):
        resp = login(viewer).get("/me/deliveries")
        assert resp.status_code == 200
        assert resp.context["schedule_rows"] == []
        assert b"No scheduled deliveries yet" in resp.content

    def test_schedule_row_has_edit_affordances(self, login, viewer, prefix, report_row):
        c = login(viewer)
        c.post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps(
                {"freq": "weekly", "weekday": 2, "send_hour": 9, "send_minute": 30}
            ),
            content_type="application/json",
        )
        resp = c.get("/me/deliveries")
        assert resp.status_code == 200
        content = resp.content.decode()

        rows = resp.context["schedule_rows"]
        assert len(rows) == 1
        assert rows[0]["report"].slug == "player-overview"

        # The row itself, and each per-row action the drawer's inline form
        # (report_delivery.js buildScheduleForm/wireDeliveryRow) hooks into.
        assert "data-delivery-row" in content
        assert f'data-prefix="{prefix}"' in content
        assert 'data-slug="player-overview"' in content
        assert "data-toggle-edit" in content
        assert "data-toggle-enable" in content
        assert "data-send-sample" in content
        assert "data-delete-row" in content
        assert "Player-Overview" in content or "player-overview" in content
        # Read-only "view report" escape hatch stays, separate from Edit.
        assert f'{prefix}/r/player-overview/"' in content

    def test_schedule_row_embeds_schedule_json_for_the_inline_form(
        self, login, viewer, prefix, report_row
    ):
        c = login(viewer)
        create = c.post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps({"freq": "daily", "send_hour": 9, "send_minute": 0}),
            content_type="application/json",
        ).json()
        schedule_id = create["schedule"]["id"]

        resp = c.get("/me/deliveries")
        content = resp.content.decode()
        assert 'data-schedule-json' in content
        assert f'"id": {schedule_id}' in content
        assert '"freq": "daily"' in content

    def test_enable_disable_label_matches_schedule_state(self, login, viewer, prefix, report_row):
        c = login(viewer)
        c.post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps(
                {"freq": "daily", "send_hour": 9, "send_minute": 0, "enabled": False}
            ),
            content_type="application/json",
        )
        resp = c.get("/me/deliveries")
        content = resp.content.decode()
        assert "ui-badge warn" in content  # "Disabled" status badge
        assert ">Enable<" in content  # the toggle button offers the opposite action

    def test_does_not_list_another_users_schedule(
        self, login, viewer, other_viewer, prefix, report_row
    ):
        login(other_viewer).post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps({"freq": "daily", "send_hour": 9, "send_minute": 0}),
            content_type="application/json",
        )
        resp = login(viewer).get("/me/deliveries")
        assert resp.context["schedule_rows"] == []

    def test_no_alert_subscription_section(self, login, viewer, prefix, report_row):
        """Alert subscriptions are gone from this page entirely -- replaced
        by one informational line about automatic failure alerts."""
        resp = login(viewer).get("/me/deliveries")
        content = resp.content.decode()
        assert "Alert subscriptions" not in content
        assert "alert_rows" not in content
        assert "rdwAlertMode" not in content
        assert "No alert subscriptions yet" not in content

    def test_informational_alert_line_renders(self, login, viewer):
        resp = login(viewer).get("/me/deliveries")
        assert (
            "When a report breaks or recovers, studio developers and admins "
            "are emailed automatically — no setup."
        ) in resp.content.decode()

    def test_loads_report_delivery_script(self, login, viewer):
        """The inline expand-into-form behavior is wired by
        static/report_delivery.js (window.ReportDelivery.mountCenter) --
        the page must actually load it."""
        resp = login(viewer).get("/me/deliveries")
        assert b"report_delivery.js" in resp.content
        assert b"mountCenter" in resp.content

    def test_recipients_cell_summarizes_roles_groups_and_people(
        self, login, viewer, prefix, report_row, make_group, attach_group
    ):
        """The recipients cell reads e.g. "All developers + Leadership + 4
        people" -- role/group chips by label, individuals by count -- not a
        bare number (see apps.reports.views._recipient_summary)."""
        group = make_group("Leadership")
        attach_group(viewer, group)
        c = login(viewer)
        c.post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps(
                {
                    "freq": "daily",
                    "send_hour": 9,
                    "send_minute": 0,
                    "recipient_ids": [viewer.pk],
                    "recipient_roles": ["developers"],
                    "recipient_group_ids": [group.pk],
                }
            ),
            content_type="application/json",
        )
        resp = c.get("/me/deliveries")
        content = resp.content.decode()
        assert "All developers + Leadership + 1 person" in content

    def test_schedule_json_carries_roles_and_groups_for_the_inline_picker(
        self, login, viewer, prefix, report_row, make_group, attach_group
    ):
        group = make_group("Leadership")
        attach_group(viewer, group)
        c = login(viewer)
        c.post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps(
                {
                    "freq": "daily",
                    "send_hour": 9,
                    "send_minute": 0,
                    "recipient_ids": [],
                    "recipient_roles": ["admins"],
                    "recipient_group_ids": [group.pk],
                }
            ),
            content_type="application/json",
        )
        resp = c.get("/me/deliveries")
        content = resp.content.decode()
        assert '"recipient_roles": ["admins"]' in content
        assert '"name": "Leadership"' in content


class TestReportDeliveryJsErrorHandling:
    """static/report_delivery.js: a rejected fetch on submit/sample/delete
    must not leave the triggering button disabled forever with no
    explanation -- every one of those needs a .catch that re-enables its
    button and shows an error flash, mirroring the .catch already on
    loadAndRender. There's no JS test runner in this repo, so this checks
    the served source directly (same spirit as the "does the page load
    this script" checks above) rather than exercising it in a browser."""

    @pytest.fixture
    def js_source(self):
        path = Path(settings.BASE_DIR) / "static" / "report_delivery.js"
        return path.read_text(encoding="utf-8")

    def _function_body(self, js_source, start_marker, end_marker):
        start = js_source.index(start_marker)
        end = js_source.index(end_marker, start)
        return js_source[start:end]

    def test_submit_has_a_catch_that_reenables_the_submit_button(self, js_source):
        body = self._function_body(
            js_source, "function submitScheduleForm(root, ctx) {", "\n    // ── state"
        )
        assert ".catch(" in body
        after_catch = body.split(".catch(", 1)[1]
        assert "submitBtn.disabled = false" in after_catch

    def test_drawer_sample_and_delete_each_have_a_catch(self, js_source):
        body = self._function_body(
            js_source, "function wireStaticControls() {", "\n    // ── create/edit form"
        )
        assert body.count(".catch(") >= 2
        assert "sampleBtn.disabled = false" in body.split(".catch(", 1)[1]
        assert "delBtn.disabled = false" in body.split(".catch(", 2)[2]

    def test_delivery_center_sample_and_delete_each_have_a_catch(self, js_source):
        body = self._function_body(
            js_source, "function wireDeliveryRow(row) {", "\n    window.ReportDelivery"
        )
        assert body.count(".catch(") >= 2

class TestSharedDeliveryManagement:
    def create(self, report, owner, **kwargs):
        from apps.reports.models import EmailSchedule
        row = EmailSchedule.objects.create(report=report, created_by=owner, **kwargs)
        if owner:
            row.recipients.add(owner)
        return row

    def url(self, report, schedule):
        studio = report.studio
        return f'/s/{studio.org.slug}/{studio.slug}/api/reports/{report.slug}/schedules/{schedule.pk}'

    def test_admin_manages_colleague_and_personal_bookmark_stays_personal(self, login, org_admin, viewer, report_row):
        row = self.create(report_row, viewer)
        client = login(org_admin)
        assert [item['schedule'].pk for item in client.get('/deliveries').context['schedule_rows']] == [row.pk]
        assert client.get('/me/deliveries').context['schedule_rows'] == []
        response = client.post(self.url(report_row, row), json.dumps({'enabled': False}), content_type='application/json')
        assert response.status_code == 200
        assert response.json()['schedule']['editable']
        row.refresh_from_db()
        assert not row.enabled

    def test_group_admin_has_same_policy(self, login, viewer, other_viewer, report_row, make_group, attach_group):
        group = make_group('Admins', org_role=roles.ORG_ADMIN)
        attach_group(viewer, group)
        row = self.create(report_row, other_viewer)
        response = login(viewer).post(self.url(report_row, row), json.dumps({'send_hour': 11}), content_type='application/json')
        assert response.status_code == 200

    def test_viewer_and_support_flag_cannot_manage_colleague(self, login, viewer, other_viewer, report_row):
        row = self.create(report_row, other_viewer)
        viewer.is_operator_flag = True
        viewer.save(update_fields=['is_operator_flag'])
        client = login(viewer)
        assert client.get('/deliveries').status_code == 404
        assert client.post(self.url(report_row, row), json.dumps({'enabled': False}), content_type='application/json').status_code == 404
        assert client.delete(self.url(report_row, row)).status_code == 404

    def test_other_tenant_not_listed_but_superuser_can_manage(self, login, org_admin, superuser, other_studio, make_user, report_row):
        from apps.reports.models import Report
        other = Report.objects.create(studio=other_studio, slug='hidden', name='Hidden')
        owner = make_user('private@other.example', org=other_studio.org)
        hidden = self.create(other, owner)
        response = login(org_admin).get('/deliveries')
        assert b'private@other.example' not in response.content
        assert b'Hidden' not in response.content
        assert login(org_admin).post(self.url(other, hidden), json.dumps({'enabled': False}), content_type='application/json').status_code == 404
        assert len(login(superuser).get('/deliveries').context['schedule_rows']) == 1

    def test_ownerless_reassign_is_audited_and_never_enables(self, login, org_admin, viewer, report_row):
        from apps.core.models import AuditLog
        row = self.create(report_row, None, enabled=False)
        response = login(org_admin).post(self.url(report_row, row), json.dumps({'owner_id': viewer.pk}), content_type='application/json')
        assert response.status_code == 200
        row.refresh_from_db()
        assert row.created_by == viewer and not row.enabled
        log = AuditLog.objects.get(action='notify.schedule_update')
        assert log.metadata['before']['owner_id'] is None
        assert log.metadata['after']['owner_id'] == viewer.pk
        assert row.last_attempt_at is None

    @pytest.mark.parametrize('invalid', ['inactive', 'no_access', 'bad_id'])
    def test_reassignment_rejects_invalid_owner(self, invalid, login, org_admin, viewer, make_user, org, report_row):
        row = self.create(report_row, None)
        candidate = make_user('candidate@example.test', org=org)
        if invalid == 'inactive':
            candidate.is_active = False
            candidate.save(update_fields=['is_active'])
        value = 'unknown' if invalid == 'bad_id' else candidate.pk
        response = login(org_admin).post(self.url(report_row, row), json.dumps({'owner_id': value}), content_type='application/json')
        assert response.status_code == 400
        row.refresh_from_db()
        assert row.created_by is None

    def test_policy_defaults_only_new_rows_and_instance_pause_wins(self, login, org_admin, superuser, org, viewer, report_row):
        from apps.core.models import InstanceConfig
        from apps.reports.deliveries import delivery_pause, schedule_defaults
        old = self.create(report_row, viewer)
        client = login(superuser)
        response = client.post('/deliveries', {'scope': 'instance', 'deliveries_paused': 'on', 'delivery_default_enabled': 'false', 'delivery_default_timezone': 'Europe/Amsterdam'}, HTTP_X_TRELLUM_FORM='1')
        assert response.status_code == 200 and response.json()['ok']
        assert schedule_defaults(org) == {'enabled': False, 'timezone': 'Europe/Amsterdam'}
        assert login(org_admin).post('/deliveries', {'scope': org.pk, 'delivery_default_enabled': '', 'delivery_default_timezone': ''}, HTTP_X_TRELLUM_FORM='1').status_code == 200
        assert 'instance' in delivery_pause(org.pk)
        prefix = f'/s/{org.slug}/{report_row.studio.slug}/api/reports/{report_row.slug}/schedules'
        created = login(viewer).post(prefix, '{}', content_type='application/json').json()['schedule']
        assert created['enabled'] is False and created['timezone'] == 'Europe/Amsterdam'
        old.refresh_from_db()
        assert old.enabled and old.timezone == 'UTC'
        assert InstanceConfig.load().deliveries_paused

    def test_policy_rejects_invalid_timezone_and_cross_org_scope(self, login, org_admin, org, other_org):
        client = login(org_admin)
        response = client.post('/deliveries', {'scope': org.pk, 'delivery_default_timezone': 'Wrong/Zone'}, HTTP_X_TRELLUM_FORM='1')
        assert response.status_code == 400
        assert 'delivery_default_timezone' in response.json()['errors']
        assert client.post('/deliveries', {'scope': other_org.pk}).status_code == 404
        assert client.post('/deliveries', {'scope': 'instance'}).status_code == 404

    def test_native_and_enhanced_validation_keep_drafts(self, login, viewer, report_row):
        row = self.create(report_row, viewer)
        body = {'return_to': '/me/deliveries', 'freq': 'daily', 'send_hour': '99', 'send_minute': '15', 'weekday': '0', 'month_day': '1', 'timezone': 'UTC', 'enabled': 'on', 'recipient_ids': [viewer.pk]}
        response = login(viewer).post(self.url(report_row, row), body)
        assert response.status_code == 400
        form = response.context['schedule_rows'][0]['form']
        assert form.is_bound and form['send_hour'].value() == '99'
        assert 'send_hour' in form.errors
        response = login(viewer).post(self.url(report_row, row), body, HTTP_X_TRELLUM_FORM='1')
        assert response.status_code == 400 and 'send_hour' in response.json()['errors']
        row.refresh_from_db()
        assert row.send_hour == 8

    def test_native_save_and_delete_snapshot(self, login, viewer, report_row):
        from apps.core.models import AuditLog
        row = self.create(report_row, viewer)
        client = login(viewer)
        assert client.post(self.url(report_row, row), {'action': 'toggle', 'enabled': 'false', 'return_to': '/me/deliveries'}).status_code == 302
        response = client.post(self.url(report_row, row), {'action': 'delete'}, HTTP_X_TRELLUM_FORM='1')
        assert response.status_code == 200
        log = AuditLog.objects.get(action='notify.schedule_delete')
        assert log.metadata['before']['id'] == row.pk
        assert log.metadata['before']['recipient_ids'] == [viewer.pk]

    def test_native_delete_requires_confirmation_and_preserves_csrf_and_owner_scope(
        self, login, viewer, other_viewer, report_row
    ):
        from django.test import Client
        from apps.core.models import AuditLog
        from apps.reports.models import EmailSchedule
        row = self.create(report_row, viewer)
        url = self.url(report_row, row)
        body = {'action': 'delete', 'return_to': '/me/deliveries'}
        client = Client(enforce_csrf_checks=True)
        client.force_login(viewer)
        client.get('/me/deliveries')
        token = client.cookies['csrftoken'].value
        assert client.post(url, body).status_code == 403
        response = client.post(url, body, HTTP_X_CSRFTOKEN=token)
        assert response.status_code == 200 and EmailSchedule.objects.filter(pk=row.pk).exists()
        assert not AuditLog.objects.filter(action='notify.schedule_delete').exists()
        assert f'#{row.pk}' in response.content.decode()
        assert viewer.email in response.content.decode() and report_row.name in response.content.decode()
        assert b'href="/me/deliveries">Cancel' in response.content
        assert client.get('/me/deliveries').status_code == 200  # cancellation only navigates
        confirmed = {**body, 'confirm_delete': 'yes'}
        assert login(other_viewer).post(url, confirmed).status_code == 404
        assert client.post(url, confirmed).status_code == 403
        assert EmailSchedule.objects.filter(pk=row.pk).exists()
        assert client.post(url, confirmed, HTTP_X_CSRFTOKEN=token).status_code == 302
        assert not EmailSchedule.objects.filter(pk=row.pk).exists()
        assert AuditLog.objects.filter(action='notify.schedule_delete').count() == 1
        assert client.post(url, confirmed, HTTP_X_CSRFTOKEN=token).status_code == 404
        assert AuditLog.objects.filter(action='notify.schedule_delete').count() == 1

    def test_pause_resume_invalidates_old_snapshot_and_next_is_strictly_future(self, login, org_admin, org, viewer, report_row):
        from datetime import datetime, timezone
        from apps.reports.deliveries import next_occurrence, schedule_snapshot, schedule_send_current
        row = self.create(report_row, viewer)
        snapshot = schedule_snapshot(row)
        assert schedule_send_current(snapshot) is not None
        client = login(org_admin)
        assert client.post('/deliveries', {'scope': org.pk, 'delivery_default_timezone': 'Europe/Amsterdam'}, HTTP_X_TRELLUM_FORM='1').status_code == 200
        assert schedule_send_current(snapshot) is not None
        for paused in ('on', ''):
            assert client.post('/deliveries', {'scope': org.pk, 'deliveries_paused': paused}, HTTP_X_TRELLUM_FORM='1').status_code == 200
        org.refresh_from_db()
        assert org.delivery_generation == 2
        assert schedule_send_current(snapshot) is None
        exact = datetime(2026, 10, 11, 8, 0, tzinfo=timezone.utc)
        assert next_occurrence(row, exact) > exact

    def test_alert_tab_shows_owner_repair_and_existing_form_link(self, login, org_admin, org, report_row):
        from apps.alerts.models import AlertRule
        rule = AlertRule.objects.create(org=org, studio=report_row.studio, report=report_row, name='Watch', created_by=None)
        response = login(org_admin).get('/deliveries?type=alerts')
        assert response.status_code == 200
        assert b'owner repair required' in response.content
        assert f'/alerts/{rule.pk}/edit' in response.content.decode()
        assert response.context['schedule_rows'] == []

    def test_key_scope_and_creator_filters(self, org_admin, other_org, org, report_row, viewer, login, make_user):
        from types import SimpleNamespace
        from django.test import RequestFactory
        from apps.orgs.models import OrgMembership
        from apps.reports.deliveries import managed_orgs, schedule_for_request
        from django.http import Http404
        row = self.create(report_row, viewer)
        OrgMembership.objects.create(user=org_admin, org=other_org, role=roles.ORG_ADMIN)
        request = RequestFactory().get('/deliveries')
        request.user = org_admin
        request.api_key = SimpleNamespace(org_id=other_org.pk)
        assert [item.pk for item in managed_orgs(request)] == [other_org.pk]
        with pytest.raises(Http404):
            schedule_for_request(request, row.pk)
        assert len(login(org_admin).get('/deliveries?creator=view@demo').context['schedule_rows']) == 1
        assert login(org_admin).get('/deliveries?creator=missing').context['schedule_rows'] == []

    def test_delete_confirmation_identifies_recipients(self, login, org_admin, viewer, report_row):
        self.create(report_row, viewer)
        response = login(org_admin).get('/deliveries')
        assert f'Recipients: 1 person + {viewer.email}.' in response.content.decode()

    def test_policy_audit_taxonomy_is_registered(self):
        from apps.core.audit_actions import category_for
        assert category_for('notify.delivery_policy_update', debug=True) == 'admin'

    @pytest.mark.parametrize('owner_id', ['abc', {}, [], True, 9223372036854775808])
    def test_malformed_owner_returns_field_validation(self, owner_id, login, org_admin, report_row):
        row = self.create(report_row, None)
        response = login(org_admin).post(self.url(report_row, row), json.dumps({'owner_id': owner_id}), content_type='application/json')
        assert response.status_code == 400
        assert 'owner_id' in response.json()['errors']

    def test_policy_form_ids_unique_across_instance_and_orgs(self, login, superuser, org, other_org):
        import re
        response = login(superuser).get('/deliveries')
        forms = re.findall(r'<form[^>]*id="delivery-policy-[^"]*"[^>]*>(.*?)</form>', response.content.decode(), re.S)
        assert len(forms) == 3
        field_ids = [field for form in forms for field in re.findall(r'\bid="([^"]+)"', form)]
        assert len(field_ids) == len(set(field_ids))
        for form in forms:
            for label in re.findall(r'<label[^>]*for="([^"]+)"', form):
                assert f'id="{label}"' in form

    @pytest.mark.parametrize('access_change', ['deactivated', 'removed'])
    def test_recipient_cannot_read_historical_addresses(self, access_change, login, viewer, other_viewer, report_row):
        from apps.studios.models import StudioMembership
        row = self.create(report_row, other_viewer)
        row.recipients.add(viewer)
        row.delivery_accepted_to = [other_viewer.email]
        row.delivery_failed_to = ['former@private.example']
        row.save(update_fields=['delivery_accepted_to', 'delivery_failed_to'])
        if access_change == 'deactivated':
            other_viewer.is_active = False
            other_viewer.save(update_fields=['is_active'])
        else:
            StudioMembership.objects.filter(user=other_viewer, studio=report_row.studio).delete()
        url = self.url(report_row, row).rsplit('/', 1)[0]
        response = login(viewer).get(url)
        schedule = response.json()['schedules'][0]
        assert schedule['accepted_to'] == [] and schedule['failed_to'] == []
        assert not any(person['email'] == other_viewer.email for person in schedule['recipients'])
