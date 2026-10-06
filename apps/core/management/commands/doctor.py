"""Preflight checks: is this deployment healthy and correctly configured?

    manage.py doctor          # human output, exit 1 on any failure

Same checks as the /system page (apps/core/health.py).
"""
from django.core.management.base import BaseCommand

from apps.core.health import run_checks


class Command(BaseCommand):
    help = "Run deployment preflight checks (DB, crypto, volume, git, framework version, worker, sandbox)."

    def handle(self, *args, **opts):  # noqa: ARG002
        self.stdout.write("trellum doctor")
        failures = 0
        for result in run_checks():
            if result["ok"]:
                self.stdout.write(f"  [ok]   {result['label']}: {result['detail']}")
            else:
                failures += 1
                self.stdout.write(
                    self.style.ERROR(f"  [FAIL] {result['label']}: {result['detail']}")
                )
        if failures:
            self.stdout.write(self.style.ERROR(f"{failures} check(s) failed"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("all checks passed"))
