"""Whole-middleware static reads and the adjacent authorization boundary."""
import datetime

import pytest
from django.contrib.sessions.models import Session
from django.test import Client, override_settings
from django.urls import path
from django.utils import timezone

from apps.accounts import session_policy
from apps.accounts.models import UserSession
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

ASSETS = (
    "/_vendor/chart.umd.min.js",
    "/api/assistant/widget.js",
    "/api/reports/menu-widget.js",
    "/api/reports/delivery-widget.js",
    "/api/reports/theme-widget.js",
    "/api/reports/share-widget.js",
    "/api/reports/embed-widget.js",
    "/api/reports/embed-element.js",
    "/api/reports/views-widget.js",
    "/api/reports/metrics-widget.js",
)


@pytest.fixture(autouse=True)
def clean_policy_cache():
    session_policy.invalidate_policy_cache()
    yield
    session_policy.invalidate_policy_cache()


@pytest.mark.parametrize("identity", (
    "anonymous", "authenticated", "idle", "revoked", "expired-cookie",
    "inactive", "mfa", "impersonated", "expired-impersonation", "bearer",
))
def test_public_bytes_need_no_database_or_session_write(
    identity, member, org, make_user, settings, django_assert_num_queries,
):
    from apps.orgs.models import OrgSecurityPolicy

    settings.SESSION_SAVE_EVERY_REQUEST = True
    client = Client()
    public = Client()
    originals = {url: public.get(url) for url in ASSETS}
    session_key = None
    headers = {}
    if identity == "bearer":
        headers["HTTP_AUTHORIZATION"] = "Bearer trellum_pk_invalid"
    elif identity != "anonymous":
        client.force_login(member)
        session = client.session
        session_key = session.session_key
        if identity == "idle":
            session["sp_last_seen"] = session_policy._now() - 86400
            session.save()
        elif identity == "revoked":
            member.bump_auth_epoch()
        elif identity == "expired-cookie":
            Session.objects.filter(session_key=session_key).update(
                expire_date=timezone.now() - datetime.timedelta(days=1),
            )
        elif identity == "inactive":
            member.is_active = False
            member.save(update_fields=["is_active"])
        elif identity == "mfa":
            OrgSecurityPolicy.objects.create(org=org, require_mfa=True, mfa_grace_days=0)
        elif identity in {"impersonated", "expired-impersonation"}:
            operator = make_user("asset-operator@example.test")
            operator.is_operator_flag = True
            operator.save(update_fields=["is_operator_flag"])
            session["impersonator_id"] = operator.pk
            minutes = -1 if identity == "expired-impersonation" else 30
            session["impersonation_expires_at"] = (
                timezone.now() + datetime.timedelta(minutes=minutes)
            ).isoformat()
            session.save()

    before_sessions = list(Session.objects.values().order_by("session_key"))
    before_registry = list(UserSession.objects.values().order_by("pk"))
    before_audit = AuditLog.objects.count()
    for url, original in originals.items():
        with django_assert_num_queries(0):
            response = client.get(url, HTTP_X_REQUEST_ID="asset-proof", **headers)
        assert response.status_code == original.status_code == 200
        assert response.content == original.content
        assert response["Content-Type"] == original["Content-Type"]
        assert response.get("Cache-Control") == original.get("Cache-Control")
        assert response["X-Request-ID"] == "asset-proof"
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["X-Frame-Options"] == settings.X_FRAME_OPTIONS
        assert response["Content-Length"] == str(len(response.content))
        assert not response.cookies and "cookie" not in response.get("Vary", "").lower()

    assert list(Session.objects.values().order_by("session_key")) == before_sessions
    assert list(UserSession.objects.values().order_by("pk")) == before_registry
    assert AuditLog.objects.count() == before_audit

    # Static reads neither consume nor erase the pending enforcement decision.
    response = client.get("/api/me", HTTP_ACCEPT="application/json", **headers)
    if identity in {"idle", "revoked"}:
        assert response.status_code == 401
        assert response.json()["error"] == "session_expired"
    elif identity == "mfa":
        assert response.status_code == 403
        assert response.json()["error"] == "mfa_enrollment_required"
    elif identity == "bearer":
        assert response.status_code == 401
        assert response.json()["error"] == "invalid_api_key"
    elif identity in {"anonymous", "expired-cookie", "inactive"}:
        assert not response.json()["authenticated"]
    elif identity == "expired-impersonation":
        assert client.session.get("impersonator_id") is None
        assert AuditLog.objects.filter(action="operator.impersonate.expired").exists()
    else:
        assert response.json()["authenticated"]


def test_public_headers_and_bytes_match_normal_chain(settings, django_assert_num_queries):
    normal = [item for item in settings.MIDDLEWARE if item != "apps.core.public_assets.PublicAssetMiddleware"]
    with override_settings(MIDDLEWARE=normal):
        originals = {url: Client().get(url) for url in ASSETS}
    client = Client()
    for url, original in originals.items():
        with django_assert_num_queries(0):
            response = client.get(url)
            head = client.head(url)
        assert response.content == original.content
        # The normal session middleware adds Vary: Cookie even to anonymous
        # static reads. The public path deliberately has no cookie dependency.
        assert {k: v for k, v in response.items() if k not in {"X-Request-ID", "Vary"}} == {
            k: v for k, v in original.items() if k not in {"X-Request-ID", "Vary"}
        }
        assert original.get("Vary", "") in {"", "Cookie"}
        assert not response.get("Vary")
        assert head.status_code == response.status_code
        assert head.content == b""
        assert head["Content-Length"] == response["Content-Length"]


@pytest.mark.parametrize("url", (
    "/_vendor/..%2fproject.py", "/_vendor/nope.js", "/_vendor/%2e%2e%2ftrellum_portal/settings/base.py",
))
def test_vendor_traversal_and_missing_remain_bounded(url, member, django_assert_num_queries):
    client = Client()
    client.force_login(member)
    with django_assert_num_queries(0):
        response = client.get(url)
    assert response.status_code in {400, 404}


def test_public_dispatch_preserves_host_rejection(settings):
    settings.ALLOWED_HOSTS = ["valid.example.test"]
    assert Client().get(ASSETS[0], HTTP_HOST="invalid.example.test").status_code == 400


@pytest.mark.parametrize("url", ASSETS)
def test_unsafe_methods_still_use_csrf(url):
    assert Client(enforce_csrf_checks=True).post(url, {}).status_code == 403


@pytest.mark.parametrize("url", (
    "/api/assistant/available", "/api/me/favorites", "/s/demo/casino/r/private/data.json",
    "/api/reports/menu-widget.js/extra", "/_vendor", "/api/reports/metrics-widget.js/",
))
def test_adjacent_paths_still_enforce_session_revocation(url, member):
    client = Client()
    client.force_login(member)
    member.bump_auth_epoch()
    response = client.get(url, HTTP_ACCEPT="application/json")
    assert response.status_code == 401
    assert response.json()["error"] == "session_expired"


def test_route_name_cannot_authorize_another_view(rf):
    from django.http import HttpResponse
    from apps.core.public_assets import PublicAssetMiddleware

    called = []

    def private(request):
        raise AssertionError("name collision must not dispatch")

    class Conf:
        urlpatterns = [path("collision", private, name="vendor-asset")]

    def downstream(request):
        called.append(request.path)
        return HttpResponse("normal chain")

    request = rf.get("/collision")
    request.urlconf = Conf
    response = PublicAssetMiddleware(downstream)(request)
    assert response.content == b"normal chain" and called == ["/collision"]
