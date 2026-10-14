"""Explicit executable coverage bindings, independent of route policy.

Infrastructure checks deliberately certify no planned product journey. Add a
reviewed Binding for each concrete principal/condition when its journey lands.
"""
from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True)
class Binding:
    route: str
    method: str
    action: str | None
    principal: str
    condition: str
    node_id: str
    obligations: tuple[str, ...]
    browser: str = "api"
    viewport: str = "none"
    mode: str = "api"

    @property
    def key(self):
        return (self.route, self.method, self.action, self.principal,
                self.condition, self.browser, self.viewport, self.mode)


# Do not populate these from status codes or prose in routes.json. Conditions
# are named explicit cases; fixture IDs and secrets are resolved only at runtime.
BINDINGS: tuple[Binding, ...] = ()
CONDITIONS: dict[str, str] = {}
JOURNEY_MODULES = {f'L{number}': () for number in range(1, 6)}
# policy tuple -> exact required binding keys; populated only by reviewed
# condition/variant inventories, never inferred from response status or prose.
REVIEWED_REQUIREMENTS = {}
INFRASTRUCTURE_CONDITIONS = {
    'infra-profile': 'Own-account POST/redirect Save, DB/reload and CSRF no-write; JS does not change this form transport.',
    'infra-group-edit': 'Own-organization group overview AJAX Save, DB/reload and visible feedback.',
}


def infrastructure_bindings(browser):
    """The small reviewed runtime proof catalog is separate from journey plans."""
    result = []
    prefix = 'apps/core/tests/portal_e2e/test_runtime_smoke.py::'
    layouts = ('desktop', 'mobile', 'desktop-200pct', 'mobile-200pct')
    for mode in ('native', 'js-enabled'):
        for layout in layouts:
            result.append(Binding('route-212', 'POST', 'action=profile', 'org-admin',
                'infra-profile', prefix + f'test_account_persistence_and_csrf[{mode}-{layout}]',
                ('actor', 'observed-actor', 'db-persistence', 'reload-persistence', 'visible-feedback', 'csrf-no-write'),
                browser, layout, mode))
    for layout in layouts:
        result.append(Binding('route-297', 'POST', 'action=edit', 'org-admin',
            'infra-group-edit', prefix + f'test_shared_ajax_group_save[{layout}]',
            ('actor', 'observed-actor', 'db-persistence', 'reload-persistence', 'ajax-visible-feedback'),
            browser, layout, 'enhanced'))
    return tuple(result)


def binding_digest(bindings=None, conditions=None):
    from dataclasses import asdict
    catalog = (*BINDINGS, *infrastructure_bindings('chromium'), *infrastructure_bindings('webkit')) if bindings is None else bindings
    payload = {"bindings": [asdict(row) for row in catalog],
               "conditions": {**CONDITIONS, **INFRASTRUCTURE_CONDITIONS} if conditions is None else conditions}
    if bindings is None:
        payload['modules'] = JOURNEY_MODULES
        payload['requirements'] = [{'policy': list(key), 'bindings': [list(item) for item in values]}
                                   for key, values in sorted(REVIEWED_REQUIREMENTS.items(), key=lambda item: repr(item[0]))]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
