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
from apps.core.public_assets import PUBLIC_ASSET_CACHE_CONTROL

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
        assert response["Cache-Control"] == (original.get("Cache-Control") or PUBLIC_ASSET_CACHE_CONTROL)
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
        # Cache-Control is compared below: the public path supplies a default.
        skip = {"X-Request-ID", "Vary", "Cache-Control"}
        assert {k: v for k, v in response.items() if k not in skip} == {
            k: v for k, v in original.items() if k not in skip
        }
        assert response["Cache-Control"] == (original.get("Cache-Control") or PUBLIC_ASSET_CACHE_CONTROL)
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


def _marked_routes():
    """(url name, view) for every route whose view carries @public_asset."""
    from django.urls import URLPattern, get_resolver

    from apps.core.public_assets import is_public_asset

    found = []

    def walk(patterns):
        for entry in patterns:
            if isinstance(entry, URLPattern):
                if is_public_asset(entry.callback):
                    found.append(entry.name)
            else:
                walk(entry.url_patterns)

    walk(get_resolver().url_patterns)
    return sorted(found)


def test_the_marker_is_the_whole_list_of_public_routes():
    # Adding or dropping a public script is a decorator on its view; this
    # fails when a mark is added or lost without a matching ASSETS entry.
    assert _marked_routes() == sorted([
        "vendor-asset", "assistant-widget", "reports-menu-widget", "reports-delivery-widget",
        "reports-theme-widget", "reports-share-widget", "reports-embed-widget",
        "reports-embed-element", "reports-views-widget", "reports-metrics-widget",
    ])
    from django.urls import resolve

    assert sorted(resolve(url).url_name for url in ASSETS) == _marked_routes()


@pytest.mark.parametrize("url", ASSETS)
def test_every_public_script_is_cacheable_and_outside_the_csp(url, settings):
    settings.CONTENT_SECURITY_POLICY = "default-src 'self'"
    response = Client().get(url)
    assert response.status_code == 200
    assert "max-age=" in response["Cache-Control"] and "public" in response["Cache-Control"]
    assert "Content-Security-Policy" not in response
    assert "Content-Security-Policy-Report-Only" not in response
    assert not response.cookies and not response.get("Vary")


def test_views_that_choose_their_own_cache_policy_keep_it():
    from django.http import HttpResponse

    from apps.core.public_assets import PublicAssetMiddleware, public_asset

    @public_asset
    def mine(request):
        response = HttpResponse("x", content_type="application/javascript")
        response["Cache-Control"] = "no-store"
        return response

    from django.test import RequestFactory

    conf = type("Conf", (), {"urlpatterns": [path("mine.js", mine, name="mine")]})
    request = RequestFactory().get("/mine.js")
    request.urlconf = conf
    response = PublicAssetMiddleware(lambda r: HttpResponse("normal"))(request)
    assert response.content == b"x" and response["Cache-Control"] == "no-store"


def test_a_missing_vendor_file_is_not_given_a_public_cache_policy():
    response = Client().get("/_vendor/nope.js")
    assert response.status_code == 404
    assert "public" not in response.get("Cache-Control", "")
