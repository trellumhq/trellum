"""Bounded HTTP transport for the annotation browser (C13 phases B2/B3).

Everything here reads the projected annotation tables and the mutable filter
state only: no events.yaml, report config, Registry or storage access.  GETs
run in one repeatable-read, read-only snapshot opened before the guards.  The
legacy ``/annotations`` page is untouched until this UI replaces it.

Permissions come from the guards' request access facts (VIEWER + full studio
visibility); state mutations additionally refuse bearer/API-key requests.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import date, timedelta
from functools import wraps
from urllib.parse import urlencode

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.core import roles
from apps.core.actions import action_payload
from apps.core.permissions import require_studio_role
from apps.core.report_access import require_full_studio_visibility
from . import annotation_filter_state as filters
from .annotation_queries import (
    EventFilter, EventPosition, FacetPosition, catalog, coverage, event_page,
    events, facet_page, month_data,
)
from .models import AnnotationEvent
from .overviews.annotations import _display_dates, _type_meta

CURSOR_SALT = "trellum.annotations.cursor.v1"
CURSOR_MAX_AGE = 3600
CURSOR_MAX_BYTES = 2048
BODY_MAX_BYTES = 40 * 1024
VIEWS = ("list", "month", "day")
MODES = VIEWS + ("types", "scopes")
NOTICES = {
    "conflict": "The filter was changed elsewhere. Showing the latest filter; apply your change again if you still want it.",
    "expired": "The saved filter expired. Showing the default view.",
    "busy": "A filter update is already in progress. Try again in a moment.",
    "invalid": "That filter change was not accepted.",
}
MESSAGES = {
    400: "Invalid request.", 403: "Not permitted.", 404: "Filter not found.", 409: "The request is out of date.",
    410: "The saved filter expired.", 413: "Request too large.", 423: "A filter update is already in progress.",
}
_GET_KEYS = {"q", "scope", "state", "revision", "defaults", "month", "date", "cursor", "type_q", "scope_q", "facet_cursor"}
_PAGE_KEYS = _GET_KEYS | {"view", "notice", "pending", "pending_hidden"}
_DATA_KEYS = _GET_KEYS | {"mode"}


class ApiError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


# ── snapshot ────────────────────────────────────────────────────────────────

def snapshot(view):
    """Read-only repeatable-read snapshot opened before guard and view SQL."""

    @transaction.non_atomic_requests
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            response = view(request, *args, **kwargs)
            if hasattr(response, "render") and callable(response.render):
                response = response.render()  # never stream outside the snapshot
            return response

    return wrapper


def _error(status, code, message=None):
    return JsonResponse({"state": "error", "code": code, "message": message or MESSAGES.get(status, "Request failed.")},
                        status=status, headers={"Cache-Control": "private, no-store"})


# ── parameter parsing ───────────────────────────────────────────────────────

def _single(query, name):
    values = query.getlist(name)
    if len(values) > 1:
        raise ApiError(400, "duplicate_" + name)
    return values[0] if values else None


def _bounded_text(value, limit, byte_limit=None):
    if value is None:
        return None
    if "\x00" in value or len(value) > limit or len(value.encode("utf-8", "replace")) > (byte_limit or limit * 4):
        raise ApiError(400, "invalid_text")
    return value


def _date(value, *, low=1, high=9999):
    try:
        if len(value) != 10:
            raise ValueError
        result = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_date") from None
    if not low <= result.year <= high:
        raise ApiError(400, "invalid_date")
    return result


def month_window(value):
    """Monday-aligned 4/5/6 week grid containing the month; <=42 days."""
    try:
        if len(value) != 7 or value[4] != "-":
            raise ValueError
        year, month = int(value[:4]), int(value[5:])
        first = date(year, month, 1)
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_month") from None
    if not 2 <= year <= 9998:
        raise ApiError(400, "invalid_month")
    last = first.replace(day=calendar.monthrange(year, month)[1])
    return first, first - timedelta(days=first.weekday()), last + timedelta(days=6 - last.weekday())


@dataclass
class Params:
    view: str = "list"
    search: str = ""
    scope: str | None = None
    state: str | None = None
    revision: int | None = None
    defaults: bool = False
    month: str = ""
    day: date | None = None
    cursor: str | None = None
    type_q: str = ""
    scope_q: str = ""
    facet_cursor: str | None = None


def parse(query, allowed):
    unknown = set(query) - allowed
    if unknown:
        raise ApiError(400, "unknown_parameter")
    p = Params()
    view = _single(query, "view") or _single(query, "mode")
    if view is not None:
        if view not in MODES:
            raise ApiError(400, "invalid_view")
        p.view = view
    p.search = _bounded_text(_single(query, "q"), 200, 800) or ""
    p.type_q = _bounded_text(_single(query, "type_q"), 200, 800) or ""
    p.scope_q = _bounded_text(_single(query, "scope_q"), 200, 800) or ""
    p.scope = _bounded_text(_single(query, "scope"), 256, 1024)
    p.state = _single(query, "state") or None
    revision = _single(query, "revision")
    if revision:
        if not revision.isascii() or not revision.isdecimal() or len(revision) > 19 or int(revision) >= 1 << 63:
            raise ApiError(400, "invalid_revision")
        p.revision = int(revision)
    if p.revision is not None and p.state is None:
        raise ApiError(400, "invalid_revision")
    flag = _single(query, "defaults")
    if flag not in (None, "", "0", "1"):
        raise ApiError(400, "invalid_defaults")
    p.defaults = flag == "1"
    if p.defaults and p.state:
        raise ApiError(400, "invalid_defaults")
    month, day = _single(query, "month"), _single(query, "date")
    p.month = month or ""
    if month:
        month_window(month)
    if day:
        p.day = _date(day)
    for name in ("cursor", "facet_cursor"):
        value = _single(query, name)
        if value is not None and (len(value) > CURSOR_MAX_BYTES or not value.isascii()):
            raise ApiError(400, "invalid_cursor")
        setattr(p, name, value or None)
    return p


# ── filter, cursor ──────────────────────────────────────────────────────────

class Ctx:
    """Everything one response shares, resolved once inside the snapshot."""

    def __init__(self, request, p):
        self.request, self.p = request, p
        self.studio = request.studio
        self.now = timezone.now()
        self.state = None
        hidden = shown = None
        if p.state:
            try:
                self.state, hidden, shown = filters.relations(
                    filters.context_from_request(request), p.state, revision=p.revision, now=self.now)
            except filters.FilterStateError as exc:
                raise ApiError(exc.status, exc.code) from None
            except PermissionDenied:
                raise ApiError(403, "forbidden") from None
        self.defaults = p.defaults or bool(self.state and self.state["mode"] == "defaults")
        self.hidden, self.shown = hidden, shown
        try:
            self.filter = EventFilter(search=p.search, scope=p.scope, defaults=self.defaults,
                                      hidden_types=hidden, shown_types=shown if self.defaults else None)
        except ValueError:
            raise ApiError(400, "invalid_filter") from None
        self.catalog = (catalog(self.studio) or {}).get("published_revision") or 0
        self.events = events(self.studio, self.filter, now=self.now)

    def binding(self, kind):
        window = [self.p.month, self.p.day.isoformat() if self.p.day else ""]
        digest = hashlib.sha256(json.dumps([self.p.search, self.p.scope, self.defaults, window, self.p.type_q,
                                            self.p.scope_q, kind], separators=(",", ":")).encode()).hexdigest()
        return {"v": 1, "s": self.studio.pk, "c": self.catalog, "t": (self.state or {}).get("token"),
                "r": (self.state or {}).get("revision"), "k": kind, "f": digest}

    def sign(self, kind, position):
        token = signing.dumps({**self.binding(kind), "p": position}, salt=CURSOR_SALT, compress=True)
        if len(token) > CURSOR_MAX_BYTES:
            raise ApiError(400, "invalid_cursor")
        return token

    def unsign(self, kind, token):
        try:
            data = signing.loads(token, salt=CURSOR_SALT, max_age=CURSOR_MAX_AGE)
        except signing.BadSignature:
            raise ApiError(400, "invalid_cursor") from None
        expected = self.binding(kind)
        if not isinstance(data, dict) or any(data.get(k) != expected[k] for k in ("v", "s", "t", "k", "f")):
            raise ApiError(400, "invalid_cursor")
        if data.get("c") != expected["c"] or data.get("r") != expected["r"]:
            raise ApiError(409, "stale_cursor")
        return data.get("p")

    def event_after(self, token):
        pk = self.unsign("events:" + self.p.view, token)
        row = AnnotationEvent.objects.filter(pk=pk, studio_id=self.studio.pk).values_list(
            "start_date", "label", "source_id", "ordinal", "id").first() if type(pk) is int else None
        if row is None:
            raise ApiError(409, "stale_cursor")
        return EventPosition(*row)

    def facet_after(self, field, token):
        pos = self.unsign("facet:" + field, token)
        if not (isinstance(pos, list) and len(pos) == 2 and type(pos[0]) is int and isinstance(pos[1], str)):
            raise ApiError(400, "invalid_cursor")
        return FacetPosition(*pos)


# ── payload builders ────────────────────────────────────────────────────────

def _row(ctx, r):
    meta = _type_meta(r["event_type"])
    slug = r["source__report__slug"]
    start = r["start_date"].isoformat()
    end = r["end_date"].isoformat() if r["end_date"] else ""
    studio = ctx.studio
    return {
        "id": r["id"], "date": start, "end_date": end, "dates_display": _display_dates(start, end or None),
        "label": r["label"], "type": r["event_type"], "type_label": meta["label"], "type_css": meta["css"],
        "scope": r["scope"], "retained": not r["source_current"],
        "source_label": f"report: {slug}" if r["source__kind"] == "report" else "events.yaml",
        "report_url": f"/s/{studio.org.slug}/{studio.slug}/r/{slug}/?display=console" if slug else "",
    }


def coverage_payload(ctx):
    result = coverage(ctx.studio, now=ctx.now)
    result["retained_since"] = result["retained_since"].isoformat() if result["retained_since"] else None
    return result


def events_payload(ctx):
    p, kind = ctx.p, ctx.p.view
    after = ctx.event_after(p.cursor) if p.cursor else None
    out = {"mode": kind}
    if kind == "month":
        first, start, end = month_window(p.month or ctx.now.date().strftime("%Y-%m"))
        data = month_data(ctx.events, start, end)
        page = data["events"]
        out.update(month=first.strftime("%Y-%m"), start=start.isoformat(), end=end.isoformat(),
                   days=[{"date": d["day"].isoformat(), "count": d["count"],
                          "points": d["point_count"], "ranges": d["range_count"]} for d in data["days"]],
                   overflow=max(0, page.total - len(page.rows)))
    elif kind == "day":
        day = p.day or ctx.now.date()
        page = event_page(ctx.events, after=after, ascending=True, start=day, end=day)
        out["date"] = day.isoformat()
    else:
        page = event_page(ctx.events, after=after)
    out.update(total=page.total, rows=[_row(ctx, r) for r in page.rows],
               next_cursor=ctx.sign("events:" + kind, page.next_after.id) if page.next_after and kind != "month" else None)
    return out


def facet_payload(ctx, field):
    p = ctx.p
    # Discover choices before the active type/scope/search filters hide them.
    base = events(ctx.studio, EventFilter(), now=ctx.now)
    key = "event_type" if field == "types" else "scope"
    after = ctx.facet_after(field, p.facet_cursor) if p.facet_cursor else None
    page = facet_page(base, key, search=p.type_q if field == "types" else p.scope_q, after=after)
    values = [row["value"] for row in page.rows]
    hidden, shown = set(), set()
    if field == "types":
        if ctx.hidden is not None:
            hidden = {r["event_type"] for r in ctx.hidden.filter(event_type__in=values)}
        if ctx.shown is not None and ctx.defaults:
            shown = {r["event_type"] for r in ctx.shown.filter(event_type__in=values)}
    rows = []
    for row in page.rows:
        value = row["value"]
        item = {"value": value, "count": row["count"]}
        if field == "types":
            meta = _type_meta(value)
            item.update(label=meta["label"], css=meta["css"], hidden=(
                value in hidden or (ctx.defaults and not row["default_visible"] and value not in shown)))
        else:
            item["selected"] = ctx.p.scope == value
        rows.append(item)
    return {"mode": field, "total": page.total, "rows": rows,
            "next_cursor": ctx.sign("facet:" + field, [page.next_after.priority, page.next_after.value]) if page.next_after else None}


def filter_meta(ctx):
    return {"state": ctx.state["token"] if ctx.state else None, "revision": ctx.state["revision"] if ctx.state else None,
            "mode": ctx.state["mode"] if ctx.state else ("defaults" if ctx.defaults else "all"),
            "expires_at": ctx.state["expires_at"].isoformat() if ctx.state else None}


# ── views ───────────────────────────────────────────────────────────────────

@snapshot
@require_studio_role(roles.VIEWER)
@require_full_studio_visibility
@require_GET
def data(request, org_slug, studio_slug):  # noqa: ARG001
    try:
        ctx = Ctx(request, parse(request.GET, _DATA_KEYS))
        body = facet_payload(ctx, ctx.p.view) if ctx.p.view in ("types", "scopes") else events_payload(ctx)
        body.update(state="ok", filter=filter_meta(ctx), catalog_revision=ctx.catalog)
        if ctx.p.view in VIEWS and not ctx.p.cursor:
            body["coverage"] = coverage_payload(ctx)
    except ApiError as exc:
        return _error(exc.status, exc.code)
    return JsonResponse(body, headers={"Cache-Control": "private, no-store"})


def _query(**values):
    return urlencode([(k, v) for k, v in values.items() if v not in (None, "", False)])


@snapshot
@require_studio_role(roles.VIEWER)
@require_full_studio_visibility
@require_GET
def browse(request, org_slug, studio_slug):
    """Native-first page: 25 rows, 25 choices per facet, real links and forms."""
    base = reverse("studio-annotations-browse", args=[org_slug, studio_slug])
    context = {"org": request.org, "studio": request.studio, "base": base, "error": None,
               "data_url": reverse("studio-annotations-data", args=[org_slug, studio_slug]),
               "filter_url": reverse("studio-annotations-filter", args=[org_slug, studio_slug]),
               "status_url": reverse("studio-annotations-filter-status", args=[org_slug, studio_slug]),
               "request_key": secrets.token_hex(16)}
    try:
        p = parse(request.GET, _PAGE_KEYS)
        if p.view not in VIEWS:
            raise ApiError(400, "invalid_view")
        ctx = Ctx(request, p)
        main = events_payload(ctx)
        token = ctx.state["token"] if ctx.state else None

        def link(**extra):
            values = {"view": p.view, "q": p.search, "state": token, "defaults": "1" if p.defaults else "",
                      "month": main.get("month", ""), "date": p.day.isoformat() if p.day else "", "scope": p.scope}
            values.update(extra)
            pairs = [(k, v) for k, v in values.items() if v not in (None, "")]
            if values["scope"] == "":
                pairs.append(("scope", ""))
            if token and extra.get("state", token) == token:
                pairs.append(("revision", ctx.state["revision"]))
            return base + "?" + urlencode(pairs)

        types, scopes = facet_payload(ctx, "types"), facet_payload(ctx, "scopes")
        days = main.get("days", [])
        context.update(
            p=p, ctx_filter=filter_meta(ctx), main=main, types=types, coverage=coverage_payload(ctx),
            scope_rows=[{**row, "href": link(scope=row["value"], cursor=None)} for row in scopes["rows"]],
            scopes=scopes,
            next_link=link(cursor=main["next_cursor"]) if main["next_cursor"] else None,
            types_next=link(facet_cursor=types["next_cursor"]) if types["next_cursor"] else None,
            scopes_next=link(facet_cursor=scopes["next_cursor"]) if scopes["next_cursor"] else None,
            first_link=link(cursor=None), reset_link=base, defaults_link=base + "?defaults=1",
            all_scopes_link=link(scope=None, cursor=None),
            list_link=link(view="list", cursor=None, month="", date=""),
            month_link=link(view="month", cursor=None, date=""), day_link=link(view="day", cursor=None, month=""),
            month_links=_month_links(main, link) if p.view == "month" else None,
            grid_weeks=[[{**d, "href": link(view="day", date=d["date"], cursor=None, month=""),
                          "outside": d["date"][:7] != main["month"], "day": int(d["date"][8:])}
                         for d in days[i:i + 7]] for i in range(0, len(days), 7)] if p.view == "month" else [],
            notice=NOTICES.get(request.GET.get("notice", "")), pending=_pending(request, p),
        )
    except ApiError as exc:
        context.update(error={"status": exc.status, "code": exc.code, "message": MESSAGES.get(exc.status, "Request failed.")})
        response = render(request, "reports/annotations_browse.html", context, status=exc.status)
        response["Cache-Control"] = "private, no-store"
        return response
    response = render(request, "reports/annotations_browse.html", context)
    response["Cache-Control"] = "private, no-store"
    return response


def _month_links(main, link):
    first = date.fromisoformat(main["month"] + "-01")
    previous = (first - timedelta(days=1)).strftime("%Y-%m")
    following = (first + timedelta(days=32)).strftime("%Y-%m")
    return {"previous": link(month=previous, cursor=None), "next": link(month=following, cursor=None),
            "label": first.strftime("%B %Y")}


def _pending(request, p):
    value, hidden = request.GET.get("pending"), request.GET.get("pending_hidden")
    if value and hidden in ("0", "1") and len(value) <= 256 and "\x00" not in value and request.GET.get("notice") == "conflict":
        return {"type": value, "hidden": hidden == "1"}
    return None


# ── state mutations ─────────────────────────────────────────────────────────

_OPS = {
    "create": ("operation_id", "mode", "expected_items", "expected_chain_digest"),
    "append": ("state", "operation_id", "chunk_index", "keys"),
    "finalize": ("state", "operation_id"),
    "delta": ("state", "operation_id", "base_revision", "changes"),
    "touch": ("state", "operation_id"),
}


def _status_url(request, token, operation_id):
    base = reverse("studio-annotations-filter-status", args=[request.org.slug, request.studio.slug])
    return base + "?" + urlencode({"state": token, "operation_id": operation_id})


def _apply(context, op, body):
    if op == "create":
        return filters.create(context, operation_id=body["operation_id"], mode=body["mode"],
                              expected_items=body["expected_items"], expected_chain_digest=body["expected_chain_digest"])
    token = body["state"]
    if op == "append":
        return filters.append(context, token, operation_id=body["operation_id"], chunk_index=body["chunk_index"], keys=body["keys"])
    if op == "finalize":
        return filters.finalize(context, token, operation_id=body["operation_id"])
    if op == "delta":
        return filters.delta(context, token, operation_id=body["operation_id"], base_revision=body["base_revision"], changes=body["changes"])
    return filters.touch(context, token, operation_id=body["operation_id"])


@require_studio_role(roles.VIEWER)
@require_full_studio_visibility
@require_POST
def filter_post(request, org_slug, studio_slug):
    """JSON state mutation (one bounded step) or the native toggle form."""
    if request.content_type != "application/json":
        return _native_toggle(request, org_slug, studio_slug)
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > BODY_MAX_BYTES:
            return _error(413, "too_large")
        raw = request.body
        if len(raw) > BODY_MAX_BYTES:
            return _error(413, "too_large")
        body = json.loads(raw.decode("utf-8"))
        op = body.get("op") if isinstance(body, dict) else None
        if op not in _OPS or set(body) != {"op", *_OPS[op]}:
            raise ApiError(400, "invalid_body")
        context = filters.context_from_request(request)
        result = _apply(context, op, body)
    except (ValueError, UnicodeDecodeError, ApiError) as exc:
        return _error(exc.status if isinstance(exc, ApiError) else 400, getattr(exc, "code", "invalid_body"))
    except filters.FilterStateError as exc:
        return _error(exc.status, exc.code)
    except PermissionDenied:
        return _error(403, "forbidden")
    token = result["token"]
    action = action_payload(action_id=result["operation_id"], state="succeeded" if result["status"] in ("succeeded", "throttled") else "unconfirmed",
                            message="Filter saved." if result["status"] == "succeeded" else "Filter unchanged.",
                            status_url=_status_url(request, token, result["operation_id"]), revision=str(result["revision"]))
    return JsonResponse({"state": "ok", "result": result, **action}, headers={"Cache-Control": "private, no-store"})


def _native_toggle(request, org_slug, studio_slug):
    form = request.POST
    base = reverse("studio-annotations-browse", args=[org_slug, studio_slug])
    try:
        key = form.get("request_key", "")
        toggle = form.get("toggle", "")
        hidden, value = toggle[:1], toggle[2:]
        if not key.isalnum() or not key.isascii() or not 8 <= len(key) <= 64 or hidden not in ("0", "1") or toggle[1:2] != ":" or not value or len(value) > 256:
            raise ApiError(400, "invalid")
        # Reuse the strict GET parser to rebuild the page the user came from.
        carry = parse(form, {"view", "q", "scope", "state", "revision", "defaults", "month", "date",
                             "request_key", "toggle", "csrfmiddlewaretoken"})
        context = filters.context_from_request(request)
        if carry.state:
            revision = carry.revision
            if revision is None:
                raise ApiError(400, "invalid")
            token = carry.state
        else:
            made = filters.create(context, operation_id=key + "-c", mode="defaults" if carry.defaults else "explicit",
                                  expected_items=0, expected_chain_digest=filters.EMPTY_DIGEST)
            token = made["token"]
            revision = filters.finalize(context, token, operation_id=key + "-f")["revision"]
        result = filters.delta(context, token, operation_id=key + "-d", base_revision=revision, changes=[[value, hidden == "1"]])
        notice, state, rev = None, token, result["revision"]
    except filters.FilterStateError as exc:
        notice = {409: "conflict", 410: "expired", 423: "busy"}.get(exc.status, "invalid")
        state, rev = (carry.state, None) if notice == "conflict" else (None, None)
    except (ApiError, PermissionDenied):
        notice, state, rev, carry = "invalid", None, None, Params()
    pairs = [("view", carry.view if carry.view in VIEWS else "list")]
    for name, item in (("q", carry.search), ("month", carry.month), ("date", carry.day and carry.day.isoformat()),
                       ("defaults", "1" if carry.defaults and not state else ""), ("state", state), ("revision", rev), ("notice", notice)):
        if item not in (None, ""):
            pairs.append((name, item))
    if carry.scope is not None:
        pairs.append(("scope", carry.scope))
    if notice == "conflict":
        pairs += [("pending", value), ("pending_hidden", hidden)]
    return HttpResponseRedirect(base + "?" + urlencode(pairs), status=303)


@snapshot
@require_studio_role(roles.VIEWER)
@require_full_studio_visibility
@require_GET
def filter_status(request, org_slug, studio_slug):  # noqa: ARG001
    """Scalar read-back of one state operation; never renews or repairs."""
    try:
        if set(request.GET) - {"state", "operation_id"}:
            raise ApiError(400, "unknown_parameter")
        token, operation = _single(request.GET, "state"), _single(request.GET, "operation_id")
        result = filters.observe(filters.context_from_request(request), token, operation_id=operation)
    except ApiError as exc:
        return _error(exc.status, exc.code)
    except filters.FilterStateError as exc:
        return _error(exc.status, exc.code)
    except PermissionDenied:
        return _error(403, "forbidden")
    done = result["status"] != "unconfirmed"
    action = action_payload(
        action_id=operation, state="succeeded" if done else "unconfirmed",
        message="Filter operation recorded." if done else "No record of this operation; its outcome is unconfirmed.",
        status_url=_status_url(request, token, operation), revision=str(result.get("revision", "")) if done else None)
    return JsonResponse({"state": "ok", "result": result, **action}, headers={"Cache-Control": "private, no-store"})
