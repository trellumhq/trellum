"""Measure one operator figure and print it as one line of JSON.

The coordinator runs this in a child process it can kill
(``apps.core.operator_observations.tick``); nothing is stored here.
"""
import json
import re
import signal

from django.core.management.base import BaseCommand, CommandError

from apps.core.operator_observations import TIMEOUT

# Credentials in a URL's userinfo, and query strings or fragments that may carry
# tokens, are never stored with a check's detail.
_URL_USERINFO = re.compile(r"(?<=://)[^/\s@]*@")
_URL_QUERY = re.compile(r"(\w+://[^\s?#]*)[?#]\S*")


def _line(value):
    """One printable line with URL secrets removed, bounded."""
    text = "".join(char if char.isprintable() else " " for char in str(value))
    text = _URL_QUERY.sub(r"\1", _URL_USERINFO.sub("", " ".join(text.split())))
    return text if len(text) <= 400 else text[:399] + "…"


class Command(BaseCommand):
    help = "Measure System health, or one organization's uploaded files, and print it as JSON."

    def add_arguments(self, parser):
        parser.add_argument("kind", choices=tuple(TIMEOUT))
        parser.add_argument("org_id", nargs="?", type=int)

    def handle(self, *args, **options):
        kind = options["kind"]
        # If the coordinator died before it could kill this process, end it anyway.
        signal.alarm(TIMEOUT[kind] + 5)
        if kind == "health":
            from apps.core.health import run_checks

            data = {"checks": [{"label": check["label"], "ok": bool(check["ok"]), "detail": _line(check["detail"])}
                               for check in run_checks()]}
        else:
            from apps.orgs.models import Organization
            from apps.orgs.quotas import storage_bytes_used

            org = Organization.objects.filter(pk=options["org_id"]).first()
            if org is None:
                raise CommandError("Unknown organization")
            data = {"bytes": storage_bytes_used(org)}
        self.stdout.write(json.dumps(data, separators=(",", ":")))
