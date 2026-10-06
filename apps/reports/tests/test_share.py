"""Public share links (internal planning#6): unauthenticated token-gated report
serving (entry/asset, expiry, revocation, password, export hiding, traversal
guard), the org-level opt-in that gates all of it (disabled by default), plus
the studio-scoped management API and its audit trail."""
import json

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.core import roles
from apps.core.models import AuditLog
from apps.reports.models import OrgSharePolicy, ShareLink
from trellum import __version__

pytestmark = pytest.mark.django_db


@pytest.fixture
def developer(make_user, org, studio_tree, grant_studio):
    user = make_user("dev@demo.example", org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
    return user


@pytest.fixture
def viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("view@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


@pytest.fixture
def prefix(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}"


@pytest.fixture
def built_report(studio_tree, write_report, report_row):
    out = studio_tree.output_dir / "player-overview"
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(
        "<html><head></head><body>hi"
        '<div class="fw-export-wrap">export controls</div>'
        '<button id="assistantAsk">Ask</button>'
        '<button id="assistantPill">Ask about this report</button>'
        '<div id="assistantPanel">assistant</div>'
        "</body></html>",
        encoding="utf-8",
    )
    (out / "data.json").write_text('{"a": 1}', encoding="utf-8")
    (out / "export.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return out


@pytest.fixture
def make_link(report_row):
    def _make(**kwargs):
        link = ShareLink(report=report_row, **kwargs)
        link.save()
        return link

    return _make


@pytest.fixture
def sharing_on(org):
    """Org-level opt-in, turned on. Sharing is disabled by default (no
    ``OrgSharePolicy`` row) -- most of this file's classes pull this in via
    an autouse fixture so their tests exercise link behavior rather than
    re-proving the org gate; ``TestShareDefaultDisabled`` and
    ``TestShareOrgPolicyToggleAffectsLiveLinks`` below deliberately do NOT,
    so the disabled-by-default and toggle-while-live cases stay meaningful."""
    return OrgSharePolicy.objects.create(org=org, share_links_enabled=True)


class TestShareEntry:
    @pytest.fixture(autouse=True)
    def _sharing_on(self, sharing_on):
        return sharing_on

    def test_unknown_token_404(self, client, db):
        assert client.get("/share/does-not-exist/").status_code == 404

    def test_active_link_serves_entry_html(self, client, built_report, make_link):
        link = make_link()
        resp = client.get(f"/share/{link.token}/")
        assert resp.status_code == 200
        assert b"hi" in resp.content

    def test_expired_link_returns_410(self, client, built_report, make_link):
        link = make_link(expires_at=timezone.now() - timezone.timedelta(hours=1))
        resp = client.get(f"/share/{link.token}/")
        assert resp.status_code == 410
        assert b"no longer active" in resp.content

    def test_revoked_link_returns_410(self, client, built_report, make_link):
        link = make_link(revoked_at=timezone.now())
        resp = client.get(f"/share/{link.token}/")
        assert resp.status_code == 410

    def test_view_count_increments_on_entry(self, client, built_report, make_link):
        link = make_link()
        client.get(f"/share/{link.token}/")
        client.get(f"/share/{link.token}/")
        link.refresh_from_db()
        assert link.view_count == 2
        assert link.last_viewed_at is not None

    def test_view_count_does_not_increment_on_assets(self, client, built_report, make_link):
        link = make_link()
        client.get(f"/share/{link.token}/")  # one entry view
        client.get(f"/share/{link.token}/data.json")
        client.get(f"/share/{link.token}/data.json")
        link.refresh_from_db()
        assert link.view_count == 1

    def test_asset_served(self, client, built_report, make_link):
        link = make_link()
        resp = client.get(f"/share/{link.token}/data.json")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("application/json")


class TestShareEntryTheming:
    """A share visitor has no session, so theme resolution
    (apps.core.themes.resolve_studio_theme) never consults a viewer
    override -- only the studio/org chain, same as any anonymous request.
    See apps/core/tests/test_theme_resolution.py for the resolver's own
    tests."""

    @pytest.fixture(autouse=True)
    def _sharing_on(self, sharing_on):
        return sharing_on

    def test_share_page_carries_the_studio_theme(self, client, built_report, make_link, studio_tree):
        studio_tree.theme = "sunset"
        studio_tree.save(update_fields=["theme"])
        link = make_link()
        html = client.get(f"/share/{link.token}/").content.decode()
        assert 'data-theme="sunset"' in html
        assert 'data-studio-theme="sunset"' in html
        assert f"https://github.com/trellumhq/trellum/tree/v{__version__}" in html
        assert ">Trellum runtime source</a>" in html

    def test_share_page_never_carries_a_members_personal_override(
        self, client, built_report, make_link, studio_tree, viewer
    ):
        from apps.studios.models import StudioMembership

        studio_tree.theme = "sunset"
        studio_tree.save(update_fields=["theme"])
        StudioMembership.objects.filter(user=viewer, studio=studio_tree).update(theme="dracula")
        link = make_link()
        html = client.get(f"/share/{link.token}/").content.decode()
        assert 'data-theme="sunset"' in html
        assert 'data-theme="dracula"' not in html


class TestShareCdnModeFailsClosed:
    """The edge grant (apps.core.cdn) is scoped to a whole studio's content
    prefix -- right for an authenticated member, wrong for an anonymous
    share visitor, who would silently gain every other report the studio has
    published. Rather than widen the grant, share routes refuse to serve at
    all while the edge read path is active (edge-signed or edge-external)."""

    @pytest.fixture(autouse=True)
    def _sharing_on(self, sharing_on):
        return sharing_on

    @pytest.fixture
    def cdn_on(self, settings, monkeypatch, report_row):
        from apps.core import cdn, storage
        from apps.core.tests.test_cdn import TEST_PEM
        from apps.core.tests.test_storage import FakeClient, FakeS3

        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_REPORTS_BUCKET = "test-bucket"
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-signed"
        settings.TRELLUM_CDN_BASE_URL = "https://reports.example.com"
        settings.TRELLUM_CDN_SIGNING_KEY = TEST_PEM
        settings.TRELLUM_CDN_SIGNING_KEY_FILE = ""
        settings.TRELLUM_CDN_COOKIE_TTL_SECONDS = 600
        cdn._reset_key_cache()
        fake = FakeS3()
        monkeypatch.setattr(storage, "_s3", lambda: fake)
        monkeypatch.setattr(storage, "_client", lambda: FakeClient(fake))
        monkeypatch.setattr(storage, "_POINTER_TTL_SECONDS", 0.0)
        storage._pointer_memo.clear()
        yield fake
        cdn._reset_key_cache()

    def test_entry_fails_closed_with_503(self, client, cdn_on, make_link):
        link = make_link()
        resp = client.get(f"/share/{link.token}/")
        assert resp.status_code == 503

    def test_asset_fails_closed_with_503(self, client, cdn_on, make_link):
        link = make_link()
        resp = client.get(f"/share/{link.token}/data.json")
        assert resp.status_code == 503

    def test_no_grant_cookie_ever_issued_to_a_share_visitor(self, client, cdn_on, make_link):
        from apps.core import cdn

        link = make_link()
        resp = client.get(f"/share/{link.token}/")
        assert cdn.GRANT_COOKIE not in resp.cookies


class TestSharePassword:
    @pytest.fixture(autouse=True)
    def _sharing_on(self, sharing_on):
        return sharing_on

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        # Wrong guesses now feed the login throttle's shared cache counters;
        # without this, one test's failures leak into the next one's.
        cache.clear()
        yield
        cache.clear()

    def test_locked_link_shows_password_form(self, client, built_report, make_link):
        link = make_link()
        link.set_password("hunter2")
        link.save()
        resp = client.get(f"/share/{link.token}/")
        assert resp.status_code == 200
        assert b"Password required" in resp.content
        assert b"export controls" not in resp.content

    def test_wrong_password_reshows_form(self, client, built_report, make_link):
        link = make_link()
        link.set_password("hunter2")
        link.save()
        resp = client.post(f"/share/{link.token}/", {"password": "nope"})
        assert resp.status_code == 200
        assert b"Incorrect password" in resp.content
        assert b"export controls" not in resp.content

    def test_right_password_serves_and_persists_for_session(self, client, built_report, make_link):
        link = make_link()
        link.set_password("hunter2")
        link.save()
        resp = client.post(f"/share/{link.token}/", {"password": "hunter2"})
        assert resp.status_code == 200
        assert b"hi" in resp.content

        # Persists: a later GET in the same client session serves directly,
        # no password prompt.
        resp2 = client.get(f"/share/{link.token}/")
        assert resp2.status_code == 200
        assert b"hi" in resp2.content

    def test_assets_blocked_until_unlocked(self, client, built_report, make_link):
        link = make_link()
        link.set_password("hunter2")
        link.save()
        resp = client.get(f"/share/{link.token}/data.json")
        assert resp.status_code == 403

    def test_repeated_wrong_passwords_lock_the_link(self, client, built_report, make_link):
        from apps.core.models import InstanceConfig

        cfg = InstanceConfig.load()
        cfg.lockout_account_threshold = 3
        cfg.save()
        link = make_link()
        link.set_password("hunter2")
        link.save()
        for _ in range(3):
            client.post(f"/share/{link.token}/", {"password": "nope"})
        # Locked now: even the right password re-shows the form.
        resp = client.post(f"/share/{link.token}/", {"password": "hunter2"})
        assert resp.status_code == 200
        assert b"Incorrect password" in resp.content
        assert not client.session.get(f"share_unlocked:{link.token}")

    def test_assets_reachable_after_unlock(self, client, built_report, make_link):
        link = make_link()
        link.set_password("hunter2")
        link.save()
        client.post(f"/share/{link.token}/", {"password": "hunter2"})
        resp = client.get(f"/share/{link.token}/data.json")
        assert resp.status_code == 200


class TestShareAssetTraversal:
    @pytest.fixture(autouse=True)
    def _sharing_on(self, sharing_on):
        return sharing_on

    def test_dotdot_encoded_traversal_blocked(self, client, built_report, make_link, studio_tree):
        secret = studio_tree.project_root / "config.yaml"
        secret.write_text("secret: yes", encoding="utf-8")
        link = make_link()
        resp = client.get(f"/share/{link.token}/..%2f..%2fconfig.yaml")
        assert resp.status_code == 404
        assert b"secret" not in resp.content

    def test_double_encoded_dotdot_blocked(self, client, built_report, make_link, studio_tree):
        secret = studio_tree.project_root / "config.yaml"
        secret.write_text("secret: yes", encoding="utf-8")
        link = make_link()
        resp = client.get(f"/share/{link.token}/%2e%2e%2f%2e%2e%2fconfig.yaml")
        assert resp.status_code == 404
        assert b"secret" not in resp.content

    def test_absolute_path_traversal_blocked(self, client, built_report, make_link):
        link = make_link()
        resp = client.get(f"/share/{link.token}/C%3A%5CWindows%5Csystem.ini")
        assert resp.status_code in (400, 404)


class TestExportHiding:
    @pytest.fixture(autouse=True)
    def _sharing_on(self, sharing_on):
        return sharing_on

    def test_export_asset_blocked_when_disallowed(self, client, built_report, make_link):
        link = make_link(allow_export=False)
        resp = client.get(f"/share/{link.token}/export.csv")
        assert resp.status_code == 403

    def test_export_asset_served_when_allowed(self, client, built_report, make_link):
        link = make_link(allow_export=True)
        resp = client.get(f"/share/{link.token}/export.csv")
        assert resp.status_code == 200

    def test_data_json_stays_reachable_when_export_disallowed(self, client, built_report, make_link):
        link = make_link(allow_export=False)
        resp = client.get(f"/share/{link.token}/data.json")
        assert resp.status_code == 200

    def test_entry_html_hides_export_ui_when_disallowed(self, client, built_report, make_link):
        link = make_link(allow_export=False)
        resp = client.get(f"/share/{link.token}/")
        assert b".fw-export-wrap,#fwOptionsWrap{display:none!important}" in resp.content

    def test_entry_html_does_not_hide_export_ui_when_allowed(self, client, built_report, make_link):
        link = make_link(allow_export=True)
        resp = client.get(f"/share/{link.token}/")
        assert b".fw-export-wrap,#fwOptionsWrap{display:none!important}" not in resp.content

    def test_options_menu_id_is_in_the_no_export_hide_css(self, client, built_report, make_link):
        # The Options menu (static/report_menu.js) never has anything to
        # show a no-export share visitor -- delivery/share/activity items
        # never register on a share route at all, so the only thing that
        # could ever populate it here is Export, which is exactly what this
        # CSS suppresses. #fwOptionsWrap belt-and-suspenders alongside it.
        link = make_link(allow_export=False)
        resp = client.get(f"/share/{link.token}/")
        assert b"#fwOptionsWrap" in resp.content

    def test_portal_chrome_always_hidden(self, client, built_report, make_link):
        # The assistant's openers and panel + the delivery button make no
        # sense to an anonymous visitor regardless of allow_export.
        link = make_link(allow_export=True)
        resp = client.get(f"/share/{link.token}/")
        assert (
            b"#assistantAsk,#assistantPill,#assistantPanel,#fwDeliveryBtn"
            b"{display:none!important}"
        ) in resp.content

    def test_authenticated_share_management_widget_not_injected_for_anonymous_link(
        self, client, built_report, make_link
    ):
        link = make_link(allow_export=True)
        resp = client.get(f"/share/{link.token}/")
        assert b"/api/reports/share-widget.js" not in resp.content


class TestShareDisablesLiveQuery:
    """Live-query filter redesign: an anonymous share view stamps
    ``window._fwShareLink=true`` so the client-render half of the live
    binder (trellum/static/js/runtime/live_query.js) never attempts to arm
    -- the endpoint (apps.reports.livequery) already refuses an anonymous
    viewer outright (require_studio_role(VIEWER) dies on a sessionless
    POST), so this is purely about the FilterBar rendering its honest
    disabled note instead of trying and failing."""

    @pytest.fixture(autouse=True)
    def _sharing_on(self, sharing_on):
        return sharing_on

    def test_entry_html_stamps_share_flag(self, client, built_report, make_link):
        link = make_link(allow_export=True)
        resp = client.get(f"/share/{link.token}/")
        assert b"window._fwShareLink=true;" in resp.content

    def test_asset_html_stamps_share_flag(self, client, built_report, make_link):
        link = make_link(allow_export=True)
        resp = client.get(f"/share/{link.token}/index.html")
        assert b"window._fwShareLink=true;" in resp.content

    def test_flag_lands_before_the_runtime_script(self, client, built_report, make_link):
        # The flag has to be readable by window._fwLiveQuery's IIFE the
        # moment it runs -- it's spliced into <head>, which always parses
        # (and executes, absent defer/async) before anything in <body>.
        link = make_link(allow_export=True)
        resp = client.get(f"/share/{link.token}/")
        html = resp.content.decode()
        assert html.index("window._fwShareLink=true;") < html.index("<body")

    def test_authenticated_view_does_not_stamp_share_flag(
        self, client, built_report, login, developer, prefix,
    ):
        # The same report, served through the ordinary authenticated route,
        # must not carry the flag -- only an anonymous share view does.
        resp = login(developer).get(f"{prefix}/r/player-overview/index.html")
        assert resp.status_code == 200
        assert b"window._fwShareLink" not in resp.content


class TestShareManagementApi:
    @pytest.fixture(autouse=True)
    def _sharing_on(self, sharing_on):
        return sharing_on

    def test_viewer_forbidden(self, login, viewer, prefix, report_row):
        resp = login(viewer).get(f"{prefix}/api/reports/player-overview/share_links")
        assert resp.status_code == 403

    def test_developer_can_create_and_list(self, login, developer, prefix, report_row):
        c = login(developer)
        resp = c.post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({"allow_export": True}),
            content_type="application/json",
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["ok"] is True
        link = body["share_link"]
        # Built from the request host (django.test.Client's "testserver"),
        # not the operator-configured PORTAL_BASE_URL -- an instance where
        # that setting doesn't match the real host must not hand out a
        # copy-URL that points nowhere.
        token = ShareLink.objects.get(pk=link["id"]).token
        assert link["url"].startswith("http://testserver/share/")
        assert link["url"].endswith(f"/{token}/")
        assert link["allow_export"] is True
        assert link["has_password"] is False
        assert link["view_count"] == 0

        listed = c.get(f"{prefix}/api/reports/player-overview/share_links").json()
        assert len(listed["share_links"]) == 1
        assert listed["share_links"][0]["id"] == link["id"]
        assert listed["share_links"][0]["blocked_by_policy"] is False
        assert listed["sharing_enabled"] is True
        # `developer` is a studio developer, not an org admin -- only an org
        # admin may flip the policy.
        assert listed["can_manage_policy"] is False
        # Untouched policy -- neither option set -- reads as off/unlimited.
        assert listed["require_password"] is False
        assert listed["max_expiry_days"] is None

    def test_create_with_password_never_returns_plaintext(self, login, developer, prefix, report_row):
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({"password": "correct-horse-battery"}),
            content_type="application/json",
        )
        body = resp.json()["share_link"]
        assert body["has_password"] is True
        assert "password" not in body
        assert "correct-horse-battery" not in json.dumps(body)

    def test_create_with_expiry(self, login, developer, prefix, report_row):
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({"expires_at": "2099-01-01T00:00:00Z"}),
            content_type="application/json",
        )
        assert resp.status_code == 201
        assert resp.json()["share_link"]["expires_at"].startswith("2099-01-01")

    def test_revoke_sets_revoked_and_link_goes_410_immediately(
        self, login, developer, prefix, report_row, built_report, client
    ):
        c = login(developer)
        created = c.post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({}),
            content_type="application/json",
        ).json()["share_link"]
        link_id = created["id"]
        token = ShareLink.objects.get(pk=link_id).token

        # Still live before revoke.
        assert client.get(f"/share/{token}/").status_code == 200

        resp = c.post(f"/api/share_links/{link_id}/revoke")
        assert resp.status_code == 200
        assert resp.json()["share_link"]["revoked_at"] is not None

        assert client.get(f"/share/{token}/").status_code == 410

    def test_viewer_cannot_revoke(self, login, developer, viewer, prefix, report_row):
        created = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({}),
            content_type="application/json",
        ).json()["share_link"]
        resp = login(viewer).post(f"/api/share_links/{created['id']}/revoke")
        assert resp.status_code == 403

    def test_requires_auth(self, client, prefix, report_row):
        assert client.get(f"{prefix}/api/reports/player-overview/share_links").status_code == 401

    def test_audit_rows_written_for_create_and_revoke(self, login, developer, prefix, report_row):
        c = login(developer)
        created = c.post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({}),
            content_type="application/json",
        ).json()["share_link"]
        c.post(f"/api/share_links/{created['id']}/revoke")

        actions = list(AuditLog.objects.order_by("id").values_list("action", "metadata"))
        create_rows = [m for a, m in actions if a == "share_link.create"]
        revoke_rows = [m for a, m in actions if a == "share_link.revoke"]
        assert len(create_rows) == 1
        assert len(revoke_rows) == 1
        # Never the full token -- only its last 6 characters.
        full_token = ShareLink.objects.get(pk=created["id"]).token
        assert create_rows[0]["token_suffix"] == full_token[-6:]
        assert revoke_rows[0]["token_suffix"] == full_token[-6:]
        for meta in create_rows + revoke_rows:
            assert full_token not in json.dumps(meta)


class TestShareDefaultDisabled:
    """No ``OrgSharePolicy`` row at all -- the opt-in default. Deliberately
    does NOT pull in the ``sharing_on`` fixture other classes use, so these
    stay a true test of "what a fresh org looks like"."""

    def test_entry_returns_410(self, client, built_report, make_link):
        link = make_link()
        resp = client.get(f"/share/{link.token}/")
        assert resp.status_code == 410
        assert b"no longer active" in resp.content

    def test_asset_returns_410(self, client, built_report, make_link):
        link = make_link()
        resp = client.get(f"/share/{link.token}/data.json")
        assert resp.status_code == 410

    def test_disabled_org_reads_identically_to_revoked(self, client, built_report, make_link):
        """An anonymous visitor must not be able to tell "this org turned
        sharing off" apart from "this specific link was revoked" -- both are
        the same page, same status."""
        revoked = make_link(revoked_at=timezone.now())
        org_disabled = make_link()
        resp_revoked = client.get(f"/share/{revoked.token}/")
        resp_disabled = client.get(f"/share/{org_disabled.token}/")
        assert resp_revoked.status_code == resp_disabled.status_code == 410
        assert resp_revoked.content == resp_disabled.content

    def test_create_is_refused(self, login, developer, prefix, report_row):
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 403
        assert resp.json() == {"error": "sharing_disabled"}
        assert not ShareLink.objects.exists()

    def test_list_reports_sharing_disabled(self, login, developer, org_admin, prefix, report_row):
        body = login(developer).get(f"{prefix}/api/reports/player-overview/share_links").json()
        assert body["sharing_enabled"] is False
        assert body["can_manage_policy"] is False

        admin_body = login(org_admin).get(f"{prefix}/api/reports/player-overview/share_links").json()
        assert admin_body["sharing_enabled"] is False
        assert admin_body["can_manage_policy"] is True


class TestShareOrgPolicyToggleAffectsLiveLinks:
    def test_disable_takes_existing_links_down_immediately(self, client, built_report, make_link, sharing_on):
        link = make_link()
        assert client.get(f"/share/{link.token}/").status_code == 200

        sharing_on.share_links_enabled = False
        sharing_on.save(update_fields=["share_links_enabled"])

        assert client.get(f"/share/{link.token}/").status_code == 410

    def test_re_enable_serves_the_same_link_again(self, client, built_report, make_link, sharing_on):
        link = make_link()
        sharing_on.share_links_enabled = False
        sharing_on.save(update_fields=["share_links_enabled"])
        assert client.get(f"/share/{link.token}/").status_code == 410

        sharing_on.share_links_enabled = True
        sharing_on.save(update_fields=["share_links_enabled"])
        resp = client.get(f"/share/{link.token}/")
        assert resp.status_code == 200
        assert b"hi" in resp.content


class TestShareOrgPolicyEndpoint:
    @pytest.fixture
    def studio_admin(self, make_user, org, studio_tree, grant_studio):
        """Studio-level admin -- NOT an org admin. The policy toggle is org
        scoped, so this role must not be able to flip it."""
        user = make_user("studio-admin@demo.example", org=org)
        grant_studio(user, studio_tree, roles.ADMIN)
        return user

    def test_org_admin_can_enable(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"enabled": True}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "ok": True,
            "share_links_enabled": True,
            "require_password": False,
            "max_expiry_days": None,
            "embed_links_enabled": False,
        }
        policy = OrgSharePolicy.objects.get(org=org)
        assert policy.share_links_enabled is True
        assert policy.updated_by == org_admin

    def test_org_admin_can_disable(self, login, org_admin, org, sharing_on):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"enabled": False}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        sharing_on.refresh_from_db()
        assert sharing_on.share_links_enabled is False
        assert sharing_on.updated_by == org_admin

    def test_toggle_is_audited(self, login, org_admin, org):
        login(org_admin).post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"enabled": True}),
            content_type="application/json",
        )
        row = AuditLog.objects.filter(action="share_policy.enable").first()
        assert row is not None
        assert row.actor == org_admin
        assert row.org == org

        login(org_admin).post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"enabled": False}),
            content_type="application/json",
        )
        assert AuditLog.objects.filter(action="share_policy.disable").exists()

    def test_studio_admin_who_is_not_org_admin_forbidden(self, login, studio_admin, org):
        resp = login(studio_admin).post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"enabled": True}),
            content_type="application/json",
        )
        assert resp.status_code == 403
        assert not OrgSharePolicy.objects.filter(org=org).exists()

    def test_developer_who_is_not_org_admin_forbidden(self, login, developer, org):
        resp = login(developer).post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"enabled": True}),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_anonymous_unauthorized(self, client, org):
        resp = client.post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"enabled": True}),
            content_type="application/json",
        )
        assert resp.status_code in (401, 403)


class TestShareRequirePasswordPolicy:
    """``OrgSharePolicy.require_password``: on, a password is mandatory on
    every new link, and any existing link without one stops serving -- the
    "policy means now" semantics also used for the kill switch and the
    lifetime cap."""

    @pytest.fixture(autouse=True)
    def _sharing_on(self, sharing_on):
        sharing_on.require_password = True
        sharing_on.save(update_fields=["require_password"])
        return sharing_on

    def test_create_without_password_is_refused(self, login, developer, prefix, report_row):
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "password_required"
        assert "message" in resp.json()
        assert not ShareLink.objects.exists()

    def test_create_with_password_succeeds(self, login, developer, prefix, report_row):
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({"password": "correct-horse-battery"}),
            content_type="application/json",
        )
        assert resp.status_code == 201
        assert resp.json()["share_link"]["has_password"] is True

    def test_existing_passwordless_link_410_while_required(self, client, built_report, make_link):
        link = make_link()  # no password -- predates the policy requiring one
        resp = client.get(f"/share/{link.token}/")
        assert resp.status_code == 410

    def test_existing_passwordless_asset_410_while_required(self, client, built_report, make_link):
        link = make_link()
        resp = client.get(f"/share/{link.token}/data.json")
        assert resp.status_code == 410

    def test_link_serves_again_once_requirement_lifted(self, client, built_report, make_link, sharing_on):
        link = make_link()
        assert client.get(f"/share/{link.token}/").status_code == 410

        sharing_on.require_password = False
        sharing_on.save(update_fields=["require_password"])

        resp = client.get(f"/share/{link.token}/")
        assert resp.status_code == 200

    def test_passworded_link_unaffected(self, client, built_report, make_link):
        link = make_link()
        link.set_password("hunter2")
        link.save()
        # Still gated by its own password (200 = the password form, not the
        # report) -- crucially NOT the 410 "no longer active" page.
        resp = client.get(f"/share/{link.token}/")
        assert resp.status_code == 200
        assert b"Password required" in resp.content

    def test_list_marks_passwordless_link_blocked_by_policy(
        self, login, developer, prefix, built_report, report_row
    ):
        link = ShareLink(report=report_row)
        link.save()
        body = login(developer).get(f"{prefix}/api/reports/player-overview/share_links").json()
        assert body["require_password"] is True
        row = next(r for r in body["share_links"] if r["id"] == link.pk)
        assert row["blocked_by_policy"] is True


class TestSharePasswordStrength:
    """Length minimum on whatever password a create call supplies --
    independent of ``OrgSharePolicy.require_password``, which only decides
    whether a password is *mandatory*, not how strong one has to be."""

    @pytest.fixture(autouse=True)
    def _sharing_on(self, sharing_on):
        return sharing_on

    def test_short_password_refused_with_policy_off(self, login, developer, prefix, report_row):
        eleven_chars = "x" * 11
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({"password": eleven_chars}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "password_too_weak"
        assert resp.json()["message"] == "Use at least 12 characters."
        assert not ShareLink.objects.exists()

    def test_short_password_refused_with_policy_on(self, login, developer, prefix, report_row, sharing_on):
        sharing_on.require_password = True
        sharing_on.save(update_fields=["require_password"])
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({"password": "eleven-char"}),
            content_type="application/json",
        )
        assert len("eleven-char") == 11
        assert resp.status_code == 400
        assert resp.json()["error"] == "password_too_weak"

    def test_exactly_twelve_characters_is_ok(self, login, developer, prefix, report_row):
        password = "x" * 12
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({"password": password}),
            content_type="application/json",
        )
        assert resp.status_code == 201
        assert resp.json()["share_link"]["has_password"] is True

    def test_longer_password_is_ok(self, login, developer, prefix, report_row):
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({"password": "a much longer passphrase entirely"}),
            content_type="application/json",
        )
        assert resp.status_code == 201

    def test_no_password_at_all_is_unaffected_by_the_minimum(self, login, developer, prefix, report_row):
        """Length only applies when a password IS supplied -- an open link
        (no password at all) is a separate, valid choice."""
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 201
        assert resp.json()["share_link"]["has_password"] is False


class TestShareMaxExpiryPolicy:
    """``OrgSharePolicy.max_expiry_days``: a cap on how far out a link's
    expiry may be set, enforced both going forward (create-time) and
    retroactively (serve-time, measured from ``created_at`` -- see
    ShareLink.effective_expires_at)."""

    @pytest.fixture(autouse=True)
    def _sharing_on(self, sharing_on):
        sharing_on.max_expiry_days = 7
        sharing_on.save(update_fields=["max_expiry_days"])
        return sharing_on

    def test_create_without_expiry_is_refused(self, login, developer, prefix, report_row):
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "expiry_required"
        assert "message" in resp.json()

    def test_create_too_far_out_is_refused(self, login, developer, prefix, report_row):
        far = (timezone.now() + timezone.timedelta(days=30)).isoformat()
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({"expires_at": far}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "expiry_too_far"

    def test_create_within_cap_succeeds(self, login, developer, prefix, report_row):
        soon = (timezone.now() + timezone.timedelta(days=3)).isoformat()
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({"expires_at": soon}),
            content_type="application/json",
        )
        assert resp.status_code == 201

    def test_old_link_with_no_expiry_stops_serving_past_the_cap(self, client, built_report, make_link):
        link = make_link()  # no explicit expiry
        ShareLink.objects.filter(pk=link.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=8)
        )
        resp = client.get(f"/share/{link.token}/")
        assert resp.status_code == 410

    def test_link_still_within_the_cap_serves(self, client, built_report, make_link):
        link = make_link()  # no explicit expiry, created 1 of 7 allowed days ago
        ShareLink.objects.filter(pk=link.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=1)
        )
        resp = client.get(f"/share/{link.token}/")
        assert resp.status_code == 200

    def test_explicit_earlier_expiry_still_wins(self, client, built_report, make_link):
        """Created well within the cap, but its OWN expiry is sooner -- the
        link's own choice, not the cap, is what takes it down."""
        link = make_link(expires_at=timezone.now() - timezone.timedelta(minutes=1))
        resp = client.get(f"/share/{link.token}/")
        assert resp.status_code == 410

    def test_raising_the_cap_resumes_an_aged_out_link(self, client, built_report, make_link, sharing_on):
        link = make_link()
        ShareLink.objects.filter(pk=link.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=8)
        )
        assert client.get(f"/share/{link.token}/").status_code == 410

        sharing_on.max_expiry_days = 30
        sharing_on.save(update_fields=["max_expiry_days"])

        resp = client.get(f"/share/{link.token}/")
        assert resp.status_code == 200

    def test_list_marks_aged_out_link_blocked_by_policy(
        self, login, developer, prefix, built_report, report_row
    ):
        link = ShareLink(report=report_row)
        link.save()
        ShareLink.objects.filter(pk=link.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=8)
        )
        body = login(developer).get(f"{prefix}/api/reports/player-overview/share_links").json()
        assert body["max_expiry_days"] == 7
        row = next(r for r in body["share_links"] if r["id"] == link.pk)
        assert row["blocked_by_policy"] is True


class TestOrgShareSettingsPage:
    """The dedicated "Report sharing" org-settings tab -- replaces the
    inline section that used to sit on the Members page."""

    def test_org_admin_sees_the_page(self, login, org_admin, org):
        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/sharing")
        assert resp.status_code == 200
        assert b"Report sharing" in resp.content
        assert b"reportSharePolicyToggle" in resp.content

    def test_non_admin_forbidden(self, login, developer, org):
        resp = login(developer).get(f"/orgs/{org.slug}/settings/sharing")
        assert resp.status_code in (403, 404)

    def test_anonymous_redirected_to_login(self, client, org):
        resp = client.get(f"/orgs/{org.slug}/settings/sharing")
        assert resp.status_code in (302, 401, 403, 404)

    def test_nav_links_to_the_new_page(self, login, org_admin, org):
        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/members")
        assert f"/orgs/{org.slug}/settings/sharing".encode() in resp.content
        assert b"Report sharing" in resp.content

    def test_members_page_no_longer_has_the_inline_section(self, login, org_admin, org):
        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/members")
        # The old include's toggle id -- its absence proves the inline
        # section (not just its text) is gone, not merely renamed.
        assert b"reportSharePolicyToggle" not in resp.content

    def test_page_reflects_saved_policy(self, login, org_admin, org, sharing_on):
        sharing_on.require_password = True
        sharing_on.embed_links_enabled = True
        sharing_on.max_expiry_days = 14
        sharing_on.save(update_fields=["require_password", "embed_links_enabled", "max_expiry_days"])
        resp = login(org_admin).get(f"/orgs/{org.slug}/settings/sharing")
        html = resp.content.decode()
        assert 'id="reportSharePolicyToggle" checked' in html
        assert 'id="reportSharePolicyRequirePassword" checked' in html
        assert 'id="reportSharePolicyEmbed" checked' in html
        assert 'value="14"' in html


class TestSharePolicyPartialUpdates:
    """``api_share_policy`` accepts partial bodies -- the settings page
    auto-saves each control independently rather than needing one combined
    Save action."""

    def test_require_password_only_update_leaves_enabled_untouched(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"require_password": True}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["require_password"] is True
        assert body["share_links_enabled"] is False  # untouched, still the default
        policy = OrgSharePolicy.objects.get(org=org)
        assert policy.require_password is True
        assert policy.share_links_enabled is False

    def test_embed_links_enabled_only_update(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"embed_links_enabled": True}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["embed_links_enabled"] is True
        assert body["share_links_enabled"] is False
        assert body["require_password"] is False
        policy = OrgSharePolicy.objects.get(org=org)
        assert policy.embed_links_enabled is True
        assert policy.share_links_enabled is False
        row = AuditLog.objects.filter(action="share_policy.update").first()
        assert row is not None
        assert row.metadata == {"embed_links_enabled": True}

    def test_max_expiry_days_only_update(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"max_expiry_days": 14}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["max_expiry_days"] == 14

    def test_clearing_max_expiry_days(self, login, org_admin, org, sharing_on):
        sharing_on.max_expiry_days = 14
        sharing_on.save(update_fields=["max_expiry_days"])
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"max_expiry_days": None}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["max_expiry_days"] is None
        sharing_on.refresh_from_db()
        assert sharing_on.max_expiry_days is None

    def test_invalid_max_expiry_days_rejected(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"max_expiry_days": 0}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert not OrgSharePolicy.objects.filter(org=org).exists()

    def test_non_enabled_update_audited_as_update_with_changed_fields(self, login, org_admin, org):
        login(org_admin).post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"require_password": True, "max_expiry_days": 5}),
            content_type="application/json",
        )
        row = AuditLog.objects.filter(action="share_policy.update").first()
        assert row is not None
        assert row.metadata == {"require_password": True, "max_expiry_days": 5}

    def test_enabled_change_still_audited_as_enable_disable(self, login, org_admin, org):
        login(org_admin).post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"enabled": True, "require_password": True}),
            content_type="application/json",
        )
        row = AuditLog.objects.filter(action="share_policy.enable").first()
        assert row is not None
        assert row.metadata == {"enabled": True, "require_password": True}
        assert not AuditLog.objects.filter(action="share_policy.update").exists()

    def test_no_recognized_fields_400(self, login, org_admin, org):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/api/share_policy",
            data=json.dumps({"bogus": 1}),
            content_type="application/json",
        )
        assert resp.status_code == 400


class TestEmbedLinks:
    """Embed links (internal planning ticket #12): a share link with a different trust model --
    indefinite, password-less, framed only by an origin allow-list enforced
    with ``frame-ancestors``. A second org opt-in
    (``OrgSharePolicy.embed_links_enabled``) sits on top of share links."""

    ORIGIN = "http://localhost:8070"

    @pytest.fixture(autouse=True)
    def _embed_on(self, sharing_on):
        sharing_on.embed_links_enabled = True
        sharing_on.save(update_fields=["embed_links_enabled"])
        return sharing_on

    @pytest.fixture
    def embed_link(self, make_link):
        return make_link(embed=True, embed_origins=[self.ORIGIN])

    @pytest.fixture
    def create(self, login, developer, prefix, report_row):
        """POST the create endpoint with a valid embed body, ``**body``
        overriding/adding keys."""
        c = login(developer)

        def _create(**body):
            return c.post(
                f"{prefix}/api/reports/player-overview/share_links",
                data=json.dumps({"embed": True, "embed_origins": [self.ORIGIN], **body}),
                content_type="application/json",
            )

        return _create

    # ── create ──

    def test_create_refused_when_embed_links_disabled(self, create, sharing_on):
        sharing_on.embed_links_enabled = False
        sharing_on.save(update_fields=["embed_links_enabled"])
        resp = create()
        assert resp.status_code == 403
        assert resp.json()["error"] == "embed_disabled"
        assert "message" in resp.json()
        assert not ShareLink.objects.exists()

    def test_create_ok(self, create):
        resp = create(allow_export=True)
        assert resp.status_code == 201
        link = resp.json()["share_link"]
        assert link["embed"] is True
        assert link["embed_origins"] == [self.ORIGIN]
        assert link["expires_at"] is None
        assert link["has_password"] is False
        assert link["allow_export"] is True
        row = AuditLog.objects.get(action="share_link.create")
        assert row.metadata["embed"] is True

    def test_plain_create_reads_as_non_embed(self, login, developer, prefix, report_row):
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({}),
            content_type="application/json",
        )
        link = resp.json()["share_link"]
        assert link["embed"] is False
        assert link["embed_origins"] == []
        assert AuditLog.objects.get(action="share_link.create").metadata["embed"] is False

    @pytest.mark.parametrize("origins", [None, [], "http://localhost:8070", {"a": 1}])
    def test_missing_or_empty_origins_refused(self, create, origins):
        resp = create(embed_origins=origins)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_embed_origins"
        assert "message" in resp.json()

    def test_origins_key_absent_refused(self, login, developer, prefix, report_row):
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({"embed": True}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_embed_origins"

    @pytest.mark.parametrize(
        "bad",
        ["localhost:8070", "http://a b", "javascript:x", "http://x/path", "http://x;", "ftp://x"],
    )
    def test_malformed_origin_refused(self, create, bad):
        resp = create(embed_origins=[self.ORIGIN, bad])
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_embed_origins"
        assert not ShareLink.objects.exists()

    def test_origins_are_lowercased_and_stripped(self, create):
        resp = create(embed_origins=["  HTTPS://App.Example.com "])
        assert resp.status_code == 201
        assert resp.json()["share_link"]["embed_origins"] == ["https://app.example.com"]

    def test_wildcard_collapses_the_list(self, create):
        resp = create(embed_origins=["http://x", "*", "https://y:8443"])
        assert resp.status_code == 201
        assert resp.json()["share_link"]["embed_origins"] == ["*"]

    def test_origin_count_is_capped(self, create):
        many = [f"https://h{i}.example.com" for i in range(21)]
        resp = create(embed_origins=many)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_embed_origins"
        assert create(embed_origins=many[:20]).status_code == 201

    @pytest.mark.parametrize(
        "extra",
        [{"password": "correct-horse-battery"}, {"expires_at": "2099-01-01T00:00:00Z"}],
    )
    def test_password_or_expiry_with_embed_refused(self, create, extra):
        resp = create(**extra)
        assert resp.status_code == 400
        assert resp.json()["error"] == "embed_no_password_or_expiry"
        assert "message" in resp.json()

    def test_create_skips_require_password_and_max_expiry_policy(self, create, sharing_on):
        sharing_on.require_password = True
        sharing_on.max_expiry_days = 7
        sharing_on.save(update_fields=["require_password", "max_expiry_days"])
        assert create().status_code == 201

    # ── serving: framing headers ──

    @pytest.mark.parametrize("path", ["", "index.html", "data.json"])
    def test_embed_responses_allow_framing_by_listed_origins(
        self, client, built_report, embed_link, path
    ):
        resp = client.get(f"/share/{embed_link.token}/{path}")
        assert resp.status_code == 200
        assert "X-Frame-Options" not in resp
        assert resp["Content-Security-Policy"] == f"frame-ancestors 'self' {self.ORIGIN}"
        # The share routes are exempt from the management CSP (internal planning ticket #92).
        assert "Content-Security-Policy-Report-Only" not in resp

    def test_multiple_origins_joined_by_space(self, client, built_report, make_link):
        link = make_link(embed=True, embed_origins=["http://a", "https://b:8443"])
        resp = client.get(f"/share/{link.token}/")
        assert resp["Content-Security-Policy"] == "frame-ancestors 'self' http://a https://b:8443"

    def test_wildcard_link_allows_any_site(self, client, built_report, make_link):
        link = make_link(embed=True, embed_origins=["*"])
        resp = client.get(f"/share/{link.token}/")
        assert "X-Frame-Options" not in resp
        assert resp["Content-Security-Policy"] == "frame-ancestors 'self' *"

    def test_embed_link_with_no_origins_frames_nowhere_but_the_portal(
        self, client, built_report, make_link
    ):
        """Defensive: a row with an empty list (never produced by the API)
        must fail closed, not open -- ``'self'`` is the portal's own pages
        (the share panel's Preview), never a third-party site."""
        link = make_link(embed=True, embed_origins=[])
        resp = client.get(f"/share/{link.token}/")
        assert resp["Content-Security-Policy"] == "frame-ancestors 'self'"

    @pytest.mark.parametrize("path", ["", "data.json"])
    def test_plain_link_still_denies_framing(self, client, built_report, make_link, path):
        link = make_link()
        resp = client.get(f"/share/{link.token}/{path}")
        assert resp.status_code == 200
        assert resp["X-Frame-Options"] == "DENY"
        assert "Content-Security-Policy" not in resp

    # ── serving: policy immunity ──

    def test_embed_link_ignores_max_expiry_cap(self, client, built_report, make_link, sharing_on):
        sharing_on.max_expiry_days = 7
        sharing_on.save(update_fields=["max_expiry_days"])
        embed = make_link(embed=True, embed_origins=[self.ORIGIN])
        plain = make_link()
        eight_days_ago = timezone.now() - timezone.timedelta(days=8)
        ShareLink.objects.update(created_at=eight_days_ago)
        assert client.get(f"/share/{embed.token}/").status_code == 200
        assert client.get(f"/share/{plain.token}/").status_code == 410

    def test_embed_link_ignores_require_password(self, client, built_report, make_link, sharing_on):
        sharing_on.require_password = True
        sharing_on.save(update_fields=["require_password"])
        embed = make_link(embed=True, embed_origins=[self.ORIGIN])
        plain = make_link()
        assert client.get(f"/share/{embed.token}/").status_code == 200
        assert client.get(f"/share/{plain.token}/").status_code == 410

    def test_embed_link_reports_no_effective_expiry(self, embed_link, sharing_on):
        sharing_on.max_expiry_days = 1
        assert embed_link.effective_expires_at(sharing_on) is None

    def test_disabling_embed_links_takes_them_down_but_not_plain_links(
        self, client, login, developer, prefix, built_report, make_link, sharing_on
    ):
        embed = make_link(embed=True, embed_origins=[self.ORIGIN])
        plain = make_link()
        sharing_on.embed_links_enabled = False
        sharing_on.save(update_fields=["embed_links_enabled"])

        assert client.get(f"/share/{embed.token}/").status_code == 410
        assert client.get(f"/share/{embed.token}/data.json").status_code == 410
        assert client.get(f"/share/{plain.token}/").status_code == 200

        body = login(developer).get(f"{prefix}/api/reports/player-overview/share_links").json()
        assert body["embed_links_enabled"] is False
        rows = {r["id"]: r for r in body["share_links"]}
        assert rows[embed.pk]["blocked_by_policy"] is True
        assert rows[plain.pk]["blocked_by_policy"] is False

    def test_revoked_embed_link_is_410(self, client, built_report, make_link):
        link = make_link(embed=True, embed_origins=[self.ORIGIN], revoked_at=timezone.now())
        assert client.get(f"/share/{link.token}/").status_code == 410

    # ── serving: head markers ──

    def test_entry_html_carries_embed_markers(self, client, built_report, embed_link):
        html = client.get(f"/share/{embed_link.token}/").content.decode()
        assert "window._fwEmbed=true;" in html
        assert 'window._fwEmbedOrigins=["http://localhost:8070"];' in html
        assert '<script src="/api/reports/embed-widget.js" defer></script>' in html
        assert ".fw-header{display:none!important}" in html
        # No scrollbars of the frame's own.
        assert "scrollbar-width:none" in html
        # Still a share link as far as the framework runtime is concerned.
        assert "window._fwShareLink=true;" in html
        assert html.index("_fwEmbed") < html.index("</head>")

    def test_plain_link_has_no_embed_markers(self, client, built_report, make_link):
        html = client.get(f"/share/{make_link().token}/").content.decode()
        assert "_fwEmbed" not in html
        assert "embed-widget.js" not in html
        assert ".fw-header{display:none" not in html
        assert "scrollbar-width:none" not in html

    def test_embed_badge_is_hidden(self, client, built_report, embed_link, settings):
        settings.TRELLUM_LICENCE = "malformed-retired-value"
        settings.TRELLUM_FEATURES = {"white_label": False}
        html = client.get(f"/share/{embed_link.token}/").content.decode()
        assert "window._fwEmbedBadge=false;" in html

    def test_embed_badge_stays_hidden_with_retired_flags(
        self, client, built_report, embed_link, settings
    ):
        settings.TRELLUM_FEATURES = {"white_label": True}
        html = client.get(f"/share/{embed_link.token}/").content.decode()
        assert "window._fwEmbedBadge=false;" in html

    # ── appearance: theme + filter bar chosen on the link ──

    def test_create_stores_and_returns_appearance(self, create):
        resp = create(embed_theme="dracula", embed_hide_filters=True)
        assert resp.status_code == 201
        link = resp.json()["share_link"]
        assert link["embed_theme"] == "dracula"
        assert link["embed_hide_filters"] is True
        row = ShareLink.objects.get()
        assert row.embed_theme == "dracula"
        assert row.embed_hide_filters is True

    def test_appearance_defaults_to_the_studio_default_and_visible_filters(self, create):
        link = create().json()["share_link"]
        assert link["embed_theme"] == ""
        assert link["embed_hide_filters"] is False

    def test_theme_is_trimmed(self, create):
        assert create(embed_theme="  trellum dark  ").json()["share_link"]["embed_theme"] == (
            "trellum dark"
        )

    @pytest.mark.parametrize(
        "bad",
        ["dark';alert(1)//", "<script>", "trellum/dark", "dark\nlight", "x" * 65, "théme"],
    )
    def test_malformed_theme_refused(self, create, bad):
        resp = create(embed_theme=bad)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_embed_theme"
        assert "message" in resp.json()
        assert not ShareLink.objects.exists()

    def test_appearance_ignored_on_a_plain_share_link(self, login, developer, prefix, report_row):
        """Both are embed-only: a plain link takes the values without
        complaint and stores neither."""
        resp = login(developer).post(
            f"{prefix}/api/reports/player-overview/share_links",
            data=json.dumps({"embed_theme": "dracula", "embed_hide_filters": True}),
            content_type="application/json",
        )
        assert resp.status_code == 201
        link = resp.json()["share_link"]
        assert link["embed_theme"] == ""
        assert link["embed_hide_filters"] is False

    def test_served_embed_forces_the_links_theme_over_a_stored_one(
        self, client, built_report, make_link
    ):
        """The report's own build-time script re-applies
        localStorage['fw-theme'] before first paint, so the head block has to
        both set the attribute and clear that key -- otherwise a browser that
        visited another embed first keeps showing the theme it stored."""
        link = make_link(embed=True, embed_origins=[self.ORIGIN], embed_theme="dracula")
        html = client.get(f"/share/{link.token}/").content.decode()
        assert 'window._fwEmbedTheme="dracula";' in html
        assert "document.documentElement.setAttribute('data-theme',window._fwEmbedTheme)" in html
        assert "localStorage.removeItem('fw-theme')" in html
        assert html.index("_fwEmbedTheme") < html.index("</head>")

    def test_served_embed_without_a_theme_forces_nothing(
        self, client, built_report, embed_link
    ):
        html = client.get(f"/share/{embed_link.token}/").content.decode()
        assert 'window._fwEmbedTheme="";' in html
        # The setAttribute/removeItem pair is guarded on that empty value, so
        # the studio's own resolved theme (and the visitor's stored one) stand.
        assert "if(window._fwEmbedTheme){" in html

    def test_served_embed_hides_the_filter_bar_only_when_asked(
        self, client, built_report, embed_link, make_link
    ):
        rule = ".fw-filter-bar{display:none!important}"
        assert rule not in client.get(f"/share/{embed_link.token}/").content.decode()
        hidden = make_link(embed=True, embed_origins=[self.ORIGIN], embed_hide_filters=True)
        html = client.get(f"/share/{hidden.token}/").content.decode()
        assert rule in html
        # Alongside the chrome rules, not instead of them.
        assert ".fw-header{display:none!important}" in html

    def test_plain_link_carries_no_appearance_rules(self, client, built_report, make_link):
        """A plain link ignores stored appearance even if a row somehow
        carries it -- the whole block is embed-only."""
        link = make_link(embed_theme="dracula", embed_hide_filters=True)
        html = client.get(f"/share/{link.token}/").content.decode()
        assert "_fwEmbedTheme" not in html
        assert ".fw-filter-bar{display:none" not in html

    # ── widget + list ──

    def test_embed_widget_js_served(self, client):
        resp = client.get("/api/reports/embed-widget.js")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("application/javascript")
        assert b"trellum:ready" in resp.content
        assert b"setProperty" in resp.content  # the trellum:theme `vars` path

    def test_embed_element_js_served(self, client):
        # The host-page half of the same protocol (internal planning ticket #12), on its own
        # frozen URL because customers type it into their markup by hand.
        resp = client.get("/api/reports/embed-element.js")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("application/javascript")
        assert b"customElements.define('trellum-report'" in resp.content

    def test_list_payload_reports_embed_links_enabled(self, login, developer, prefix, report_row):
        body = login(developer).get(f"{prefix}/api/reports/player-overview/share_links").json()
        assert body["embed_links_enabled"] is True
