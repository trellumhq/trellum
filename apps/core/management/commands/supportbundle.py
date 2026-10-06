"""Collect diagnostics for a support request.

    manage.py supportbundle                    # writes support-bundle-<ts>.tar.gz
    manage.py supportbundle --print            # print the JSON instead
    manage.py supportbundle --output /tmp/b.tar.gz

Contains configuration shape, fleet and run state, and health results. It
contains no report output, no query results, no datasource credentials, and no
secrets — see apps/core/supportbundle.py for the allowlist.
"""
from __future__ import annotations

import io
import tarfile
import time
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.core import supportbundle


class Command(BaseCommand):
    help = "Collect a redacted diagnostics bundle for support."

    def add_arguments(self, parser):
        parser.add_argument("--output", default="", help="Path for the .tar.gz.")
        # Not "--stdout": BaseCommand already owns self.stdout, and shadowing it
        # replaces the output stream with a bool.
        parser.add_argument(
            "--print", dest="print_json", action="store_true",
            help="Print the JSON instead of archiving.",
        )
        parser.add_argument(
            "--runs", type=int, default=100, help="How many recent runs to include."
        )

    def handle(self, *args, **opts):  # noqa: ARG002
        bundle = supportbundle.build(limit_runs=opts["runs"])
        payload = supportbundle.to_json(bundle)

        if opts["print_json"]:
            self.stdout.write(payload)
            return

        path = Path(opts["output"] or f"support-bundle-{int(time.time())}.tar.gz")
        raw = payload.encode("utf-8")
        with tarfile.open(path, "w:gz") as tar:
            info = tarfile.TarInfo(name="support-bundle/bundle.json")
            info.size = len(raw)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(raw))

        failures = [c for c in bundle["health"] if not c["ok"]]
        self.stdout.write(self.style.SUCCESS(f"Wrote {path} ({path.stat().st_size} bytes)"))
        if failures:
            self.stdout.write("\nFailing checks (likely the thing you are chasing):")
            for check in failures:
                self.stdout.write(self.style.ERROR(f"  {check['label']}: {check['detail']}"))
        self.stdout.write(
            "\nThis bundle contains no report output, credentials or secrets.\n"
            "Inspect it before sending:  tar xzOf "
            f"{path} support-bundle/bundle.json | less\n"
        )
