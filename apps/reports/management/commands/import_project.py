"""Seed a studio's project root from an existing framework project directory.

    manage.py import_project <org_slug>/<studio_slug> <source_project_root>

Copies reports/, data-sources/ and every project-root file the portal
materializes (see runner.executor.project_root_materialized_files) into the
studio's project root, then re-scans the registry. Used to
migrate an existing single-tenant project (or the framework demo) into a
studio; day-to-day report delivery is the studio's git repository.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.reports.scan import sync_studio_registry
from apps.studios.models import Studio


class Command(BaseCommand):
    help = "Copy an existing framework project into a studio's project root."

    def add_arguments(self, parser):
        parser.add_argument("studio", help="<org_slug>/<studio_slug>")
        parser.add_argument("source", help="Path of the project to import")

    def handle(self, *args, **opts):  # noqa: ARG002
        try:
            org_slug, studio_slug = opts["studio"].split("/", 1)
        except ValueError as exc:
            raise CommandError("studio must be <org_slug>/<studio_slug>") from exc
        studio = Studio.objects.filter(org__slug=org_slug, slug=studio_slug).first()
        if studio is None:
            raise CommandError(f"studio {opts['studio']} not found")

        src = Path(opts["source"]).resolve()
        if not (src / "reports").is_dir():
            raise CommandError(f"{src} has no reports/ directory")

        studio.ensure_dirs()
        copied = []
        for name in ("reports", "data-sources"):
            src_dir = src / name
            if src_dir.is_dir():
                dst = studio.project_root / name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src_dir, dst)
                copied.append(name + "/")
        from apps.runner.executor import project_root_materialized_files

        for name in project_root_materialized_files():
            if (src / name).is_file():
                shutil.copyfile(src / name, studio.project_root / name)
                copied.append(name)
        studio.ensure_dirs()  # recreate anything the copies replaced

        from apps.reports.scan import ReportQuotaExceeded

        try:
            n = sync_studio_registry(studio)
        except ReportQuotaExceeded as exc:
            from apps.reports.models import Report

            n = Report.objects.filter(studio=studio, present_in_scan=True).count()
            self.stderr.write(self.style.WARNING(str(exc)))

        # Data sources become DB rows (the portal owns config.yaml now and
        # rewrites it before every run — YAML-only sources would vanish).
        ds_note = ""
        ds_yaml = src / "data-sources" / "config.yaml"
        if ds_yaml.is_file():
            from apps.datasources.importer import import_yaml

            imported, needs_creds = import_yaml(studio, ds_yaml)
            ds_note = f" Data sources imported: {', '.join(imported) or 'none'}."
            if needs_creds:
                ds_note += (
                    f" NEEDS CREDENTIALS (enter in the portal UI): {', '.join(needs_creds)}."
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {', '.join(copied)} into {studio} — {n} report(s) registered."
                + ds_note
            )
        )
