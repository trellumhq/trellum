"""Validate the checked-in route plan against the actual Django resolver.

Planned node IDs are contracts for future suites, never execution evidence.
"""
import argparse
import ast
import hashlib
import textwrap
import inspect
import json
from pathlib import Path
import re
import sys
import django
from django.urls import URLResolver, get_resolver

LEDGER = Path(__file__).with_name('routes.json')
from apps.core.tests.portal_e2e.identities import ROLES
from apps.core.tests.portal_e2e.parameters import SEED_PARAMETERS, NATIVE_PARAMETERS

DISPOSITIONS = {'enhanced', 'native with feedback', 'existing async', 'non-UI API/asset'}

def resolved(patterns, prefix=''):
    for item in patterns:
        pattern = prefix + str(item.pattern)
        if isinstance(item, URLResolver):
            yield from resolved(item.url_patterns, pattern)
        else:
            yield pattern, item

def handler_contract(callback):
    callback = inspect.unwrap(callback)
    source = inspect.getsource(callback)
    module = inspect.getmodule(callback)
    queue, visited, fragments = [source], set(), []
    while queue:
        fragment = queue.pop()
        fragments.append(textwrap.dedent(fragment))
        for helper in re.findall(r'\b(_[a-zA-Z_]\w*)\s*\(', fragment):
            function = getattr(module, helper, None)
            if helper not in visited and inspect.isfunction(function) and inspect.getmodule(function) == module:
                visited.add(helper)
                queue.append(inspect.getsource(inspect.unwrap(function)))
    actions = set()
    full = '\n'.join(fragments)
    for node in ast.walk(ast.parse(full)):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        kind = left.id if isinstance(left, ast.Name) and left.id in ('action','tab') else None
        if isinstance(left, ast.Call) and isinstance(left.func, ast.Attribute) and left.func.attr == 'get' and left.args and isinstance(left.args[0], ast.Constant) and left.args[0].value in ('action','tab'):
            kind = left.args[0].value
        if kind:
            for comparator in node.comparators:
                for leaf in ast.walk(comparator):
                    if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str):
                        actions.add(kind+'='+leaf.value)
    return hashlib.sha256(source.encode()).hexdigest(), hashlib.sha256(full.encode()).hexdigest(), actions

def validate(ledger=None):
    ledger = ledger or json.loads(LEDGER.read_text())
    actual = {(pattern, item.lookup_str): item for pattern, item in resolved(get_resolver().url_patterns)}
    rows = ledger['routes']
    keys = [(row['pattern'], row['view']) for row in rows]
    errors = []
    if len(set(keys)) != len(keys):
        errors.append('duplicate route mapping')
    if set(keys) != set(actual):
        errors.append('resolver drift: missing='+str(set(actual)-set(keys))+' stale='+str(set(keys)-set(actual)))
    if sum(row['first_party'] for row in rows) != ledger['first_party_expected']:
        errors.append('first-party route count drift')
    for row in rows:
        prefix = row['id']+': '
        for field in ('case', 'shard', 'allowed_roles', 'role_policy', 'scenarios'):
            if not row.get(field):
                errors.append(prefix+'missing '+field)
        if set(row['allowed_roles']+row['denied_roles']) - set(ROLES):
            errors.append(prefix+'unknown principal role')
        if not row['denied_roles'] and row['role_policy'] not in ('native-public-boundary','public-availability-probe','native-provider-boundary','public-native-redirect'):
            errors.append(prefix+'missing deny boundary')
        if row.get('disposition') not in DISPOSITIONS:
            errors.append(prefix+'invalid disposition')
        parameters = set(re.findall(r'<(?:[^:>]+:)?([^>]+)>', row['pattern']))
        if parameters != set(row['seeded_parameters']):
            errors.append(prefix+'parameter mapping mismatch')
        for value in row['seeded_parameters'].values():
            if not value or not (value.get('provider') or 'value' in value):
                errors.append(prefix+'empty fixture parameter')
            if value.get('provider'):
                namespace,name = value['provider'].split('.',1)
                valid = SEED_PARAMETERS if namespace=='seed' else NATIVE_PARAMETERS if namespace=='native-boundary' else set()
                if name not in valid:
                    errors.append(prefix+'unknown parameter provider')
        for scenario in row['scenarios']:
            if scenario.get('method') not in ('GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS') or not scenario.get('planned_node_id'):
                errors.append(prefix+'invalid action scenario')
            if scenario.get('verification') != 'pending':
                errors.append(prefix+'execution status must come from real result evidence')
        item = actual.get((row['pattern'],row['view']))
        if row['first_party'] and item:
            digest, helper_digest, actions = handler_contract(item.callback)
            declared_actions = {scenario.get('action') for scenario in row['scenarios']}
            if actions - declared_actions:
                errors.append(prefix+'unmapped source actions '+str(actions-declared_actions))
            if digest != row['source_sha256'] or helper_digest != row.get('adapter_sha256'):
                errors.append(prefix+'handler changed: review route/action plan')
    return {'mapping_status': 'complete' if not errors else 'fail', 'verification_status': 'pending',
            'first_party_routes': sum(row['first_party'] for row in rows), 'native_routes': sum(not row['first_party'] for row in rows),
            'planned_scenarios': sum(len(row['scenarios']) for row in rows), 'errors': errors, 'routes': rows}

def refresh_reviewed_views(views):
    """Update only reviewed digests; reject new unmapped actions first."""
    ledger = json.loads(LEDGER.read_text())
    actual = {(pattern,item.lookup_str):item for pattern,item in resolved(get_resolver().url_patterns)}
    matched = set()
    for row in ledger['routes']:
        if row['view'] not in views:
            continue
        if not row['first_party']:
            raise ValueError('only first-party handler digests are maintained')
        matched.add(row['view'])
        item = actual[(row['pattern'],row['view'])]
        digest,adapter,actions = handler_contract(item.callback)
        declared = {scenario.get('action') for scenario in row['scenarios']}
        if actions-declared:
            raise ValueError('review/add new action policy before digest refresh: '+str(actions-declared))
        source = inspect.getsource(inspect.unwrap(item.callback))
        explicit = re.search(r'@require_http_methods\((\[[^\]]+\])\)',source)
        methods = ast.literal_eval(explicit.group(1)) if explicit else ['POST'] if '@require_POST' in source else []
        if set(methods)-{scenario['method'] for scenario in row['scenarios']}:
            raise ValueError('review/add new method policy before digest refresh')
        row['source_sha256'],row['adapter_sha256'] = digest,adapter
    if set(views)-matched:
        raise ValueError('unknown selected handler '+str(set(views)-matched))
    report = validate(ledger)
    # Other unreviewed handler drift remains visible but does not prevent this
    # explicitly bounded edit. Structural/policy errors always reject it.
    if any('handler changed: review route/action plan' not in error for error in report['errors']):
        raise ValueError(report['errors'])
    LEDGER.write_text(json.dumps(ledger,indent=2)+'\n')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--refresh-reviewed-view',action='append',default=[])
    arguments = parser.parse_args()
    django.setup()
    if arguments.refresh_reviewed_view:
        refresh_reviewed_views(arguments.refresh_reviewed_view)
    report = validate()
    arguments.output.write_text(json.dumps(report,indent=2))
    if report['errors']:
        raise SystemExit('\n'.join(report['errors']))
