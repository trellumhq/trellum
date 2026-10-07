"""The report-storage check.

A publish failure is otherwise discovered at the *end* of a build — the most
expensive moment to learn that a bucket name is wrong. This check moves that
discovery to `doctor`, before anything depends on it.
"""
import pytest

from apps.core import storage
from apps.core.health import run_checks

pytestmark = pytest.mark.django_db


def _storage_check():
    return next(c for c in run_checks() if c["label"] == "report storage")


def _access_check():
    return next(c for c in run_checks() if c["label"] == "report access")


class TestReportAccessCheck:
    """The doctor check that proves report content is not readable without
    authentication — the one that must fail loudly before an edge install goes
    live with an open bucket."""

    def test_proxy_posture_is_a_pass(self, settings):
        settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"
        result = _access_check()
        assert result["ok"] is True
        assert "nothing is exposed" in result["detail"]

    def test_an_exposed_edge_fails_hard(self, settings, monkeypatch):
        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-signed"
        settings.TRELLUM_CDN_BASE_URL = "https://reports.example.com"

        def _open(req, timeout=None):  # anonymous request is NOT refused
            class _R:
                def getcode(self):
                    return 200

            return _R()

        monkeypatch.setattr("urllib.request.urlopen", _open)
        result = _access_check()
        assert result["ok"] is False
        assert "publicly readable" in result["detail"]

    def test_a_protected_edge_passes(self, settings, monkeypatch):
        import urllib.error

        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-external"
        settings.TRELLUM_CDN_BASE_URL = "https://reports.example.com"

        def _open(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 403, "denied", {}, None)

        monkeypatch.setattr("urllib.request.urlopen", _open)
        result = _access_check()
        assert result["ok"] is True
        assert "protected" in result["detail"]


class FakeBackend:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.listed = 0

    def list_prefixes(self):
        self.listed += 1
        if self.error:
            raise self.error
        return ["demo"]


class TestLocalBackend:
    def test_passes_and_says_so(self, settings):
        settings.TRELLUM_STORAGE_BACKEND = "local"
        result = _storage_check()
        assert result["ok"] is True
        assert "local" in result["detail"]

    def test_never_touches_the_network(self, settings, monkeypatch):
        settings.TRELLUM_STORAGE_BACKEND = "local"

        def boom():
            raise AssertionError("the local backend must not reach the store")

        monkeypatch.setattr(storage, "_s3", boom)
        assert _storage_check()["ok"] is True


class TestRemoteBackend:
    def test_a_reachable_bucket_passes(self, settings, monkeypatch):
        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_REPORTS_BUCKET = "reports-prod"
        backend = FakeBackend()
        monkeypatch.setattr(storage, "_s3", lambda: backend)
        result = _storage_check()
        assert result["ok"] is True
        assert "s3://reports-prod" in result["detail"]
        assert backend.listed == 1

    def test_a_missing_bucket_name_fails_before_any_call(self, settings, monkeypatch):
        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_REPORTS_BUCKET = ""

        def boom():
            raise AssertionError("must not reach the store without a bucket name")

        monkeypatch.setattr(storage, "_s3", boom)
        result = _storage_check()
        assert result["ok"] is False
        assert "TRELLUM_REPORTS_BUCKET" in result["detail"]

    def test_an_unreachable_bucket_fails_with_a_usable_reason(self, settings, monkeypatch):
        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_REPORTS_BUCKET = "typo-bucket"
        monkeypatch.setattr(
            storage, "_s3", lambda: FakeBackend(RuntimeError("AccessDenied"))
        )
        result = _storage_check()
        assert result["ok"] is False
        assert "typo-bucket" in result["detail"]
        assert "AccessDenied" in result["detail"]

    def test_the_check_never_raises(self, settings, monkeypatch):
        """run_checks() promises never to raise; a store that cannot even be
        constructed must not be the one thing that takes the health page down."""
        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_REPORTS_BUCKET = "reports-prod"

        def explode():
            raise ImportError("the S3 driver is not installed")

        monkeypatch.setattr(storage, "_s3", explode)
        assert _storage_check()["ok"] is False
