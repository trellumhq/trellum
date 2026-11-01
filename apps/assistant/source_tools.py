"""Assistant source tools share durable jobs and the caller's current grants."""
from __future__ import annotations

import json
import uuid

from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.assistant import source_queries
from apps.assistant.source_access import grants_for, resolve_grant
from apps.core import roles


def _schema(name, description, properties, required=()):
    return {"name": name, "description": description, "mutates": False, "role": roles.VIEWER,
            "input_schema": {"type": "object", "properties": properties, "required": list(required)}}


SOURCE_SCHEMAS = [
    _schema("list_data_sources", "List data sources and schema-qualified relations you may query. Source IDs are grant IDs. Use search to narrow a truncated list.", {"search": {"type": "string"}}),
    _schema("describe_source", "Read permitted column names and types from a source. Starts a durable read; the application waits or resumes automatically. Never query a source before understanding its schema.",
            {"source_id": {"type": "integer"}}, ["source_id"]),
    _schema("query_source", "Run one read-only PostgreSQL analytical SELECT against permitted schema-qualified relations. Joins, CTEs and aggregations are allowed. Use canonical metric definitions where available; otherwise explain the calculation. Results may be truncated; compute aggregates in SQL. The application handles waiting, do not repeatedly submit the same query.",
            {"source_id": {"type": "integer"}, "sql": {"type": "string"}}, ["source_id", "sql"]),
]
SOURCE_NAMES = frozenset(s["name"] for s in SOURCE_SCHEMAS)


def available(toolbox):
    return bool(toolbox.actor is not None and toolbox.session is not None
                and not toolbox.is_report_bound and not toolbox.report_id)


def status_url(studio, session, query_id):
    return reverse("studio-assistant-source-query", kwargs={
        "org_slug": studio.org.slug, "studio_slug": studio.slug,
        "session_id": session.pk, "query_id": query_id})


def evidence(job, payload):
    result = payload.get("result")
    if not result:
        return None
    return {"schema_version": 1, "status": "answered", "policy_revision": job.policy_fingerprint, "kind": "source_query", "query_id": str(job.pk), "source_id": job.grant_id,
            "assurance": "Exploratory query", "query": result["query"],
            "executed_at": result["executed_at"], "columns": result["columns"],
            "rows": result["rows"], "truncated": result["truncated"],
            "rows_returned": len(result["rows"])}


def result_for(toolbox, job):
    payload = source_queries.public_result(job, actor=toolbox.actor, studio=toolbox.studio, session=toolbox.session)
    payload["status_url"] = status_url(toolbox.studio, toolbox.session, payload["id"])
    toolbox.provenance = evidence(job, payload)
    if payload.get("result"):
        toolbox.session.state.setdefault("_source_policies", {})[str(job.grant_id)] = job.policy_fingerprint
    return payload


def execute(toolbox, name, args):
    if not available(toolbox):
        return "Error: Start a studio conversation to use your authorized data sources."
    try:
        if name == "list_data_sources":
            search = str(args.get("search") or "")[:100]
            grants = list(grants_for(toolbox.studio, toolbox.actor, session=toolbox.session)
                          .filter(datasource__name__icontains=search)[:26])
            from apps.assistant.source_models import validate_relations
            grants = [g for g in grants if not any(v for k, v in g.datasource.all_fields().items()
                                                   if k.startswith("ssh_"))]
            for grant in grants:
                validate_relations(grant.relations)
            payload = {"sources": [{"source_id": g.pk, "name": g.datasource.name,
                                     "type": "postgres", "relations": g.relations}
                                    for g in grants[:25]], "truncated": len(grants) > 25}
            if len(json.dumps(payload).encode()) > 65536:
                return "Error: Source metadata exceeds the discovery limit; narrow the source grants."
            from apps.assistant.source_access import policy_fingerprint
            pins = toolbox.session.state.setdefault("_source_policies", {})
            for grant in grants[:25]:
                pins[str(grant.pk)] = policy_fingerprint(grant)
            return json.dumps(payload)
        if toolbox.session.state.get("_source_steps", 0) >= 12:
            return "Error: This question reached its source-read limit. Explain the evidence collected and ask the user to narrow the remaining question."
        key = getattr(toolbox, "source_call_id", None) or uuid.uuid4().hex
        job, created = source_queries.admit(
            studio=toolbox.studio, actor=toolbox.actor, session=toolbox.session,
            grant_id=args.get("source_id"), kind="describe" if name == "describe_source" else "query",
            sql=args.get("sql", ""), idempotency_key=key)
        if created:
            toolbox.session.state["_source_steps"] = toolbox.session.state.get("_source_steps", 0) + 1
        payload = result_for(toolbox, job)
        if payload["state"] in ("accepted", "running"):
            toolbox.source_pending = str(job.pk)
        return json.dumps(payload)
    except PermissionDenied as exc:
        return "Error: " + str(exc)
    except source_queries.PendingLimitReached:
        return "Error: A source read is already queued or running. Wait for its result before submitting another."
    except ValueError:
        return "Error: This source query is not supported. Use one read-only SELECT over the granted schema-qualified relations."
    except Exception:
        return "Error: Source reads are temporarily unavailable."


def check_history(session, *, actor, studio):
    """Revoked source evidence must not be replayed to a model or browser."""
    reports = (session.state or {}).get("_metric_reports", [])
    if reports:
        from apps.assistant.tools import AssistantToolbox
        if not isinstance(reports, list) or len(reports) > 100:
            raise PermissionDenied("Start a new conversation; this metric history is too large to authorize.")
        box = AssistantToolbox(studio, actor=actor, session=session, scope=session.scope, report=session.report)
        if any(box._access_error(slug) for slug in reports):
            raise PermissionDenied("Report access changed. Start a new conversation with your current permissions.")
    policies = (session.state or {}).get("_source_policies", {})
    if len(policies) > 100:
        raise PermissionDenied("Start a new conversation; this source history is too large to authorize.")
    from apps.assistant.source_access import policy_fingerprint
    for grant_id, fingerprint in policies.items():
        grant = resolve_grant(studio, actor, grant_id, session=session)
        if policy_fingerprint(grant) != fingerprint:
            raise PermissionDenied("Source access changed. Start a new conversation with your current permissions.")


def resume(session, *, actor, studio, query_id):
    """Replace the pending tool response; never append a synthetic user message."""
    from apps.assistant.models import SourceQuery

    pending = (session.state or {}).get("_source_pending")
    if not pending or pending.get("id") != str(query_id):
        raise ValueError("This conversation is not waiting for that source read.")
    job = SourceQuery.objects.get(pk=query_id, session=session, user=actor, studio=studio)
    payload = source_queries.public_result(job, actor=actor, studio=studio, session=session)
    if payload["state"] in SourceQuery.ACTIVE_STATES:
        return False
    prov = evidence(job, payload)
    if prov:
        session.state.setdefault("_source_policies", {})[str(job.grant_id)] = job.policy_fingerprint
    for entry in reversed(session.transcript):
        if entry.get("role") == "tool" and entry.get("tool_use_id") == pending["tool_use_id"]:
            entry["result"] = '<data source="source-query">' + json.dumps(payload) + "</data>"
            entry["provenance"] = prov
            entry["is_error"] = payload["state"] != SourceQuery.SUCCEEDED
            break
    else:
        raise ValueError("The pending source response is unavailable.")
    session.state.pop("_source_pending", None)
    return True
