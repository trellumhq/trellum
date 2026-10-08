"""Email-domain ownership verification via DNS TXT.

When enforcement is enabled, a claimed domain must be proved by publishing a
TXT record controlled by the domain owner before it can route SSO logins.

Enforcement defaults on for existing deployments. Fresh-install templates
explicitly disable it for trusted single-organization deployments; shared
deployments must explicitly enable it.
"""
from __future__ import annotations

import secrets

from django.conf import settings
from django.utils import timezone

RECORD_PREFIX = "trellum-verification="


def normalise(domain: str) -> str:
    """Lower-cased, stripped of scheme, path, port, leading @ and trailing dot."""
    value = (domain or "").strip().lower()
    for prefix in ("http://", "https://"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    value = value.split("/", 1)[0]
    value = value.rsplit("@", 1)[-1]
    value = value.split(":", 1)[0]
    return value.rstrip(".")


def new_token() -> str:
    return secrets.token_hex(16)


def enforcement_enabled() -> bool:
    return settings.TRELLUM_SSO_DOMAIN_VERIFICATION


def claim(org, domain: str):
    """Register a claim, returning (OrgDomain, created).

    Raises ValueError when another organization already holds the domain — the
    first verified claim wins, and letting two orgs claim one domain would
    restore the ambiguity this exists to remove.
    """
    from apps.orgs.models import OrgDomain

    value = normalise(domain)
    if not value or "." not in value:
        raise ValueError(f"{domain!r} is not a valid domain")

    existing = OrgDomain.objects.filter(domain=value).first()
    if existing is not None:
        if existing.org_id != org.pk:
            raise ValueError(
                f"{value} is already claimed by another organization"
                + (" and verified" if existing.is_verified else "")
            )
        return existing, False

    return (
        OrgDomain.objects.create(org=org, domain=value, verification_token=new_token()),
        True,
    )


def lookup_txt(domain: str) -> list[str]:
    """TXT records for the verification name. Isolated so tests can replace it.

    Returns [] when DNS cannot answer — indistinguishable, deliberately, from
    "no record": either way the claim is not proved.
    """
    name = f"_trellum-verification.{domain}"
    try:
        import dns.resolver  # type: ignore
    except ImportError:
        return _lookup_txt_stdlib(name)

    try:
        answers = dns.resolver.resolve(name, "TXT")
    except Exception:  # noqa: BLE001 - NXDOMAIN, timeout, servfail all mean "no"
        return []
    values = []
    for record in answers:
        raw = b"".join(getattr(record, "strings", []) or [])
        values.append(raw.decode("utf-8", "replace") if raw else str(record).strip('"'))
    return values


def _lookup_txt_stdlib(name: str) -> list[str]:
    """Fallback when dnspython is absent: shell out to the system resolver.

    Kept because adding a dependency for one lookup is a poor trade, and an
    air-gapped install will never call this at all.
    """
    import shutil
    import subprocess

    for cmd in (["dig", "+short", "TXT", name], ["nslookup", "-type=TXT", name]):
        if shutil.which(cmd[0]) is None:
            continue
        try:
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        return [line.strip().strip('"') for line in out.splitlines() if line.strip()]
    return []


def verify(org_domain) -> tuple[bool, str]:
    """Check DNS and record the outcome. Returns (verified, message)."""
    records = lookup_txt(org_domain.domain)
    expected = f"{RECORD_PREFIX}{org_domain.verification_token}"

    now = timezone.now()
    if any(expected in record for record in records):
        org_domain.verified_at = now
        org_domain.last_checked_at = now
        org_domain.last_error = ""
        org_domain.save(
            update_fields=["verified_at", "last_checked_at", "last_error"]
        )
        return True, f"{org_domain.domain} verified"

    found = ", ".join(r[:60] for r in records) if records else "no TXT records"
    message = (
        f"expected a TXT record at {org_domain.dns_record_name} containing "
        f"{expected!r}; found {found}. DNS changes can take a while to publish."
    )
    org_domain.last_checked_at = now
    org_domain.last_error = message[:400]
    org_domain.save(update_fields=["last_checked_at", "last_error"])
    return False, message


def verified_domains(org) -> set[str]:
    from apps.orgs.models import OrgDomain

    return set(
        OrgDomain.objects.filter(org=org, verified_at__isnull=False).values_list(
            "domain", flat=True
        )
    )


def routable_domains(cfg) -> list[str]:
    """Which of an SSO config's claimed domains may actually route logins.

    With enforcement off, every claimed domain routes, exactly as before.
    With it on, only domains this org has proved it owns.
    """
    claimed = [normalise(d) for d in (cfg.email_domains or [])]
    claimed = [d for d in claimed if d]
    if not enforcement_enabled():
        return claimed
    verified = verified_domains(cfg.org)
    return [d for d in claimed if d in verified]
