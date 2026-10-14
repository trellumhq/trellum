"""Worker-side "queue the reports a source just unblocked".

Admission (:func:`admit`) happens inside the transaction that stores a
successful check, so it cannot be lost to a crash or a response that never
arrives. The worker (:func:`process_next`) then walks the organization's
studios one at a time, keeping a keyset cursor on the request row. It reuses
``apps.runner.services.enqueue`` (idempotent while a run is active) for each
eligible report, and re-validates the exact binding/declaration revision under
the declaration->binding lock order before every enqueue. Obsolete requests
(binding changed, deleted, check no longer passing) stop without enqueuing.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.datasources.models import DataSource, RepoDataSource, UnblockRequest

logger = logging.getLogger("trellum.unblock")

LEASE_SECONDS = 60
MAX_ATTEMPTS = 5


def _digest(snapshot) -> str:
    return hashlib.sha256(json.dumps(snapshot, cls=DjangoJSONEncoder, sort_keys=True).encode()).hexdigest()


def admit(binding, *, check_studio=None) -> UnblockRequest:
    """Record (or reset) the unblock request for ``binding``. Call inside the
    transaction that stored the successful check evidence."""
    request, _ = UnblockRequest.objects.update_or_create(
        binding=binding,
        defaults={
            "check_studio_id": getattr(check_studio, "pk", None),
            "binding_updated_at": binding.updated_at,
            "declaration_digest": _digest(getattr(binding, "_check_declaration", None)),
            "status": UnblockRequest.PENDING, "cursor_studio_id": 0, "queued": 0,
            "attempts": 0, "lease_until": None, "created_at": timezone.now(),
        },
    )
    return request


def _declaration(binding, check_studio_id):
    qs = RepoDataSource.objects.filter(name=binding.name, present=True)
    if check_studio_id is not None:
        return qs.filter(studio_id=check_studio_id).first()
    if binding.studio_id:
        return qs.filter(studio_id=binding.studio_id).first()
    return qs.filter(studio__org_id=binding.org_id).order_by("studio_id").first()


def _is_current(request, *, lock=False) -> bool:
    """The request still describes the binding and declaration as they are."""
    from apps.datasources.testing import _declaration_snapshot

    binding = DataSource.objects.filter(pk=request.binding_id).first()
    declaration = _declaration(binding, request.check_studio_id) if binding else None
    if lock and declaration is not None:
        # Repo sync writes declarations before bindings; same lock order.
        declaration = RepoDataSource.objects.select_for_update().filter(pk=declaration.pk).first()
    if binding is None:
        return False
    qs = DataSource.objects.select_for_update() if lock else DataSource.objects
    current = qs.filter(pk=binding.pk, updated_at=request.binding_updated_at, last_check_ok=True).first()
    return current is not None and _digest(_declaration_snapshot(declaration)) == request.declaration_digest


def _progress(request, **fields) -> bool:
    """Record progress only while this exact admission is still the one in flight."""
    return bool(UnblockRequest.objects.filter(
        pk=request.pk, status=UnblockRequest.RUNNING, binding_updated_at=request.binding_updated_at,
        declaration_digest=request.declaration_digest, created_at=request.created_at,
    ).update(**fields))


def _walk_studio(request, binding, studio) -> int:
    from apps.datasources.status import blocked_reports, referenced_names
    from apps.reports.models import Report
    from apps.runner.services import enqueue
    from trellum.meta import read_meta

    queued = 0
    reports = Report.objects.filter(
        studio=studio, present_in_scan=True, disabled=False
    ).exclude(slug__in=blocked_reports(studio)).select_related("studio")
    for report in reports:
        if binding.name not in referenced_names(report.config):
            continue
        blocked_by = read_meta(str(studio.output_dir / report.slug)).get("blocked_by") or []
        if report.last_built_at is None or binding.name in blocked_by:
            # Only the final DB decision is locked; file reads stay outside.
            with transaction.atomic():
                if not _is_current(request, lock=True):
                    return -1
                if enqueue(report, trigger="manual") == "queued":
                    queued += 1
    return queued


def process_next(*, now=None) -> bool:
    """Claim one admitted request and advance it by one studio. Returns whether
    there was work. Safe to call from several runners."""
    from apps.studios.models import Studio

    now = now or timezone.now()
    with transaction.atomic():
        request = (
            UnblockRequest.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(status__in=[UnblockRequest.PENDING, UnblockRequest.RUNNING])
            .filter(Q(lease_until__isnull=True) | Q(lease_until__lt=now))
            .select_related("binding").order_by("created_at").first()
        )
        if request is None:
            return False
        if request.attempts >= MAX_ATTEMPTS:
            request.status = UnblockRequest.DONE
            request.lease_until = None
            request.save(update_fields=["status", "lease_until", "updated_at"])
            logger.warning("unblock request abandoned", extra={"binding_id": request.binding_id})
            return True
        request.status = UnblockRequest.RUNNING
        request.attempts += 1
        request.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        request.save(update_fields=["status", "attempts", "lease_until", "updated_at"])

    binding = request.binding
    finish = {"status": UnblockRequest.DONE, "lease_until": None}
    if not _is_current(request):
        _progress(request, **finish)
        return True
    studios = Studio.objects.filter(pk__gt=request.cursor_studio_id)
    studios = studios.filter(pk=binding.studio_id) if binding.studio_id else studios.filter(org_id=binding.org_id)
    studio = studios.order_by("pk").first()
    if studio is None:
        _progress(request, **finish)
        return True
    queued = _walk_studio(request, binding, studio)
    if queued < 0:  # superseded mid-walk
        _progress(request, **finish)
        return True
    _progress(
        request, cursor_studio_id=studio.pk, queued=request.queued + queued,
        attempts=0, lease_until=None,
    )
    return True
