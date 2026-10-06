"""AI assistant data catalog — one studio's built reports.

Ported from the legacy single-tenant portal, scoped to a studio instead of the
single process-wide project root. Builds a compact, model-readable inventory
of every built report: description, datasets, columns, row counts and the
actual date coverage of each dataset. It goes into the (cached) system prompt
so the assistant can route a question to the right report + dataset in ONE
tool call instead of exploring report-by-report.

Per-report entries are cached keyed on (studio, slug) plus the data files'
mtimes, so repeated prompt builds are cheap and the catalog refreshes itself
when a report is re-run.
"""
from __future__ import annotations

import re
from threading import Lock

_CACHE: dict[tuple[int, str], tuple[tuple, str]] = {}
_LOCK = Lock()

_DATE_COL_RE = re.compile(r"date|week|month|day", re.IGNORECASE)

#: Framework-owned top-level data.json keys — everything else non-underscore
#: is custom RawHTML data (hybrid dashboards like a live pulse).
_FRAMEWORK_KEYS = {"components", "defaultScope"}


def _date_range(payload: dict) -> tuple[str, str, str] | None:
    """(column, min, max) for the first date-like column, or None."""
    cols = payload.get("_cols") or []
    rows = payload.get("_data") or []
    dct = payload.get("_dict") or {}
    for idx, col in enumerate(cols):
        if not _DATE_COL_RE.search(col):
            continue
        if col in dct:
            values = [v for v in dct[col] if isinstance(v, str) and v]
        else:
            values = [r[idx] for r in rows if isinstance(r[idx], str) and r[idx]]
        sample = values[0] if values else ""
        if not re.match(r"^\d{4}-\d{2}", str(sample)):
            continue
        if values:
            return col, min(values), max(values)
    return None


def _records_date_range(records: list[dict]) -> tuple[str, str, str] | None:
    """Same as _date_range but for a list-of-dicts (custom RawHTML data)."""
    if not records:
        return None
    for col in records[0].keys():
        if not _DATE_COL_RE.search(str(col)):
            continue
        values = [r.get(col) for r in records
                  if isinstance(r.get(col), str) and r.get(col)]
        if values and re.match(r"^\d{4}-\d{2}", values[0]):
            return str(col), min(values), max(values)
    return None


def _custom_dataset_lines(data: dict, scope: str) -> list[str]:
    """Catalog lines for custom RawHTML data blocks (lists of records)."""
    lines = []
    for key, payload in data.items():
        if key.startswith("_") or key in _FRAMEWORK_KEYS:
            continue
        if isinstance(payload, list):
            candidates = {key: payload}
        elif isinstance(payload, dict):
            candidates = {f"{key}.{k}": v for k, v in payload.items()}
        else:
            continue
        for cid, rows in candidates.items():
            if not (isinstance(rows, list) and rows and isinstance(rows[0], dict)):
                continue
            cols = list(rows[0].keys())
            rng = _records_date_range(rows)
            rng_txt = f"; {rng[0]} {rng[1]} → {rng[2]}" if rng else ""
            lines.append(
                f"- dataset `{cid}`{scope}: {len(rows):,} rows{rng_txt}; "
                f"columns: {', '.join(map(str, cols))}"
            )
    return lines


def _report_entry(toolbox, report: dict) -> str:
    slug = report.get("slug") or ""
    lines = [f"### {report.get('name')} — slug `{slug}` — link {toolbox.link(slug)}"]
    desc = (report.get("description") or "").strip()
    if desc:
        lines.append(desc)
    meta_bits = []
    if report.get("category"):
        meta_bits.append(f"category: {report['category']}")
    if report.get("tags"):
        meta_bits.append("tags: " + ", ".join(map(str, report["tags"])))
    if report.get("last_status"):
        meta_bits.append(f"last run: {report.get('last_status')}")
    if meta_bits:
        lines.append("(" + " | ".join(meta_bits) + ")")

    meta = toolbox.read_meta(slug)
    details = (meta or {}).get("details") or {}
    if details.get("data_source") == "mock":
        lines.append("⚠ MOCK DATA — last build was a test run, numbers are synthetic.")

    for f in toolbox.data_files(slug):
        # Shared with the tools for the rest of this turn -- the catalog is
        # usually the first thing to touch a report's data.json and every
        # query that follows would otherwise pay the parse again.
        data = toolbox.read_data(f)
        if data is None:
            lines.append(f"- (data file {f.name} could not be indexed)")
            continue
        scope = ""
        m = re.fullmatch(r"data_(.+)\.json", f.name)
        if m and not m.group(1).startswith("chunk_"):
            scope = f" [scope {m.group(1)}]"
        for key, payload in data.items():
            if not key.startswith("_ds_") or not isinstance(payload, dict):
                continue
            ds_id = key[4:]
            cols = payload.get("_cols") or []
            n = len(payload.get("_data") or [])
            rng = _date_range(payload)
            rng_txt = f"; {rng[0]} {rng[1]} → {rng[2]}" if rng else ""
            chunked = ""
            if ds_id in (data.get("_chunk_manifests") or {}):
                chunked = " (chunked: older periods not inline)"
            lines.append(
                f"- dataset `{ds_id}`{scope}: {n:,} rows{rng_txt}{chunked}; "
                f"columns: {', '.join(cols)}"
            )
        lines.extend(_custom_dataset_lines(data, scope))
    # Report names and descriptions are author-controlled text going into a
    # system prompt; the tag marks them as data the HEADER tells the model
    # never to take instructions from.
    return f'<data source="report:{slug}">\n' + "\n".join(lines) + "\n</data>"


def build_catalog(toolbox) -> str:
    """Markdown catalog of every report in the toolbox's studio with output."""
    studio_pk = toolbox.studio.pk
    entries = []
    reports = toolbox.reports()
    for r in sorted(reports, key=lambda r: (r.get("category") or "", r.get("slug") or "")):
        slug = r.get("slug") or ""
        files = toolbox.data_files(slug)
        if not files:
            continue  # nothing built — not answerable from output
        # Size as well as mtime: st_mtime_ns has nanosecond *units* but not
        # nanosecond *resolution*. On several filesystems (notably bind mounts
        # and some network mounts) two writes inside the same coarse tick share
        # a timestamp, and a rebuild would then serve a stale catalog.
        mtime_key = tuple(
            (f.name, st.st_mtime_ns, st.st_size)
            for f, st in ((f, f.stat()) for f in files)
        )
        cache_key = (studio_pk, slug)
        with _LOCK:
            cached = _CACHE.get(cache_key)
            if cached and cached[0] == mtime_key:
                entries.append(cached[1])
                continue
        entry = _report_entry(toolbox, r)
        with _LOCK:
            _CACHE[cache_key] = (mtime_key, entry)
        entries.append(entry)
    # ponytail: evict by diffing against the live registry rather than keying
    # on the studio's latest run -- same effect (a removed report's entry
    # goes the next time the catalog is built) with no extra query.
    if not toolbox.is_report_bound:
        current = {r.get("slug") or "" for r in reports}
        with _LOCK:
            for key in [k for k in _CACHE if k[0] == studio_pk and k[1] not in current]:
                _CACHE.pop(key, None)
    return "\n\n".join(entries)


def invalidate(studio=None) -> None:
    """Drop cached entries (whole cache, or one studio's)."""
    with _LOCK:
        if studio is None:
            _CACHE.clear()
        else:
            for key in [k for k in _CACHE if k[0] == studio.pk]:
                _CACHE.pop(key, None)
