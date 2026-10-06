"""MCP over streamable HTTP: the agent door to the assistant toolbox.

One POST endpoint per studio (``/s/<org>/<studio>/mcp``) speaking JSON-RPC
2.0 with plain JSON answers -- no SSE stream, no session id. A personal API
key is the only credential: the customer's own coding agent brings its own
model and sees exactly what the key's owner would see in the panel. The
toolbox decides what that is (role, scope, org policy); this view only
transports.

A mutating tool runs at once under a ``write`` key -- no proposal row: the
caller's harness asks them before every tool call, and that is the approval.
The audit row carries the key id (``audit()`` adds it) and ``via=mcp``.
Secrets never travel this way: ``configure_data_source`` answers with the
portal page where they are typed. Hand-rolled on purpose; the ``mcp``
package is for when the protocol churns. Design: internal documentation
``internal design notes`` §4, §7 decision 1, §10.
"""
from __future__ import annotations

import json
import logging
import re
from functools import wraps
from urllib.parse import quote

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.assistant import actions
from apps.assistant.tools import TOOL_SCHEMAS, AssistantToolbox
from apps.assistant.views import caller_role
from apps.core import roles
from apps.core.permissions import require_studio_role
from apps.core.report_access import selected_report_access_block_reason
from apps.datasources.models import CREDENTIAL_KEYS
from trellum_portal import __version__

logger = logging.getLogger(__name__)

#: Answered when the client names none; a client's own version is echoed.
PROTOCOL_VERSION = "2025-06-18"
INSTRUCTIONS = (
    "Tools over this studio's built reports and its portal configuration. "
    "Tools described as proposals run immediately here, under your API key; "
    "your harness asks before each call. Never pass a password, key or token "
    "as an argument: configure_data_source returns the portal page where "
    "secrets are entered."
)
_BY_NAME = {s["name"]: s for s in TOOL_SCHEMAS}


def _rpc(id_, result):
    return JsonResponse({"jsonrpc": "2.0", "id": id_, "result": result})


def _error(id_, code: int, message: str):
    return JsonResponse({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def _text(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _api_key_only(view):
    """A browser session is not a credential here, so CSRF never applies."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if getattr(request, "api_key", None) is None:
            return JsonResponse({"error": "api_key_required"}, status=401)
        return view(request, *args, **kwargs)

    return wrapper


def _toolbox(request, report=None) -> AssistantToolbox:
    from apps.orgs.models import OrgAssistantConfig

    # Read directly, not through LLMConfig.for_org: reads need no model
    # provider on our side (this door works with the assistant off), and the
    # org's actions switch is the same one the panel obeys.
    cfg = OrgAssistantConfig.objects.filter(org=request.org).first()
    return AssistantToolbox(
        request.studio,
        share_report_source=cfg.share_report_source if cfg else True,
        actor=request.user,
        scope="report" if report is not None else "full",
        report=report,
        role=caller_role(request),
        may_write=request.api_key.can_write and bool(cfg and cfg.actions_enabled),
    )


def _configure_url(request, name: str) -> str:
    """The studio's Configure form for one declared source, absolute."""
    return request.build_absolute_uri(
        f"/s/{request.org.slug}/{request.studio.slug}/settings/datasources"
        f"?configure={quote(name)}#configure"
    )


def _call(request, toolbox: AssistantToolbox, name: str, args: dict) -> dict:
    schema = _BY_NAME[name]
    if not toolbox.allowed(schema):
        if schema["mutates"] and not request.api_key.can_write:
            return _text("Error: write scope required -- this API key is read-only.", True)
        return _text(f"Error: '{name}' is not available to you in this studio.", True)

    report_scope = request.org_roles.report_scope_for(request.studio)
    if report_scope is not None:
        reason = selected_report_access_block_reason()
        if reason:
            return _text(f"Error: selected report access is unavailable: {reason}.", True)

        from apps.reports.models import Report

        reports = Report.objects.filter(
            studio=request.studio, present_in_scan=True, pk__in=report_scope
        ).order_by("name", "slug")
        if name == "list_reports":
            results = []
            for report in reports:
                result = _toolbox(request, report).execute(name, args)
                if result != "No reports matched.":
                    results.append(result)
            return _text("\n\n".join(results) if results else "No reports matched.")

        slug = str(args.get("slug") or args.get("report") or "").strip()
        if name == "read_doc":
            match = re.match(r"^reports/([^/]+)(?:/|$)", str(args.get("path") or ""))
            slug = match.group(1) if match else ""
        report = reports.filter(slug=slug).first()
        if report is None:
            return _text("Error: that report is not available to this API key.", True)
        toolbox = _toolbox(request, report)
        if not toolbox.allowed(schema):
            return _text(f"Error: '{name}' is not available to you in this studio.", True)

    if not schema["mutates"]:
        result = toolbox.execute(name, args)
        return _text(result, result.startswith("Error"))

    src = str(args.get("name") or "").strip()
    if name == "configure_data_source" and CREDENTIAL_KEYS & set(args):
        return _text(
            "Error: secrets are never accepted over MCP and nothing was stored. "
            f"Enter them at {_configure_url(request, src)}.", True,
        )
    spec = actions.describe(request.studio, name, args)
    if isinstance(spec, str):
        return _text(spec, True)
    if actions.rank(toolbox.role) < actions.rank(spec["required_role"]):
        return _text(f"Error: this needs the {spec['required_role']} role.", True)
    if name == "configure_data_source":
        # The declaration (host, port, ...) is the repository's; the portal
        # holds only the secrets, and those are typed in the browser.
        return _text(
            f"{spec['summary']}: enter {', '.join(spec['secret_fields'])} at "
            f"{_configure_url(request, src)}. Nothing was changed here."
        )
    try:
        return _text(actions.execute(request, name, spec["arguments"], {}, via="mcp"))
    except actions.ActionFailed as e:
        return _text(str(e), True)
    except Exception:  # noqa: BLE001 -- the agent must get an answer, not a 500
        logger.exception("mcp: %s failed", name)
        return _text("Error: the action failed unexpectedly.", True)


@csrf_exempt
@require_POST
@_api_key_only
@require_studio_role(roles.VIEWER)
def mcp(request, org_slug, studio_slug):  # noqa: ARG001
    try:
        msg = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _error(None, -32700, "Parse error")
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
    if "id" not in msg:  # a notification (notifications/initialized): nothing to answer
        return HttpResponse(status=202)
    id_, method, params = msg["id"], msg["method"], msg.get("params") or {}
    if not isinstance(params, dict):
        return _error(id_, -32602, "Invalid params")

    if method == "initialize":
        client_version = params.get("protocolVersion")
        return _rpc(id_, {
            "protocolVersion": client_version if isinstance(client_version, str) else PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "trellum", "version": __version__},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _rpc(id_, {})
    if method == "tools/list":
        return _rpc(id_, {"tools": [
            {"name": s["name"], "description": s["description"], "inputSchema": s["input_schema"]}
            for s in _toolbox(request).schemas
        ]})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            return _error(id_, -32602, "Invalid params: 'name' (string) and 'arguments' (object)")
        if name not in _BY_NAME:
            return _error(id_, -32602, f"Unknown tool: {name}")
        return _rpc(id_, _call(request, _toolbox(request), name, args))
    return _error(id_, -32601, f"Method not found: {method}")


# Every MCP method is a POST; scope is checked per tool in _call, not by the
# middleware's non-GET rule (apps.accounts.api_keys.ApiKeyMiddleware).
mcp.checks_api_key_scope = True
