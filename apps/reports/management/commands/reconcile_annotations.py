"""Explicitly register a selected local project or observe existing ownership."""
from django.core.management.base import BaseCommand, CommandError
from apps.studios.models import Studio


class Command(BaseCommand):
    help = "Register a selected local annotation workspace; ordinary HTTP reads never repair it."

    def add_arguments(self, parser):
        parser.add_argument("studio", help="<org_slug>/<studio_slug>")
        parser.add_argument("--register-local", action="store_true", help="Explicitly select this materialized local project as authoritative.")

    def handle(self, *args, **options):
        from apps.reports.annotation_reconciliation import authoritative_handoff, observe
        from apps.reports.models import AnnotationSourceState
        from apps.reports.scan import ReportQuotaExceeded, sync_studio_registry
        from apps.runner.gitsync import studio_sync_lock
        try:
            org, slug = options["studio"].split("/", 1)
            studio = Studio.objects.get(org__slug=org, slug=slug)
        except (ValueError, Studio.DoesNotExist) as exc:
            raise CommandError("select an existing studio as <org>/<studio>") from exc
        with studio_sync_lock(studio.pk) as locked:
            if not locked:
                raise CommandError("studio synchronization is already running")
            if options["register_local"]:
                if not studio.project_root.is_dir():
                    raise CommandError("local project is not materialized")
                try:
                    sync_studio_registry(studio)
                except ReportQuotaExceeded as exc:
                    self.stderr.write(self.style.WARNING(str(exc)))
                authoritative_handoff(studio)
            source = AnnotationSourceState.objects.filter(studio=studio, kind="global").first()
            if source is None or not source.owner_workspace_id:
                raise CommandError("source is unindexed; explicitly select a local workspace with --register-local")
            observe(source.pk)
        self.stdout.write("Annotation reconciliation finished; inspect source coverage before relying on counts.")
