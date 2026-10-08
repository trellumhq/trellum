"""SSO email-domain ownership.

The vulnerability this closes: `OrgSSOConfig.email_domains` is a claim an org
admin types in. On a shared instance, claiming `victim.com` routes every login
at that domain to *your* identity provider, where you decide who authenticates.
That is tenant takeover with no exploit required.
"""
import pytest

from apps.accounts import sso
from apps.orgs import domains
from apps.orgs.models import OrgDomain, OrgSSOConfig

pytestmark = pytest.mark.django_db


@pytest.fixture
def sso_config(org):
    return OrgSSOConfig.objects.create(
        org=org,
        enabled=True,
        issuer_url="https://idp.example/realms/demo",
        client_id="client-1",
        email_domains=["victim.com"],
    )


@pytest.fixture
def enforce(settings):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = True


@pytest.fixture
def dns(monkeypatch):
    """Control what DNS appears to say."""
    records: dict[str, list[str]] = {}

    def _lookup(domain):
        return records.get(domain, [])

    monkeypatch.setattr(domains, "lookup_txt", _lookup)
    return records


class TestNormalise:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Example.COM", "example.com"),
            ("user@example.com", "example.com"),
            ("https://example.com/path", "example.com"),
            ("example.com.", "example.com"),
            ("example.com:443", "example.com"),
            ("  example.com  ", "example.com"),
        ],
    )
    def test_shapes_that_should_collapse_to_one_domain(self, raw, expected):
        assert domains.normalise(raw) == expected


class TestClaiming:
    def test_claiming_creates_a_pending_record_with_a_token(self, org):
        record, created = domains.claim(org, "Example.com")
        assert created is True
        assert record.domain == "example.com"
        assert record.is_verified is False
        assert len(record.verification_token) >= 16

    def test_claiming_twice_is_idempotent(self, org):
        first, _ = domains.claim(org, "example.com")
        again, created = domains.claim(org, "example.com")
        assert created is False
        assert again.pk == first.pk
        assert again.verification_token == first.verification_token

    def test_another_org_cannot_claim_the_same_domain(self, org, other_org):
        domains.claim(org, "example.com")
        with pytest.raises(ValueError, match="already claimed"):
            domains.claim(other_org, "example.com")

    @pytest.mark.parametrize("bad", ["", "   ", "notadomain", "@"])
    def test_nonsense_is_refused(self, org, bad):
        with pytest.raises(ValueError):
            domains.claim(org, bad)


class TestVerification:
    def test_the_right_txt_record_verifies(self, org, dns):
        record, _ = domains.claim(org, "example.com")
        dns["example.com"] = [f"trellum-verification={record.verification_token}"]

        ok, message = domains.verify(record)

        assert ok is True
        record.refresh_from_db()
        assert record.is_verified is True

    def test_another_orgs_token_does_not_verify(self, org, other_org, dns):
        """The token is per-claim, so publishing someone else's is useless."""
        mine, _ = domains.claim(org, "mine.com")
        theirs, _ = domains.claim(other_org, "theirs.com")
        dns["mine.com"] = [f"trellum-verification={theirs.verification_token}"]

        ok, _ = domains.verify(mine)
        assert ok is False

    def test_no_dns_records_does_not_verify(self, org, dns):
        record, _ = domains.claim(org, "example.com")
        ok, message = domains.verify(record)
        assert ok is False
        assert "no TXT records" in message

    def test_unrelated_records_do_not_verify(self, org, dns):
        record, _ = domains.claim(org, "example.com")
        dns["example.com"] = ["v=spf1 include:_spf.google.com ~all"]
        assert domains.verify(record)[0] is False

    def test_failure_is_recorded_for_the_admin(self, org, dns):
        record, _ = domains.claim(org, "example.com")
        domains.verify(record)
        record.refresh_from_db()
        assert record.last_checked_at is not None
        assert record.dns_record_name in record.last_error

    def test_a_dns_outage_is_not_a_verification(self, org, monkeypatch):
        """Resolver failures must fail closed."""
        record, _ = domains.claim(org, "example.com")

        def _explode(domain):
            raise OSError("resolver unreachable")

        monkeypatch.setattr(domains, "lookup_txt", _explode)
        with pytest.raises(OSError):
            domains.verify(record)
        record.refresh_from_db()
        assert record.is_verified is False


class TestRouting:
    """The part that actually protects a tenant."""

    def test_unverified_domains_do_not_route_when_enforced(
        self, sso_config, enforce
    ):
        assert domains.routable_domains(sso_config) == []
        assert sso.config_for_email_domain("someone@victim.com") is None

    def test_verified_domains_route(self, org, sso_config, enforce, dns):
        record, _ = domains.claim(org, "victim.com")
        dns["victim.com"] = [f"trellum-verification={record.verification_token}"]
        domains.verify(record)

        assert domains.routable_domains(sso_config) == ["victim.com"]
        assert sso.config_for_email_domain("someone@victim.com") == sso_config

    def test_without_enforcement_claims_route_as_before(self, sso_config, settings):
        """A single-tenant install should not have to prove it owns its own
        domain to itself."""
        settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
        assert domains.routable_domains(sso_config) == ["victim.com"]
        assert sso.config_for_email_domain("someone@victim.com") == sso_config

    def test_an_attacker_claiming_a_domain_cannot_capture_logins(
        self, other_org, enforce
    ):
        """The whole point, stated as the attack."""
        attacker_cfg = OrgSSOConfig.objects.create(
            org=other_org,
            enabled=True,
            issuer_url="https://idp.example/realms/demo",
            client_id="attacker",
            email_domains=["bigbank.com"],
        )
        domains.claim(other_org, "bigbank.com")  # claimed, never verified

        assert domains.routable_domains(attacker_cfg) == []
        assert sso.config_for_email_domain("cfo@bigbank.com") is None


class TestAdapterGuard:
    def test_idp_cannot_assert_an_unverified_domain(self, org, sso_config, enforce):
        """Second line of defence: even inside the callback, an org's IdP may
        only assert emails at domains that org has proved it owns."""
        from apps.orgs import domains as domain_service

        assert domain_service.routable_domains(sso_config) == []


class TestSettingsPage:
    def test_admin_can_claim_and_see_the_dns_record(self, login, org_admin, org):
        page = login(org_admin)
        page.post(f"/orgs/{org.slug}/settings/sso", {"action": "claim-domain", "domain": "example.com"})
        body = page.get(f"/orgs/{org.slug}/settings/sso").content.decode()

        record = OrgDomain.objects.get(org=org, domain="example.com")
        assert record.dns_record_name in body
        assert record.verification_token in body

    def test_optional_verification_controls_are_collapsed(
        self, login, org_admin, org, settings
    ):
        settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
        page = login(org_admin)
        body = page.get(f"/orgs/{org.slug}/settings/sso").content.decode()

        assert '<details>' in body
        assert '<summary>Advanced: optional domain verification</summary>' in body
        assert 'email-domain' in body
        assert 'Claimed but unverified domains are ignored.' not in body

    def test_required_verification_controls_remain_visible(
        self, login, org_admin, org, settings
    ):
        settings.TRELLUM_SSO_DOMAIN_VERIFICATION = True
        page = login(org_admin)
        body = page.get(f"/orgs/{org.slug}/settings/sso").content.decode()

        assert '<details>' not in body
        assert 'Claimed but unverified domains are ignored.' in body

    def test_verify_button_reports_failure_without_crashing(
        self, login, org_admin, org, dns
    ):
        page = login(org_admin)
        page.post(f"/orgs/{org.slug}/settings/sso", {"action": "claim-domain", "domain": "example.com"})
        record = OrgDomain.objects.get(org=org, domain="example.com")

        resp = page.post(
            f"/orgs/{org.slug}/settings/sso",
            {"action": "verify-domain", "domain_id": record.pk},
            follow=True,
        )
        assert resp.status_code == 200
        record.refresh_from_db()
        assert record.is_verified is False

    def test_removing_a_claim_works(self, login, org_admin, org):
        page = login(org_admin)
        page.post(f"/orgs/{org.slug}/settings/sso", {"action": "claim-domain", "domain": "example.com"})
        record = OrgDomain.objects.get(org=org, domain="example.com")
        page.post(
            f"/orgs/{org.slug}/settings/sso",
            {"action": "remove-domain", "domain_id": record.pk},
        )
        assert not OrgDomain.objects.filter(pk=record.pk).exists()

    def test_a_member_cannot_claim_domains(self, login, member, org):
        resp = login(member).post(
            f"/orgs/{org.slug}/settings/sso",
            {"action": "claim-domain", "domain": "example.com"},
        )
        assert resp.status_code in (403, 404)
        assert not OrgDomain.objects.filter(domain="example.com").exists()


class TestSelfSignup:
    def test_operators_can_always_create_orgs(self, login, superuser, settings):
        from apps.orgs.models import Organization

        settings.TRELLUM_ORG_SELF_SIGNUP = False
        login(superuser).post("/orgs/create", {"slug": "opsmade", "name": "Ops Made"})
        assert Organization.objects.filter(slug="opsmade").exists()

    def test_ordinary_users_cannot_when_signup_is_closed(
        self, login, member, settings
    ):
        from apps.orgs.models import Organization

        settings.TRELLUM_ORG_SELF_SIGNUP = False
        resp = login(member).post("/orgs/create", {"slug": "sneaky", "name": "Sneaky"})
        assert resp.status_code == 403
        assert not Organization.objects.filter(slug="sneaky").exists()

    def test_ordinary_users_can_when_signup_is_open(self, login, member, settings):
        from apps.orgs.models import Organization

        settings.TRELLUM_ORG_SELF_SIGNUP = True
        login(member).post("/orgs/create", {"slug": "trial", "name": "Trial"})
        assert Organization.objects.filter(slug="trial").exists()

    def test_the_creator_becomes_its_admin(self, login, member, settings):
        from apps.core import roles
        from apps.orgs.models import OrgMembership

        settings.TRELLUM_ORG_SELF_SIGNUP = True
        login(member).post("/orgs/create", {"slug": "trial", "name": "Trial"})
        membership = OrgMembership.objects.get(user=member, org__slug="trial")
        assert membership.role == roles.ORG_ADMIN

    def test_anonymous_is_sent_to_login(self, client):
        resp = client.post("/orgs/create", {"slug": "x", "name": "X"})
        assert resp.status_code in (302, 401)
