"""Mirror the data sources a studio's repository declares into
``RepoDataSource`` rows. Called from ``apps.runner.gitsync`` after the
registry scan; nothing here reads or writes the portal-owned
``<studio>/project/data-sources/config.yaml`` (see ``materialize``)."""
from __future__ import annotations

import logging

import yaml
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.datasources.models import CONFIG_KEYS, CREDENTIAL_KEYS, RepoDataSource, name_validator

from apps.datasources.status import clear_awaiting_first_sync

logger = logging.getLogger(__name__)

#: Project-relative path of the central declaration file.
DECLARATION_FILE = "data-sources/config.yaml"

#: Keys never stored: secrets, the credential reference they hang off, and
#: the two that are columns of their own.
_DROPPED_KEYS = CREDENTIAL_KEYS | {"credentials", "local_env", "secret", "name", "type"}
#: ``type`` is free-form, so a driver the portal does not know may name its
#: secrets anything; a key containing one of these is dropped too.
_SECRET_HINTS = ("pass", "secret", "token", "key")


def _dropped(key) -> bool:
    key = str(key).lower()
    if key in CONFIG_KEYS:  # the portal's own non-secret vocabulary (ssh_host_key carries "key")
        return False
    return key in _DROPPED_KEYS or any(hint in key for hint in _SECRET_HINTS)



def _entry(name, raw, source_file: str) -> dict | None:
    """One declared source as ``{name, type, config, source_file}``, or None
    when the entry is not a usable declaration (logged, skipped)."""
    name = str(name)
    try:
        name_validator(name)
    except ValidationError:
        logger.warning("%s: skipping data source with invalid name %r", source_file, name)
        return None
    if not isinstance(raw, dict):
        logger.warning("%s: skipping data source %r: not a mapping", source_file, name)
        return None
    return {
        "name": name,
        # The framework's default when ``type`` is omitted
        # (trellum.data.connections.resolve_connection).
        "type": str(raw.get("type") or "vertica")[:64],
        "config": {k: v for k, v in raw.items() if not _dropped(k)},
        "source_file": source_file,
    }


def parse_declaration(text: str) -> list[dict]:
    """``data-sources/config.yaml`` text -> declared entries.

    Raises ``yaml.YAMLError`` or ``ValueError`` when the document is not a
    ``sources:`` mapping; an empty or absent file is an empty declaration.
    """
    data = yaml.safe_load(text)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("document is not a mapping")
    sources = data.get("sources") or {}
    if not isinstance(sources, dict):
        raise ValueError("`sources` is not a mapping")
    entries = (_entry(name, raw, DECLARATION_FILE) for name, raw in sources.items())
    return [e for e in entries if e]


def _inline_declarations(studio) -> list[dict]:
    """Dict entries with a ``name`` in each registered report's
    ``data_sources`` list; string entries reference a central source."""
    from apps.reports.models import Report

    found: list[dict] = []
    rows = Report.objects.filter(studio=studio, present_in_scan=True).values_list("slug", "config")
    for slug, config in rows:
        declared = (config or {}).get("data_sources")
        if not isinstance(declared, list):
            continue
        for raw in declared:
            if isinstance(raw, dict) and raw.get("name"):
                entry = _entry(raw["name"], raw, f"reports/{slug}/report.yaml")
                if entry:
                    found.append(entry)
    return found


def sync_repo_datasources(studio, text: str) -> int:
    """Upsert ``RepoDataSource`` rows from the central declaration ``text``
    ("" when the repository has none) plus inline ``report.yaml`` entries;
    the central file wins on a name clash. Rows that vanish are flagged
    ``present=False``, never deleted. Returns the number present.

    Parses before writing, so a malformed central file (raised as
    ``yaml.YAMLError``/``ValueError``) leaves every row untouched.
    """
    declared = parse_declaration(text) + _inline_declarations(studio)
    now = timezone.now()
    seen: set[str] = set()
    with transaction.atomic():
        for entry in declared:
            if entry["name"] in seen:
                continue
            seen.add(entry["name"])
            RepoDataSource.objects.update_or_create(
                studio=studio,
                name=entry["name"],
                defaults={
                    "type": entry["type"],
                    "config": entry["config"],
                    "source_file": entry["source_file"],
                    "present": True,
                    "last_seen_at": now,
                },
            )
        RepoDataSource.objects.filter(studio=studio, present=True).exclude(
            name__in=seen
        ).update(present=False)
        clear_awaiting_first_sync(studio)
    return len(seen)
