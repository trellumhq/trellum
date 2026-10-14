"""Fail-closed evidence validation; passing infrastructure is not coverage."""
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path


def source_digest(root):
    """Hash tracked content, including edits, without embedding source or secrets."""
    import subprocess
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root).split(b"\0")
    digest = hashlib.sha256()
    for raw in sorted(path for path in paths if path):
        path = Path(root) / raw.decode()
        digest.update(raw + b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"missing")
    return digest.hexdigest()


def requirements(ledger):
    """Enumerate policy tuples only. Concrete conditions require reviewed bindings.

    A policy's unstructured request_conditions never becomes an executable
    condition. Conditional/not-applicable entries retain their reviewed text;
    they remain pending until an explicit named binding covers the tuple.
    """
    rows = []
    for route in ledger["routes"]:
        for scenario in route["scenarios"]:
            policy = scenario if "allowed_roles" in scenario else route
            for disposition in ("allowed_roles", "denied_roles"):
                for principal in policy.get(disposition, ()):
                    rows.append({"route": route["id"], "method": scenario["method"],
                                 "action": scenario.get("action"), "principal": principal,
                                 "disposition": disposition, "condition": None})
            for disposition in ("conditional_roles", "not_applicable_roles"):
                for principal, condition in policy.get(disposition, {}).items():
                    rows.append({"route": route["id"], "method": scenario["method"],
                                 "action": scenario.get("action"), "principal": principal,
                                 "disposition": disposition, "condition": condition["condition"]})
    return rows


def validate_execution(bindings, collected, outcomes, evidence, events, *, source,
                       binding_hash, conditions, ledger=None, identity_registry=None):
    """Require real collected passes, completed matching requests and checkpoints.

    Headers identify evidence windows, never authenticate actors. Each binding
    must have an actor assertion plus a matching server-observed actor/session;
    impersonation records both effective and operator identities.
    """
    errors = []
    if len(collected) != len(set(collected)):
        errors.append('duplicate collected node')
    keys = Counter(row.key for row in bindings)
    if any(count != 1 for count in keys.values()):
        errors.append("duplicate binding ownership")
    if any(not isinstance(row, dict) for row in (*evidence, *events)):
        return {'status': 'blocked', 'errors': ['malformed evidence shape'], 'executed_bindings': 0}
    def evidence_key(row):
        value = row.get('key')
        if not isinstance(value, (list, tuple)) or len(value) != 8 or any(not isinstance(item, (str, type(None))) for item in value):
            return ()
        return tuple(value)
    event_ids = Counter(event.get("event_id") for event in events)
    if any(count != 1 for count in event_ids.values()):
        errors.append("duplicate request event")
    evidence_keys = Counter(evidence_key(row) for row in evidence)
    if any(count != 1 for count in evidence_keys.values()):
        errors.append("duplicate evidence ownership")
    known = set(keys)
    if set(evidence_keys) - known:
        errors.append("unbound evidence")
    observed = {event["event_id"]: event for event in events if event.get("event_id")}
    by_key = {evidence_key(row): row for row in evidence}
    route_map = {row["id"]: row for row in (ledger or {}).get("routes", ())}
    for binding in bindings:
        prefix = str(binding.key) + ": "
        if binding.condition not in conditions:
            errors.append(prefix + "unreviewed condition")
        if not binding.obligations or "actor" not in binding.obligations:
            errors.append(prefix + "missing actor obligation")
        if ledger is not None:
            route = route_map.get(binding.route)
            scenarios = [] if route is None else [row for row in route["scenarios"]
                if (row["method"], row.get("action")) == (binding.method, binding.action)]
            if len(scenarios) != 1:
                errors.append(prefix + "route/action drift")
            else:
                policy = scenarios[0] if "allowed_roles" in scenarios[0] else route
                principals = set(policy.get("allowed_roles", ())) | set(policy.get("denied_roles", ())) | set(policy.get("conditional_roles", {})) | set(policy.get("not_applicable_roles", {}))
                if binding.principal not in principals:
                    errors.append(prefix + "principal policy drift")
        if binding.node_id not in collected:
            errors.append(prefix + "uncollected node")
        if outcomes.get(binding.node_id) != "passed":
            errors.append(prefix + "required node did not pass")
        row = by_key.get(binding.key)
        if row is None:
            errors.append(prefix + "missing evidence")
            continue
        if row.get("node_id") != binding.node_id or row.get("source") != source or row.get("binding_hash") != binding_hash:
            errors.append(prefix + "stale or wrong attribution")
        if not set(binding.obligations) <= set(row.get("checkpoints", ())):
            errors.append(prefix + "missing assertion checkpoint")
        actor = row.get("actor")
        if not isinstance(actor, dict) or actor.get("principal") != binding.principal or "actor_id" not in actor or "session_digest" not in actor or "impersonator_id" not in actor:
            errors.append(prefix + "missing actor assertion")
            actor = {}
        expected_actor = (identity_registry or {}).get(binding.principal)
        if expected_actor is None or any(actor.get(name) != expected_actor.get(name) for name in ('actor_id', 'impersonator_id')):
            errors.append(prefix + "fixture identity mismatch")
        if expected_actor and expected_actor.get('actor_id') is not None and not actor.get('session_digest'):
            errors.append(prefix + "missing authenticated session proof")
        refs = row.get("events", ())
        if not refs:
            errors.append(prefix + "missing request observation")
        for event_id in refs:
            event = observed.get(event_id)
            if event is None:
                errors.append(prefix + "unknown event")
                continue
            if not event.get("complete") or event.get("node_id") != binding.node_id or event.get("correlation") != row.get("correlation"):
                errors.append(prefix + "incomplete or foreign request window")
            if (event.get("route"), event.get("method"), event.get("action")) != (binding.route, binding.method, binding.action):
                errors.append(prefix + "wrong request route/action")
            if any(event.get(key) != actor.get(key) for key in ("actor_id", "session_digest", "impersonator_id")):
                errors.append(prefix + "observed identity mismatch")
            if event.get("principal") != binding.principal or event.get("condition") != binding.condition:
                errors.append(prefix + "wrong principal/condition")
            if not isinstance(event.get('status'), int):
                errors.append(prefix + 'missing actual response status')
    return {"status": "pass" if bindings and not errors else "blocked", "errors": errors,
            "executed_bindings": len(bindings), "empty": not bindings}


def coverage_summary(ledger, bindings, execution, reviewed_requirements=None):
    planned = requirements(ledger)
    # An explicit condition catalog must bind every planned tuple before full
    # coverage. Phase 1 has no journey bindings and reports every tuple pending.
    # A reviewer explicitly lists every required named condition/variant for a
    # policy tuple. One happy-path binding cannot stand in for that list.
    reviewed_requirements = reviewed_requirements or {}
    actual = {row.key for row in bindings}
    pending = []
    for row in planned:
        policy_key = (row['route'], row['method'], row['action'], row['principal'], row['disposition'], row['condition'])
        required = reviewed_requirements.get(policy_key)
        if not required or not set(required) <= actual:
            pending.append(row)
    return {"status": "blocked" if pending or execution["status"] != "pass" else "pass",
            "planned_scenarios": sum(len(row["scenarios"]) for row in ledger["routes"]),
            "required_policy_tuples": len(planned), "pending_policy_tuples": pending,
            "execution": execution}
