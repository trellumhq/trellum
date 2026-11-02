"""Chat delivery gate: reconcile cells, record assessments, never certify prose.

This is an orchestration tool, deliberately absent from the public MCP toolbox.
The provider remains responsible for question interpretation and SQL suitability;
the deterministic contract establishes only evidence identity, context and cells.
"""
from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal

NAME = "submit_answer"
MAX_ATTEMPTS = 3
MAX_BYTES = 24000
CHECKS = (
    "question_interpretation", "business_meaning", "assumptions", "source_suitability",
    "period_bounds_timezone", "filters", "grain_units", "data_quality", "reconciliation",
)
STATES = ("assessed", "unknown", "not_applicable")
MODES = ("analysis", "clarification", "limitation", "greeting", "help", "action")
FIELDS = ("business_meaning", "source", "period", "timezone", "filters", "grain", "units")


def _object(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


_REF = _object({"tool_result_id": {"type": "string"}, "evidence_hash": {"type": "string"}})
_CHECK = _object({"state": {"type": "string", "enum": list(STATES)},
                  "note": {"type": "string"},
                  "evidence": {"type": "array", "items": _REF}})
SCHEMA = {
    "name": NAME,
    "description": (
        "Finish this chat question. Ordinary prose is private draft and will never be delivered. "
        "Submit this tool ALONE after data tools finish. Analysis has no free-form conclusion: the server renders "
        "the referenced cells and execution context. Each claim must echo the exact context from the evidence "
        "reference, identify a zero-based row and column name, and optionally provide an expected JSON value. "
        "Assess every BA check, with notes and explicit evidence references; unknown semantics must remain unknown. "
        "Checks are model judgments, never proof of intent/SQL equivalence. A complete_total requires an "
        "untruncated single-row aggregate. Use clarification/limitation when evidence or definitions are missing. "
        "Use greeting/help for ordinary chat and action only after an actual action proposal. For an earlier action, explicitly provide its proposal_id. "
        "No arbitrary prose is delivered by these modes. Limits: 24KB submission, 12 claims, 9 checks, "
        "500 characters per assessment note, 8 references per check."
    ),
    # Detailed validation lives in submit(), so malformed submissions receive
    # bounded correction feedback rather than terminating the provider protocol.
    "input_schema": {"type": "object", "properties": {
        "mode": {"type": "string", "enum": list(MODES)},
        "proposal_id": {"type": "integer", "minimum": 1},
        "checks": _object({key: _CHECK for key in CHECKS}),
        "claims": {"type": "array", "items": _object({
            **_REF["properties"], "row": {"type": "integer", "minimum": 0},
            "column": {"type": "string"}, "scope": {"type": "string", "enum": ["cell", "complete_total"]},
            "context": {"type": "object"}, "expected": {},
        }, ["tool_result_id", "evidence_hash", "row", "column", "scope", "context"])},
        "clarify": {"type": "array", "items": {"type": "string", "enum": list(FIELDS)}},
    }},
}

INSTRUCTION = (
    "Chat answer delivery requires submit_answer. Tool reads remain autonomous. Never treat a draft as an answer. "
    "After gathering evidence, submit structured checks and cell claims; do not put numerical conclusions into prose. "
    "For follow-ups, explicitly reference prior tool results using their current evidence hash and context. "
    "Approval is question-specific. Unknown business meaning/assumptions must remain visible limitations. "
    "Canonical assurance comes only from query_metric executor evidence, not from your own assessment. "
    "Without canonical executor evidence business_meaning must be unknown. Source SQL alone also leaves "
    "period_bounds_timezone, filters and grain_units unknown: their semantic equivalence is not established. "
    "No model-authored conclusion is independently verified. "
)

LIMITATION = (
    "I could not complete the structured answer review within this question's limit. "
    "No analytical answer was approved. Please narrow the question or clarify its business definition."
)
MESSAGES = {
    "greeting": "Hello. I can help explore your authorized reports and data sources.",
    "help": "I can read authorized reports and data sources, explain their evidence, and propose permitted actions. Tell me the business question, period and metric definition you want to explore.",
    "limitation": "The available evidence does not support an analytical answer yet. Business definitions, source suitability and data quality may need clarification; no result has been certified.",
    "action": "The action proposal is available for your review. Its execution still requires the application's approval checks.",
}


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def start(state):
    # An ordinal binds identical repeated questions to separate reviews.
    users = [e for e in state["transcript"] if e.get("role") == "user" and not e.get("_internal")]
    state["_answer_review"] = {"question": digest([len(users), users[-1]["content"]]), "attempts": 0, "start": len(state["transcript"]) - 1}


def reference(entry):
    """Only tool-produced structured evidence can enter the cell registry."""
    prov = entry.get("provenance")
    if (entry.get("role") != "tool" or entry.get("is_error") or not isinstance(prov, dict)
            or prov.get("status") != "answered"
            or (entry.get("name"), prov.get("kind")) not in {
                ("query_source", "source_query"), ("query_metric", "metric_query"),
                ("query_report_data", "report_query")}
            or not entry.get("tool_use_id")):
        return None
    try:
        if len(encoded(prov).encode()) > 70000:
            return None
        columns, rows = prov["columns"], prov["rows"]
        if (not isinstance(columns, list) or not 1 <= len(columns) <= 200
                or any(not isinstance(c, str) for c in columns) or len(set(columns)) != len(columns)
                or not isinstance(rows, list) or len(rows) > 200
                or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)
                or type(prov.get("truncated")) is not bool):
            return None
        context = {k: v for k, v in prov.items() if k not in {"columns", "rows", "evidence_id"}}
        return {"tool_result_id": entry["tool_use_id"], "evidence_hash": digest(prov), "context": context}
    except (ValueError, KeyError, TypeError, RecursionError):
        return None


def evidence_catalog(transcript):
    # Bound both the number and byte size of reference descriptors. Cell data
    # already lives in bounded tool results; it is not duplicated in the prompt.
    catalog, seen, size = [], set(), 0
    for entry in reversed(transcript[-200:]):
        ref = reference(entry)
        if ref:
            # Some gateways reuse call IDs across responses. The hash binds
            # the exact execution context and cells, so resolve the pair.
            identity = (ref["tool_result_id"], ref["evidence_hash"])
            if identity in seen:
                continue
            seen.add(identity)
            size += len(encoded(ref).encode())
            if len(catalog) >= 32 or size > 48000:
                break
            catalog.append(ref)
    return catalog


def prompt(state):
    return {"type": "text", "text": INSTRUCTION + "\nCurrent evidence references: " + encoded(evidence_catalog(state["transcript"]))}


def recheck(toolbox, references=()):
    """Current authorization, independently of the model's declared checks."""
    from django.core.exceptions import PermissionDenied
    error = toolbox.authorize({})
    if error:
        raise PermissionDenied(error)
    # authorize sets a one-shot dispatch flag. A final check is not dispatch.
    toolbox._access_prechecked = False
    if getattr(toolbox, "session", None) is not None:
        from .source_tools import check_history
        check_history(toolbox.session, actor=toolbox.actor, studio=toolbox.studio)
    for ref in references:
        report = ref["context"].get("report")
        if report:
            error = toolbox._access_error(report)
            if error:
                raise PermissionDenied(error)


def _check_submission(args, state):
    from .llm import _validate_arguments
    from .transport import TransportError
    try:
        if len(encoded(args).encode()) > MAX_BYTES:
            raise ValueError("Submission exceeds the byte limit.")
        _validate_arguments(args, _object(SCHEMA["input_schema"]["properties"], ["mode"]))
    except (TransportError, TypeError, RecursionError):
        raise ValueError("Use the declared submission schema.") from None
    mode = args["mode"]
    claims, checks, clarify = args.get("claims", []), args.get("checks", {}), args.get("clarify", [])
    if len(claims) > 12 or len(clarify) > len(FIELDS):
        raise ValueError("Too many claims or clarification fields.")
    if mode != "analysis":
        if claims or checks:
            raise ValueError("Only analysis can submit claims or assessments.")
        if mode == "clarification":
            if not clarify:
                raise ValueError("Select the business fields requiring clarification.")
            return "Please clarify: " + ", ".join(v.replace("_", " ") for v in clarify) + ".", [], {}
        if mode == "action":
            # A follow-up may name a real conversation action explicitly. It
            # inherits no analytical approval and never executes an action.
            entries = state["transcript"][-200:] if "proposal_id" in args else state["transcript"][state["_answer_review"]["start"]:]
            proposals = [e["proposal"] for e in entries if e.get("proposal")
                         and ("proposal_id" not in args or e["proposal"].get("id") == args["proposal_id"])]
            if not proposals:
                raise ValueError("Reference an actual action proposal in this conversation.")
            status = proposals[-1].get("status")
            if status == "proposed":
                return MESSAGES[mode], [], {}
            if status not in {"approved", "rejected", "expired", "executed", "failed"}:
                raise ValueError("The action's recorded status is unavailable.")
            return "The action's recorded status is " + status + ". See its card for details.", [], {}
        return MESSAGES[mode], [], {}
    if not claims or set(checks) != set(CHECKS):
        raise ValueError("Analysis requires cell claims and all business-analysis checks.")
    catalog = {(r["tool_result_id"], r["evidence_hash"]): r for r in evidence_catalog(state["transcript"])}
    entries = {(e["tool_use_id"], digest(e["provenance"])): e for e in state["transcript"][-200:] if reference(e)}
    used = {}

    def resolve(ref):
        identity = (ref["tool_result_id"], ref["evidence_hash"])
        item = catalog.get(identity)
        if not item:
            raise ValueError("Reference real successful tool evidence and its current hash.")
        used[identity] = item
        return item, entries[identity]["provenance"]

    claim_sources = [resolve(claim)[1] for claim in claims]
    claim_refs = set(used)
    for name, check in checks.items():
        if len(check["note"]) > 500 or len(check["evidence"]) > 8:
            raise ValueError("Assessment note or reference count exceeds the limit.")
        if check["state"] == "assessed" and (not check["note"].strip() or not check["evidence"]):
            raise ValueError("Assessed checks require a rationale and evidence references.")
        for ref in check["evidence"]:
            resolve(ref)
        if check["state"] == "assessed":
            supporting_refs = {(r["tool_result_id"], r["evidence_hash"]) for r in check["evidence"]}
            if not claim_refs <= supporting_refs:
                raise ValueError("Assessed checks must reference every execution used by the answer's claims.")
            if name in {"business_meaning", "period_bounds_timezone", "grain_units"} and any(p["kind"] != "metric_query" for p in claim_sources):
                raise ValueError("Business meaning, semantic period and grain/units without canonical executor evidence must remain unknown.")
            if name == "filters" and any(p["kind"] == "source_query" for p in claim_sources):
                raise ValueError("SQL alone does not establish semantic filters; mark this check unknown.")
    lines = ["Evidence-grounded observations (business interpretation is a model assessment):"]
    for claim in claims:
        ref, prov = resolve(claim)
        if encoded(claim["context"]) != encoded(ref["context"]):
            raise ValueError("Claim period, filters, units and source context must exactly match execution evidence.")
        row, column = claim["row"], claim["column"]
        if row >= len(prov["rows"]) or column not in prov["columns"]:
            raise ValueError("The referenced result cell does not exist; empty results cannot become zero.")
        if claim["scope"] == "complete_total":
            aggregated = (prov["kind"] == "metric_query" and not prov.get("breakdown")) or (
                prov["kind"] == "report_query" and prov.get("aggregate") and not prov.get("group_by"))
            # Source SQL semantics are not proven by a model's assessment.
            if prov["truncated"] or len(prov["rows"]) != 1 or not aggregated:
                raise ValueError("Complete totals require untruncated single-row structured aggregation evidence; source SQL supports cell observations only.")
        value = prov["rows"][row][prov["columns"].index(column)]
        if isinstance(value, (list, dict)):
            raise TypeError("Claims require scalar cells.")
        if "expected" in claim:
            expected = claim["expected"]
            numeric = type(expected) in (int, float) and type(value) in (int, float)
            matches = (Decimal(str(expected)) == Decimal(str(value))) if numeric else encoded(expected) == encoded(value)
            if not matches:
                raise ValueError("Claimed value does not match the canonical result cell (NULL is not zero).")
        displayed = "NULL (missing; not zero)" if value is None else encoded(value)
        # Keep source strings literal rather than interpreting their Markdown.
        observation = (encoded(column) + ": " + displayed).replace("`", "\\u0060")
        result_id = encoded(ref["tool_result_id"]).replace("`", "\\u0060")
        lines.append(f"\n- `{observation}` [result `{result_id}`, row {row}].")
    for identity in sorted(claim_refs):
        ref = used[identity]
        context = ref["context"]
        assurance = "Canonical executor calculation" if context["kind"] == "metric_query" else "Exploratory observation"
        result_id = encoded(ref["tool_result_id"]).replace("`", "\\u0060")
        lines.append("\nResult `" + result_id + "`: " + assurance + ". Execution context: `" + encoded(context).replace("`", "\\u0060") + "`.")
    unresolved = [key.replace("_", " ") + " (" + value["state"] + ")" for key, value in checks.items() if value["state"] != "assessed"]
    if unresolved:
        lines.append("\nUnresolved or inapplicable business checks: " + ", ".join(unresolved) + ". Treat this answer as exploratory.")
    lines.append("\nCell values and execution context were checked by the server. Question interpretation, business meaning, source suitability and SQL equivalence are not independently proven.")
    text = "\n".join(lines)
    if len(text.encode()) > 65536:
        raise ValueError("Rendered answer exceeds the byte limit; select fewer cells.")
    return text, list(used.values()), checks


def submit(args, state, toolbox):
    """Return rendered answer or bounded, non-echoing correction feedback."""
    review = state["_answer_review"]
    try:
        text, refs, checks = _check_submission(args, state)
    except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as exc:
        # All error text is authored here or in the schema validator, never a
        # source value, draft, tool argument or provider exception.
        reason = str(exc) if isinstance(exc, ValueError) else "Use the declared submission schema."
        return reject(state, reason)
    recheck(toolbox, refs)
    review.update(status="accepted", mode=args["mode"],
                  evidence=[{k: r[k] for k in ("tool_result_id", "evidence_hash")} for r in refs],
                  checks=checks,
                  deterministic=["evidence_identity", "execution_context", "cell_values"] if refs else [],
                  judgment="model_assessed_not_independently_verified")
    return text, None


def reject(state, reason):
    review = state["_answer_review"]
    review["attempts"] += 1
    review["status"] = "correction_required"
    if review["attempts"] >= MAX_ATTEMPTS:
        review["status"] = "limited"
        return LIMITATION, None
    return None, "Answer review rejected. " + reason + " Correct using submit_answer, or submit clarification/limitation."


def deliver(state, text):
    state["transcript"].append({"role": "assistant", "content": [{"type": "text", "text": text}],
                                "ts": time.time(), "stop_reason": "stop"})
    return {"type": "text_delta", "text": text}


def visible_transcript(transcript):
    """Internal submission calls and correction messages are protocol only."""
    out = []
    for entry in transcript:
        if entry.get("_internal"):
            continue
        if entry.get("role") == "assistant" and isinstance(entry.get("content"), list):
            content = [block for block in entry["content"] if block.get("name") != NAME]
            if not content:
                continue
            entry = dict(entry, content=content)
        out.append(entry)
    return out
