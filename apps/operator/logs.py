"""Read-only instance diagnostics; never exposed to impersonated sessions."""
from __future__ import annotations

import json
import uuid
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from apps.core.impersonation import is_impersonating
from apps.core.models import ServerLogEvent
from apps.core.permissions import require_operator
from apps.core.server_logs import bounded_text, safe_context
from apps.runner.models import Run

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
OUTPUT_LIMIT = 50_000


def protected(view):
    @wraps(view)
    def private(request, *args, **kwargs):
        if is_impersonating(request):
            raise Http404
        return view(request, *args, **kwargs)

    authorized = require_operator(require_GET(private))

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            response = authorized(request, *args, **kwargs)
        except Http404:
            response = HttpResponse("Not found", status=404)
        response["Cache-Control"] = "no-store"
        return response
    return wrapped


def _number(params, name, default=None, maximum=2**63 - 1):
    raw = params.get(name)
    if raw is None:
        return default
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 19:
        raise ValueError(f"Invalid {name}")
    number = int(raw)
    if not 1 <= number <= maximum:
        raise ValueError(f"Invalid {name}")
    return number


def _query(params):
    query = ServerLogEvent.objects.all()
    for name, length in {"service": 32, "worker_id": 100, "run_id": 36,
                         "report_slug": 200, "request_id": 200}.items():
        value = params.get(name, "")
        if len(value) > length:
            raise ValueError(f"Invalid {name}")
        if value:
            if name == "run_id":
                try:
                    value = str(uuid.UUID(value))
                except ValueError:
                    raise ValueError("Invalid run_id") from None
            query = query.filter(**{name: value})
    level = params.get("level", "")
    if level:
        if level not in LEVELS:
            raise ValueError("Invalid level")
        query = query.filter(level__in=LEVELS[LEVELS.index(level):])
    times = {}
    for name, lookup in (("since", "gte"), ("until", "lte")):
        raw = params.get(name, "")
        if raw:
            if len(raw) > 64:
                raise ValueError(f"Invalid {name}")
            try:
                value = parse_datetime(raw)
            except ValueError:
                value = None
            if value is None or timezone.is_naive(value):
                raise ValueError(f"Invalid {name}: include a timezone")
            times[name] = value
            query = query.filter(**{f"timestamp__{lookup}": value})
    if "since" in times and "until" in times and times["since"] > times["until"]:
        raise ValueError("since must precede until")
    text = params.get("q", "")
    if len(text) > 200:
        raise ValueError("Search is limited to 200 characters")
    if text:
        query = query.filter(Q(message__icontains=text) | Q(exception__icontains=text) | Q(logger__icontains=text))
    before = _number(params, "before")
    after = _number(params, "after")
    if before is not None and after is not None:
        raise ValueError("Use either before or after")
    if before:
        query = query.filter(id__lt=before)
    if after:
        query = query.filter(id__gt=after)
    return query.order_by("id" if after else "-id"), after


def serialize(event):
    return {
        "id": event.id, "time": event.timestamp.isoformat(), "level": event.level,
        "service": event.service, "host": event.host, "process": event.process,
        "logger": event.logger, "message": bounded_text(event.message, 8192),
        "exception": bounded_text(event.exception, 16384), "context": safe_context(event.context),
        "worker_id": event.worker_id, "run_id": event.run_id,
        "report_slug": event.report_slug, "request_id": event.request_id, "trigger": event.trigger,
    }


def coverage():
    return {
        "enabled": settings.SERVER_LOG_CAPTURE_ENABLED,
        "retention_days": max(1, settings.SERVER_LOG_RETENTION_DAYS),
        "max_rows": max(1, settings.SERVER_LOG_MAX_ROWS),
        "source": "Python application logging from processes sharing this database",
        "limitations": "Best-effort capture: queue overflow, database outages and abrupt process exits can lose events. Console output remains the fallback. Proxy, container and report subprocess output are not collected here; report output is available as bounded run tails.",
        "redaction": "Common credential formats are scrubbed; arbitrary secrets in free text may remain.",
    }


@protected
def page(request):
    from apps.operator import nav
    return render(request, "operator/logs.html", {"operator_nav": nav.items(), "coverage": coverage()})


@protected
def events(request):
    try:
        query, after = _query(request.GET)
        limit = _number(request.GET, "limit", default=100, maximum=200)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    rows = list(query[:limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    return JsonResponse({
        "entries": [serialize(row) for row in rows], "has_more": more,
        "next_before": min((row.id for row in rows), default=None),
        "next_after": max((row.id for row in rows), default=after), "coverage": coverage(),
    })


@protected
def export(request):
    try:
        query, _ = _query(request.GET)
        limit = _number(request.GET, "limit", default=1000, maximum=1000)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    rows = list(query[:limit + 1])
    response = HttpResponse(
        "".join(json.dumps(serialize(row)) + "\n" for row in rows[:limit]),
        content_type="application/x-ndjson",
    )
    response["Content-Disposition"] = 'attachment; filename="server-logs.ndjson"'
    response["X-Log-Export-Limit"] = "1000"
    response["X-Log-Export-Truncated"] = "true" if len(rows) > limit else "false"
    return response


@protected
def run_detail(request, run_id):
    from apps.runner.executor import read_tail

    run = get_object_or_404(Run.objects.select_related("studio__org"), id=run_id)
    stdout, stderr = run.stdout_tail, run.stderr_tail
    source = "stored tails"
    truncated = len(stdout) >= OUTPUT_LIMIT or len(stderr) >= OUTPUT_LIMIT
    if run.is_active and run.log_dir:
        source = "active file tails"
        for name in ("stdout", "stderr"):
            path = Path(run.log_dir) / f"{name}.log"
            try:
                truncated |= path.stat().st_size >= OUTPUT_LIMIT
            except OSError:
                pass
            text = read_tail(path, max_bytes=OUTPUT_LIMIT)
            if name == "stdout":
                stdout = text or stdout
            else:
                stderr = text or stderr
    data = run.to_history_dict()
    data.update({
        "id": str(run.id), "worker_id": run.worker_id, "studio": run.studio.slug,
        "org": run.studio.org.slug,
        "stdout": bounded_text(stdout[-OUTPUT_LIMIT:], OUTPUT_LIMIT),
        "stderr": bounded_text(stderr[-OUTPUT_LIMIT:], OUTPUT_LIMIT),
        "output_truncated": truncated, "output_source": source,
        "output_note": "Only retained output tails (up to 50,000 characters each) are available; active output may be partial and is read only when accessible on this host.",
    })
    return JsonResponse({"run": data})
