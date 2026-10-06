"""manage.py check_sso_domains: who breaks if sso_domain_verification is turned on."""
import contextlib
import io

import pytest
from django.core.management import call_command

from apps.orgs import domains
from apps.orgs.models import OrgSSOConfig

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def domain_verification_enabled(settings):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = True


@pytest.fixture
def dns(monkeypatch):
    records: dict[str, list[str]] = {}
    monkeypatch.setattr(domains, "lookup_txt", lambda domain: records.get(domain, []))
    return records


@pytest.fixture
def sso_config(org):
    return OrgSSOConfig.objects.create(
        org=org,
        enabled=True,
        issuer_url="https://idp.example/realms/demo",
        client_id="client-1",
        email_domains=["Good.example", "pending.example", "missing.example"],
    )


def run(*args) -> str:
    """The report text; a failing report's SystemExit(1) is swallowed here."""
    out = io.StringIO()
    with contextlib.suppress(SystemExit):
        call_command("check_sso_domains", *args, stdout=out)
    return out.getvalue()


def claim_and_verify(org, domain, dns):
    record, _ = domains.claim(org, domain)
    dns[domain] = [record.dns_record_value]
    assert domains.verify(record)[0] is True


def test_all_verified_exits_clean(org, sso_config, dns):
    for domain in ("good.example", "pending.example", "missing.example"):
        claim_and_verify(org, domain, dns)

    out = run()

    assert out.count("[ok]") == 3
    assert "[FAIL]" not in out
    assert "sso_domain_verification is on" in out


def test_unverified_and_unclaimed_are_named(org, sso_config, dns):
    claim_and_verify(org, "good.example", dns)
    pending, _ = domains.claim(org, "pending.example")
    domains.verify(pending)  # no record published -> stored last_error

    out = run()

    assert "[ok]   demo  good.example" in out
    assert "[FAIL] demo  missing.example  not claimed" in out
    assert "[FAIL] demo  pending.example  unverified: " in out
    assert "1 organization(s) have lost SSO logins" in out
    pytest.raises(SystemExit, call_command, "check_sso_domains", stdout=io.StringIO())


def test_domain_held_by_another_org_is_named(org, other_org, sso_config, dns):
    for domain in ("good.example", "pending.example"):
        claim_and_verify(org, domain, dns)
    domains.claim(other_org, "missing.example")

    assert "[FAIL] demo  missing.example  claimed by rival" in run()


def test_verify_flag_rechecks_dns_first(org, sso_config, dns):
    for domain in ("good.example", "missing.example"):
        claim_and_verify(org, domain, dns)
    pending, _ = domains.claim(org, "pending.example")
    dns["pending.example"] = [pending.dns_record_value]  # published after the claim

    assert "[FAIL] demo  pending.example  unverified" in run()
    out = run("--verify")

    assert "[FAIL]" not in out
    assert out.count("[ok]") == 3
