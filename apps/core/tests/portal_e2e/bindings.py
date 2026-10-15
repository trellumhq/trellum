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


KEY_DIR = 'apps/core/tests/portal_e2e/'
DESKTOP_VARIANT = ('chromium-desktop', 'chromium', '1440x900')
MOBILE_VARIANT = ('webkit-mobile', 'webkit', '390x844')

# Small, reviewed set of the most common user journeys. This is deliberately NOT
# the full permission matrix: the unbound remainder of routes.json is declared
# out of scope below instead of staying "pending" forever. Conditions are named
# journeys, never inferred from status codes or policy prose.
KEY_CONDITIONS = {
    'key-login-dashboard': 'Real login form submit lands on the organization dashboard listing the studio.',
    'key-catalog-back': 'Catalog search, open a report, then Back restores search and scroll.',
    'key-metrics': 'Metrics definitions search, a metric detail view and the comparison workspace.',
    'key-analytics-experiments': 'Analytics and Experiments render primary rows, then secondary content settles without errors.',
    'key-schedules': 'Deliveries list, create a schedule and edit it; persisted after reload.',
    'key-alerts': 'Alerts list, edit a rule and save; persisted after reload.',
    'key-invite-access': 'Invite a member (no real mail) and change a member studio access; persisted after reload.',
    'key-operations-filter': 'Operations state filter and search against the real bounded status contract.',
    'key-operations-run': 'Run a report through a real worker, read its log, and run registry refresh to a terminal state.',
    'key-datasource-test': 'Datasource connection test: a file source succeeds and a network source whose endpoint never answers reports a bounded timeout (no external service).',
    'key-selected-scope': 'A viewer scoped to selected reports sees only those reports and cannot open others.',
}


def _journey(module, test, condition, principal, variants, rows):
    """rows: (route, method, action, checkpoints) -> one reviewed binding per row/variant."""
    result = []
    for variant, engine, viewport in variants:
        for route, method, action, checkpoints in rows:
            result.append(Binding(route, method, action, principal, condition, f'{KEY_DIR}{module}.py::{test}[{variant}]',
                ('actor', 'observed-actor', *checkpoints), engine, viewport, 'enhanced'))
    return result


BOTH = (DESKTOP_VARIANT, MOBILE_VARIANT)
DESKTOP = (DESKTOP_VARIANT,)
KEY_BINDINGS: tuple[Binding, ...] = tuple([
    # L2 reports / overviews / operations
    *_journey('test_key_journeys_l2', 'test_catalog_search_open_back', 'key-catalog-back', 'org-admin', BOTH,
        [('route-139', 'GET', None, ('catalog-search', 'catalog-paging', 'report-open', 'back-restored', 'scroll-restored'))]),
    *_journey('test_key_journeys_l2', 'test_metrics_detail_comparison', 'key-metrics', 'org-admin', DESKTOP,
        [('route-146', 'GET', None, ('definitions', 'search')),
         ('route-324', 'GET', None, ('metric-detail',)),
         ('route-147', 'GET', None, ('comparison',))]),
    *_journey('test_key_journeys_l2', 'test_analytics_experiments_progressive', 'key-analytics-experiments', 'org-admin', DESKTOP,
        [('route-145', 'GET', None, ('primary-rows', 'secondary-settled')),
         ('route-149', 'GET', None, ('primary-rows', 'secondary-settled'))]),
    *_journey('test_key_journeys_l2', 'test_operations_filter', 'key-operations-filter', 'org-admin', BOTH,
        [('route-144', 'GET', None, ('filter', 'search')),
         ('route-346', 'GET', None, ('status-contract',))]),
    *_journey('test_key_journeys_l2', 'test_selected_scope_viewer', 'key-selected-scope', 'group-selected', DESKTOP,
        [('route-139', 'GET', None, ('scope-visible', 'scope-hidden', 'direct-url-denied')),
         ('route-146', 'GET', None, ('scope-denied',))]),
    # L3 lists / permissions
    *_journey('test_key_journeys_l3', 'test_login_lands_on_dashboard', 'key-login-dashboard', 'org-admin', BOTH,
        [('route-244', 'GET', None, ('landing', 'studio-card'))]),
    *_journey('test_key_journeys_l3', 'test_schedules_list_create_edit', 'key-schedules', 'org-admin', DESKTOP,
        [('route-141', 'GET', None, ('list',)),
         ('route-142', 'POST', None, ('db-persistence', 'reload-persistence', 'visible-feedback')),
         ('route-178', 'POST', 'action=save', ('db-persistence', 'reload-persistence', 'visible-feedback'))]),
    *_journey('test_key_journeys_l3', 'test_alerts_list_edit_save', 'key-alerts', 'org-admin', DESKTOP,
        [('route-196', 'GET', None, ('list',)),
         ('route-199', 'POST', 'action=save', ('db-persistence', 'reload-persistence', 'visible-feedback'))]),
    # L4 forms / mutations
    *_journey('test_key_journeys_l4', 'test_invite_member_and_change_access', 'key-invite-access', 'org-admin', DESKTOP,
        [('route-306', 'POST', None, ('db-persistence', 'reload-persistence', 'no-mail')),
         ('route-292', 'POST', None, ('db-persistence', 'reload-persistence', 'visible-feedback'))]),
    *_journey('test_key_journeys_l4', 'test_datasource_connection_test', 'key-datasource-test', 'org-admin', DESKTOP,
        [('route-263', 'POST', None, ('test-success', 'test-timeout'))]),
    # L5 services / operator
    *_journey('test_key_journeys_l5', 'test_operations_run_log_registry_refresh', 'key-operations-run', 'org-admin', BOTH,
        [('route-174', 'POST', None, ('run-accepted', 'run-terminal', 'log-visible')),
         ('route-157', 'POST', None, ('refresh-terminal',))]),
])
BINDINGS: tuple[Binding, ...] = KEY_BINDINGS
CONDITIONS: dict[str, str] = dict(KEY_CONDITIONS)
JOURNEY_MODULES = {'L1': (),
    'L2': (KEY_DIR + 'test_key_journeys_l2.py',), 'L3': (KEY_DIR + 'test_key_journeys_l3.py',),
    'L4': (KEY_DIR + 'test_key_journeys_l4.py',), 'L5': (KEY_DIR + 'test_key_journeys_l5.py',)}
# Everything in routes.json that no key journey binds is reported as out of
# scope (counted by the gate), never as pending. Adding a binding above brings a
# policy tuple back into scope only through REVIEWED_REQUIREMENTS.
OUT_OF_SCOPE_REMAINDER = ('Full permission matrix (all principals x conditions x methods, 7,470 cases) and L1 navigation '
                          'shard: explicitly out of scope for the key-journey set; see README "Key journeys".')
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
            add('route-322','POST',None,principal,'members-invalid-role',node,('visible-error','draft','page-preserved','search','no-write'),
                body={'q':'Cohort person','page':'2','role':'not-a-role','user_id':'$candidate'},status=400,**common)
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
