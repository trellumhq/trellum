"""Run one bounded annotation UI-filter expiry cleanup tick."""
import json

from django.core.management.base import BaseCommand, CommandError
from apps.reports.annotation_filter_cleanup import cleanup_once


class Command(BaseCommand):
    help = "Delete at most500 expired annotation filter rows across at most25 states."

    def add_arguments(self, parser):
        parser.add_argument("--max-rows", type=int, default=500)
        parser.add_argument("--max-candidates", type=int, default=25)

    def handle(self, *args, **options):
        try:
            result = cleanup_once(max_rows=options["max_rows"], max_candidates=options["max_candidates"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, sort_keys=True))
