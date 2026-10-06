"""Timeline events and weekday highlighting, config to annotations."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml

from trellum.project import get_project_root

_events_missing_noted = False

def _load_events(config: dict, ctx: Any = None) -> list[dict]:
    """Load events from events.yaml at the project root (optional).

    If events.yaml does not exist, annotations are silently skipped -- that is a
    project that never opted in. If it exists but is malformed, the build FAILS:
    a file that is present is a file someone meant to use, and dropping every
    annotation while still exiting 0 is indistinguishable from having no events.

    Returns only events whose date falls within the last 90 days of the
    REPORT'S clock (``ctx``, which honours FW_NOW) and whose ``studio``
    field matches one the report can legitimately show. The wall clock is
    only a fallback: judged against it, a pinned or backfilled build would
    silently lose annotations the moment real time drifted 90 days past
    the data -- the same failure shape FW_NOW exists to prevent.

    Studio filter semantics:
    - Events with ``studio: shared`` always pass (cross-product events).
    - Events matching the report's top-level ``studio`` field pass.
    - For multi-scope reports that declare scopes via ``ctx.set_scope``,
      events matching any scope name also pass. Example: a shared report
      with scopes ``gop3`` and ``monop`` receives events from gop3, monop,
      and shared, and each event carries its originating ``studio`` so
      the client can hide gop3 events when the user is viewing the monop
      scope (see js_runtime ``_buildAnnotations``).

    Visibility priority (highest wins):
    1. ``report.yaml`` ``annotations.default_visible`` list
    2. Per-event ``default_visible`` field in events.yaml
    3. ``type_defaults`` section in events.yaml (e.g. ``campaign: false``)
    4. ``True`` (visible)
    """
    anno_cfg = config.get("annotations", True)
    if anno_cfg is False:
        return []

    # When annotations is a dict, extract the per-report type visibility overrides
    report_visible_types: list[str] | None = None
    if isinstance(anno_cfg, dict):
        dv = anno_cfg.get("default_visible")
        if isinstance(dv, list):
            report_visible_types = [str(t) for t in dv]

    project_root = get_project_root()
    events_path = os.path.join(project_root, "events.yaml")
    if not os.path.exists(events_path):
        global _events_missing_noted  # noqa: PLW0603
        if not _events_missing_noted:
            _events_missing_noted = True
            print("  (no events.yaml found -- chart annotations disabled)")
        return []

    # A malformed events.yaml used to warn and carry on, which produced a report
    # that built cleanly, exited 0, and was silently missing every annotation --
    # the worst shape of failure, because nothing downstream can tell it from a
    # report that legitimately has no events. It is a hard error now, matching
    # data-sources/config.yaml, which has always propagated its parse errors.
    #
    # The escape hatch is explicit rather than a flag: delete or rename the file,
    # or set `annotations: false` in report.yaml.
    try:
        with open(events_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise RuntimeError(
            f"events.yaml is not valid YAML, so chart annotations cannot be "
            f"loaded:\n  {events_path}\n  {exc}\n"
            f"  Fix the file, or delete it to build without annotations."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"events.yaml exists but could not be read:\n  {events_path}\n  {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            f"events.yaml must contain a YAML mapping, not "
            f"{type(data).__name__}:\n  {events_path}\n"
            f"  Expected top-level keys such as 'events:' and 'type_defaults:'."
        )

    raw_events = data.get("events") or []
    if not isinstance(raw_events, list):
        raise RuntimeError(
            f"events.yaml: 'events' must be a list, not "
            f"{type(raw_events).__name__}:\n  {events_path}"
        )
    if not raw_events:
        return []

    # Global per-type defaults from events.yaml (e.g. campaign: false)
    raw_type_defaults = data.get("type_defaults") or {}
    if not isinstance(raw_type_defaults, dict):
        raise RuntimeError(
            f"events.yaml: 'type_defaults' must be a mapping of event type to "
            f"boolean, not {type(raw_type_defaults).__name__}:\n  {events_path}"
        )
    type_defaults: dict[str, bool] = {}
    for k, v in raw_type_defaults.items():
        type_defaults[str(k)] = bool(v)

    studio = config.get("studio", "shared")
    now = getattr(ctx, "_now_utc", None) if ctx is not None else None
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=90)).strftime("%Y-%m-%d")

    # A multi-scope report can display events from any of its scopes
    # (e.g. a shared report with scopes gop3+monop receives events from
    # studios gop3, monop, shared). Non-scoped reports see only their
    # own studio + shared.
    allowed_studios: set[str] = {"shared", studio}
    if ctx is not None:
        try:
            allowed_studios.update(ctx.scopes.keys())
        except Exception:
            pass

    filtered = []
    for ev in raw_events:
        ev_studio = ev.get("studio", "shared")
        if ev_studio not in allowed_studios:
            continue
        ev_date = str(ev.get("date", ""))
        if ev_date < cutoff:
            continue

        ev_type = ev.get("type", "release")

        # Priority: report.yaml override > per-event field > type_defaults > True
        if report_visible_types is not None:
            default_vis = ev_type in report_visible_types
        elif "default_visible" in ev:
            default_vis = bool(ev["default_visible"])
        else:
            default_vis = type_defaults.get(ev_type, True)

        # The client keeps the origin studio so a scoped report can hide
        # events from other scopes when the user switches tabs. Its filter
        # knows two categories: "shared" (always shown) and "a scope name"
        # (shown on that scope). An event belonging to the REPORT'S OWN
        # studio is neither -- the business the report belongs to, already
        # admitted by the server-side filter above -- and without this
        # rewrite a scoped view would hide its own business's events.
        client_studio = "shared" if ev_studio == studio else ev_studio

        filtered.append({
            "date": ev_date,
            "end_date": str(ev.get("end_date", "")),
            "label": ev.get("label", ""),
            "type": ev_type,
            "studio": client_studio,
            "default_visible": default_vis,
        })

    return filtered

def local_annotation_events(config: dict) -> list[dict]:
    """Report-scoped annotation events from report.yaml ``annotations.events``.

    Merged into ``_events`` after global events.yaml loading. Not subject to the
    90-day cutoff — dates are declared explicitly per report.
    """
    anno_cfg = config.get("annotations")
    if not isinstance(anno_cfg, dict):
        return []
    raw = anno_cfg.get("events") or []
    if not raw:
        return []
    studio = config.get("studio", "shared")
    out: list[dict] = []
    for ev in raw:
        if not ev.get("date"):
            continue
        out.append({
            "date": str(ev["date"]),
            "end_date": str(ev.get("end_date", "")),
            "label": str(ev.get("label", "")),
            "type": str(ev.get("type", "event")),
            "studio": str(ev.get("studio", studio)),
            "default_visible": bool(ev.get("default_visible", True)),
        })
    return out

# JS Date.getUTCDay() convention: Sunday=0 .. Saturday=6.
_WEEKDAY_NAME_TO_IDX = {
    "sunday": 0, "sun": 0, "su": 0,
    "monday": 1, "mon": 1, "mo": 1,
    "tuesday": 2, "tue": 2, "tues": 2, "tu": 2,
    "wednesday": 3, "wed": 3, "we": 3,
    "thursday": 4, "thu": 4, "thur": 4, "thurs": 4, "th": 4,
    "friday": 5, "fri": 5, "fr": 5,
    "saturday": 6, "sat": 6, "sa": 6,
}

_WEEKDAY_PLURAL = {
    0: "Sundays", 1: "Mondays", 2: "Tuesdays", 3: "Wednesdays",
    4: "Thursdays", 5: "Fridays", 6: "Saturdays",
}

def _weekday_highlight_config(config: dict) -> dict | None:
    """Parse ``annotations.weekday_highlight`` from report.yaml.

    Returns a client-ready dict ``{days, label, default_visible}`` where
    ``days`` is a sorted list of weekday indices (Sun=0..Sat=6, matching
    JS ``Date.getUTCDay()``), or ``None`` when not configured / invalid.

    Accepts day names ("Wed", "Wednesday"), or integers 0-6. ``days`` may
    be a single value or a list. Example report.yaml::

        annotations:
          weekday_highlight:
            days: [Wed]
            default_visible: true
    """
    anno_cfg = config.get("annotations")
    if not isinstance(anno_cfg, dict):
        return None
    wh = anno_cfg.get("weekday_highlight")
    if not isinstance(wh, dict):
        return None
    raw_days = wh.get("days", wh.get("day"))
    if raw_days is None:
        return None
    if isinstance(raw_days, (str, int)):
        raw_days = [raw_days]

    idxs: set[int] = set()
    for d in raw_days:
        if isinstance(d, bool):
            continue
        if isinstance(d, int):
            if 0 <= d <= 6:
                idxs.add(d)
        else:
            key = str(d).strip().lower()
            if key in _WEEKDAY_NAME_TO_IDX:
                idxs.add(_WEEKDAY_NAME_TO_IDX[key])
    if not idxs:
        return None

    ordered = sorted(idxs)
    label = wh.get("label")
    if not label:
        label = ", ".join(_WEEKDAY_PLURAL[i] for i in ordered)
    return {
        "days": ordered,
        "label": str(label),
        "default_visible": bool(wh.get("default_visible", True)),
    }
