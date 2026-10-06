"""The annotations calendar: everything that happened, per studio.

Reads the same ``events.yaml`` the framework loads for chart markers
(``trellum/runner.py:_load_events``) plus per-report ``annotations.events``
(``trellum/runner.py:local_annotation_events``) and merges them into one
studio-wide, full-history view.

Deliberate differences from the framework's chart-annotation loading:

- **No 90-day cutoff.** Charts only need recent markers; this page is a
  historical record, so a three-year-old event is shown same as yesterday's.
- **No scope-based exclusion.** The framework drops events whose ``studio``
  field doesn't match the report/scope reading it. Here every event is
  shown regardless of scope — the scope is a badge and a filter pill, never
  a reason an event disappears from its own studio's calendar.

Terminology: the ``studio:`` key inside events.yaml is never called "studio"
in this module's output or in the UI — it is presented as **"scope"**
(in-studio report scoping: which reports' charts show the event; ``shared``
means all of them). "Studio" is reserved for the portal studio concept.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import yaml
from django.shortcuts import render

from apps.core import roles
from apps.core.permissions import require_studio_role
from apps.core.report_access import require_full_studio_visibility

#: Soft limit: above this many merged events the template shows a warning
#: banner. Events are never truncated — this is a "your events.yaml has
#: grown large" nudge, not a page-breaking limit.
TOO_MANY_EVENTS = 5000

#: Display label + CSS class suffix per known type, mirroring the framework's
#: chart-annotation legend (``_ANNO_STYLES`` in js_runtime.py) so the pills
#: read the same words as the chart toggle bar. ``ab_test`` gets the short
#: "ab" CSS suffix to match the annotation-color custom properties, which are
#: also duplicated verbatim from the framework in annotations.html.
_TYPE_META: dict[str, dict[str, str]] = {
    "campaign": {"label": "Campaigns", "css": "campaign"},
    "ab_test": {"label": "A/B Tests", "css": "ab"},
    "release": {"label": "Releases", "css": "release"},
    "incident": {"label": "Incidents", "css": "incident"},
    "event": {"label": "Events", "css": "event"},
}
#: Stable pill order for known types; anything else is appended after,
#: alphabetically, at render time.
_TYPE_ORDER = ("campaign", "ab_test", "release", "incident", "event")
#: An unrecognized type falls back to the release color/CSS class — the same
#: fallback the framework's chart renderer uses (js_runtime.py ~line 796) —
#: but keeps its own name as the label, since the label is what makes an
#: unrecognized type distinguishable at all (color alone never carries type).
_FALLBACK_CSS = "release"


def _type_meta(event_type: str) -> dict[str, str]:
    meta = _TYPE_META.get(event_type)
    if meta:
        return meta
    return {"label": event_type.replace("_", " ").title() or "Event", "css": _FALLBACK_CSS}


def _normalize_date(value: Any) -> str | None:
    """Coerce a YAML date value to ``YYYY-MM-DD``, or ``None`` if unparseable.

    PyYAML's safe_load auto-converts an unquoted ``2024-03-03`` into a
    ``datetime.date`` (YAML 1.1 timestamp resolution); a quoted string comes
    through as ``str``. Both are accepted, same as the framework's loader.
    """
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        return _dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _fmt_day(iso_date: str) -> str:
    d = _dt.date.fromisoformat(iso_date)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def _display_dates(date_iso: str, end_date_iso: str | None) -> str:
    if end_date_iso and end_date_iso != date_iso:
        return f"{_fmt_day(date_iso)} – {_fmt_day(end_date_iso)}"
    return _fmt_day(date_iso)


def _resolve_visible(
    event: dict, event_type: str, type_defaults: dict[str, bool]
) -> bool:
    """Visibility priority: per-event ``default_visible`` > ``type_defaults``
    > True. Mirrors ``_load_events`` minus the report.yaml override, which
    has no equivalent here (the calendar has no single "current report")."""
    if "default_visible" in event:
        return bool(event["default_visible"])
    return bool(type_defaults.get(event_type, True))


def _build_event(
    *,
    date_iso: str,
    end_date_iso: str,
    label: str,
    event_type: str,
    scope: str,
    visible_default: bool,
    source: str,
    source_label: str,
    report_url: str = "",
) -> dict:
    """The one place a calendar event dict is assembled -- both
    :func:`load_studio_events` and :func:`report_config_events` funnel their
    already-normalized fields through here, so the 12-key shape can't drift
    between the two sources."""
    meta = _type_meta(event_type)
    return {
        "date": date_iso,
        "end_date": end_date_iso,
        "label": label,
        "type": event_type,
        "type_label": meta["label"],
        "type_css": meta["css"],
        "scope": scope,
        "source": source,
        "source_label": source_label,
        "report_url": report_url,
        "visible_default": visible_default,
        "dates_display": _display_dates(date_iso, end_date_iso),
    }


def load_studio_events(studio) -> tuple[list[dict], dict[str, bool], str]:
    """Parse ``<project_root>/events.yaml``.

    Returns ``(events, type_defaults, parse_error)``. A missing file is not
    an error (``""``, empty list) — most studios simply have no events yet.
    A malformed file surfaces its message but still returns whatever the
    caller can build the page around (an empty list): the view stays a 200
    with a banner, never a 500.
    """
    path = Path(studio.project_root) / "events.yaml"
    if not path.is_file():
        return [], {}, ""

    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
    except Exception as exc:  # noqa: BLE001 - surfaced to the page, not raised
        return [], {}, str(exc)

    if not isinstance(data, dict):
        return [], {}, "events.yaml must be a mapping with an `events:` list"

    type_defaults: dict[str, bool] = {}
    for key, val in (data.get("type_defaults") or {}).items():
        type_defaults[str(key)] = bool(val)

    events: list[dict] = []
    for raw_event in data.get("events") or []:
        if not isinstance(raw_event, dict):
            continue
        date_iso = _normalize_date(raw_event.get("date"))
        if date_iso is None:
            continue  # no parseable date -> dropped, per spec
        end_date_iso = _normalize_date(raw_event.get("end_date")) or ""
        event_type = str(raw_event.get("type") or "release")
        scope = str(raw_event.get("studio") or "shared")
        events.append(
            _build_event(
                date_iso=date_iso,
                end_date_iso=end_date_iso,
                label=str(raw_event.get("label") or ""),
                event_type=event_type,
                scope=scope,
                visible_default=_resolve_visible(raw_event, event_type, type_defaults),
                source="events.yaml",
                source_label="events.yaml",
            )
        )
    return events, type_defaults, ""


def report_config_events(studio) -> list[dict]:
    """Per-report ``annotations.events`` from ``report.yaml`` (``Report.config``).

    Parsing is delegated to the framework's own
    ``trellum.runner.local_annotation_events`` (the portal already imports
    from ``trellum.runner`` in production -- see
    ``apps.reports.scan.sync_studio_registry``) rather than re-implemented
    here, so this can never quietly diverge from what the framework actually
    does with a report's ``annotations.events`` -- e.g. its ``studio``
    resolution substitutes the report's default *only when the key is
    absent* (``ev.get("studio", studio)``), which a naive
    ``ev.get("studio") or studio`` here would get wrong for an event that
    explicitly sets ``studio: ""``. Only the calendar-specific enrichment
    (type label/CSS, source, report link, date formatting) is layered on
    top, via :func:`_build_event`.

    Only reports the registry currently knows about (``present_in_scan``) are
    read -- a report that has been deleted from the repo shouldn't keep
    contributing calendar rows for a page that says "here's what your studio
    currently has".
    """
    from apps.reports.models import Report
    from trellum.runner import local_annotation_events

    out: list[dict] = []
    reports = Report.objects.filter(studio=studio, present_in_scan=True).only(
        "slug", "config", "studio_id"
    )
    for report in reports:
        config = report.config if isinstance(report.config, dict) else {}
        raw_events = local_annotation_events(config)
        if not raw_events:
            continue
        report_url = (
            f"/s/{studio.org.slug}/{studio.slug}/r/{report.slug}/?display=console"
        )
        source_label = f"report: {report.slug}"
        for raw in raw_events:
            # The framework already resolved date/end_date to str, type
            # (default "event"), studio (default-substituted only when
            # absent), and default_visible (bool, default True) -- the
            # calendar's only extra requirement is a strictly parseable
            # ISO date, which local_annotation_events does not itself
            # enforce (it only checks the date is truthy).
            date_iso = _normalize_date(raw["date"])
            if date_iso is None:
                continue
            end_date_iso = _normalize_date(raw["end_date"]) or ""
            out.append(
                _build_event(
                    date_iso=date_iso,
                    end_date_iso=end_date_iso,
                    label=raw["label"],
                    event_type=raw["type"],
                    scope=raw["studio"],
                    visible_default=raw["default_visible"],
                    source=f"report:{report.slug}",
                    source_label=source_label,
                    report_url=report_url,
                )
            )
    return out


def _counts(events: list[dict], key: str, *, order: tuple[str, ...] = ()) -> list[dict]:
    tally: dict[str, int] = {}
    for ev in events:
        tally[ev[key]] = tally.get(ev[key], 0) + 1
    ordered_keys = [k for k in order if k in tally]
    ordered_keys += sorted(k for k in tally if k not in order)
    return [{"value": k, "count": tally[k]} for k in ordered_keys]


def calendar_payload(studio) -> dict:
    """The merged, sorted view this whole page renders from.

    Full history, no dedupe across sources (an event.yaml entry and a
    report's own annotations.events entry are two different rows even if
    they happen to describe the same real-world thing -- the calendar shows
    what was declared, not a guess about what refers to what).
    """
    events_yaml, type_defaults, parse_error = load_studio_events(studio)
    report_events = report_config_events(studio)
    merged = events_yaml + report_events
    merged.sort(key=lambda ev: (ev["date"], ev["label"]))

    type_counts = _counts(merged, "type", order=_TYPE_ORDER)
    for row in type_counts:
        meta = _type_meta(row["value"])
        row["label"] = meta["label"]
        row["css"] = meta["css"]

    scope_counts = _counts(merged, "scope", order=("shared",))

    return {
        "events": merged,
        "list_groups": _group_by_month(merged),
        "type_counts": type_counts,
        "scope_counts": scope_counts,
        "total_count": len(merged),
        "type_defaults": type_defaults,
        "parse_error": parse_error,
        "too_many": len(merged) > TOO_MANY_EVENTS,
    }


def _group_by_month(events: list[dict]) -> list[dict]:
    """Events grouped by the month of their start date, newest month first,
    newest event first within a group -- the list view's reading order (and
    the no-JS fallback, so it has to make sense read top to bottom on its
    own)."""
    buckets: dict[tuple[int, int], list[dict]] = {}
    for ev in events:
        d = _dt.date.fromisoformat(ev["date"])
        buckets.setdefault((d.year, d.month), []).append(ev)

    groups = []
    for (year, month) in sorted(buckets.keys(), reverse=True):
        bucket_events = sorted(buckets[(year, month)], key=lambda ev: ev["date"], reverse=True)
        month_label = _dt.date(year, month, 1).strftime("%B %Y")
        groups.append({"month_label": month_label, "events": bucket_events})
    return groups


#: Keys annotations_calendar.js actually reads. `list_groups` duplicates
#: `events` (the same dicts re-nested by month) purely for the server-side
#: list-view render below, and `total_count`/`parse_error` never leave
#: Python -- embedding the full payload as JSON as well as rendering every
#: row as HTML was ~3x the page weight at the 5000-event soft cap. The JS
#: re-filters the server-rendered list rows by their data-* attributes
#: rather than reading list_groups from JSON, so list_groups need only ever
#: exist once, as HTML.
_CLIENT_PAYLOAD_KEYS = ("events", "type_counts", "scope_counts", "type_defaults", "too_many")


@require_studio_role(roles.VIEWER)
@require_full_studio_visibility
def calendar_page(request, org_slug, studio_slug):  # noqa: ARG001
    payload = calendar_payload(request.studio)
    client_payload = {k: payload[k] for k in _CLIENT_PAYLOAD_KEYS}
    return render(
        request,
        "reports/annotations.html",
        {
            "org": request.org,
            "studio": request.studio,
            # `payload`: full context for server-side rendering (list_groups,
            # total_count, parse_error, the type/scope pills).
            "payload": payload,
            # `client_payload`: embedded via
            # `{{ client_payload|json_script:"calendar-data" }}` -- json_script
            # does its own json.dumps, so the raw dict goes in, not a
            # pre-serialized string.
            "client_payload": client_payload,
        },
    )
