"""Org-level data sources: shadowing, scope moves, permissions, file-type
restriction — and the SSO enforcement option (grouped here as this batch)."""
import yaml as yaml_lib
import pytest

from apps.core import roles
from apps.datasources.models import DataSource, sources_for_studio

pytestmark = pytest.mark.django_db


@pytest.fixture
def org_source(org):
    return DataSource.objects.create(
        org=org, name="warehouse", type="postgres",
        config={"host": "shared.internal"}, credentials={"user": "u", "password": "p"},
    )


class TestResolution:
    def test_org_source_visible_to_every_studio(self, org, org_source, studio, studio2):
        assert [d.pk for d in sources_for_studio(studio)] == [org_source.pk]
        assert [d.pk for d in sources_for_studio(studio2)] == [org_source.pk]

    def test_studio_source_shadows_org_source(self, org, org_source, studio):
        mine = DataSource.objects.create(
            studio=studio, name="warehouse", type="postgres",
            config={"host": "studio-own.internal"},
        )
        resolved = {d.name: d for d in sources_for_studio(studio)}
        assert resolved["warehouse"].pk == mine.pk  # studio wins

    def test_materialize_includes_org_sources(self, studio_tree, org_source):
        from apps.datasources.materialize import materialize

        env = materialize(studio_tree)
        doc = yaml_lib.safe_load(
            (studio_tree.datasources_dir / "config.yaml").read_text(encoding="utf-8")
        )
        assert "warehouse" in doc["sources"]
        assert env[f"{org_source.env_prefix}_HOST"] == "shared.internal"
        assert env[f"{org_source.env_prefix}_PASS"] == "p"

    def test_scope_constraint(self, org, studio):
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            DataSource.objects.create(org=org, studio=studio, name="both", type="postgres")


class TestScopeManagement:
    def test_studio_page_always_creates_studio_scope(
        self, login, org_admin, org, studio_tree
    ):
        # Org-wide sources are created in Organization settings; the studio
        # page pins scope to the studio even for org admins asking for "org".
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        login(org_admin).post(
            url,
            {"name": "shared", "type": "postgres", "scope": "org",
             "host": "h", "user": "u", "password": "p"},
        )
        ds = DataSource.objects.get(name="shared")
        assert ds.scope == "studio" and ds.studio == studio_tree

    def test_org_page_creates_org_source(self, login, org_admin, org):
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/datasources",
            {"name": "shared", "type": "postgres", "host": "h",
             "user": "u", "password": "p"},
        )
        ds = DataSource.objects.get(name="shared")
        assert ds.scope == "org" and ds.org == org and ds.studio is None

    def test_editing_a_shared_source_redirects_to_org_settings(
        self, login, org_admin, org, studio_tree, org_source
    ):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        resp = login(org_admin).get(f"{url}?edit={org_source.pk}")
        assert resp.status_code == 302
        assert resp["Location"] == (
            f"/orgs/{org.slug}/settings/datasources?edit={org_source.pk}"
        )

    def test_studio_admin_cannot_create_org_source(
        self, login, make_user, org, studio_tree, grant_studio
    ):
        admin = make_user("sadmin@demo.example", org=org)
        grant_studio(admin, studio_tree, roles.ADMIN)
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        login(admin).post(
            url, {"name": "sneaky", "type": "postgres", "scope": "org", "host": "h"}
        )
        ds = DataSource.objects.get(name="sneaky")
        assert ds.scope == "studio"  # silently clamped to studio

    def test_scope_cannot_be_moved_from_the_studio_page(
        self, login, org_admin, org, studio_tree
    ):
        # There is no move-between-scopes flow anymore: recreate the source at
        # the other scope instead. A studio source POSTed with scope=org stays
        # a studio source; a shared source POSTed here bounces to org settings.
        ds = DataSource.objects.create(
            studio=studio_tree, name="wh", type="postgres", config={"host": "h"},
            credentials={"user": "u", "password": "p"},
        )
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        login(org_admin).post(
            url,
            {"id": ds.pk, "name": "wh", "type": "postgres", "scope": "org",
             "host": "h", "user": "u", "password": ""},
        )
        ds.refresh_from_db()
        assert ds.scope == "studio" and ds.studio == studio_tree

    def test_shared_source_post_from_studio_page_redirects(
        self, login, org_admin, org, studio_tree, org_source
    ):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        resp = login(org_admin).post(
            url,
            {"id": org_source.pk, "name": "warehouse", "type": "postgres",
             "scope": "studio", "host": "shared.internal", "user": "u", "password": ""},
        )
        assert resp.status_code == 302
        assert resp["Location"].startswith(f"/orgs/{org.slug}/settings/datasources")
        org_source.refresh_from_db()
        assert org_source.scope == "org"  # untouched

    def test_file_types_allowed_at_org_scope(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/settings/datasources",
            {"name": "img", "type": "image", "upload": "on"},
        )
        assert resp.status_code == 302
        ds = DataSource.objects.get(name="img")
        assert ds.scope == "org" and ds.is_uploaded
        # No path typed in: the first upload sets one under the org's own dir.
        assert "path" not in ds.config

    def test_org_file_keeps_a_path_the_admin_chose(
        self, login, org_admin, org, studio_tree
    ):
        """The path is the admin's to set at either scope; only what it is
        RELATIVE TO differs (the org's own directory, not a studio project)."""
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        login(org_admin).post(
            url,
            {"name": "shared", "type": "file", "scope": "org",
             "path": "files/market-extract.csv", "upload": "on"},
        )
        ds = DataSource.objects.get(name="shared")
        assert ds.config == {"path": "files/market-extract.csv", "upload": True}

    def test_studio_admin_cannot_edit_org_source(
        self, login, make_user, org, studio_tree, grant_studio, org_source
    ):
        admin = make_user("sadmin@demo.example", org=org)
        grant_studio(admin, studio_tree, roles.ADMIN)
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        assert login(admin).get(f"{url}?edit={org_source.pk}").status_code == 404

    def test_org_settings_page_manages_org_sources(self, login, org_admin, org, org_source):
        url = f"/orgs/{org.slug}/settings/datasources"
        html = login(org_admin).get(url).content.decode()
        assert "warehouse" in html
        resp = login(org_admin).post(
            f"{url.rstrip('/')}" , {"action": "delete", "id": org_source.pk}
        )
        assert resp.status_code == 302
        assert not DataSource.objects.filter(pk=org_source.pk).exists()

    def test_org_test_endpoint(self, login, org_admin, org, org_source, monkeypatch):
        class FakeDriver:
            def connect(self, conn_info):
                class C:
                    def close(self):
                        pass

                return C()

        import trellum.data.drivers as drivers_mod

        monkeypatch.setattr(drivers_mod, "get_driver", lambda t: FakeDriver())
        body = login(org_admin).post(
            f"/orgs/{org.slug}/settings/datasources/warehouse/test"
        ).json()
        assert body["ok"] is True

    def test_studio_test_reaches_org_source(self, login, org_admin, org, studio_tree, org_source, monkeypatch):
        class FakeDriver:
            def connect(self, conn_info):
                class C:
                    def close(self):
                        pass

                return C()

        import trellum.data.drivers as drivers_mod

        monkeypatch.setattr(drivers_mod, "get_driver", lambda t: FakeDriver())
        body = login(org_admin).post(
            f"/s/{org.slug}/{studio_tree.slug}/api/datasources/warehouse/test"
        ).json()
        assert body["ok"] is True


class TestEnforceSSO:
    @pytest.fixture
    def enforcing_cfg(self, org):
        from apps.orgs.models import OrgSSOConfig

        return OrgSSOConfig.objects.create(
            org=org, enabled=True, enforce_sso=True,
            issuer_url="https://idp.example/realms/demo", client_id="cid", client_secret="s",
            email_domains=["demo.example"],
        )

    def test_member_password_login_redirected_to_sso(self, client, member, enforcing_cfg):
        resp = client.post(
            "/login", {"email": member.email, "password": "pw-Str0ng-pw"}
        )
        assert resp.status_code == 302
        assert "oidc-demo/login" in resp.url
        # And no session was created.
        assert client.get("/api/me").status_code == 401

    def test_enforcement_ignores_email_domain(self, client, make_user, org, enforcing_cfg):
        outsider_domain = make_user("member@partner.io", org=org)
        resp = client.post(
            "/login", {"email": outsider_domain.email, "password": "pw-Str0ng-pw"}
        )
        assert resp.status_code == 302 and "oidc-demo/login" in resp.url

    def test_operator_exempt(self, client, superuser, org, enforcing_cfg):
        from apps.orgs.models import OrgMembership

        OrgMembership.objects.create(user=superuser, org=org, role="admin")
        resp = client.post(
            "/login", {"email": superuser.email, "password": "pw-Str0ng-pw"}
        )
        assert resp.status_code == 302 and "oidc-" not in resp.url
        assert client.get("/api/me").status_code == 200

    def test_non_members_unaffected(self, client, make_user, enforcing_cfg):
        stranger = make_user("solo@elsewhere.io")
        resp = client.post(
            "/login", {"email": stranger.email, "password": "pw-Str0ng-pw"}
        )
        assert resp.status_code == 302 and "oidc-" not in resp.url

    def test_disabled_sso_means_no_enforcement(self, client, member, enforcing_cfg):
        enforcing_cfg.enabled = False
        enforcing_cfg.save()
        resp = client.post(
            "/login", {"email": member.email, "password": "pw-Str0ng-pw"}
        )
        assert resp.status_code == 302 and "oidc-" not in resp.url
