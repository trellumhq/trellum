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
    transport: dict | None = None
    variant: str = ""

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

COHORT = 'members-access'
FIXTURE_ID = 'members-access-small-v1'
COHORT_MODULE = 'apps/core/tests/portal_e2e/test_l3_groups_members.py'
COHORT_PRINCIPALS = ('anonymous', 'inactive', 'org-admin', 'group-admin', 'operator',
    'studio-admin', 'developer', 'viewer', 'viewer-full', 'private-viewer',
    'group-member', 'group-selected', 'cross-org', 'revoked', 'impersonated')
ORG_STATUS = dict(zip(COHORT_PRINCIPALS, (302,302,200,200,200,403,403,403,404,403,403,403,404,404,403)))
STUDIO_JSON_STATUS = dict(zip(COHORT_PRINCIPALS, (401,401,200,200,200,200,403,403,404,403,403,403,404,404,404)))
COHORT_VARIANTS = (
    ('overview-chromium-desktop', 'overview', 'chromium',1440,900,'org-admin','js-enabled'),
    ('overview-webkit-mobile', 'overview', 'webkit',390,844,'group-admin','native'),
    *tuple((f'access-{engine}-{width}-{mode}', 'access', engine,width,900 if width==1440 else 844,principal,mode)
        for mode in ('native','enhanced')
        for engine,width,principal in (('chromium',1440,'org-admin'),('chromium',390,'group-admin'),('webkit',1440,'group-admin'),('webkit',390,'org-admin'))),
    ('access-chromium-195-native','access','chromium',195,422,'org-admin','native'),
    ('access-webkit-720-enhanced','access','webkit',720,450,'group-admin','enhanced'),
    ('native-members-webkit-desktop','members','webkit',1440,900,'studio-admin','native'),
    ('native-members-chromium-mobile','members','chromium',390,844,'org-admin','native'),
)
COHORT_CONDITIONS = {
    'group-counts': 'Owned group list exact counts and complete off-page name search.',
    'overview-discovery': 'Owned group grant total and matching studio discovery.',
    'access-browse': 'Native POST browse preserves a bounded draft, page and search without a grant write.',
    'access-search': 'Enhanced live report choice search retains selected missing/inactive reports and tenant scope.',
    'access-invalid': 'Foreign report validation fails without changing grants or losing the draft.',
    'access-save': 'One bounded explicit Save after browse persists the exact grant delta and audit multiset.',
    'access-save-after-browse': 'Reviewed semantic alias: actual studio_access Save plus prior observed browse chain and persisted delta.',
    'access-csrf': 'Missing-CSRF POST rejects without a business write.',
    'members-browse': 'Native candidate search is complete and retains off-page selection/direct role without membership writes.',
    'members-invalid-role': 'Native invalid role/page browse returns errors and retains the draft without saving.',
    'auth-org': 'Alpha group access requires actual effective organization administrator rights.',
    'auth-studio': 'Alpha candidate API requires actual effective studio administrator rights.',
    'auth-write-denied': 'Genuine valid bounded grant draft is rejected for a principal without organization administrator rights.',
    'operator-save': 'Actual operator identity writes one bounded grant delta and its exact expected audit set.',
}


def cohort_bindings(executor):
    result = []
    def add(route, method, scenario, principal, condition, node, obligations, *, observed=None, query=None, body=None, variant='', viewport='none', mode='http', status=None, prior=()):
        transport = {'action': observed if observed is not None else scenario, 'query': query or {}, 'body': body or {},
                     'fixture': FIXTURE_ID, 'status': status, 'prior': list(prior)}
        result.append(Binding(route,method,scenario,principal,condition,node,tuple(('actor','observed-actor',*obligations)),
                              executor,viewport,mode,transport,variant))
    for variant,family,engine,width,height,principal,mode in COHORT_VARIANTS:
        if engine != executor: continue
        node = COHORT_MODULE + f'::test_browser_workflow[{variant}]'
        common = {'variant':variant,'viewport':f'{width}x{height}','mode':mode}
        if family == 'overview':
            add('route-295','GET',None,principal,'group-counts',node,('counts','search','scope','no-write'),query={'q':'$group_search'},**common)
            add('route-297','GET','overview-paging',principal,'overview-discovery',node,('counts','search','scope','no-write'),observed='tab=overview',query={'tab':'overview','grant_q':'$studio_search'},**common)
        elif family == 'access':
            if mode == 'native':
                add('route-297','POST','action=browse_access',principal,'access-browse',node,('draft','page-precedence','search','no-write'),
                    body={'action':'browse_access','report_page':'2','report_q':'Report'},query={'report_page':'1'},**common)
            else:
                add('route-328','GET','search-and-pagination',principal,'access-search',node,('draft','search','scope','missing-inactive-labels','no-write'),observed=None,query={'q':'$report_search'},**common)
                # None is the actual GET transport selector on this endpoint.
                result[-1].transport['action'] = None
            add('route-297','POST','action=studio_access',principal,'access-invalid',node,('draft','visible-error','scope','no-write'),
                body={'action':'studio_access','reports':'$invalid_reports'},**common)
            obligations=('db-persistence','reload-persistence','visible-feedback','audit-delta','single-save','scope')
            add('route-297','POST','action=studio_access',principal,'access-save',node,obligations,body={'action':'studio_access','reports':'$saved_reports'},**common)
            add('route-297','POST','explicit-save-after-browse',principal,'access-save-after-browse',node,(*obligations,'prior-browse'),
                observed='action=studio_access',body={'action':'studio_access','reports':'$saved_reports'},prior=('access-browse' if mode=='native' else 'access-search',),**common)
            add('route-297','POST','action=studio_access',principal,'access-csrf',node,('csrf-no-write','no-write'),body={'action':'studio_access'},status=403,**common)
        else:
            add('route-322','POST',None,principal,'members-browse',node,('search','draft','scope','counts','no-write'),
                body={'q':'$member_search','page':'1','role':'viewer','user_id':'$candidate'},**common)
            add('route-322','POST',None,principal,'members-invalid-role',node,('visible-error','draft','no-write'),
                body={'page':'2','role':'not-a-role','user_id':'$candidate'},status=400,**common)
    if executor == 'http':
        for principal in COHORT_PRINCIPALS:
            node=COHORT_MODULE+f'::test_authorization_boundaries[{principal}]'
            add('route-297','GET','tab=access',principal,'auth-org',node,('authorization','scope','no-write'),query={'tab':'access'},status=ORG_STATUS[principal])
            add('route-321','GET',None,principal,'auth-studio',node,('authorization','scope','no-write'),query={'q':'$member_search'},status=STUDIO_JSON_STATUS[principal])
            if ORG_STATUS[principal] != 200:
                add('route-297','POST','action=studio_access',principal,'auth-write-denied',node,('authorization','scope','no-write'),body={'action':'studio_access'},status=ORG_STATUS[principal])
        add('route-297','POST','action=studio_access','operator','operator-save',COHORT_MODULE+'::test_operator_bounded_access_save',
            ('db-persistence','reload-persistence','audit-delta','single-save','scope'),body={'action':'studio_access','reports':'$saved_reports'})
    return tuple(result)


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
    catalog = (*BINDINGS, *infrastructure_bindings('chromium'), *infrastructure_bindings('webkit'),
               *(row for executor in ('chromium','webkit','http') for row in cohort_bindings(executor))) if bindings is None else bindings
    payload = {"bindings": [asdict(row) for row in catalog],
               "conditions": {**CONDITIONS, **INFRASTRUCTURE_CONDITIONS, **COHORT_CONDITIONS} if conditions is None else conditions}
    if bindings is None:
        payload['modules'] = JOURNEY_MODULES
        payload['requirements'] = [{'policy': list(key), 'bindings': [list(item) for item in values]}
                                   for key, values in sorted(REVIEWED_REQUIREMENTS.items(), key=lambda item: repr(item[0]))]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
