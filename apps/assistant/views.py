"""AI assistant endpoints.

Two families of route:

* ``/api/assistant/widget.js`` and ``/api/assistant/available`` are instance-level.
  The bundle URL is a frozen contract — the framework's extensions block and
  every already-built report page reference it — so it must stay
  byte-identical in URL and self-contained in content.
* everything else is studio-scoped under
  ``/s/<org>/<studio>/api/assistant/`` and gated on studio viewer access, because
  the assistant reads that studio's built report output.

Sessions are private to the user who created them: another user's id is a
404, not a 403 (existence is not leaked).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.assistant import actions
from apps.assistant import budget as budget_mod
from apps.assistant import llm
from apps.assistant.models import AssistantSession, ProposedAction, infer_title
from apps.assistant.system_prompt import build_system_prompt
from apps.assistant.tools import AssistantToolbox
from apps.core import roles
from apps.core.audit import audit
from apps.core.http import json_body
from apps.core.permissions import effective_roles, get_effective, require_studio_role
from apps.core.report_access import can_view_report, selected_report_access_block_reason

logger = logging.getLogger(__name__)

_WIDGET_LOCK = threading.Lock()
_WIDGET_CACHE: str | None = None

_STUDIO_PATH_RE = re.compile(r"^/s/([a-z0-9-]+)/([a-z0-9-]+)(?:/|$)")
_REPORT_PATH_RE = re.compile(r"/r/([A-Za-z0-9_-]+)/")


def _widget_bundle() -> str:
    """assistant.css + assistant.js as one self-injecting script (cached)."""
    global _WIDGET_CACHE
    with _WIDGET_LOCK:
        if _WIDGET_CACHE is None:
            static_dir = settings.BASE_DIR / "static"
            css = (static_dir / "assistant.css").read_text(encoding="utf-8")
            js = (static_dir / "assistant.js").read_text(encoding="utf-8")
            _WIDGET_CACHE = (
                "(function(){var s=document.createElement('style');"
                "s.textContent=" + json.dumps(css) + ";"
                "document.head.appendChild(s);})();\n" + js
            )
        return _WIDGET_CACHE


def widget_js(request):  # noqa: ARG001
    # No auth: static code; every API call it makes is auth-gated.
    return HttpResponse(_widget_bundle(), content_type="application/javascript")


# ── Availability ────────────────────────────────────────────────────────────

def _org_from_request(request):
    """Best-effort org for the instance-level availability probe.

    Priority: an explicit ``?org=`` slug, then the studio path in the
    referring page (report pages live at /s/<org>/<studio>/r/...), then the
    user's org when they belong to exactly one. Membership is always checked.
    """
    from apps.orgs.models import Organization, OrgMembership

    slug = (request.GET.get("org") or "").strip()
    if not slug:
        referer = request.META.get("HTTP_REFERER") or ""
        path = referer
        for marker in ("://",):
            if marker in path:
                path = "/" + path.split(marker, 1)[1].split("/", 1)[-1]
        m = _STUDIO_PATH_RE.match(path)
        if m:
            slug = m.group(1)
    if slug:
        org = Organization.objects.filter(slug=slug, is_active=True).first()
        if org is None or not get_effective(request, org).is_member:
            return None
        return org

    memberships = list(
        OrgMembership.objects.filter(user=request.user, org__is_active=True).select_related("org")[:2]
    )
    if len(memberships) == 1:
        return memberships[0].org
    return None


def available(request):
    """Instance-level probe kept for backward compatibility.

    Report pages built before the studio-scoped route existed still call this.
    It resolves the org from the query string, the referring studio path or a
    single-org membership, and answers ``available: false`` when it cannot.
    """
    if not request.user.is_authenticated:
        return JsonResponse(
            {"available": False, "reason": "Log in to the portal to use the AI assistant."}
        )
    org = _org_from_request(request)
    if org is None:
        return JsonResponse(
            {"available": False, "reason": "No organization context for the AI assistant."}
        )
    ok, reason = llm.is_available(org)
    return JsonResponse({"available": ok, "reason": reason})


def _suggestions(toolbox, path: str) -> list[str]:
    """Up to three opening questions for where the viewer is. No model call.

    On a report page: summarise it, explain its first chart, ask about its
    first dataset -- all read from the report's own _meta.json. Elsewhere:
    the studio's three largest categories, or one generic prompt.
    """
    m = _REPORT_PATH_RE.search(path)
    if m:
        if toolbox.is_report_bound and m.group(1) != toolbox.report_slug:
            return []
        details = toolbox.read_meta(m.group(1)).get("details") or {}
        out = ["Summarise this report"]
        # An untitled component reports its class name as its title; that is
        # not something a viewer would ask to have explained.
        charts = [
            r.get("chart_title") for r in details.get("filter_matrix") or []
            if r.get("chart_title") and r.get("chart_title") != r.get("chart_kind")
        ]
        if charts:
            out.append(f"Explain {charts[0]}")
        datasets = [d.get("id") for d in details.get("datasets") or [] if d.get("id")]
        if datasets:
            out.append(f"What does {datasets[0]} show over the last 4 weeks?")
        return out[:3]
    counts: dict[str, int] = {}
    for r in toolbox.reports():
        cat = r.get("category") or "Uncategorized"
        if cat != "Uncategorized":
            counts[cat] = counts.get(cat, 0) + 1
    top = sorted(counts, key=lambda c: (-counts[c], c))[:3]
    return [f"What's in {c}?" for c in top] or ["Which reports cover revenue?"]


@require_studio_role(roles.VIEWER)
def studio_available(request, org_slug, studio_slug):  # noqa: ARG001
    from django.middleware.csrf import get_token
    from apps.orgs.models import OrgAssistantConfig

    # The widget probes this before anything else, including from a built
    # report page (which renders no form). Minting the token here guarantees
    # the csrftoken cookie exists for the POSTs that follow.
    get_token(request)
    ok, reason = llm.is_available(request.org)
    scope = request.org_roles.report_scope_for(request.studio)
    report = None
    if ok and scope is not None:
        if selected_report_access_block_reason():
            ok = False
            reason = "Selected report access is temporarily unavailable."
        else:
            match = _REPORT_PATH_RE.search(request.GET.get("path") or "")
            report = _report_for(request.studio, match.group(1)) if match else None
            if report is None or report.pk not in scope:
                ok = False
                reason = "Open a report you can view to use the AI assistant."
    toolbox = AssistantToolbox(
        request.studio,
        actor=request.user,
        scope="report" if scope is not None else "full",
        report=report,
    )
    can_configure = get_effective(request, request.org).is_org_admin
    return JsonResponse({
        "available": ok,
        "reason": reason,
        "configured": OrgAssistantConfig.objects.filter(org=request.org).exists(),
        "can_configure": can_configure,
        "settings_url": f"/orgs/{request.org.slug}/settings/assistant" if can_configure else None,
        "suggestions": _suggestions(toolbox, request.GET.get("path") or "") if ok else [],
        "deadline_s": settings.ASSISTANT_TURN_DEADLINE_S,
    })


# ── Budget ──────────────────────────────────────────────────────────────────

@require_studio_role(roles.VIEWER)
def budget(request, org_slug, studio_slug):  # noqa: ARG001
    return JsonResponse(budget_mod.usage_state(request.org, request.user))


# ── Sessions ────────────────────────────────────────────────────────────────

def _session_only(view):
    """The panel is the human door; a bearer API key never reaches the chat
    endpoints (the agent door is :mod:`apps.assistant.mcp` -- design §4)."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if getattr(request, "api_key", None) is not None:
            return JsonResponse({"error": "session_required"}, status=403)
        return view(request, *args, **kwargs)

    return wrapper


def _session_rows(request):
    rows = AssistantSession.objects.filter(
        user=request.user, org=request.org, studio=request.studio
    ).select_related("report", "alert_run__rule")
    scope = effective_roles(request.user, request.org).report_scope_for(request.studio)
    if scope is not None and selected_report_access_block_reason():
        return rows.none()
    if scope is None:
        return rows.filter(Q(scope=AssistantSession.SCOPE_FULL) | Q(
            scope=AssistantSession.SCOPE_REPORT,
            report__isnull=False,
            report__studio=request.studio,
            report__present_in_scan=True,
        ))
    return rows.filter(
        scope=AssistantSession.SCOPE_REPORT,
        report_id__in=scope,
        report__isnull=False,
        report__studio=request.studio,
        report__present_in_scan=True,
    )


def _get_session(request, session_id: int) -> AssistantSession:
    """One of the requesting user's sessions in this studio, or 404."""
    return get_object_or_404(_session_rows(request), pk=session_id)


def _report_for(studio, slug: str):
    if not slug:
        return None
    from apps.reports.models import Report

    return Report.objects.filter(studio=studio, slug=slug, present_in_scan=True).first()


def caller_role(request) -> str:
    """What the catalogue compares against: ``org_admin`` for an organization
    admin, else the effective studio role the decorator resolved."""
    return actions.ORG_ADMIN if request.org_roles.is_org_admin else request.studio_role


@require_studio_role(roles.VIEWER)
@require_http_methods(["GET", "POST"])
@_session_only
def sessions(request, org_slug, studio_slug):  # noqa: ARG001
    if request.method == "GET":
        rows = _session_rows(request)
        return JsonResponse({"sessions": [s.to_summary() for s in rows]})

    body = json_body(request)
    report_slug = (body.get("report") or "").strip()
    report = _report_for(request.studio, report_slug)
    full = effective_roles(request.user, request.org).has_full_studio_visibility(request.studio)
    if not full:
        if selected_report_access_block_reason():
            return JsonResponse(
                {"error": "Selected report access is temporarily unavailable."}, status=503
            )
        if not report_slug:
            return JsonResponse({"error": "A report is required for this conversation."}, status=400)
        if report is None or not can_view_report(request.user, report):
            raise Http404
    session = AssistantSession.objects.create(
        user=request.user,
        org=request.org,
        studio=request.studio,
        report=report,
        scope=AssistantSession.SCOPE_FULL if full else AssistantSession.SCOPE_REPORT,
        title="New conversation",
    )
    return JsonResponse(session.to_full(), status=201)


@require_studio_role(roles.VIEWER)
@require_http_methods(["GET"])
@_session_only
def from_alert(request, org_slug, studio_slug, run_id):
    """The alert email's "open in the assistant" link: a new conversation
    bound to that evaluation, opened on the report's page. The widget reads
    ``?assistant=<id>`` at load (the ``?rdw=1`` deep-link shape), adopts the
    session for this studio and opens the panel."""
    from django.shortcuts import redirect

    from apps.alerts.models import AlertRun

    run = get_object_or_404(
        AlertRun.objects.select_related("rule__report"), pk=run_id, rule__studio=request.studio
    )
    permissions = effective_roles(request.user, request.org)
    full = permissions.has_full_studio_visibility(request.studio)
    if not full and selected_report_access_block_reason():
        return JsonResponse(
            {"error": "Selected report access is temporarily unavailable."}, status=503
        )
    if not can_view_report(request.user, run.rule.report):
        raise Http404
    session = AssistantSession.objects.create(
        user=request.user, org=request.org, studio=request.studio, report=run.rule.report,
        alert_run=run,
        scope=AssistantSession.SCOPE_FULL if full else AssistantSession.SCOPE_REPORT,
        title=f"Alert: {run.title or run.rule.name}"[:200],
    )
    return redirect(f"/s/{org_slug}/{studio_slug}/r/{run.rule.report.slug}/?assistant={session.pk}")


@require_studio_role(roles.VIEWER)
@require_http_methods(["GET", "DELETE"])
@_session_only
def session_detail(request, org_slug, studio_slug, session_id):  # noqa: ARG001
    session = _get_session(request, session_id)
    if request.method == "DELETE":
        from apps.core.audit import audit

        # A deleted transcript is gone for good; the trail keeps that it was.
        audit(request, "assistant.session.delete", target=session, title=session.title,
              message_count=session.to_summary()["message_count"])
        session.delete()
        return JsonResponse({"ok": True})
    return JsonResponse(session.to_full())


# ── The turn: SSE ───────────────────────────────────────────────────────────

def _frame(event: dict) -> bytes:
    payload = json.dumps(event, default=str)
    return (f"event: {event.get('type', 'message')}\n" f"data: {payload}\n\n").encode("utf-8")


def _page_context(page) -> str | None:
    if not isinstance(page, dict):
        return None
    path = str(page.get("path") or "")[:200]
    title = str(page.get("title") or "")[:200]
    if not (path or title):
        return None
    ctx = f"{title} ({path})" if title else path
    chart = page.get("chart")
    if isinstance(chart, dict) and chart.get("title"):
        ctx += (
            f". The viewer is asking about chart '{str(chart.get('title'))[:200]}' "
            f"({str(chart.get('kind') or 'chart')[:50]}, dataset "
            f"{str(chart.get('dataset_id') or 'unknown')[:100]})."
        )
    return ctx


@require_studio_role(roles.VIEWER)
@require_POST
@_session_only
def session_message(request, org_slug, studio_slug, session_id):  # noqa: ARG001
    session = _get_session(request, session_id)
    body = json_body(request)
    message = (body.get("message") or "").strip()
    if not message:
        return JsonResponse({"error": "missing 'message'"}, status=400)

    page = body.get("page") or {}
    if session.scope == AssistantSession.SCOPE_REPORT and isinstance(page, dict):
        match = _REPORT_PATH_RE.search(str(page.get("path") or ""))
        if match and match.group(1) != getattr(session.report, "slug", None):
            return JsonResponse(
                {"error": "Start a new conversation for this report.", "code": "report_context_changed"},
                status=409,
            )
    page_context = _page_context(page)

    ok, reason = llm.is_available(request.org)
    if not ok:
        return JsonResponse({"error": reason}, status=503)

    allowed, reason = budget_mod.precheck(request.org, request.user)
    if not allowed:
        return JsonResponse({"error": reason}, status=402)

    config = llm.LLMConfig.for_org(request.org)
    if config is None:  # raced with an admin disabling assistant
        return JsonResponse({"error": "The AI assistant is disabled for this organization."}, status=503)

    toolbox = AssistantToolbox(
        request.studio,
        share_report_source=config.share_report_source,
        actor=request.user,
        scope=session.scope,
        report=session.report,
        role=caller_role(request), may_write=config.actions_enabled, session=session,
    )

    if not session.transcript:
        session.title = infer_title(message)
    org, user = request.org, request.user
    deadline_s = settings.ASSISTANT_TURN_DEADLINE_S
    deadline = time.monotonic() + deadline_s

    def remaining() -> float:
        return deadline - time.monotonic()

    # A provider failure carries a technical ``detail`` line; only someone who
    # can fix the configuration gets to see it.
    is_admin = get_effective(request, org).is_org_admin

    # One reply at a time per conversation. A conditional UPDATE is the
    # whole lock: it succeeds only when no other turn holds the row, and the
    # claim expires with the deadline so a dead worker cannot wedge it.
    now = timezone.now()
    claimed = (
        AssistantSession.objects.filter(pk=session.pk)
        .filter(Q(in_flight_until__isnull=True) | Q(in_flight_until__lt=now))
        .update(in_flight_until=now + timedelta(seconds=deadline_s + 10))
    )
    if not claimed:
        return JsonResponse(
            {"error": "A reply is still streaming in another tab.", "code": "busy"}, status=409
        )

    def events():
        # turn_start goes out BEFORE the prompt is assembled. Building it walks
        # every built report in the studio to refresh the data catalog, and
        # doing that above this generator meant the browser waited on it with
        # an idle connection and no way to show that anything was happening.
        yield _frame({"type": "turn_start", "session_id": session.pk, "model": config.model})
        try:
            system_blocks = build_system_prompt(toolbox)
            for event in llm.stream_turn(
                config,
                session.state,
                message,
                toolbox=toolbox,
                system_blocks=system_blocks,
                page_context=page_context,
                remaining=remaining,
                deadline_s=deadline_s,
            ):
                if event.get("type") == "keepalive":
                    # An SSE comment, not an event: keeps the connection warm
                    # through a long tool call without the client parsing it.
                    yield b": keepalive\n\n"
                    continue
                if event.get("type") == "usage":
                    if event.get("cost_delta_usd") is not None:
                        budget_mod.record(org, user, event["cost_delta_usd"])
                if event.get("type") == "error" and not is_admin:
                    event.pop("detail", None)
                yield _frame(event)
                # Enforce the cap between tool-use iterations too: a single
                # user message can drive many API calls.
                if event.get("type") == "usage":
                    still_ok, why = budget_mod.precheck(org, user)
                    if not still_ok:
                        yield _frame({"type": "error", "code": "budget", "message": why})
                        break
        except Exception:  # provider exceptions can contain credentials or prompts
            logger.warning("assistant: turn failed (session %s)", session.pk)
            yield _frame({
                "type": "error", "code": "unavailable",
                "message": "The assistant hit an unexpected error. Try again.",
            })
        finally:
            session.in_flight_until = None  # release the claim with the same write
            try:
                session.save()
            except Exception:
                pass
        yield _frame({
            "type": "turn_end",
            "session_id": session.pk,
            "updated_at": session.updated_at.timestamp() if session.updated_at else None,
        })

    response = StreamingHttpResponse(events(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


# ── Proposed actions: the user's decision ───────────────────────────────────

def _open_proposal(request, proposal_id: int):
    """The caller's own, still-open proposal, or the JSON refusal to send.

    Private like sessions (another user's id is a 404). Decided is 409,
    expired is 410 -- and a stale card is marked expired on the way out. A
    streaming turn holds the transcript, so a decision waits for it (409
    ``busy``, the same answer a second message gets).
    """
    proposal = get_object_or_404(
        ProposedAction.objects.select_related("session", "session__report"),
        pk=proposal_id,
        user=request.user,
        org=request.org,
        studio=request.studio,
        session__in=_session_rows(request),
    )
    now = timezone.now()
    if proposal.status != ProposedAction.PROPOSED:
        return None, JsonResponse(
            {"error": f"This action was already {proposal.status}.", "status": proposal.status},
            status=409,
        )
    if proposal.expires_at < now:
        proposal.status = ProposedAction.EXPIRED
        proposal.save(update_fields=["status"])
        return None, JsonResponse(
            {"error": "This proposal has expired. Ask again.", "status": proposal.status}, status=410
        )
    until = proposal.session.in_flight_until
    if until and until > now:
        return None, JsonResponse(
            {"error": "A reply is still streaming in another tab.", "code": "busy"}, status=409
        )
    return proposal, None


def _close_proposal(proposal: ProposedAction, status: str, result: str) -> JsonResponse:
    """Record the decision on the row, on the card's transcript copy and as a
    tool entry, then answer with the line the panel sends back as the next
    user turn."""
    proposal.status, proposal.result, proposal.decided_at = status, result, timezone.now()
    proposal.save(update_fields=["status", "result", "decided_at"])
    verb = "rejected" if status == ProposedAction.REJECTED else "approved"
    line = f"Action #{proposal.pk} {verb}" + (f": {result}" if result else ".")
    session = proposal.session
    for entry in session.transcript:
        if (entry.get("proposal") or {}).get("id") == proposal.pk:
            entry["proposal"].update(status=status, result=result)
    session.transcript.append({
        "role": "tool",  # no tool_use_id: the panel's record, not replayed to the model
        "name": proposal.tool,
        "proposal_id": proposal.pk,
        "input": proposal.arguments,
        "result": line,
        "is_error": status == ProposedAction.FAILED,
        "ts": time.time(),
    })
    session.save(update_fields=["state", "updated_at"])
    return JsonResponse({"id": proposal.pk, "status": status, "result": result, "message": line})


@require_studio_role(roles.VIEWER)
@require_POST
@_session_only
def proposal_approve(request, org_slug, studio_slug, proposal_id):  # noqa: ARG001
    """Run the action under the approver's CURRENT role and org policy --
    both may have changed since the model proposed it. The body carries the
    values for ``secret_fields``; they reach the action and nothing else."""
    proposal, refusal = _open_proposal(request, proposal_id)
    if refusal is not None:
        return refusal
    if proposal.session.scope == AssistantSession.SCOPE_REPORT and (
        proposal.tool != "run_report"
        or proposal.arguments.get("slug") != proposal.session.report.slug
    ):
        return JsonResponse({"error": "You can no longer approve this action."}, status=403)
    config = llm.LLMConfig.for_org(request.org)
    if (
        config is None or not config.actions_enabled
        or actions.rank(caller_role(request)) < actions.rank(proposal.required_role)
    ):
        return JsonResponse({"error": "You can no longer approve this action."}, status=403)
    body = json_body(request)
    secrets = {k: body[k] for k in proposal.secret_fields if body.get(k)}
    try:
        result = actions.execute(
            request, proposal.tool, proposal.arguments, secrets, proposal_id=proposal.pk
        )
        status = ProposedAction.EXECUTED
    except actions.ActionFailed as e:
        result, status = str(e), ProposedAction.FAILED
    except Exception:  # noqa: BLE001 -- the card must get an answer
        logger.exception("assistant: proposal %s failed", proposal.pk)
        result, status = "Error: the action failed unexpectedly.", ProposedAction.FAILED
    audit(
        request, "assistant.proposal.approve", target=proposal,
        tool=proposal.tool, proposal_id=proposal.pk, status=status,
    )
    return _close_proposal(proposal, status, result)


@require_studio_role(roles.VIEWER)
@require_POST
@_session_only
def proposal_reject(request, org_slug, studio_slug, proposal_id):  # noqa: ARG001
    proposal, refusal = _open_proposal(request, proposal_id)
    if refusal is not None:
        return refusal
    reason = str(json_body(request).get("reason") or "").strip()[:500]
    audit(
        request, "assistant.proposal.reject", target=proposal,
        tool=proposal.tool, proposal_id=proposal.pk, reason=reason,
    )
    return _close_proposal(proposal, ProposedAction.REJECTED, reason)
