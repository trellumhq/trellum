"""Key journey: run a report through a real worker, read its log, refresh the registry (L5 routes)."""
import pytest

from .key_journeys import VARIANTS, bind, browser_session, record_metrics

pytestmark = pytest.mark.portal_e2e
S = '/s/alpha/studio-0'
HEAVY = pytest.mark.heavy
BOTH = [pytest.param('chromium-desktop'), pytest.param('webkit-mobile', marks=HEAVY)]
TERMINAL_RUN = ('success', 'error', 'timeout', 'oom', 'stopped')


def latest_run():
    from apps.runner.models import Run
    run = Run.objects.filter(studio__org__slug='alpha', studio__slug='studio-0', slug='report-0001').order_by('-created_at').first()
    return None if run is None else {'status': run.status, 'exit_code': run.exit_code, 'requested_by': run.requested_by_id}


def refresh_actions():
    from apps.runner.models import StudioMaintenanceAction
    return [{'state': row.state, 'terminal': row.is_terminal}
            for row in StudioMaintenanceAction.objects.filter(studio__org__slug='alpha', studio__slug='studio-0')]


@pytest.mark.worker
@pytest.mark.parametrize('variant', BOTH)
def test_operations_run_log_registry_refresh(journey, variant):
    from playwright.sync_api import expect
    spec = VARIANTS[variant]
    origin = journey.unit.origin
    with browser_session(journey, 'org-admin', spec, condition='key-operations-run') as (page, context):
        page.goto(origin + S + '/operations')
        row = page.locator('[data-ops-row=report-0001]')
        expect(row).to_be_visible(timeout=20000)
        expect(page.get_by_text('Worker offline')).to_have_count(0, timeout=20000)
        row.locator('.ops-run-dropdown .ops-row-btn').click()
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/reports/report-0001/run')) as accepted:
            page.locator('#opsRunMenu_report-0001 .ops-run-menu-item', has_text='Generate').click()
        assert accepted.value.ok and accepted.value.json()['ok'] is True
        journey.checkpoint('run-accepted')
        expect(row.locator('.ops-status')).to_contain_text('Success', timeout=90000)
        run = journey.database(latest_run)
        assert run['status'] == 'success' and run['exit_code'] == 0
        journey.checkpoint('run-terminal')
        # The run's own log is readable from the same row.
        row.locator('details.ops-secondary summary').click()
        row.get_by_role('button', name='Log').click()
        expect(page.locator('body')).to_contain_text('Published to LocalBackend', timeout=30000)
        journey.checkpoint('log-visible')
        bind(journey, 'route-174', 'POST', None, 'org-admin', spec, ())
        # Registry refresh is a durable maintenance action: accepted, then a terminal state.
        refresh = page.locator('[data-ops-action=refresh]')
        refresh.scroll_into_view_if_needed()
        refresh.click()
        actions, deadline_ok = [], False
        for _ in range(60):
            actions = journey.database(refresh_actions)
            if actions and all(item['terminal'] for item in actions):
                deadline_ok = True
                break
            page.wait_for_timeout(1000)
        assert deadline_ok, actions
        assert [item['state'] for item in actions] == ['succeeded'], actions
        journey.checkpoint('refresh-terminal')
        bind(journey, 'route-157', 'POST', None, 'org-admin', spec, ())
        record_metrics(journey, page)
