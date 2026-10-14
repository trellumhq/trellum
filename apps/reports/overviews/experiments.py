"""The per-studio experiment overview (internal planning: cross-report A/B roll-up).

The request consumer reads normalized worker-observed declarations and result
facts from the database. It aggregates the complete studio relation and pages
the table and timeline independently. The artifact extraction helpers remain
available for producer normalization and compatibility tests.

**Status rule (the one non-obvious piece of business logic here).** An
experiment's lifecycle is derived from ``ab_test.start_date`` and
``ab_test.end_date`` (or its more tentative-sounding alias
``planned_end`` — both are optional-key portal conveniences the framework
itself does not read):

- ``unknown``   — ``start_date`` is missing or unparseable.
- ``scheduled`` — ``start_date`` is in the future.
- ``running``   — started, end not yet passed (or no end declared at all).
- ``concluded`` — an end date exists and has passed.
- ``past_end``  — an end date exists and has passed, **but** the report is
  still on a live build schedule (``Report.disabled is False`` and
  ``schedule_cron`` set). That combination means the test outran its own
  declared horizon and nobody told the pipeline to stop rebuilding it — worth
  a nudge (rendered amber), not an error. A report that is disabled, or has
  no schedule, past its end date is simply ``concluded``: nothing is going to
  keep producing "current" numbers for it. An entry may also declare
  ``closed: true`` to say the decision was made and the chapter is closed —
  that reads as ``concluded`` even while the page rebuilds, which is the
  only honest option for one finished test sharing a page (and therefore a
  schedule) with one still running.

``today`` is always ``datetime.now(timezone.utc).date()`` — UTC, because
``start_date``/``end_date`` are bare ISO dates with no timezone of their own
in report.yaml, and UTC is the one reading every studio agrees on regardless
of where its members happen to sit.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from django.shortcuts import render

from apps.core import roles
from apps.core.permissions import require_studio_role
from apps.core.report_access import require_full_studio_visibility

logger = logging.getLogger(__name__)

_MONTH_ABBR = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


# ── report.yaml / data.json plumbing ────────────────────────────────────────

def is_ab_report(config: dict) -> bool:
    """Does this report's config declare an ``ab_test:`` block?

    A block is one experiment's dict, or a list of such dicts for a report
    carrying several experiments on one page.
    """
    return bool(_ab_entries(config))


def _ab_entries(config: dict) -> list[dict]:
    """The ``ab_test:`` declaration as a list of entry dicts.

    A single dict is one entry; a list keeps its dict members in declared
    order (non-dict members are ignored rather than failing the report —
    same degrade-don't-hide stance as the payload plumbing below). Anything
    else is no declaration at all.
    """
    raw = (config or {}).get("ab_test")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    return []


def _read_json(path: Path) -> dict | None:
    """Parse ``path`` as JSON. Never raises; ``None`` on any read/parse
    failure or a top-level value that isn't an object."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _find_ab_components(components: dict | None) -> list[dict]:
    return [
        comp
        for comp in (components or {}).values()
        if isinstance(comp, dict) and comp.get("type") == "ab_compare"
    ]


def extract_ab_components(output_dir: Path) -> dict | None:
    """Every ``ab_compare`` component in ``output_dir/data.json``, in build
    order.

    Checks the main file's own ``components`` first; if none is found there
    and the report split its data across scopes (``_scopeFiles``), each
    sibling ``data_<scope>.json`` is checked in turn -- a scoped report still
    joins the overview from whichever scope happens to carry the A/B
    component. Missing/corrupt JSON, or no ``ab_compare`` component anywhere,
    returns ``None``; this never raises, so one report's broken output never
    breaks the page for every other report in the studio.
    """
    data = _read_json(output_dir / "data.json")
    if data is None:
        return None

    components = _find_ab_components(data.get("components"))
    if not components:
        try:
            resolved_root = output_dir.resolve()
        except OSError:
            resolved_root = None
        for fname in (data.get("_scopeFiles") or {}).values():
            if not isinstance(fname, str):
                continue
            candidate = (output_dir / fname).resolve()
            if resolved_root is None or (
                candidate != resolved_root
                and not str(candidate).startswith(str(resolved_root) + "/")
                and not str(candidate).startswith(str(resolved_root) + "\\")
            ):
                continue  # never follow a scope filename outside output_dir
            scope_data = _read_json(candidate)
            if scope_data is None:
                continue
            components = _find_ab_components(scope_data.get("components"))
            if components:
                break

    if not components:
        return None

    return {
        "components": components,
        "generated_at": (data.get("_freshness") or {}).get("generated_at"),
        "framework_version": data.get("_framework_version"),
    }


def extract_ab_component(output_dir: Path) -> dict | None:
    """The first ``ab_compare`` component -- :func:`extract_ab_components`
    narrowed to the single-experiment shape the original callers expect."""
    extracted = extract_ab_components(output_dir)
    if extracted is None:
        return None
    return {
        "component": extracted["components"][0],
        "generated_at": extracted["generated_at"],
        "framework_version": extracted["framework_version"],
    }


def _component_for_entry(
    ab_cfg: dict, components: list[dict], index: int, total: int
) -> dict | None:
    """The payload component belonging to one ``ab_test`` entry.

    A declared ``test_name`` matches the component whose payload header
    carries the same name (case-insensitively) -- report.yaml stays the
    authority on which numbers belong to which declaration. Without a name
    to match on, a single-entry report takes the first component (the
    original behaviour), and a multi-entry report falls back to declared
    order against build order.
    """
    wanted = str(ab_cfg.get("test_name") or "").strip().lower()
    if wanted:
        for comp in components:
            header = comp.get("header") or {}
            if str(header.get("test_name") or "").strip().lower() == wanted:
                return comp
        if total > 1:
            # A declared name nothing in the payload carries: better an
            # honest "no payload" row than another experiment's numbers.
            return None
    if total == 1:
        return components[0] if components else None
    return components[index] if index < len(components) else None


_extract_cache_lock = threading.Lock()
#: {(studio_pk, slug): (str(data.json path), mtime, extracted-or-None)}.
#:
#: One entry per report, not per build: on the remote backend
#: ``storage.output_root`` returns a fresh cache directory for every new
#: build, so keying by path (as an earlier version of this cache did) meant
#: every rebuild left its predecessor's entry behind forever -- a slow, silent
#: leak in any long-lived worker process. Keying by (studio, slug) instead
#: means a rebuild's new path simply *replaces* this report's one entry, and
#: the value held is only the small extracted triple (component + freshness +
#: version), never the full parsed data.json, which can run to several MB.
_extract_cache: dict[tuple[int, str], tuple[str, float, dict | None]] = {}


def _cached_extract(studio, slug: str, output_dir: Path) -> dict | None:
    """:func:`extract_ab_components`, memoised per (studio, slug) and
    invalidated by the data.json path + mtime changing underneath it."""
    path = output_dir / "data.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None

    key = (studio.pk, slug)
    if mtime is not None:
        with _extract_cache_lock:
            hit = _extract_cache.get(key)
            if hit is not None and hit[0] == str(path) and hit[1] == mtime:
                return hit[2]

    extracted = extract_ab_components(output_dir)
    with _extract_cache_lock:
        if mtime is not None:
            _extract_cache[key] = (str(path), mtime, extracted)
        else:
            # No stat to key a memo on -- don't cache, and drop any stale
            # entry so a later successful stat doesn't fall through to it.
            _extract_cache.pop(key, None)
    return extracted


def _active_body(component: dict) -> tuple[dict | None, str]:
    """The component's currently-open view: ``modes[default_mode]`` (falling
    back to the first mode) when the report declares modes, else its plain
    ``body``. The label is the report author's own name for that mode
    ("raw" / "winsorised" / "CUPED-adjusted", ...) -- empty for a plain body,
    which has no modes to name."""
    modes = component.get("modes")
    if isinstance(modes, dict) and modes:
        default_mode = component.get("default_mode")
        mode = modes.get(default_mode) if default_mode else None
        if not isinstance(mode, dict):
            mode = next((m for m in modes.values() if isinstance(m, dict)), None)
        if mode is None:
            return None, ""
        return mode, str(mode.get("label") or "")
    body = component.get("body")
    return (body, "") if isinstance(body, dict) else (None, "")


def primary_metric(body: dict | None, ab_cfg: dict) -> dict | None:
    """The one KPI row this report's overview card leads with.

    ``ab_cfg.primary_metric`` (a portal-only convenience key, matched
    case-insensitively against a row's ``key`` or its ``metric`` label) picks
    it explicitly; otherwise the first KPI row in the payload's own order
    wins. ``None`` when the payload has no KPI rows at all (e.g. a
    volumes-only report).
    """
    if not body:
        return None
    kpi_rows = body.get("kpi_rows") or []
    if not kpi_rows:
        return None

    wanted = str(ab_cfg.get("primary_metric") or "").strip().lower()
    row = None
    if wanted:
        for candidate in kpi_rows:
            key = str(candidate.get("key") or "").lower()
            metric = str(candidate.get("metric") or "").lower()
            if wanted in (key, metric):
                row = candidate
                break
    if row is None:
        row = kpi_rows[0]

    ci_pct = None
    significant = False
    raw_ci = row.get("ci_pct")
    if isinstance(raw_ci, (list, tuple)) and len(raw_ci) == 2:
        try:
            lo, hi = float(raw_ci[0]), float(raw_ci[1])
        except (TypeError, ValueError):
            lo = hi = None
        if lo is not None:
            ci_pct = [lo, hi]
            significant = not (lo <= 0 <= hi)

    return {
        "metric": row.get("metric", ""),
        "delta_pct": row.get("delta_pct"),
        "ci_pct": ci_pct,
        "higher_is_better": bool(row.get("higher_is_better", True)),
        "control_val": row.get("control_val", ""),
        "test_val": row.get("test_val", ""),
        "significant": significant,
    }


# ── lifecycle ────────────────────────────────────────────────────────────

def derive_status(ab_cfg: dict, today: date, *, schedule_active: bool = False) -> dict:
    """Classify one experiment's lifecycle -- see the module docstring for the
    full rule, in particular the ``past_end`` carve-out. ``schedule_active``
    is ``(not report.disabled) and bool(report.schedule_cron)``, passed in
    by the caller rather than a ``Report`` instance so this stays a pure
    function of the declared dates plus one boolean.
    """

    def _parse(value) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    start = _parse(ab_cfg.get("start_date"))
    end = _parse(ab_cfg.get("end_date"))
    if end is None:
        end = _parse(ab_cfg.get("planned_end"))

    if start is None:
        return {
            "status": "unknown",
            "days": None,
            "start_date": None,
            "end_date": end,
            "no_end_declared": end is None,
            "ends_in_days": None,
            "ended_days_ago": None,
        }

    if start > today:
        return {
            "status": "scheduled",
            "days": 0,
            "start_date": start,
            "end_date": end,
            "no_end_declared": end is None,
            "ends_in_days": None,
            "ended_days_ago": None,
        }

    if end is not None and today > end:
        ended_days_ago = (today - end).days
        # `closed: true` is the author saying the decision was made: concluded
        # even while a shared page's schedule keeps rebuilding the numbers.
        if schedule_active and not ab_cfg.get("closed"):
            return {
                "status": "past_end",
                "days": (today - start).days + 1,
                "start_date": start,
                "end_date": end,
                "no_end_declared": False,
                "ends_in_days": None,
                "ended_days_ago": ended_days_ago,
            }
        return {
            "status": "concluded",
            "days": (end - start).days + 1,
            "start_date": start,
            "end_date": end,
            "no_end_declared": False,
            "ends_in_days": None,
            "ended_days_ago": ended_days_ago,
        }

    return {
        "status": "running",
        "days": (today - start).days + 1,
        "start_date": start,
        "end_date": end,
        "no_end_declared": end is None,
        "ends_in_days": (end - today).days if end is not None else None,
        "ended_days_ago": None,
    }


# ── timeline ─────────────────────────────────────────────────────────────

def _month_floor(d: date) -> date:
    return d.replace(day=1)


def _shift_months(d: date, months: int) -> date:
    idx = d.month - 1 + months
    year = d.year + idx // 12
    month = idx % 12 + 1
    return date(year, month, 1)


def _build_timeline(rows: list[dict], today: date) -> dict:
    """Left/width percentages for the last ~4 calendar months (this month
    plus the 3 before it), so the axis lands on clean month boundaries the
    way the approved mockup draws it. A bar with no declared end draws open
    (dashed right edge, per the mockup) out to today rather than stopping
    short.

    Scheduled experiments (a future ``start_date``) never get a bar: they
    haven't started, so there is nothing to draw yet, and letting one through
    used to draw a backwards sliver (``bar_end`` clamped to today, which sits
    *before* a future start).

    Month labels get their width from their own actual day count rather than
    an even ``1fr`` split, so a 31-day month and a 28-day month don't claim
    the same width a day-percentage-positioned bar uses -- an even split drifts
    the axis out of alignment with the bars by up to a day and a half.
    """
    window_start = _shift_months(_month_floor(today), -3)
    window_end = _shift_months(_month_floor(today), 1)
    window_days = max((window_end - window_start).days, 1)

    month_bounds = [_shift_months(window_start, i) for i in range(5)]
    months = [
        {
            "label": _MONTH_ABBR[month_bounds[i].month - 1],
            "width_pct": round(
                (month_bounds[i + 1] - month_bounds[i]).days / window_days * 100.0, 2
            ),
        }
        for i in range(4)
    ]

    def pct(d: date) -> float:
        return max(0.0, min(100.0, (d - window_start).days / window_days * 100.0))

    lanes = []
    for row in rows:
        if row["status"] == "scheduled":
            continue  # hasn't started -- nothing to draw
        start = row.get("start_date")
        if start is None or start >= window_end:
            continue
        end = row.get("end_date")
        open_end = end is None
        bar_end = end if end is not None else min(today, window_end)
        if bar_end < window_start:
            continue
        left = pct(start)
        right = pct(bar_end)
        if right < left:
            continue  # defensive: never draw a backwards bar
        width = max(right - left, 1.2)
        lanes.append({
            "slug": row["slug"],
            "name": row["name"],
            "left_pct": round(left, 1),
            "width_pct": round(min(width, 100.0 - left), 1),
            "open_end": open_end,
            "done": row["status"] in ("concluded", "past_end"),
        })

    return {
        "months": months,
        "lanes": lanes,
        "today_pct": round(pct(today), 1),
    }


# ── compatibility cache invalidation ─────────────────────────────────────────

_studio_cache_lock = threading.Lock()
_studio_cache: dict[int, tuple[float, dict]] = {}


def invalidate_experiments_cache(studio=None) -> None:
    with _studio_cache_lock:
        if studio is None:
            _studio_cache.clear()
        else:
            _studio_cache.pop(studio.pk, None)
    with _extract_cache_lock:
        if studio is None:
            _extract_cache.clear()
        else:
            for key in [k for k in _extract_cache if k[0] == studio.pk]:
                _extract_cache.pop(key, None)


def _matches_status(row_status: str, wanted: str) -> bool:
    if wanted == "running":
        return row_status in ("running", "past_end")
    return row_status == wanted


def studio_experiments(studio, *, params=None) -> dict:
    """Read bounded worker-observed facts, with global database aggregates."""
    from .experiment_queries import overview
    return overview(studio, params=params)


# ── view ─────────────────────────────────────────────────────────────────

@require_studio_role(roles.VIEWER)
@require_full_studio_visibility
def experiments_page(request, org_slug, studio_slug):  # noqa: ARG001
    data = studio_experiments(request.studio, params=request.GET)
    return render(request, "reports/experiments.html", {
        "org": request.org, "studio": request.studio, **data,
    })
