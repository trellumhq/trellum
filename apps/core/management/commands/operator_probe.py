"""Closed child probes; only the supervising parent publishes observations."""
import json
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core import operator_observations as observations
from apps.core.models import OpsState
from apps.core.workspace import cached_workspace_identity, workspace_identity


class Command(BaseCommand):
    help = "Execute one coordinator operator measurement without publishing it."

    def add_arguments(self, parser):
        parser.add_argument("--kind", choices=tuple(observations.KINDS), required=True)
        parser.add_argument("--operation-id", required=True)

    def handle(self, *args, **options):
        operation_id = str(UUID(options["operation_id"]))
        row = OpsState.objects.filter(key=observations.SLOT).values("payload").first()
        entry = row and row["payload"].get("active")
        if not entry or entry["id"] != operation_id or entry["state"] != "running" or entry["kind"] != options["kind"]:
            raise CommandError("No matching diagnostic operation")
        if observations.fingerprint(entry["kind"], producer=True) != entry["producer_fingerprint"]:
            raise CommandError("Diagnostic producer context changed")
        if entry["kind"] == "upload-usage":
            # This branch is supplied by the separately scoped usage consumer.
            raise CommandError("Storage measurement is not available")
        from apps.core.health import run_checks
        checks = run_checks(quick=entry["kind"] == "maintenance")
        # Raw provider exceptions/config paths never cross the observation pipe.
        result = {
            "version": 1, "kind": entry["kind"], "quality": "complete",
            "scope": "coordinator-local", "workspace_id": cached_workspace_identity(),
            "producer_fingerprint": entry["producer_fingerprint"],
            "started_at": entry["started_at"], "observed_at": timezone.now().isoformat(),
            "checks": [{"label": check["label"], "ok": bool(check["ok"]),
                        "detail": "Passed on coordinator." if check["ok"] else "Failed on coordinator. Run manage.py doctor there for details."}
                       for check in checks[:32]],
        }
        if len(checks) > 32 or workspace_identity(settings.DATA_DIR) != entry["producer_workspace_id"]:
            raise CommandError("Diagnostic scope changed or exceeded its limit")
        self.stdout.write(json.dumps(result, separators=(",", ":")))
