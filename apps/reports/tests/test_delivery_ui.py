"""v1.2 delivery UI: the envelope's bulk "do I have anything set up here"
endpoint (api_my_subscriptions) and the cross-studio "Deliveries" page
(my_deliveries). Schedule *mutation* is covered by test_notify.py -- this
file only exercises the read surfaces.

Both surfaces are schedules-only now: failure/recovery alerts go to every
studio developer/admin automatically, with no per-user subscription to
read back (see apps.reports.notify.alert_recipients)."""
import json

import pytest

from apps.core import roles
from apps.reports.models import EmailSchedule

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


class TestMySubscriptionsEndpoint:
    """Contract: {"reports": {"<slug>": {"schedules": <count>}}} for every
    report where the current user is a recipient of at least one enabled
    schedule. Sparse -- a report the user isn't a recipient on is absent."""

    def test_empty_when_nothing_set_up(self, login, viewer, prefix, report_row):
        body = login(viewer).get(f"{prefix}/api/my-subscriptions").json()
        assert body == {"reports": {}}

    def test_reflects_own_schedule(self, login, viewer, prefix, report_row):
        c = login(viewer)
        c.post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps({"freq": "daily", "send_hour": 9, "send_minute": 0}),
            content_type="application/json",
        )
        body = c.get(f"{prefix}/api/my-subscriptions").json()
        assert body["reports"] == {"player-overview": {"schedules": 1}}

    def test_counts_multiple_enabled_schedules_on_one_report(self, login, viewer, prefix, report_row):
        c = login(viewer)
        for hour in (9, 14):
            c.post(
                f"{prefix}/api/reports/player-overview/schedules",
                data=json.dumps({"freq": "daily", "send_hour": hour, "send_minute": 0}),
                content_type="application/json",
            )
        body = c.get(f"{prefix}/api/my-subscriptions").json()
        assert body["reports"] == {"player-overview": {"schedules": 2}}

    def test_disabled_schedule_is_not_counted(self, login, viewer, prefix, report_row):
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer, enabled=False)
        schedule.recipients.add(viewer)
        body = login(viewer).get(f"{prefix}/api/my-subscriptions").json()
        assert body == {"reports": {}}

    def test_not_a_recipient_is_not_counted(self, login, viewer, other_viewer, prefix, report_row):
        # created_by=viewer but recipients only has other_viewer: the
        # contract keys off recipient membership, not who created the row.
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipients.add(other_viewer)
        body = login(viewer).get(f"{prefix}/api/my-subscriptions").json()
        assert body == {"reports": {}}

    def test_another_users_schedule_is_not_mine(
        self, login, viewer, other_viewer, prefix, report_row
    ):
        login(other_viewer).post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps({"freq": "daily", "send_hour": 9, "send_minute": 0}),
            content_type="application/json",
        )
        body = login(viewer).get(f"{prefix}/api/my-subscriptions").json()
        assert body == {"reports": {}}

    def test_role_reached_user_counts_as_subscribed(
        self, login, viewer, make_user, org, prefix, report_row, grant_studio, studio_tree
    ):
        """A user swept in only via a role chip -- never added to the
        ``recipients`` M2M -- still lights up their own envelope badge (see
        apps.reports.notify.resolve_recipients)."""
        developer = make_user("dev@demo.example", org=org)
        grant_studio(developer, studio_tree, roles.DEVELOPER)
        schedule = EmailSchedule.objects.create(
            report=report_row, created_by=viewer, recipient_roles=["developers"]
        )

        body = login(developer).get(f"{prefix}/api/my-subscriptions").json()
        assert body["reports"] == {"player-overview": {"schedules": 1}}
        assert schedule.recipients.count() == 0  # never touched the M2M

    def test_group_reached_user_counts_as_subscribed(
        self, login, viewer, other_viewer, prefix, report_row, make_group, attach_group
    ):
        group = make_group("Leadership")
        attach_group(other_viewer, group)
        schedule = EmailSchedule.objects.create(report=report_row, created_by=viewer)
        schedule.recipient_groups.add(group)

        body = login(other_viewer).get(f"{prefix}/api/my-subscriptions").json()
        assert body["reports"] == {"player-overview": {"schedules": 1}}

    def test_requires_studio_access(self, client, prefix, report_row):
        assert client.get(f"{prefix}/api/my-subscriptions").status_code == 401


class TestMySubscriptionsQueryCount:
    """api_my_subscriptions used to call notify.resolve_recipients per
    schedule -- resolving a schedule's *entire* audience, fanning a
    per-user access check out over the studio's whole membership -- just to
    answer "is the current user one of them". The query count must stay
    flat as the number of schedules on a report grows (see
    notify.user_in_schedule_audience, which precomputes the requester's own
    membership once instead)."""

    def test_query_count_does_not_scale_with_schedule_count(
        self, django_assert_max_num_queries, login, viewer, prefix, report_row
    ):
        for i in range(15):
            EmailSchedule.objects.create(
                report=report_row,
                created_by=viewer,
                recipient_roles=["everyone"],
                send_hour=i % 24,
                send_minute=i % 60,
            )
        c = login(viewer)

        with django_assert_max_num_queries(20):
            resp = c.get(f"{prefix}/api/my-subscriptions")

        assert resp.json()["reports"] == {"player-overview": {"schedules": 15}}


class TestMyDeliveriesPage:
    def test_requires_auth(self, client):
        resp = client.get("/me/deliveries")
        assert resp.status_code in (302, 401)

    def test_lists_own_schedule(self, login, viewer, prefix, report_row):
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
        rows = resp.context["schedule_rows"]
        assert len(rows) == 1
        assert rows[0]["report"].slug == "player-overview"
        assert rows[0]["cadence"] == "Weekly at 09:30 on Wednesday UTC"
        assert rows[0]["edit_url"] == f"{prefix}/r/player-overview/?rdw=1"
        assert "Player-Overview".encode() in resp.content  # the report name is shown

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

    def test_hides_rows_once_studio_access_is_revoked(
        self, login, viewer, prefix, report_row, studio_tree
    ):
        from apps.studios.models import StudioMembership

        c = login(viewer)
        c.post(
            f"{prefix}/api/reports/player-overview/schedules",
            data=json.dumps({"freq": "daily", "send_hour": 9, "send_minute": 0}),
            content_type="application/json",
        )
        StudioMembership.objects.filter(user=viewer, studio=studio_tree).delete()
        resp = c.get("/me/deliveries")
        assert resp.context["schedule_rows"] == []
        # The row survives server-side: regaining access restores it.
        assert EmailSchedule.objects.filter(created_by=viewer).exists()

    def test_empty_state_renders(self, login, viewer):
        resp = login(viewer).get("/me/deliveries")
        assert resp.status_code == 200
        assert resp.context["schedule_rows"] == []
        assert "alert_rows" not in resp.context
