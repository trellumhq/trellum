"""Closed child probes; only the supervising parent publishes observations."""
import json
import re
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core import operator_observations as observations
from apps.core.models import OpsState
from apps.core.workspace import WorkspaceUnavailable, cached_workspace_identity, workspace_identity


# Credentials in a URL's userinfo, and query strings or fragments that may carry
# tokens, are never stored with a check's detail.
_URL_USERINFO = re.compile(r"(?<=://)[^/\s@]*@")
_URL_QUERY = re.compile(r"(\w+://[^\s?#]*)[?#]\S*")


def _detail(value):
    """The check's own explanation, as the page showed it before it was measured
    on the coordinator: one printable line, URL secrets removed, bounded."""
    text = "".join(char if char.isprintable() else " " for char in str(value))
    text = _URL_QUERY.sub(r"\1", _URL_USERINFO.sub("", " ".join(text.split())))
    return text if len(text) <= 400 else text[:399] + "…"


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
            from apps.operator.usage_observations import scan
            result = scan(entry["org_id"], entry["workspace_id"])
            result.update(version=1, kind=entry["kind"], workspace_id=cached_workspace_identity(),
                          producer_fingerprint=entry["producer_fingerprint"], started_at=entry["started_at"],
                          observed_at=timezone.now().isoformat())
            self.stdout.write(json.dumps(result, separators=(",", ":")))
            return
        from apps.core.health import run_checks
        checks = run_checks()
        # The detail is the operator-only text the page always showed; it is
        # reduced to one bounded line with URL credentials and queries removed.
        result = {
            "version": 1, "kind": entry["kind"], "quality": "complete",
            "scope": "coordinator-local", "workspace_id": cached_workspace_identity(),
            "producer_fingerprint": entry["producer_fingerprint"],
            "started_at": entry["started_at"], "observed_at": timezone.now().isoformat(),
            "checks": [{"label": check["label"], "ok": bool(check["ok"]),
                        "detail": _detail(check["detail"])}
                       for check in checks[:32]],
        }
        try:
            current_workspace = workspace_identity(settings.DATA_DIR)
        except WorkspaceUnavailable:
            current_workspace = None
        if len(checks) > 32 or current_workspace != entry["producer_workspace_id"]:
            raise CommandError("Diagnostic scope changed or exceeded its limit")
        self.stdout.write(json.dumps(result, separators=(",", ":")))
