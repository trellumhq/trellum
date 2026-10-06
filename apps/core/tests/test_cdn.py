"""The report read path: postures, the Ed25519 grant, and the exposure guard.

The signer tests verify the *protocol* (the exact bytes the edge worker will
re-derive), not any provider: a grant is an Ed25519 signature over a tiny
``{scope, exp}`` payload, and the crux is that it verifies against the public
key — the same check the Cloudflare Worker performs.
"""
from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from apps.core import cdn
from apps.core.report_access import (
    selected_report_access_block_reason,
    selected_report_access_ready,
)

pytestmark = pytest.mark.django_db

_KEY = Ed25519PrivateKey.generate()
TEST_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@pytest.fixture
def edge_signed(settings):
    """edge-signed posture with the remote store and a signing key present."""
    settings.TRELLUM_STORAGE_BACKEND = "s3"
    settings.TRELLUM_REPORTS_BUCKET = "test-bucket"
    settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-signed"
    settings.TRELLUM_CDN_BASE_URL = "https://reports.example.com"
    settings.TRELLUM_CDN_SIGNING_KEY = TEST_PEM
    settings.TRELLUM_CDN_SIGNING_KEY_FILE = ""
    settings.TRELLUM_CDN_COOKIE_TTL_SECONDS = 600
    cdn._reset_key_cache()
    cdn._reset_probe_cache()
    yield
    cdn._reset_key_cache()
    cdn._reset_probe_cache()


class TestGrantSigning:
    def test_token_shape_is_two_b64url_parts(self, edge_signed):
        token = cdn.sign_grant("/content/demo/casino/")
        assert token.count(".") == 1
        for part in token.split("."):
            assert not set(part) & set("+/=")  # url-safe, unpadded

    def test_payload_carries_scope_and_epoch_expiry(self, edge_signed):
        token = cdn.sign_grant("/content/demo/casino/", ttl=300)
        payload = json.loads(_b64url_decode(token.split(".")[0]))
        assert payload["scope"] == "/content/demo/casino/"
        assert isinstance(payload["exp"], int)

    def test_signature_verifies_against_the_public_key(self, edge_signed):
        """The check the edge worker performs, performed here — no provider,
        no AWS, a real interop test."""
        token = cdn.sign_grant("/content/demo/casino/")
        payload_b64, sig_b64 = token.split(".")
        pub = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(cdn.public_key_b64())
        )
        pub.verify(_b64url_decode(sig_b64), _b64url_decode(payload_b64))  # raises on bad sig

    def test_a_tampered_payload_fails_verification(self, edge_signed):
        token = cdn.sign_grant("/content/demo/casino/")
        _, sig_b64 = token.split(".")
        forged = json.dumps(
            {"scope": "/content/demo/secret/", "exp": 9999999999},
            separators=(",", ":"),
        ).encode()
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(cdn.public_key_b64()))
        with pytest.raises(Exception):
            pub.verify(_b64url_decode(sig_b64), forged)

    def test_key_can_come_from_a_file(self, edge_signed, settings, tmp_path):
        pem_file = tmp_path / "grant.pem"
        pem_file.write_text(TEST_PEM, encoding="ascii")
        settings.TRELLUM_CDN_SIGNING_KEY = ""
        settings.TRELLUM_CDN_SIGNING_KEY_FILE = str(pem_file)
        cdn._reset_key_cache()
        assert cdn.sign_grant("/content/a/b/")


class TestPostures:
    @pytest.fixture(autouse=True)
    def _remote(self, settings, tmp_path):
        settings.DATA_DIR = tmp_path
        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_REPORTS_BUCKET = "test-bucket"
        settings.TRELLUM_CDN_SIGNING_KEY = TEST_PEM
        settings.TRELLUM_CDN_BASE_URL = "https://reports.example.com"
        cdn._reset_key_cache()

    def test_default_is_proxy(self, settings):
        settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"
        assert cdn.serves_from_edge() is False
        assert cdn.signs_grants() is False

    def test_unknown_model_reads_as_proxy(self, settings):
        settings.TRELLUM_REPORT_ACCESS_MODEL = "wizardry"
        assert cdn.access_model() == "proxy"
        assert cdn.serves_from_edge() is False

    def test_edge_signed_serves_and_signs(self, settings):
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-signed"
        assert cdn.serves_from_edge() is True
        assert cdn.signs_grants() is True

    def test_edge_signed_without_a_key_does_not_sign(self, settings):
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-signed"
        settings.TRELLUM_CDN_SIGNING_KEY = ""
        settings.TRELLUM_CDN_SIGNING_KEY_FILE = ""
        assert cdn.serves_from_edge() is True
        assert cdn.signs_grants() is False  # falls back to proxy serving

    def test_edge_external_serves_but_never_signs(self, settings):
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-external"
        assert cdn.serves_from_edge() is True
        assert cdn.signs_grants() is False

    def test_local_backend_is_always_proxy(self, settings):
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-signed"
        settings.TRELLUM_STORAGE_BACKEND = "local"
        assert cdn.serves_from_edge() is False

    def test_retired_feature_flag_does_not_disable_edge(self, settings):
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-signed"
        settings.TRELLUM_FEATURES = {"object_storage": False}
        assert cdn.serves_from_edge() is True


class TestPathsAndScope:
    def test_content_path_mirrors_the_bucket_key(self, studio):
        assert cdn.content_path(studio, "sales", "b1", "index.html") == (
            "/content/demo/casino/sales/builds/b1/index.html"
        )

    def test_grant_scope_has_a_trailing_slash(self, studio):
        """casino/ must never unlock casino-secret/... — the slash is the
        thing that prevents it."""
        assert cdn.grant_scope(studio, "sales") == "/content/demo/casino/sales/"

    def test_attach_grant_scopes_the_cookie(self, edge_signed, rf):
        from django.http import HttpResponse

        request = rf.get("/", secure=True)

        class _S:
            class org:
                slug = "demo"
            slug = "casino"

        resp = HttpResponse()
        cdn.attach_grant(resp, request, _S, "sales")
        morsel = resp.cookies[cdn.GRANT_COOKIE]
        assert morsel["path"] == "/content/demo/casino/sales"
        assert morsel["max-age"] == 600
        assert morsel["httponly"]

    def test_attach_grant_is_a_noop_in_external_mode(self, settings, rf):
        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_REPORTS_BUCKET = "test-bucket"
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-external"
        settings.TRELLUM_CDN_SIGNING_KEY = TEST_PEM
        cdn._reset_key_cache()
        from django.http import HttpResponse

        class _S:
            class org:
                slug = "demo"
            slug = "casino"

        resp = HttpResponse()
        cdn.attach_grant(resp, rf.get("/"), _S, "sales")
        assert cdn.GRANT_COOKIE not in resp.cookies


class TestSelectedAccessReadiness:
    def test_operator_must_explicitly_enable_it(self, settings):
        settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
        assert not selected_report_access_ready()
        assert "TRELLUM_REPORT_SCOPED_ACCESS_READY" in selected_report_access_block_reason()

        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        assert selected_report_access_ready()

    def test_external_identity_mode_is_always_incompatible(self, settings):
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-external"
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        assert not selected_report_access_ready()
        assert "edge-external" in selected_report_access_block_reason()

    def test_doctor_probe_rejects_existing_grants_when_readiness_is_removed(
        self, settings, member, report_row, make_group, attach_group
    ):
        from apps.core import roles
        from apps.orgs.models import PermissionGroupGrant
        from apps.reports.models import ReportPermissionGrant

        settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        group = make_group("Selected", grants=[(report_row.studio, roles.VIEWER)])
        grant = group.grants.get()
        grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
        grant.save(update_fields=["viewer_scope"])
        attach_group(member, group)
        ReportPermissionGrant.objects.create(grant=grant, report=report_row)

        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
        with pytest.raises(RuntimeError, match="selected report access is unsafe"):
            cdn.probe_exposure()


class TestExposureGuard:
    """The one check that means real data is readable without auth. An
    anonymous request to the content origin MUST be refused."""

    @pytest.fixture(autouse=True)
    def _edge(self, settings):
        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_REPORTS_BUCKET = "test-bucket"
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-signed"
        settings.TRELLUM_CDN_BASE_URL = "https://reports.example.com"
        settings.TRELLUM_CDN_SIGNING_KEY = TEST_PEM
        cdn._reset_key_cache()
        cdn._reset_probe_cache()

    def _fake_urlopen(self, monkeypatch, status):
        import urllib.error

        def _open(req, timeout=None):
            if 400 <= status < 600:
                raise urllib.error.HTTPError(req.full_url, status, "denied", {}, None)

            class _Resp:
                def getcode(self):
                    return status

            return _Resp()

        monkeypatch.setattr("urllib.request.urlopen", _open)

    def test_anonymous_403_is_protected(self, monkeypatch):
        self._fake_urlopen(monkeypatch, 403)
        assert "protected" in cdn.probe_exposure()

    def test_anonymous_200_is_a_leak(self, monkeypatch):
        self._fake_urlopen(monkeypatch, 200)
        with pytest.raises(cdn.ExposureError):
            cdn.probe_exposure()

    def test_external_mode_accepts_an_auth_redirect(self, settings, monkeypatch):
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-external"
        self._fake_urlopen(monkeypatch, 302)
        assert "protected" in cdn.probe_exposure()

    def test_signed_mode_rejects_a_redirect(self, settings, monkeypatch):
        """A signed-mode edge should 403 a grantless request, not redirect —
        a 3xx is not proof of protection here."""
        self._fake_urlopen(monkeypatch, 302)
        with pytest.raises(cdn.ExposureError):
            cdn.probe_exposure()

    def test_missing_base_url_is_a_hard_error(self, settings):
        settings.TRELLUM_CDN_BASE_URL = ""
        with pytest.raises(RuntimeError, match="TRELLUM_CDN_BASE_URL"):
            cdn.probe_exposure()

    def test_proxy_posture_exposes_nothing(self, settings):
        settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"
        assert "nothing is exposed" in cdn.probe_exposure()

    def test_exposure_ok_caches_and_reflects_a_leak(self, monkeypatch):
        self._fake_urlopen(monkeypatch, 200)
        ok, why = cdn.exposure_ok()
        assert ok is False and "publicly readable" in why

    def test_exposure_ok_is_true_when_protected(self, monkeypatch):
        self._fake_urlopen(monkeypatch, 403)
        ok, _ = cdn.exposure_ok()
        assert ok is True
