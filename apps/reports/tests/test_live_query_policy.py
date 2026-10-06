"""Org-level live-query rate limit (live-query filter redesign):
``OrgLiveQueryPolicy``, its org-admin settings API, and the settings page.
Same shape as ``TestShareOrgPolicyEndpoint``/``TestOrgShareSettingsPage`` in
test_share.py -- a partial-update POST an org-admin settings page auto-saves
against."""
import json

import pytest

from apps.core import roles
from apps.core.models import AuditLog
from apps.reports.models import (
    DEFAULT_LIVE_QUERY_RATE_LIMIT,
    OrgLiveQueryPolicy,
    live_query_rate_limit,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def developer(make_user, org, studio_tree, grant_studio):
    user = make_user("dev@demo.example", org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
    return user


class TestLiveQueryRateLimitHelper:
    def test_no_row_reads_as_default(self, org):
        assert live_query_rate_limit(org) == DEFAULT_LIVE_QUERY_RATE_LIMIT

    def test_row_with_null_reads_as_default(self, org):
        OrgLiveQueryPolicy.objects.create(org=org, rate_limit_per_minute=None)
        assert live_query_rate_limit(org) == DEFAULT_LIVE_QUERY_RATE_LIMIT

    def test_row_with_value_overrides_default(self, org):
        OrgLiveQueryPolicy.objects.create(org=org, rate_limit_per_minute=90)
        assert live_query_rate_limit(org) == 90


class TestApiLiveQueryPolicy:
    def test_org_admin_can_set(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/api/live_query_policy",
            data=json.dumps({"rate_limit_per_minute": 90}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "ok": True,
            "rate_limit_per_minute": 90,
            "effective_rate_limit_per_minute": 90,
        }
        policy = OrgLiveQueryPolicy.objects.get(org=org)
        assert policy.rate_limit_per_minute == 90
        assert policy.updated_by == org_admin

    def test_null_resets_to_default(self, login, org_admin, org):
        OrgLiveQueryPolicy.objects.create(org=org, rate_limit_per_minute=90)
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/api/live_query_policy",
            data=json.dumps({"rate_limit_per_minute": None}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["effective_rate_limit_per_minute"] == DEFAULT_LIVE_QUERY_RATE_LIMIT
        policy = OrgLiveQueryPolicy.objects.get(org=org)
        assert policy.rate_limit_per_minute is None

    def test_negative_rejected(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/api/live_query_policy",
            data=json.dumps({"rate_limit_per_minute": -1}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert not OrgLiveQueryPolicy.objects.filter(org=org).exists()

    def test_zero_disables_live_queries(self, login, org_admin, org):
        # 0 is the org's "off" switch, not an error: it persists and makes the
        # effective limit 0, which blocks every live execution.
        from apps.reports.models import live_query_rate_limit

        resp = login(org_admin).post(
            f"/orgs/{org.slug}/api/live_query_policy",
            data=json.dumps({"rate_limit_per_minute": 0}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert OrgLiveQueryPolicy.objects.get(org=org).rate_limit_per_minute == 0
        assert live_query_rate_limit(org) == 0

    def test_non_numeric_rejected(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/api/live_query_policy",
            data=json.dumps({"rate_limit_per_minute": "lots"}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert not OrgLiveQueryPolicy.objects.filter(org=org).exists()

    def test_missing_field_rejected(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/api/live_query_policy",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_update_is_audited(self, login, org_admin, org):
        login(org_admin).post(
            f"/orgs/{org.slug}/api/live_query_policy",
            data=json.dumps({"rate_limit_per_minute": 60}),
            content_type="application/json",
        )
        row = AuditLog.objects.filter(action="live_query_policy.update").first()
        assert row is not None
        assert row.actor == org_admin
        assert row.org == org

    def test_developer_who_is_not_org_admin_forbidden(self, login, developer, org):
        resp = login(developer).post(
            f"/orgs/{org.slug}/api/live_query_policy",
            data=json.dumps({"rate_limit_per_minute": 60}),
            content_type="application/json",
        )
        assert resp.status_code == 403
        assert not OrgLiveQueryPolicy.objects.filter(org=org).exists()

    def test_anonymous_unauthorized(self, client, org):
        resp = client.post(
            f"/orgs/{org.slug}/api/live_query_policy",
            data=json.dumps({"rate_limit_per_minute": 60}),
            content_type="application/json",
        )
        assert resp.status_code in (401, 403)

    def test_get_not_allowed(self, login, org_admin, org):
        resp = login(org_admin).get(f"/orgs/{org.slug}/api/live_query_policy")
        assert resp.status_code == 405


class TestOrgLiveQuerySettingsPage:
    def test_org_admin_can_view(self, login, org_admin, org):
        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/live-queries")
        assert resp.status_code == 200
        assert b"Live queries" in resp.content

    def test_shows_configured_value(self, login, org_admin, org):
        OrgLiveQueryPolicy.objects.create(org=org, rate_limit_per_minute=45)
        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/live-queries")
        assert b'value="45"' in resp.content

    def test_developer_who_is_not_org_admin_forbidden(self, login, developer, org):
        resp = login(developer).get(f"/orgs/{org.slug}/settings/live-queries")
        assert resp.status_code == 403
