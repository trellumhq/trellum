"""Optional bounded repair; normal runworker also reconciles automatically."""
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core import storage
from apps.reports.source_reconciliation import MAX_INSPECTIONS, run_batch


class Command(BaseCommand):
    help = "Reconcile one bounded batch of report source summaries."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=MAX_INSPECTIONS)

    def handle(self, *args, **options):
        try:
            result = run_batch(pools=None if storage.is_remote() else settings.TRELLUM_RUNNER_POOLS, limit=options["limit"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result))
