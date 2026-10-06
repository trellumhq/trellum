"""Regenerate static/themes.generated.css from the framework's theme registry.

    manage.py sync_themes           # write the file
    manage.py sync_themes --check   # exit 1 if it is stale (CI)

The framework owns the themes; this command is the portal's copy of them. Run
it after changing trellum/themes or editing apps/core/theme_overlay.py.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.themes import GENERATED_PATH, render
from trellum.themes import THEME_REGISTRY


class Command(BaseCommand):
    help = "Generate static/themes.generated.css from trellum.themes.THEME_REGISTRY."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Do not write; exit 1 if the checked-in file is out of date.",
        )

    def handle(self, *args, **opts):  # noqa: ARG002
        target = settings.BASE_DIR / GENERATED_PATH
        fresh = render(THEME_REGISTRY)
        current = target.read_text(encoding="utf-8") if target.exists() else None

        if opts["check"]:
            if current == fresh:
                self.stdout.write(self.style.SUCCESS(f"{GENERATED_PATH} is up to date"))
                return
            self.stdout.write(
                self.style.ERROR(
                    f"{GENERATED_PATH} is stale — run `manage.py sync_themes`"
                )
            )
            raise SystemExit(1)

        if current == fresh:
            self.stdout.write(f"{GENERATED_PATH} already up to date")
            return
        target.write_text(fresh, encoding="utf-8")
        self.stdout.write(
            self.style.SUCCESS(f"wrote {GENERATED_PATH} ({len(THEME_REGISTRY)} themes)")
        )
