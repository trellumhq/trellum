"""Delivery center, dedicated CRUD pages and shared schedule authorization."""
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
def developer(make_user, org, studio_tree, grant_studio):
    user = make_user("dev@demo.example", org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
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
        assert b"No scheduled deliveries match these filters" in resp.content

    def test_schedule_row_has_edit_affordances(self, login, developer, prefix, report_row):
        c = login(developer)
        c.post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps(
                {"recipient_ids": [developer.pk], "freq": "weekly", "weekday": 2, "send_hour": 9, "send_minute": 30}
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
        assert f'/deliveries/{rows[0]["schedule"].pk}/edit' in content
        assert "data-schedule-editor" not in content
        assert "data-toggle-enable" in content
        assert "data-send-sample" in content
        assert "data-delete-row" in content
        assert "Player-Overview" in content or "player-overview" in content
        # Read-only "view report" escape hatch stays, separate from Edit.
        assert f'{prefix}/r/player-overview/"' in content

    def test_report_filter_is_scoped_and_filters_by_report_id(
        self, login, viewer, report_row, other_studio
    ):
        from apps.reports.models import EmailSchedule, Report

        selected_report = Report.objects.create(
            studio=report_row.studio, slug="second-report", name="Second report"
        )
        hidden_report = Report.objects.create(
            studio=other_studio, slug="private-report", name="Hidden report"
        )
        EmailSchedule.objects.create(report=report_row, created_by=viewer)
        EmailSchedule.objects.create(report=selected_report, created_by=viewer)

        client = login(viewer)
        response = client.get("/me/deliveries?q=no-match")
        option_ids = {report.pk for report in response.context["report_filter_options"]}
        assert option_ids == {report_row.pk, selected_report.pk}
        assert hidden_report.name not in response.content.decode()
        assert response.context["schedule_rows"] == []

        response = client.get(f"/me/deliveries?report=id%3A{selected_report.pk}")
        assert [row["report"].pk for row in response.context["schedule_rows"]] == [selected_report.pk]
        assert f'value="id:{selected_report.pk}" selected' in response.content.decode()

        response = client.get(f"/me/deliveries?report={selected_report.slug}")
        assert [row["report"].pk for row in response.context["schedule_rows"]] == [selected_report.pk]
        assert 'value="slug:second-report" selected' in response.content.decode()

    def test_schedule_row_embeds_schedule_json_for_the_inline_form(
        self, login, developer, prefix, report_row
    ):
        c = login(developer)
        create = c.post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps({"recipient_ids": [developer.pk], "freq": "daily", "send_hour": 9, "send_minute": 0}),
            content_type="application/json",
        ).json()
        schedule_id = create["schedule"]["id"]

        resp = c.get("/me/deliveries")
        content = resp.content.decode()
        assert 'data-schedule-json' in content
        assert f'"id": {schedule_id}' in content
        assert '"freq": "daily"' in content

    def test_enable_disable_label_matches_schedule_state(self, login, developer, prefix, report_row):
        c = login(developer)
        c.post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps(
                {"recipient_ids": [developer.pk], "freq": "daily", "send_hour": 9, "send_minute": 0, "enabled": False}
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
        self, login, developer, prefix, report_row, make_group, attach_group
    ):
        """The recipients cell reads e.g. "All developers + Leadership + 4
        people" -- role/group chips by label, individuals by count -- not a
        bare number (see apps.reports.views._recipient_summary)."""
        group = make_group("Leadership")
        attach_group(developer, group)
        c = login(developer)
        c.post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps(
                {
                    "freq": "daily",
                    "send_hour": 9,
                    "send_minute": 0,
                    "recipient_ids": [developer.pk],
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
        self, login, developer, prefix, report_row, make_group, attach_group
    ):
        group = make_group("Leadership")
        attach_group(developer, group)
        c = login(developer)
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

    def test_admin_manages_colleague_and_legacy_bookmark_lists_shared_schedules(self, login, org_admin, viewer, report_row):
        row = self.create(report_row, viewer)
        client = login(org_admin)
        assert client.get('/deliveries').url == f'/orgs/{report_row.studio.org.slug}/settings/deliveries'
        assert [item['schedule'].pk for item in client.get('/me/deliveries').context['schedule_rows']] == [row.pk]
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
        assert login(superuser).get('/deliveries').url == '/instance/settings#scheduled-deliveries'

    def test_creator_cannot_be_reassigned_and_schedule_is_unchanged(self, login, org_admin, viewer, report_row):
        from apps.core.models import AuditLog
        row = self.create(report_row, None, enabled=False)
        response = login(org_admin).post(self.url(report_row, row), json.dumps({'owner_id': viewer.pk}), content_type='application/json')
        assert response.status_code == 400
        row.refresh_from_db()
        assert row.created_by is None and not row.enabled
        assert not AuditLog.objects.filter(action='notify.schedule_update').exists()
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

    def test_policy_defaults_only_new_rows_and_instance_pause_wins(self, login, org_admin, superuser, org, developer, report_row):
        from apps.core.models import InstanceConfig
        from apps.reports.deliveries import delivery_pause, schedule_defaults
        old = self.create(report_row, developer)
        client = login(superuser)
        response = client.post('/deliveries', {'scope': 'instance', 'deliveries_paused': 'on', 'delivery_default_enabled': 'false', 'delivery_default_timezone': 'Europe/Amsterdam'}, HTTP_X_TRELLUM_FORM='1')
        assert response.status_code == 200 and response.json()['ok']
        assert schedule_defaults(org) == {'enabled': False, 'timezone': 'Europe/Amsterdam'}
        assert login(org_admin).post('/deliveries', {'scope': org.pk, 'delivery_default_enabled': '', 'delivery_default_timezone': ''}, HTTP_X_TRELLUM_FORM='1').status_code == 200
        assert 'instance' in delivery_pause(org.pk)
        prefix = f'/s/{org.slug}/{report_row.studio.slug}/api/reports/{report_row.slug}/schedules'
        created = login(developer).post(prefix, json.dumps({'recipient_ids': [developer.pk]}), content_type='application/json').json()['schedule']
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

    def test_native_and_enhanced_validation_keep_drafts(self, login, developer, report_row):
        row = self.create(report_row, developer)
        body = {'return_to': '/me/deliveries', 'freq': 'daily', 'send_hour': '99', 'send_minute': '15', 'weekday': '0', 'month_day': '1', 'timezone': 'UTC', 'enabled': 'on', 'recipient_ids': [developer.pk]}
        response = login(developer).post(self.url(report_row, row), body)
        assert response.status_code == 400
        form = response.context['form']
        assert form.is_bound and form['send_hour'].value() == '99'
        assert 'send_hour' in form.errors
        response = login(developer).post(self.url(report_row, row), body, HTTP_X_TRELLUM_FORM='1')
        assert response.status_code == 400 and 'send_hour' in response.json()['errors']
        row.refresh_from_db()
        assert row.send_hour == 8

    def test_native_save_and_delete_snapshot(self, login, developer, report_row):
        from apps.core.models import AuditLog
        row = self.create(report_row, developer)
        client = login(developer)
        assert client.post(self.url(report_row, row), {'action': 'toggle', 'enabled': 'false', 'return_to': '/me/deliveries'}).status_code == 302
        response = client.post(self.url(report_row, row), {'action': 'delete'}, HTTP_X_TRELLUM_FORM='1')
        assert response.status_code == 200
        log = AuditLog.objects.get(action='notify.schedule_delete')
        assert log.metadata['before']['id'] == row.pk
        assert log.metadata['before']['recipient_ids'] == [developer.pk]

    def test_native_delete_requires_confirmation_and_preserves_csrf_and_owner_scope(
        self, login, developer, other_viewer, report_row
    ):
        from django.test import Client
        from apps.core.models import AuditLog
        from apps.reports.models import EmailSchedule
        row = self.create(report_row, developer)
        url = self.url(report_row, row)
        body = {'action': 'delete', 'return_to': '/me/deliveries'}
        client = Client(enforce_csrf_checks=True)
        client.force_login(developer)
        client.get('/me/deliveries')
        token = client.cookies['csrftoken'].value
        assert client.post(url, body).status_code == 403
        response = client.post(url, body, HTTP_X_CSRFTOKEN=token)
        assert response.status_code == 200 and EmailSchedule.objects.filter(pk=row.pk).exists()
        assert not AuditLog.objects.filter(action='notify.schedule_delete').exists()
        assert f'#{row.pk}' in response.content.decode()
        assert developer.email in response.content.decode() and report_row.name in response.content.decode()
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

    def test_legacy_alert_tab_redirects_to_policy_settings(self, login, org_admin, org):
        response = login(org_admin).get('/deliveries?type=alerts')
        assert response.status_code == 302
        assert response.url == f'/orgs/{org.slug}/settings/deliveries'

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
        assert len(login(org_admin).get(f'/s/{org.slug}/{report_row.studio.slug}/deliveries?creator=view@demo').context['schedule_rows']) == 1
        assert login(org_admin).get(f'/s/{org.slug}/{report_row.studio.slug}/deliveries?creator=missing').context['schedule_rows'] == []

    def test_delete_confirmation_identifies_recipients(self, login, org_admin, viewer, report_row):
        self.create(report_row, viewer)
        response = login(org_admin).get(f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}/deliveries')
        assert f'Recipients: 1 person + {viewer.email}.' in response.content.decode()

    def test_policy_audit_taxonomy_is_registered(self):
        from apps.core.audit_actions import category_for
        assert category_for('notify.delivery_policy_update', debug=True) == 'admin'

    @pytest.mark.parametrize('owner_id', ['abc', {}, [], True, 9223372036854775808])
    def test_malformed_owner_returns_field_validation(self, owner_id, login, org_admin, report_row):
        row = self.create(report_row, None)
        response = login(org_admin).post(self.url(report_row, row), json.dumps({'owner_id': owner_id}), content_type='application/json')
        assert response.status_code == 400
        assert 'cannot be reassigned' in response.json()['error']

    def test_policy_form_ids_unique_across_instance_and_orgs(self, login, superuser, org, other_org):
        import re
        responses = [login(superuser).get(path) for path in ('/instance/settings', f'/orgs/{org.slug}/settings/deliveries', f'/orgs/{other_org.slug}/settings/deliveries')]
        forms = [form for response in responses for form in re.findall(r'<form[^>]*id="delivery-policy-form"[^>]*>(.*?)</form>', response.content.decode(), re.S)]
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


class TestStudioDeliveryPages:
    def body(self, user, report, **changes):
        return {'report_id': report.pk, 'delivery_page': '1',
                'return_to': f'/s/{report.studio.org.slug}/{report.studio.slug}/deliveries',
                'freq': 'weekly', 'send_hour': '12', 'send_minute': '15', 'weekday': '2',
                'month_day': '1', 'timezone': 'Europe/Amsterdam', 'enabled': 'on',
                'recipient_ids': [user.pk], **changes}

    @pytest.mark.parametrize('enhanced', [False, True])
    def test_create_and_edit_navigation_and_shared_validation(self, enhanced, login, developer, prefix, report_row):
        from apps.reports.models import EmailSchedule
        client = login(developer)
        headers = {'HTTP_X_TRELLUM_FORM': '1'} if enhanced else {}
        picker = client.get(f'{prefix}/deliveries/new')
        assert picker.status_code == 200 and list(picker.context['reports']) == [report_row]
        form = client.get(f'{prefix}/deliveries/new?report={report_row.pk}')
        assert form.context['form']['recipient_ids'].value() == []
        body = self.body(developer, report_row)
        response = client.post(f'{prefix}/deliveries/new', body, **headers)
        assert response.status_code == (200 if enhanced else 302)
        assert (response.json()['redirect'] if enhanced else response.url) == f'{prefix}/deliveries'
        row = EmailSchedule.objects.get(report=report_row)
        assert row.created_by == developer and row.recipients.get() == developer
        assert client.get(f'{prefix}/deliveries/{row.pk}/edit').status_code == 200
        response = client.post(f'{prefix}/api/reports/{report_row.slug}/schedules/{row.pk}', {**body, 'send_hour': '13'}, **headers)
        if enhanced:
            assert response.json()['ok'] and 'redirect' not in response.json()
            assert response.json()['values']['send_time'] == '13:15'
        else:
            assert response.url == f'{prefix}/deliveries'
        row.refresh_from_db()
        assert row.send_hour == 13 and row.delivery_revision == 1
        listing = client.get(f'{prefix}/deliveries').content.decode()
        assert f'{prefix}/deliveries/{row.pk}/edit' in listing and 'data-schedule-editor' not in listing
        assert 'Add delivery' in listing and '>Deliveries<' in listing

    def test_native_time_accepts_arbitrary_minutes_and_bad_time_is_inline(self, login, developer, prefix, report_row):
        from apps.reports.models import EmailSchedule
        client = login(developer)
        create = client.post(f'{prefix}/deliveries/new', {**self.body(developer, report_row), 'send_time': '18:37'})
        assert create.status_code == 302
        row = EmailSchedule.objects.get(report=report_row)
        assert (row.send_hour, row.send_minute) == (18, 37)
        invalid = client.post(f'{prefix}/api/reports/{report_row.slug}/schedules/{row.pk}',
                              {**self.body(developer, report_row), 'send_time': ''}, HTTP_X_TRELLUM_FORM='1')
        assert invalid.status_code == 400 and 'send_time' in invalid.json()['errors']
        row.refresh_from_db()
        assert (row.send_hour, row.send_minute) == (18, 37)

    def test_schedules_json_includes_manifest_resolved_picker_assets(self, login, developer, prefix, report_row):
        response = login(developer).get(f'{prefix}/api/reports/{report_row.slug}/schedules')
        assert response.status_code == 200
        assets = response.json()['picker_assets']
        assert set(assets) == {'choice_css', 'choice_js', 'recipient_css', 'recipient_js'}
        assert all(value.endswith(name) for value, name in zip(assets.values(),
            ('choice-picker.css', 'choice-picker.js', 'recipient-picker.css', 'recipient-picker.js')))

    @pytest.mark.parametrize('action', ['create', 'edit'])
    def test_invalid_native_and_ajax_keep_draft_without_mutation(self, action, login, developer, prefix, report_row):
        from apps.reports.models import EmailSchedule
        row = EmailSchedule.objects.create(report=report_row, created_by=developer)
        row.recipients.add(developer)
        client = login(developer)
        url = f'{prefix}/deliveries/new' if action == 'create' else f'{prefix}/api/reports/{report_row.slug}/schedules/{row.pk}'
        body = self.body(developer, report_row, send_hour='99', timezone='Wrong/Zone')
        native = client.post(url, body)
        enhanced = client.post(url, body, HTTP_X_TRELLUM_FORM='1')
        assert native.status_code == enhanced.status_code == 400
        assert native.context['form']['send_hour'].value() == '99'
        assert native.context['form']['timezone'].value() == 'Wrong/Zone'
        assert {'send_hour', 'timezone'} <= set(native.context['form'].errors)
        assert {'send_hour', 'timezone'} <= set(enhanced.json()['errors'])
        row.refresh_from_db()
        assert row.send_hour == 8 and row.timezone == 'UTC' and EmailSchedule.objects.count() == 1

    def test_studio_admin_manages_all_owners_without_policy_or_reassignment(self, login, make_user, org, grant_studio, studio_tree, viewer, report_row, prefix):
        from apps.reports.models import EmailSchedule
        admin = make_user('studio-admin@example.test', org=org)
        grant_studio(admin, studio_tree, roles.ADMIN)
        row = EmailSchedule.objects.create(report=report_row, created_by=viewer, enabled=False)
        row.recipients.add(viewer)
        client = login(admin)
        response = client.get(f'{prefix}/deliveries')
        assert [item['schedule'].pk for item in response.context['schedule_rows']] == [row.pk]
        assert response.context['schedule_rows'][0]['editable']
        assert b'Manage deliveries across organizations' not in response.content
        assert client.get('/deliveries').status_code == 404
        edit = client.get(f'{prefix}/deliveries/{row.pk}/edit')
        assert edit.status_code == 200 and 'owner_id' not in edit.context['form'].fields
        url = f'{prefix}/api/reports/{report_row.slug}/schedules/{row.pk}'
        assert client.post(url, self.body(viewer, report_row)).status_code == 302
        assert client.post(url, self.body(viewer, report_row, owner_id=admin.pk)).status_code == 400

    def test_viewer_creator_recipient_and_nonrecipient_are_read_only(self, login, viewer, other_viewer, prefix, report_row, org, make_user, grant_studio):
        from apps.reports.models import EmailSchedule, Report
        from apps.studios.models import Studio
        own = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        visible = EmailSchedule.objects.create(report=report_row, created_by=other_viewer)
        visible.recipients.add(viewer)
        hidden = EmailSchedule.objects.create(report=report_row, created_by=other_viewer, enabled=False)
        other = Studio.objects.create(org=org, slug='second', name='Second')
        grant_studio(viewer, other, roles.VIEWER)
        other_report = Report.objects.create(studio=other, slug='elsewhere', name='Elsewhere')
        cross = EmailSchedule.objects.create(report=other_report, created_by=viewer)
        client = login(viewer)
        response = client.get(f'{prefix}/deliveries')
        rows = {item['schedule'].pk: item for item in response.context['schedule_rows']}
        assert set(rows) == {own.pk, visible.pk, hidden.pk}
        assert all(not row['editable'] for row in rows.values())
        assert b'Add delivery' not in response.content
        assert client.get(f'{prefix}/deliveries/{own.pk}/edit').status_code == 404
        assert client.post(f'{prefix}/api/reports/{report_row.slug}/schedules/{own.pk}', self.body(viewer, report_row)).status_code == 404
        assert client.get(f'{prefix}/deliveries/{visible.pk}/edit').status_code == 404
        assert client.post(f'{prefix}/api/reports/{report_row.slug}/schedules/{visible.pk}', self.body(viewer, report_row)).status_code == 404
        assert client.delete(f'{prefix}/api/reports/{report_row.slug}/schedules/{visible.pk}').status_code == 404
        assert client.get(f'{prefix}/deliveries/{cross.pk}/edit').status_code == 404
        assert client.post(f'{prefix}/api/reports/{report_row.slug}/schedules/{cross.pk}', self.body(viewer, report_row)).status_code == 404
        assert client.get(f'{prefix}/deliveries/new?report={other_report.pk}').status_code == 403
        assert client.post(f'{prefix}/deliveries/new', self.body(viewer, other_report)).status_code == 403
        assert 'Read only' in response.content.decode()

    def test_legacy_admin_edit_redirects_to_studio_without_global_link(self, login, org_admin, viewer, prefix, report_row):
        from apps.reports.models import EmailSchedule
        row = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        row.recipients.add(viewer)
        client = login(org_admin)
        assert b'Manage deliveries' not in client.get(f'{prefix}/deliveries').content
        response = client.get(f'/deliveries/{row.pk}/edit')
        assert response.status_code == 302 and response.url == f'{prefix}/deliveries/{row.pk}/edit'
        assert 'owner_id' not in client.get(response.url).context['form'].fields
        assert f'{prefix}/deliveries/{row.pk}/edit' in client.get(f'{prefix}/deliveries').content.decode()
        result = client.post(f'{prefix}/api/reports/{report_row.slug}/schedules/{row.pk}', self.body(viewer, report_row, return_to='/deliveries'), HTTP_X_TRELLUM_FORM='1').json()
        assert result['ok'] and 'redirect' not in result

    def test_create_requires_csrf_and_rejects_ineligible_recipients(self, developer, other_viewer, prefix, report_row, login, make_user, org):
        from django.test import Client
        from apps.reports.models import EmailSchedule
        client = Client(enforce_csrf_checks=True)
        client.force_login(developer)
        assert client.post(f'{prefix}/deliveries/new', self.body(developer, report_row)).status_code == 403
        outsider = make_user('no-access@example.test', org=org)
        client = login(developer)
        response = client.post(f'{prefix}/deliveries/new', self.body(developer, report_row, recipient_ids=[outsider.pk]), HTTP_X_TRELLUM_FORM='1')
        assert response.status_code == 400 and not EmailSchedule.objects.exists()

    def test_picker_contains_visible_present_reports_and_analyses(self, login, developer, prefix, report_row, settings):
        from apps.reports.models import Report
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        analysis = Report.objects.create(studio=report_row.studio, slug='analysis', kind=Report.KIND_ANALYSIS)
        private = Report.objects.create(studio=report_row.studio, slug='private', audience=Report.AUDIENCE_PRIVATE)
        missing = Report.objects.create(studio=report_row.studio, slug='missing', present_in_scan=False)
        client = login(developer)
        assert set(client.get(f'{prefix}/deliveries/new').context['reports']) == {report_row, analysis, private}
        assert client.get(f'{prefix}/deliveries/new?report={analysis.pk}').status_code == 200
        for denied in (missing,):
            assert client.get(f'{prefix}/deliveries/new?report={denied.pk}').status_code == 404
            assert client.post(f'{prefix}/deliveries/new', self.body(developer, denied)).status_code == 404

    def test_native_delete_cancel_and_confirm_return_to_studio_list(self, login, developer, prefix, report_row):
        from apps.reports.models import EmailSchedule
        row = EmailSchedule.objects.create(report=report_row, created_by=developer)
        row.recipients.add(developer)
        client = login(developer)
        url = f'{prefix}/api/reports/{report_row.slug}/schedules/{row.pk}'
        body = {'action': 'delete', 'return_to': f'{prefix}/deliveries'}
        response = client.post(url, body)
        assert response.status_code == 200 and response.context['return_to'] == f'{prefix}/deliveries'
        assert EmailSchedule.objects.filter(pk=row.pk).exists()
        assert client.get(response.context['return_to']).status_code == 200
        response = client.post(url, {**body, 'confirm_delete': 'yes'})
        assert response.status_code == 302 and response.url == f'{prefix}/deliveries'
        assert not EmailSchedule.objects.filter(pk=row.pk).exists()

    def test_redirect_cannot_leave_scope_and_recipient_history_stays_private(self, login, viewer, developer, other_viewer, prefix, report_row):
        from apps.reports.models import EmailSchedule
        row = EmailSchedule.objects.create(report=report_row, created_by=other_viewer,
            delivery_accepted_to=['accepted@private.example'], delivery_failed_to=['failed@private.example'])
        row.recipients.add(viewer)
        client = login(viewer)
        content = client.get(f'{prefix}/deliveries').content.decode()
        assert 'accepted@private.example' not in content and 'failed@private.example' not in content
        response = login(developer).post(f'{prefix}/deliveries/new', self.body(viewer, report_row, return_to='https://outside.example'), HTTP_X_TRELLUM_FORM='1')
        assert response.json()['redirect'] == f'{prefix}/deliveries'

    def test_group_granted_studio_admin_can_edit_colleague(self, login, viewer, other_viewer, prefix, report_row, make_group, attach_group):
        from apps.reports.models import EmailSchedule
        group = make_group('Studio admins', grants=[(report_row.studio, roles.ADMIN)])
        attach_group(viewer, group)
        row = EmailSchedule.objects.create(report=report_row, created_by=other_viewer)
        assert login(viewer).get(f'{prefix}/deliveries/{row.pk}/edit').status_code == 200

    def test_account_menu_has_no_delivery_links(self, login, viewer, prefix):
        import re
        html = login(viewer).get(f'{prefix}/deliveries').content.decode()
        account = re.search(r'<div class="tl-console-account-menu">(.*?)</details>', html, re.S).group(1)
        assert '/deliveries' not in account and 'Deliveries' in html

    def test_api_key_cannot_open_another_org_delivery_pages(self, org_admin, other_org, org, studio_tree, report_row):
        from types import SimpleNamespace
        from django.http import Http404
        from django.test import RequestFactory
        from apps.orgs.models import OrgMembership
        from apps.reports.views import studio_deliveries, studio_delivery_new
        OrgMembership.objects.create(user=org_admin, org=other_org, role=roles.ORG_ADMIN)
        request = RequestFactory().get(f'/s/{org.slug}/{studio_tree.slug}/deliveries')
        request.user = org_admin
        request.api_key = SimpleNamespace(org_id=other_org.pk)
        for view in (studio_deliveries, studio_delivery_new):
            with pytest.raises(Http404):
                view(request, org_slug=org.slug, studio_slug=studio_tree.slug)
