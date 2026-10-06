"""Health, version and identity endpoints.

All three are unauthenticated by design — the compose healthcheck and
already-built report pages depend on their exact paths — so what they
disclose is part of the contract, not an accident. The payload shape is
pinned rather than spot-checked for that reason.
"""
import pytest


@pytest.mark.django_db
def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.django_db
class TestAuthMe:
    """The endpoint data_loader.js's admin-only filter-health badge check
    reads. Bug: it 404d on every portal-served report with a live
    FilterBar, since the route never existed — see CHANGELOG [0.2.0]."""

    def test_never_errors_anonymous(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json() == {"is_admin": False}

    def test_never_errors_with_no_path(self, client, org_admin):
        """Logged in, but the caller gave no ?path= to resolve a studio."""
        client.force_login(org_admin)
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json() == {"is_admin": False}

    def test_admin_on_their_own_studio(self, client, org_admin, org, studio):
        client.force_login(org_admin)
        path = f"/s/{org.slug}/{studio.slug}/r/some-report/"
        resp = client.get("/api/auth/me", {"path": path})
        assert resp.status_code == 200
        assert resp.json() == {"is_admin": True}

    def test_viewer_is_not_admin(self, client, make_user, org, studio, grant_studio):
        from apps.core import roles

        user = make_user("viewer@demo.example", org=org)
        grant_studio(user, studio, roles.VIEWER)
        client.force_login(user)
        path = f"/s/{org.slug}/{studio.slug}/r/some-report/"
        assert client.get("/api/auth/me", {"path": path}).json() == {"is_admin": False}

    def test_studio_admin_is_admin(self, client, make_user, org, studio, grant_studio):
        from apps.core import roles

        user = make_user("studio-admin@demo.example", org=org)
        grant_studio(user, studio, roles.ADMIN)
        client.force_login(user)
        path = f"/s/{org.slug}/{studio.slug}/r/some-report/"
        assert client.get("/api/auth/me", {"path": path}).json() == {"is_admin": True}

    def test_unresolvable_org_never_errors(self, client, org_admin):
        client.force_login(org_admin)
        resp = client.get("/api/auth/me", {"path": "/s/nope/nope/r/x/"})
        assert resp.status_code == 200
        assert resp.json() == {"is_admin": False}

    def test_studio_in_a_different_org_never_errors(
        self, client, org_admin, other_org, other_studio
    ):
        """Admin of `org`, asking about a studio that belongs to `other_org`
        — must not leak an org-admin grant across an org boundary."""
        client.force_login(org_admin)
        path = f"/s/{other_org.slug}/{other_studio.slug}/r/x/"
        resp = client.get("/api/auth/me", {"path": path})
        assert resp.status_code == 200
        assert resp.json() == {"is_admin": False}


@pytest.mark.django_db
class TestVersion:
    def test_payload_shape(self, client):
        body = client.get("/api/version").json()
        assert set(body) == {
            "version",
            "sha",
            "build_time",
            "branch",
            "source_url",
            "framework",
            "schema",
        }
        assert set(body["framework"]) == {"tag", "meta_schema"}

    def test_reports_the_release_version(self, client):
        import trellum_portal

        assert client.get("/api/version").json()["version"] == trellum_portal.__version__

    def test_reports_exact_source_with_safe_fork_override(self, client, settings):
        settings.TRELLUM_SOURCE_URL = "https://code.example/trellum/tree/exact"
        assert client.get("/api/version").json()["source_url"] == settings.TRELLUM_SOURCE_URL

    def test_rejects_unsafe_source_override(self, client, settings):
        settings.TRELLUM_SOURCE_URL = "javascript:alert(1)"
        with pytest.raises(ValueError, match=r"HTTP\(S\)"):
            client.get("/api/version")

    def test_migration_names_stay_off_the_public_endpoint(self, client):
        """They describe the shape of the schema, and this endpoint is open to
        anyone who can reach the host."""
        assert "unapplied_migrations" not in client.get("/api/version").json()

    def test_schema_is_ok_on_a_migrated_database(self, client):
        assert client.get("/api/version").json()["schema"] == "ok"

    def test_schema_reports_pending_when_the_database_is_behind(self, client, monkeypatch):
        """The state upgrade.sh asserts on: new image, migration never applied."""
        monkeypatch.setattr(
            "apps.core.version.unapplied_migrations", lambda: ["core.0099_future"]
        )
        assert client.get("/api/version").json()["schema"] == "pending"

    def test_schema_reports_error_when_it_cannot_be_read(self, client, monkeypatch):
        """An unreachable database is an answer, not a traceback on a status
        endpoint that a monitor is polling."""
        def boom():
            raise RuntimeError("no database")

        monkeypatch.setattr("apps.core.version.unapplied_migrations", boom)
        assert client.get("/api/version").json()["schema"] == "error"


@pytest.mark.django_db
class TestMigrationsHealthCheck:
    """A web container whose `migrate` failed used to report fully healthy."""

    def _check(self):
        from apps.core.health import run_checks

        return next(c for c in run_checks() if c["label"] == "migrations")

    def test_passes_on_a_migrated_database(self):
        assert self._check()["ok"] is True

    def test_fails_and_names_the_migrations_when_behind(self, monkeypatch):
        monkeypatch.setattr(
            "apps.core.version.unapplied_migrations",
            lambda: ["core.0099_future", "orgs.0100_future"],
        )
        result = self._check()
        assert result["ok"] is False
        assert "core.0099_future" in result["detail"]
        assert "2 migration(s) not applied" in result["detail"]

    def test_long_lists_are_truncated(self, monkeypatch):
        """A hundred names in a health row is not a diagnostic."""
        monkeypatch.setattr(
            "apps.core.version.unapplied_migrations",
            lambda: [f"core.{i:04d}_x" for i in range(12)],
        )
        detail = self._check()["detail"]
        assert "+7 more" in detail
