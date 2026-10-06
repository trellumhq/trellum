"""Read/write _meta.json for report output directories."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

#: Schema version of the ``_meta.json`` artifact. Bump on any breaking change
#: to its shape; consumers outside this repo read it to decide
#: whether they understand the file. See docs/COMPATIBILITY.md.
#:
#: v2 (semantic-layer Phase 2): ``metrics_used`` entries became objects
#: (``{"id", "definition_hash", "version"}``) instead of bare id strings, so
#: a host can tell a claimed metric's build-time definition apart from its
#: current one without opening data.json. See :func:`normalize_metrics_used`.
META_SCHEMA_VERSION = 2


def read_meta(output_dir: str) -> dict:
    """Read _meta.json from a report output directory. Returns {} if missing.

    Defensive: also handles the case where the file on disk is gzipped
    (e.g. left over from a transfer that did not decompress
    Content-Encoding: gzip objects). The first two bytes of a gzip stream
    are always 0x1f 0x8b -- check for that and decompress before parsing
    so a corrupt file does not crash the read.
    """
    meta_path = os.path.join(output_dir, "_meta.json")
    if not os.path.isfile(meta_path):
        return {}
    with open(meta_path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        import gzip as _gzip
        raw = _gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def write_meta(output_dir: str, updates: dict) -> None:
    """Merge updates into existing _meta.json (or create it)."""
    meta_path = os.path.join(output_dir, "_meta.json")
    meta = read_meta(output_dir)
    meta.setdefault("schema_version", META_SCHEMA_VERSION)
    meta.update(updates)
    os.makedirs(output_dir, exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)


def write_error(output_dir: str, error_msg: str) -> str:
    """Record an error in _meta.json. Returns the ISO timestamp."""
    now = datetime.now(timezone.utc).isoformat()
    write_meta(
        output_dir,
        {
            "last_run": now,
            "last_status": "error",
            "last_error": error_msg[:500],
        },
    )
    return now


def normalize_metrics_used(entries) -> list[dict]:
    """``_meta.json``'s ``metrics_used`` list, coerced to the documented v2
    shape: ``[{"id", "definition_hash", "version"}]``, deduplicated by id and
    sorted by it.

    A host reads a report's claimed metrics through this function rather
    than the raw key so it never has to branch on schema version itself:

    - **v2** (current): entries are already ``{"id", "definition_hash",
      "version"}`` objects -- passed through, missing ``definition_hash``/
      ``version`` read as ``None``.
    - **v1** (``schema_version`` 1 or absent): entries are bare id strings.
      Each normalizes to ``{"id": <id>, "definition_hash": None, "version":
      None}`` -- there is nothing else in a v1 artifact to read them from, so
      "unknown" is the honest answer, not a guessed ``0``/``""``.

    A dict entry always wins over a bare-id entry for the same id, whichever
    order they appear in -- defensive, since nothing should ever mix the two
    shapes in one list, but a richer entry silently losing to a poorer one
    would be a strange way to fail if something did.
    """
    seen: dict[str, dict] = {}
    for entry in entries or []:
        if isinstance(entry, dict):
            metric_id = entry.get("id")
            if not metric_id:
                continue
            seen[str(metric_id)] = {
                "id": str(metric_id),
                "definition_hash": entry.get("definition_hash"),
                "version": entry.get("version"),
            }
        elif entry:
            metric_id = str(entry)
            seen.setdefault(
                metric_id,
                {"id": metric_id, "definition_hash": None, "version": None},
            )
    return [seen[k] for k in sorted(seen)]


def is_fresh(output_dir: str, max_age: int) -> bool:
    """Check if report output is fresh (successful and younger than max_age seconds)."""
    meta = read_meta(output_dir)
    last_run = meta.get("last_run")
    if not last_run or meta.get("last_status") != "success":
        return False
    last_dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - last_dt).total_seconds()
    return age < max_age
