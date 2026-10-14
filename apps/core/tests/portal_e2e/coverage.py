"""Fail-closed evidence validation; passing infrastructure is not coverage."""
from collections import Counter
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
    if any(not isinstance(event.get('event_id'), str) for event in events):
        return {'status': 'blocked', 'errors': ['malformed request identifier'], 'executed_bindings': 0}
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
            if event.get('source') != source or event.get('binding_hash') != binding_hash:
                errors.append(prefix + 'stale request provenance')
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
        if not required or any(len(key) != 8 or tuple(key[:4]) != policy_key[:4] for key in required) or not set(required) <= actual:
            pending.append(row)
    return {"status": "blocked" if pending or execution["status"] != "pass" else "pass",
            "planned_scenarios": sum(len(row["scenarios"]) for row in ledger["routes"]),
            "required_policy_tuples": len(planned), "pending_policy_tuples": pending,
            "execution": execution}


def parameter_fingerprint(values):
    """Credential-free, order-independent form/query value observation."""
    if not isinstance(values, (list, tuple)): values = [values]
    return hashlib.sha256(json.dumps(sorted(str(value) for value in values), separators=(',', ':')).encode()).hexdigest()


def validate_cohort(executor, collected, outcomes, assertions, events, *, source,
                    digest, manifest_hash, fixture_variables, identities, ledger):
    from .bindings import cohort_bindings, COHORT_CONDITIONS, FIXTURE_ID
    bindings = cohort_bindings(executor)
    errors = []
    if executor not in ('chromium','webkit','http'): errors.append('unknown executor')
    rows = [row for row in assertions if row.get('scope') == 'cohort']
    if len(collected) != len(set(collected)): errors.append('duplicate executor/node namespace')
    if any(row.get('executor') != executor for row in rows): errors.append('cross-executor assertion substitution')
    by_key = {tuple(row.get('key', ())):row for row in rows}
    if len(by_key) != len(rows): errors.append('duplicate cohort binding')
    known = {binding.key for binding in bindings}
    if set(by_key)-known: errors.append('unbound cohort assertion')
    event_map = {event['event_id']:event for event in events}
    if len(event_map) != len(events): errors.append('duplicate request event')
    routes = {row['id']:row for row in ledger['routes']}
    ownership={}
    for row in rows:
        for event_id in row.get('events',()):ownership.setdefault(event_id,[]).append(row)
    for owners in ownership.values():
        if len(owners)>1 and (len(owners)!=2 or {row['key'][4] for row in owners}!={'access-save','access-save-after-browse'} or len({(row['node_id'],row['correlation'],row['executor'],row['variant']) for row in owners})!=1):
            errors.append('unreviewed physical request reuse')
    for binding in bindings:
        prefix = repr(binding.key)+': '
        row = by_key.get(binding.key)
        if binding.node_id not in collected or outcomes.get(binding.node_id) != 'passed':
            errors.append(prefix+'required executed node missing/nonpass')
        if row is None:
            errors.append(prefix+'missing evidence'); continue
        route = routes.get(binding.route)
        if not route or not any((item['method'],item.get('action'))==(binding.method,binding.action) for item in route['scenarios']):
            errors.append(prefix+'planned scenario drift')
        if row.get('source') != source or row.get('binding_hash') != digest or row.get('manifest_hash') != manifest_hash:
            errors.append(prefix+'stale source/binding/manifest')
        if row.get('fixture') != FIXTURE_ID or row.get('variant') != binding.variant:
            errors.append(prefix+'wrong fixture/variant')
        if row.get('node_id') != binding.node_id or not set(binding.obligations)<=set(row.get('checkpoints',())):
            errors.append(prefix+'wrong node or missing checkpoint')
        actor = row.get('actor') or {}
        expected = identities.get(binding.principal)
        if actor.get('principal') != binding.principal or expected is None or any(actor.get(key)!=expected.get(key) for key in ('actor_id','impersonator_id')):
            errors.append(prefix+'fixture actor mismatch')
        if expected and expected.get('actor_id') is not None and not actor.get('session_digest'):
            errors.append(prefix+'missing real session identity')
        refs = row.get('events', ())
        if not refs: errors.append(prefix+'no request evidence')
        variables = fixture_variables.get(binding.variant or 'http', {})
        for ref in refs:
            event = event_map.get(ref)
            if not event:
                errors.append(prefix+'unknown request'); continue
            if not event.get('complete') or event.get('source')!=source or event.get('binding_hash')!=digest:
                errors.append(prefix+'stale/incomplete worker event')
            if event.get('executor')!=executor or event.get('node_id')!=binding.node_id or event.get('correlation')!=row.get('correlation'):
                errors.append(prefix+'foreign execution window')
            if (event.get('route'),event.get('method'),event.get('action')) != (binding.route,binding.method,binding.transport['action']):
                errors.append(prefix+'wrong independently observed transport')
            if any(event.get(key)!=actor.get(key) for key in ('actor_id','session_digest','impersonator_id')):
                errors.append(prefix+'observed identity mismatch')
            if event.get('principal')!=binding.principal:
                errors.append(prefix+'wrong principal attribution')
            if binding.transport['status'] is not None and event.get('status')!=binding.transport['status']:
                errors.append(prefix+'wrong actual status')
            for source_name in ('query','body'):
                for name,expression in binding.transport[source_name].items():
                    value = variables.get(expression[1:]) if isinstance(expression,str) and expression.startswith('$') else expression
                    if value is None or event.get('parameters',{}).get(source_name,{}).get(name)!=parameter_fingerprint(value):
                        errors.append(prefix+'wrong '+source_name+' '+name+' observation')
        for condition in binding.transport['prior']:
            prior = [candidate for candidate in rows if candidate.get('node_id')==binding.node_id and candidate.get('correlation')==row.get('correlation') and candidate.get('key',[None]*5)[4]==condition]
            if len(prior)!=1 or not prior[0].get('events') or not set(prior[0]['events'])<=set(row.get('prior_events',())):
                errors.append(prefix+'missing reviewed prior browse chain')
            else:
                prior_observations=[event_map.get(ref,{}) for ref in prior[0]['events']]
                save_observations=[event_map.get(ref,{}) for ref in refs]
                if any(not isinstance(event.get('finished_ns'),int) for event in prior_observations) or any(not isinstance(event.get('started_ns'),int) for event in save_observations) or max(event.get('finished_ns',0) for event in prior_observations)>=min(event.get('started_ns',0) for event in save_observations):
                    errors.append(prefix+'browse did not complete before Save')
    return {'status':'pass' if bindings and not errors else 'blocked','errors':errors,'executor':executor,
            'executed_bindings':len(bindings),'fixture':FIXTURE_ID,'manifest_hash':manifest_hash}


def aggregate_cohort(bundles):
    """A complete cohort matrix is separate from still-pending full L3."""
    errors=[]
    if sorted(row.get('executor') for row in bundles)!=['chromium','http','webkit']:
        errors.append('missing or duplicate required executor')
    for name in ('source','binding_hash','manifest_hash','fixture_hash'):
        if len({row.get(name) for row in bundles})!=1 or any(not row.get(name) for row in bundles):
            errors.append('mixed or missing '+name)
    if any(row.get('status')!='pass' for row in bundles): errors.append('required executor nonpass')
    identities=[(row['executor'],node) for row in bundles for node in row.get('collected',())]
    if len(identities)!=len(set(identities)): errors.append('duplicate executor/node identity')
    return {'status':'pass' if not errors else 'blocked','scope':'members-access cohort matrix only',
            'errors':errors,'execution_count':len(identities),'full_journey_gate':'pending'}


def validate_na(dispositions, approved_policy, executed_proofs):
    """NA is reviewed policy evidence, never a fabricated runtime pass."""
    errors=[]
    for row in dispositions:
        policy=approved_policy.get(row.get('policy_key'))
        if not policy or row.get('reason')!=policy.get('reason') or row.get('policy_digest')!=policy.get('policy_digest') or not policy.get('reviewer'):
            errors.append('unreviewed or stale NA')
        if row.get('proof') not in executed_proofs: errors.append('missing governing NA proof')
    return {'status':'pass' if not errors else 'blocked','errors':errors,'runtime_pass_count':0}
