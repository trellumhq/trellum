"""Import a legacy ``data-sources/config.yaml`` into DataSource rows.

Secrets were never in that file (it referenced env prefixes / Secrets
Manager), so credentialed sources import as not-configured and an admin
enters user/password in the portal UI afterwards.
"""
from __future__ import annotations

import yaml

from apps.datasources.models import CONFIG_KEYS, DataSource


def import_yaml(studio, yaml_path, updated_by=None) -> tuple[list[str], list[str]]:
    """Returns (imported_names, needs_credentials_names)."""
    with open(yaml_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    imported: list[str] = []
    needs_creds: list[str] = []
    for name, entry in (raw.get("sources") or {}).items():
        entry = dict(entry or {})
        ds_type = entry.pop("type", "file")
        description = entry.pop("description", "")
        entry.pop("credentials", None)  # env-prefix refs, meaningless now
        entry.pop("local_env", None)
        entry.pop("secret", None)
        entry.pop("name", None)
        config = {k: v for k, v in entry.items() if k in CONFIG_KEYS}

        ds, _ = DataSource.objects.update_or_create(
            studio=studio,
            name=name,
            defaults={
                "type": ds_type,
                "description": description,
                "config": config,
                "updated_by": updated_by,
            },
        )
        imported.append(name)
        if not ds.is_configured:
            needs_creds.append(name)
    return imported, needs_creds
