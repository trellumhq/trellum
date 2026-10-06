"""Pre-flight for enabling ``TRELLUM_SSO_DOMAIN_VERIFICATION``.

    manage.py check_sso_domains            # report; exit 1 while any org would break
    manage.py check_sso_domains --verify   # re-check DNS for pending claims first

With verification on, only verified domains route logins (see
``apps.orgs.domains.routable_domains``). Turning it on while an enabled org
still has an unverified claimed domain fails every SSO login at that domain,
at the login page and in the callback, so run this until it exits clean first.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "List every enabled SSO org whose claimed email domains are not verified."

    def add_arguments(self, parser):
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Re-check DNS for every unverified claimed domain before reporting.",
        )

    def handle(self, *args, **opts):
        from apps.accounts import sso
        from apps.orgs import domains
        from apps.orgs.models import OrgDomain

        if opts["verify"]:
            for record in OrgDomain.objects.filter(verified_at__isnull=True):
                domains.verify(record)

        enforced = domains.enforcement_enabled()
        self.stdout.write(f"sso_domain_verification is {'on' if enforced else 'off'}")

        broken = 0
        for cfg in sso.enabled_configs().order_by("org__slug"):
            claimed = sorted({domains.normalise(d) for d in cfg.email_domains or []} - {""})
            # Across all orgs: a domain is held by one org globally, and "not
            # claimed" would send the operator to claim() only to be refused.
            records = {
                d.domain: d
                for d in OrgDomain.objects.filter(domain__in=claimed).select_related("org")
            }
            failed = False
            for domain in claimed:
                record = records.get(domain)
                if record is not None and record.org_id == cfg.org_id and record.is_verified:
                    self.stdout.write(f"  [ok]   {cfg.org.slug}  {domain}")
                    continue
                failed = True
                if record is None:
                    reason = "not claimed"
                elif record.org_id != cfg.org_id:
                    reason = f"claimed by {record.org.slug}"
                else:
                    reason = "unverified"
                    if record.last_error:
                        reason += f": {record.last_error}"
                self.stdout.write(
                    self.style.ERROR(f"  [FAIL] {cfg.org.slug}  {domain}  {reason}")
                )
            broken += failed

        if broken:
            verb = "have lost" if enforced else "would lose"
            self.stdout.write(
                self.style.ERROR(
                    f"{broken} organization(s) {verb} SSO logins at the domains above"
                    + ("." if enforced else " if sso_domain_verification were enabled now.")
                )
            )
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("all claimed domains are verified"))
