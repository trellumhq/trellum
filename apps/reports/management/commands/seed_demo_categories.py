"""Dev-only: seed placeholder reports so a studio has many categories.

Production studios run 20+ categories, which is the case the dashboard's
category row has to survive. The demo studio has four, so there is nothing to
test the overflow menu against locally.

    manage.py seed_demo_categories                    # add them
    manage.py seed_demo_categories --remove           # take them away again

Writes `report.yaml` files under the studio's project reports directory —
report.yaml is the source of truth, so these survive a registry rescan (rows
inserted straight into the database would be flagged present_in_scan=False by
the next scan and vanish). The reports are never built; they show as "Not yet
run", which is all the category row needs.

Every generated file carries MARKER, and --remove deletes only directories
that contain it, so this can never touch a real report.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.reports.scan import sync_studio_registry
from apps.studios.models import Studio

MARKER = "seeded-by: seed_demo_categories"

# Categories a games-analytics portal plausibly grows into.
CATEGORIES = [
    "Retention", "Acquisition", "Monetisation", "Live Ops", "Player Support",
    "Marketing", "Finance", "Store", "Progression", "Economy", "Social",
    "Ads", "Churn", "Cohorts", "Benchmarks", "Forecasting", "Compliance",
    "Infrastructure",
]

TEMPLATE = """# {marker}
# Placeholder for local UI testing — safe to delete, or run
# `manage.py seed_demo_categories --remove`.
name: "{name}"
slug: {slug}
description: "Placeholder report seeded to give the dashboard a realistic number of categories."
version: 0.1.0

schedule:
  cron: "0 7 * * *"
  timezone: UTC

tags:
  - seeded
category: {category}
"""

# scan_report_configs skips any directory without a generator.py, so a
# report.yaml alone is invisible to the registry. This one needs no data
# source, so a seeded report builds into something honest instead of raising
# if anyone presses Run.
GENERATOR = '''"""{marker}"""

from trellum import BaseReport
from trellum.components import RawHTML


class SeededPlaceholderReport(BaseReport):
    def generate(self, ctx):
        ctx.add_section("Placeholder", [
            RawHTML(
                "<p>Seeded placeholder for the <strong>{category}</strong> category. "
                "Remove it with <code>manage.py seed_demo_categories --remove</code>.</p>"
            ),
        ])
'''


class Command(BaseCommand):
    help = "Dev-only: seed placeholder reports so a studio has many categories."

    def add_arguments(self, parser):
        parser.add_argument("--org", default="demo")
        parser.add_argument("--studio", default="demo")
        parser.add_argument("--remove", action="store_true", help="Delete the seeded reports.")

    def handle(self, *args, **opts):  # noqa: ARG002
        if not settings.DEBUG:
            raise CommandError("seed_demo_categories is a development helper; DEBUG is off.")

        try:
            studio = Studio.objects.get(slug=opts["studio"], org__slug=opts["org"])
        except Studio.DoesNotExist:
            raise CommandError(f"no studio {opts['org']}/{opts['studio']}") from None

        reports_dir = Path(str(studio.reports_dir))
        if opts["remove"]:
            removed = self._remove(reports_dir)
            verb = "removed"
        else:
            reports_dir.mkdir(parents=True, exist_ok=True)
            removed = self._add(reports_dir)
            verb = "wrote"

        total = sync_studio_registry(studio)
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {removed} placeholder report(s); {studio} now scans {total} reports"
            )
        )

    def _add(self, reports_dir: Path) -> int:
        count = 0
        for category in CATEGORIES:
            slug = category.lower().replace(" ", "-") + "-overview"
            target = reports_dir / slug
            target.mkdir(parents=True, exist_ok=True)
            (target / "report.yaml").write_text(
                TEMPLATE.format(
                    marker=MARKER, name=f"{category} Overview", slug=slug, category=category
                ),
                encoding="utf-8",
            )
            (target / "generator.py").write_text(
                GENERATOR.format(marker=MARKER, category=category), encoding="utf-8"
            )
            (target / "__init__.py").write_text("", encoding="utf-8")
            count += 1
        return count

    def _remove(self, reports_dir: Path) -> int:
        import shutil

        count = 0
        if not reports_dir.is_dir():
            return 0
        for child in sorted(reports_dir.iterdir()):
            config = child / "report.yaml"
            if not config.is_file():
                continue
            # Only ever delete what this command wrote.
            if MARKER in config.read_text(encoding="utf-8"):
                shutil.rmtree(child)
                count += 1
        return count
