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
