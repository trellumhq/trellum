"""Read and export the audit trail.

Every filter below maps to an indexed access path on ``AuditLog`` (see the
model's ``Meta.indexes`` in ``apps/core/models.py``): ``category`` and
``actor`` each ride their own ``(org, ..., -created_at)`` index, ``outcome``
rides the partial non-success index, and ``target_type``/``target_id`` ride
the object-history index. ``q`` is the one unindexed path — a bounded
``icontains`` scan applied *after* every other filter has already cut the
window down; see :func:`_apply_filters`.
"""
from __future__ import annotations

import csv
import json

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, Q, TextField
from django.db.models.functions import Cast
from django.http import StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.core import roles
from apps.core.audit import audit
from apps.core.audit_actions import (
    CATEGORIES,
    CATEGORY_LABELS,
    emittable_actions,
    label_for,
)
from apps.core.models import AuditLog
from apps.core.permissions import require_org_role

#: Correct-and-simple MVP pagination (design §6.3). The known cliff is
#: Paginator.count's COUNT(*) at millions of rows; the documented successor
#: is a keyset pager (created_at < cursor) that drops the total count --
#: Phase 3, only once an instance actually feels it.
PAGE_SIZE = 50

#: A full-history handoff is the archive's job (apps.core.retention), not
#: this export's -- see RETENTION_AUDIT_ARCHIVE. This just bounds one
#: request's worth of streaming.
CSV_ROW_CAP = 100_000

CSV_COLUMNS = (
    "timestamp", "actor", "impersonator", "action", "category", "outcome",
    "target_type", "target_id", "ip", "user_agent", "metadata",
)

#: GET params the structured filter form (actor/action/outcome/date range)
#: owns. Everything else in request.GET is carried forward as hidden fields
#: when that form is submitted, so a category pill or a target-cell click
#: made before it survives -- the same "every filter composes" rule
#: _list_toolbar.html already follows for search.
_STRUCTURED_FILTER_KEYS = {"actor", "action", "outcome", "from", "to", "page"}


def _base_queryset(org):
    return AuditLog.objects.filter(org=org).select_related("actor", "impersonator")


def _apply_filters(request, qs):
    """Every composable GET filter. Returns ``(qs, applied)`` -- ``applied``
    is what the template needs to re-render the toolbar/pills/table with the
    current selection, and what the CSV export records as "which filters
    produced this file".
    """
    category = (request.GET.get("category") or "").strip()
    action = (request.GET.get("action") or "").strip()
    actor_id = (request.GET.get("actor") or "").strip()
    outcome = (request.GET.get("outcome") or "").strip()
    date_from = parse_date((request.GET.get("from") or "").strip())
    date_to = parse_date((request.GET.get("to") or "").strip())
    target_type = (request.GET.get("target_type") or "").strip()
    target_id = (request.GET.get("target_id") or "").strip()
    q = (request.GET.get("q") or "").strip()

    if category:
        qs = qs.filter(category=category)
    if action:
        qs = qs.filter(action=action)
    if actor_id:
        qs = qs.filter(actor_id=actor_id)
    if outcome:
        qs = qs.filter(outcome=outcome)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if target_type:
        qs = qs.filter(target_type=target_type)
    if target_id:
        qs = qs.filter(target_id=target_id)
    if q:
        # Cast, not the JSONField's own lookup machinery: "metadata cast to
        # text" is meant literally, so a search for `"acme.com"` (quotes and
        # all) behaves the same as it would against any other text column.
        qs = qs.annotate(_metadata_text=Cast("metadata", output_field=TextField())).filter(
            Q(action__icontains=q) | Q(target_id__icontains=q) | Q(_metadata_text__icontains=q)
        )

    applied = {
        "category": category,
        "action": action,
        "actor": actor_id,
        "outcome": outcome,
        "from": request.GET.get("from") or "",
        "to": request.GET.get("to") or "",
        "target_type": target_type,
        "target_id": target_id,
        "q": q,
    }
    return qs, applied


def _hidden_carry(request) -> list[tuple[str, str]]:
    return [
        (k, v) for k, v in request.GET.items()
        if k not in _STRUCTURED_FILTER_KEYS
    ]


def _tiles_and_pills(org):
    """Four COUNT queries over the trailing 30-day window (the partial
    non-success index makes the denials/failed-logins ones cheap), plus the
    same window's per-category breakdown for the filter pills -- the pills'
    numbers are the tiles' EVENTS total split five ways, not an all-time or
    currently-filtered count. "Recent denials" is the non-success index's
    top 5 in the same window.
    """
    window_start = timezone.now() - timezone.timedelta(days=30)
    window = AuditLog.objects.filter(org=org, created_at__gte=window_start)

    tiles = {
        "events": window.count(),
        "denials": window.exclude(outcome="success").count(),
        "failed_logins": window.filter(action="auth.login_failed").count(),
        "actors": window.exclude(actor__isnull=True).values("actor_id").distinct().count(),
    }

    counts_by_category = dict(
        window.values("category").annotate(n=Count("id")).values_list("category", "n")
    )
    pills = [
        {
            "category": cat,
            "label": CATEGORY_LABELS[cat],
            "count": counts_by_category.get(cat, 0),
        }
        for cat in CATEGORIES
    ]

    # "who" is precomputed here rather than in the template: an actor FK is
    # SET_NULL on account deletion, and auth.sso_denied's actor is always
    # None (the login never completed) with the asserted email living in
    # metadata instead -- a dict that will not have that key on every other
    # denial type. Using a possibly-missing metadata lookup as a *filter
    # argument* in the template (`|default:row.metadata.asserted_email`)
    # raises VariableDoesNotExist when the key is absent rather than
    # resolving to empty, because filter arguments do not get the same
    # ignore-failures treatment plain `{{ variable }}` output does.
    recent_denials = [
        {
            "row": row,
            "who": row.actor.email if row.actor_id else (row.metadata or {}).get("asserted_email") or "—",
        }
        for row in (
            window.exclude(outcome="success")
            .select_related("actor")
            .order_by("-created_at")[:5]
        )
    ]
    return tiles, pills, recent_denials


def _action_choices(base_qs):
    """The registry's labels, restricted to actions this org actually has --
    a static list intersected with real usage, not a raw distinct-values
    scan rendered verbatim (the dashboard used to show bare dotted verbs)."""
    used = set(base_qs.values_list("action", flat=True).distinct())
    choices = [(name, label_for(name)) for name in emittable_actions() if name in used]
    choices.sort(key=lambda pair: pair[1])
    return choices


def _actor_choices(base_qs):
    User = get_user_model()
    actor_ids = base_qs.exclude(actor__isnull=True).values_list("actor_id", flat=True).distinct()
    return list(User.objects.filter(pk__in=actor_ids).order_by("email").values_list("pk", "email"))


@require_org_role(roles.ORG_ADMIN)
def audit_log(request, org_slug):  # noqa: ARG001
    org = request.org
    base = _base_queryset(org)
    qs, applied = _apply_filters(request, base)

    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    tiles, pills, recent_denials = _tiles_and_pills(org)

    return render(
        request,
        "orgs/audit.html",
        {
            "org": org,
            "page": page,
            "tiles": tiles,
            "pills": pills,
            "recent_denials": recent_denials,
            "applied": applied,
            "action_choices": _action_choices(base),
            "actor_choices": _actor_choices(base),
            "hidden_carry": _hidden_carry(request),
            "any_filter_active": any(applied.values()),
        },
    )


class _Echo:
    """A file-like object whose write() hands the string straight back --
    csv.writer wants somewhere to write to, and this makes that "somewhere"
    the thing StreamingHttpResponse yields, so nothing is buffered."""

    def write(self, value):
        return value


def _csv_rows(pks: list, chunk_size: int = 1000):
    """Stream rows for exactly the primary keys captured before the
    audit.export row was written -- see audit_export_csv. Re-fetched in
    chunks (not held as model instances all at once) so a 100k-row export
    stays bounded in memory the way one built from a single lazy queryset
    would be, while still being immune to that queryset picking up its own
    audit.export row on re-evaluation during iteration.
    """
    writer = csv.writer(_Echo())
    yield writer.writerow(CSV_COLUMNS)
    for start in range(0, len(pks), chunk_size):
        chunk = pks[start:start + chunk_size]
        by_pk = {
            row.pk: row
            for row in AuditLog.objects.filter(pk__in=chunk).select_related("actor", "impersonator")
        }
        for pk in chunk:
            row = by_pk.get(pk)
            if row is None:
                continue  # deleted between listing and streaming -- skip, don't crash
            yield writer.writerow([
                row.created_at.isoformat(),
                row.actor.email if row.actor_id else "",
                row.impersonator.email if row.impersonator_id else "",
                row.action,
                row.category,
                row.outcome,
                row.target_type,
                row.target_id,
                row.ip or "",
                row.user_agent,
                json.dumps(row.metadata, default=str),
            ])


@require_org_role(roles.ORG_ADMIN)
def audit_export_csv(request, org_slug):  # noqa: ARG001
    org = request.org
    qs, applied = _apply_filters(request, _base_queryset(org))
    # The primary keys are captured now, before the audit.export row below is
    # written -- not a lazy queryset streamed later. A lazy queryset is
    # re-evaluated at iteration time, which (for an unfiltered or
    # category=system export) would pick up the audit.export row this very
    # request is about to write, making an export include itself. A plain
    # list of ints is cheap even at the 100k cap; the actual row data is
    # fetched back in bounded chunks by _csv_rows.
    pks = list(qs.order_by("-created_at", "-pk").values_list("pk", flat=True)[:CSV_ROW_CAP])
    row_count = len(pks)

    # Exporting the audit log is itself audited -- the design's whole point
    # for this row existing. Written before the stream starts so it lands
    # even if the client disconnects partway through a large download.
    audit(
        request, "audit.export", org=org,
        filters={k: v for k, v in applied.items() if v}, row_count=row_count,
    )

    response = StreamingHttpResponse(_csv_rows(pks), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="audit-log.csv"'
    return response
