"""Dev login bypass: DEBUG double-gate, personas, redirect hygiene.

The URL is only registered when DEBUG is on (test settings run with it off),
so these tests exercise the view function directly.
"""
import pytest
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import Http404
from django.test import RequestFactory

from apps.accounts.dev import dev_login
from apps.core import roles
from apps.orgs.models import OrgMembership
from apps.studios.models import StudioMembership

pytestmark = pytest.mark.django_db


@pytest.fixture
def rf_get():
    rf = RequestFactory()

    def _get(query: str = ""):
        request = rf.get(f"/__dev__/login/{query}")
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request.user = AnonymousUser()
        return request

    return _get


class TestGate:
    def test_404_when_debug_off(self, rf_get, settings):
        settings.DEBUG = False
        with pytest.raises(Http404):
            dev_login(rf_get())


class TestLogin:
    @pytest.fixture(autouse=True)
    def _debug(self, settings):
        settings.DEBUG = True

    def test_existing_user_logs_in_and_redirects(self, rf_get, make_user, org):
        user = make_user("who@demo.example", org=org)
        request = rf_get("?user=who@demo.example&next=/orgs/demo/")
        response = dev_login(request)
        assert response.status_code == 302
        assert response.url == "/orgs/demo/"
        assert request.session[SESSION_KEY] == str(user.pk)

    def test_unknown_email_404s(self, rf_get):
        assert dev_login(rf_get("?user=ghost@nowhere")).status_code == 404

    def test_unknown_email_is_escaped(self, rf_get):
        response = dev_login(rf_get("?user=<script>x</script>@nowhere"))
        assert response.status_code == 404
        assert b"<script>" not in response.content

    def test_cross_site_requests_rejected(self, rf_get, make_user, org):
        make_user("who@demo.example", org=org)
        request = rf_get("?user=who@demo.example")
        request.META["HTTP_SEC_FETCH_SITE"] = "cross-site"
        response = dev_login(request)
        assert response.status_code == 403
        assert SESSION_KEY not in request.session

    def test_offsite_next_is_dropped(self, rf_get, make_user, org):
        make_user("who@demo.example", org=org)
        response = dev_login(rf_get("?user=who@demo.example&next=https://evil.example/"))
        assert response.url == "/"

    def test_superuser_persona(self, rf_get):
        response = dev_login(rf_get("?as=superuser"))
        assert response.status_code == 302
        user = get_user_model().objects.get(email="dev-superuser@dev.local")
        assert user.is_superuser

    def test_studio_persona_creates_memberships(self, rf_get, studio):
        request = rf_get(f"?as=developer:{studio.org.slug}/{studio.slug}")
        dev_login(request)
        user = get_user_model().objects.get(
            email=f"dev-developer-{studio.org.slug}-{studio.slug}@dev.local"
        )
        assert OrgMembership.objects.filter(user=user, org=studio.org).exists()
        grant = StudioMembership.objects.get(user=user, studio=studio)
        assert grant.role == roles.DEVELOPER
        assert request.session[SESSION_KEY] == str(user.pk)

    def test_persona_against_missing_org_404s(self, rf_get):
        with pytest.raises(Http404, match="does not exist"):
            dev_login(rf_get("?as=org_admin:nope"))

    def test_unknown_persona_404s(self, rf_get):
        with pytest.raises(Http404, match="unknown persona"):
            dev_login(rf_get("?as=galactic-emperor"))

    def test_picker_page_lists_users(self, rf_get, make_user, org):
        make_user("who@demo.example", org=org)
        response = dev_login(rf_get())
        assert response.status_code == 200
        assert b"who@demo.example" in response.content
        assert b"superuser" in response.content
