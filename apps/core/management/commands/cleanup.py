"""Apply the retention policy.

    manage.py cleanup --dry-run     # what would go, changing nothing
    manage.py cleanup               # actually do it

Runs nightly on the coordinator (see runworker), but is safe to run by hand at
any time — it takes no lock of its own and deletes in bounded batches.

Each target is independent: one failing does not stop the others, because a
locked table should not mean the disk keeps filling for another day.
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Delete data past its retention window (runs, audit, sessions, …)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be removed without removing it.",
        )
        parser.add_argument(
            "--target", action="append", default=None,
            help="Only run this target (repeatable). Default: all of them.",
        )

    def handle(self, *args, **options):
        from apps.core import retention
        from apps.core.audit import audit_system
        from apps.core.models import OpsState

        dry_run = options["dry_run"]
        wanted = options["target"]

        targets = retention.TARGETS
        if wanted:
            by_name = {fn.__name__: fn for fn in retention.TARGETS}
            unknown = [name for name in wanted if name not in by_name]
            if unknown:
                self.stderr.write(
                    f"Unknown target(s): {', '.join(unknown)}. "
                    f"Available: {', '.join(by_name)}"
                )
                return
            targets = [by_name[name] for name in wanted]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — nothing is deleted\n"))

        results: dict[str, int] = {}
        failures: dict[str, str] = {}

        for fn in targets:
            try:
                label, count = fn(dry_run=dry_run)
            except Exception as exc:  # noqa: BLE001 — one target must not stop the rest
                failures[fn.__name__] = str(exc)
                self.stdout.write(self.style.ERROR(f"  {fn.__name__:<24} FAILED: {exc}"))
                continue
            results[label] = count
            style = self.style.SUCCESS if count else (lambda s: s)
            verb = "would remove" if dry_run else "removed"
            self.stdout.write(f"  {label:<24} {style(str(count)):>8}  {verb}")

        total = sum(results.values())
        self.stdout.write("")
        if dry_run:
            self.stdout.write(f"{total} item(s) would be removed.")
            return

        # Recorded so `doctor` can tell whether cleanup is still happening, and
        # so "what went last night" survives the container logs.
        OpsState.record(
            "cleanup", ok=not failures, removed=results, failures=failures,
            policy=dict(settings.RETENTION),
        )
        # One summary row for the whole run, not one per deletion -- the
        # audit trail records that retention happened and what it touched,
        # not a line item per row purged. audit_system(): there is no
        # request behind a nightly job.
        audit_system("retention.purge", removed=results, failures=failures)

        if failures:
            self.stdout.write(self.style.ERROR(
                f"{total} item(s) removed, {len(failures)} target(s) failed."
            ))
            return
        self.stdout.write(self.style.SUCCESS(f"{total} item(s) removed."))
