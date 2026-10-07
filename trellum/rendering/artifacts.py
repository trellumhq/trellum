"""The files written beside index.html: manifest, meta, data chunks.

Each is a contract with something outside this repo -- a phone's home
screen, a host reading _meta.json, a browser fetching a chunk on scroll --
so they are gathered here rather than trailing off the end of the render.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import orjson

import trellum
from trellum.licensing import LICENSE_ID, source_url, write_output_license
from trellum.meta import META_SCHEMA_VERSION, normalize_metrics_used


def _write_manifest(out: str, slug: str, name: str) -> None:
    """Write a per-report Web App Manifest to ``manifest.json``."""
    icon_data_uri = (
        "data:image/svg+xml,"
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'>"
        "<rect width='40' height='40' rx='8' fill='white'/>"
        "<g stroke='%23c7cbe0' stroke-width='3'>"
        "<line x1='8' y1='8' x2='8' y2='32'/>"
        "<line x1='20' y1='8' x2='20' y2='32'/>"
        "<line x1='32' y1='8' x2='32' y2='32'/></g>"
        "<circle cx='8' cy='32' r='5' fill='%230D9488'/>"
        "<circle cx='20' cy='20' r='5' fill='%237C6FE0'/>"
        "<circle cx='32' cy='8' r='5' fill='%230D9488'/></svg>"
    )
    short = name if len(name) <= 12 else name[:12]
    # start_url and scope are relative to the manifest's own URL, not the
    # origin root: the same artifact is served standalone at /<slug>/ and
    # through the portal at /r/<slug>/ (and under studio prefixes). An
    # absolute /<slug>/ scope is out-of-scope for the portal-served
    # document, which makes Chrome drop start_url/scope with a console
    # warning. Relative values resolve correctly under any prefix.
    manifest = {
        "name": name,
        "short_name": short,
        "start_url": "index.html",
        "scope": "./",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#0D9488",
        "theme_color": "#0D9488",
        "icons": [
            {
                "src": icon_data_uri,
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any",
            }
        ],
    }
    import json as _json
    manifest_path = os.path.join(out, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        _json.dump(manifest, f, indent=2)

def _gc_stale_chunks(output_dir: str, canonical_filenames: set[str]) -> None:
    """Delete ``data_chunk_*.json`` files that the current run did not produce.

    The framework writes chunk files per-DataSource per-period at every
    run, but never deletes chunks for periods (or DataSource ids) that
    are no longer in the current report. Result: ``output/<slug>/`` grows
    monotonically over the lifetime of a report, including chunks from
    DataSources that were removed in later refactors.

    ``canonical_filenames`` is the set of ``data_chunk_*.json`` filenames
    this run wrote. Anything else matching the pattern is stale and gets
    removed. Logs a single line summarising the cleanup when something
    was removed; silent at steady state.
    """
    if not os.path.isdir(output_dir):
        return
    removed = 0
    bytes_freed = 0
    for fname in os.listdir(output_dir):
        if not (fname.startswith("data_chunk_") and fname.endswith(".json")):
            continue
        if fname in canonical_filenames:
            continue
        path = os.path.join(output_dir, fname)
        try:
            sz = os.path.getsize(path)
            os.remove(path)
            removed += 1
            bytes_freed += sz
        except OSError:
            # Best-effort GC — a failure here should never break the run.
            continue
    if removed:
        mb = bytes_freed / (1024 * 1024)
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(
            f"[{ts}] [fw] Removed {removed} stale chunk file(s) "
            f"({mb:.2f} MB freed) from {output_dir}",
            flush=True,
        )

def _write_meta(
    out: str, slug: str, name: str, config: dict,
    *, validation=None, timings: dict | None = None,
    details: dict | None = None,
    metrics_used: list[dict] | list[str] | None = None,
) -> None:
    # Card metadata for whatever lists these reports -- icon, colour, sort
    # order. The framework reads none of it; it only carries it through.
    display = config.get("display") or {}

    meta = {
        "schema_version": META_SCHEMA_VERSION,
        "slug": slug,
        "kind": config.get("kind", "report"),
        "author": config.get("author", "") if config.get("kind") == "analysis" else "",
        "name": config.get("name", name),
        "description": config.get("description", ""),
        "version": config.get("version", "0.1.0"),
        "studio": config.get("studio", ""),
        "category": config.get("category", ""),
        "tags": config.get("tags", []),
        "schedule": config.get("schedule", {}).get("cron", ""),
        "last_run": datetime.now(timezone.utc).isoformat(),
        "last_status": "success",
        "last_error": None,
        "display": display,
        "framework_version": trellum.__version__,
        "framework_license": LICENSE_ID,
        "framework_source_url": source_url(),
        # Which metrics.yaml metrics this build claims, and what it baked in
        # for each: {"id", "definition_hash", "version"}. Always present
        # (empty when none) so a host can index reports by metric -- and
        # compare a claim's build-time definition against the current one --
        # without opening data.json. Accepts bare ids too (a caller that
        # only has the id, or a v1-shaped list passed straight through);
        # see trellum.meta.normalize_metrics_used and docs/COMPATIBILITY.md.
        "metrics_used": normalize_metrics_used(metrics_used),
    }
    if timings:
        meta["generation_timings"] = timings
    if validation is not None:
        meta["validation"] = {
            "summary": validation.summary,
            "checks": [
                {k: v for k, v in {
                    "id": c.id, "level": c.level, "message": c.message,
                    "component": c.component or None,
                    "section": c.section or None,
                    "dataset_id": c.dataset_id or None,
                    "suppressed": True if c.suppressed else None,
                }.items() if v is not None}
                for c in validation.checks
            ],
        }
    if details is not None:
        meta["details"] = details
    meta_path = os.path.join(out, "_meta.json")
    with open(meta_path, "wb") as f:
        f.write(orjson.dumps(
            meta,
            option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS,
        ))
    write_output_license(out)


def _write_live_queries(out: str, live_queries: dict) -> None:
    """Write ``_live_queries.json``: the server-side half of a live lookup.

    Schema v1, a contract with the serving host (see docs/COMPATIBILITY.md)::

        {"version": 1, "queries": {"<query_id>": {
            "sql": "...", "datasource": "...",
            "params": [{"name": ..., "type": ..., "required": ...}]}}}

    This is the ONLY artifact that carries the SQL. data.json and index.html
    get the query id and the param schema, never the query text — a report
    published anywhere stays a snapshot that says nothing about the warehouse.

    Written only when the report declared queries; a manifest left over from
    a build that no longer declares any is removed, so the file's presence
    means exactly "this build has live queries".
    """
    path = os.path.join(out, "_live_queries.json")
    if not live_queries:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    manifest = {
        "version": 1,
        "queries": {
            qid: {
                "sql": q["sql"],
                "datasource": q["datasource"],
                "params": q["params"],
            }
            for qid, q in live_queries.items()
        },
    }
    with open(path, "wb") as f:
        f.write(orjson.dumps(manifest, option=orjson.OPT_INDENT_2))

def _cron_to_seconds(cron: str) -> int:
    if not cron:
        return 0
    parts = cron.strip().split()
    if len(parts) < 5:
        return 0
    minute_part = parts[0]
    if minute_part.startswith("*/"):
        try:
            return int(minute_part[2:]) * 60
        except ValueError:
            return 0
    if minute_part == "0" and parts[1] == "*":
        return 3600
    return 0
