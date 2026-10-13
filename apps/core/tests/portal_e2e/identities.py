"""Named isolated principals used by the route plan (never credentials)."""
ROLES = {
    'org-admin': 'alpha-admin@example.invalid',
    'studio-admin': 'alpha-studio-admin@example.invalid',
    'developer': 'alpha-developer@example.invalid',
    'viewer-full': 'beta-full-viewer@example.invalid',
    'private-viewer': 'alpha-full-viewer@example.invalid',
    'viewer': 'alpha-viewer@example.invalid',
    'group-member': 'alpha-group-member@example.invalid',
    'group-admin': 'alpha-group-admin@example.invalid',
    'group-selected': 'alpha-group-selected@example.invalid',
    'operator': 'operator@example.invalid',
    'impersonated': 'alpha-impersonated@example.invalid',
    'inactive': 'inactive@example.invalid',
    'revoked': 'alpha-revoked@example.invalid',
    'cross-org': 'beta-viewer@example.invalid',
    'anonymous': None,
}
ACTIVE = ['org-admin','studio-admin','developer','viewer','viewer-full','private-viewer','group-member','group-admin','group-selected','operator','impersonated']
