"""Inventory actual Django routing, keeping unverified scenarios explicit."""
import json
import sys
import re
import inspect
import django
from django.urls import URLPattern, URLResolver, get_resolver

def inventory(patterns, prefix=''):
    for item in patterns:
        pattern = prefix + str(item.pattern)
        if isinstance(item, URLResolver):
            yield from inventory(item.url_patterns, pattern)
        elif isinstance(item, URLPattern):
            try:
                source = inspect.getsource(item.callback)
            except (TypeError, OSError):
                source = ''
            actions = sorted(set(re.findall(r'action\s*==\s*[\"\']([^\"\']+)', source)))
            parameters = {'org_slug': 'alpha', 'studio_slug': 'studio-0', 'slug': 'report-0000'}
            yield {'pattern': pattern, 'name': item.name, 'view': item.lookup_str,
                   'disposition': 'non-UI API/asset' if '/api/' in '/'+pattern or 'asset' in pattern else 'native with feedback',
                   'verification': 'pending', 'allowed_roles': [], 'denied_roles': [],
                   'seeded_parameters': {name: parameters.get(name, 'pending fixture') for name in re.findall(r'<(?:[^:>]+:)?([^>]+)>', pattern)}, 'methods_actions': ['GET', *['POST action='+action for action in actions]], 'case': None, 'node_ids': []}

if __name__ == '__main__':
    django.setup()
    rows = list(inventory(get_resolver().url_patterns))
    with open(sys.argv[1], 'w') as out:
        json.dump({'schema': 1, 'routes': rows, 'notes': 'Dispositions provisional; pending action/role/case mapping is not verified coverage.'}, out, indent=2)
