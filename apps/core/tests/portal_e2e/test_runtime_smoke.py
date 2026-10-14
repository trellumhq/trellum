"""Infrastructure proof only, not complete account/group journey coverage."""
import json
from pathlib import Path
import pytest

from .fixtures import browser_metrics, login, visible_geometry

pytestmark = pytest.mark.portal_e2e
VIEWPORTS = [(1440, 900), (390, 844), (720, 450), (195, 422)]


@pytest.mark.parametrize('size', VIEWPORTS, ids=['desktop', 'mobile', 'desktop-200pct', 'mobile-200pct'])
@pytest.mark.parametrize('javascript', [False, True], ids=['native', 'js-enabled'])
def test_account_persistence_and_csrf(journey, size, javascript):
    from django.contrib.auth import get_user_model
    from playwright.sync_api import sync_playwright, expect
    origin = journey.unit.origin
    with sync_playwright() as playwright:
        browser = getattr(playwright, journey.unit.env['PORTAL_E2E_BROWSER']).launch()
        context = browser.new_context(viewport={'width': size[0], 'height': size[1]}, java_script_enabled=javascript,
                                      extra_http_headers=journey.headers('org-admin', 'infra-profile'))
        page = context.new_page()
        context.tracing.start(screenshots=True, snapshots=True)
        try:
            login(page, origin, 'org-admin')
            actor = journey.assert_actor(context, 'org-admin')
            field = page.locator('input[name=name]')
            name = f'Infrastructure {size[0]} {javascript}'
            field.fill(name)
            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/account')) as saved:
                field.locator('xpath=ancestor::form').locator('button[type=submit]').click()
            assert saved.value.status == 302  # This form remains POST/redirect with JS enabled.
            expect(page.locator('input[name=name]')).to_have_value(name)
            visible_geometry(page.get_by_role('status').filter(has_text='Profile updated.'))
            journey.checkpoint('visible-feedback')
            assert journey.database(lambda: get_user_model().objects.get(pk=actor['actor_id']).name) == name
            journey.checkpoint('db-persistence')
            page.reload()
            expect(page.locator('input[name=name]')).to_have_value(name)
            journey.checkpoint('reload-persistence')
            response = context.request.post(origin + '/account', form={'action': 'profile', 'name': 'CSRF must reject'},
                                            headers={'Referer': origin + '/account'})
            assert response.status == 403
            assert journey.database(lambda: get_user_model().objects.get(pk=actor['actor_id']).name) == name
            journey.checkpoint('csrf-no-write')
            events = [row for row in journey.events() if row['route'] == 'route-212' and row['method'] == 'POST']
            assert len(events) == 2 and [row['status'] for row in events] == [302, 403]
            for event in events:
                journey.assert_observed_actor(event)
                assert event['action'] == 'action=profile'
                assert event['complete']
            journey.record(event_ids=[row['event_id'] for row in events])
            if javascript:
                metrics = browser_metrics(page)
                metrics.update({'node_id': journey.node_id, 'javascript': True})
                with (Path(journey.unit.env['PORTAL_E2E_ARTIFACTS']) / 'browser-metrics.jsonl').open('a') as output:
                    output.write(json.dumps(metrics) + '\n')
        finally:
            context.tracing.stop(path=str(Path(journey.unit.env['PORTAL_E2E_ARTIFACTS']) / f'{journey.correlation}-private-trace.zip'))
            browser.close()


@pytest.mark.parametrize('size', VIEWPORTS, ids=['desktop', 'mobile', 'desktop-200pct', 'mobile-200pct'])
def test_shared_ajax_group_save(journey, size):
    from apps.orgs.models import PermissionGroup
    from playwright.sync_api import sync_playwright, expect
    group = PermissionGroup.objects.get(org__slug='alpha', name='member')
    path = f'/orgs/alpha/settings/groups/{group.pk}'
    origin = journey.unit.origin
    with sync_playwright() as playwright:
        browser = getattr(playwright, journey.unit.env['PORTAL_E2E_BROWSER']).launch()
        context = browser.new_context(viewport={'width': size[0], 'height': size[1]},
                                      extra_http_headers=journey.headers('org-admin', 'infra-group-edit'))
        page = context.new_page()
        context.tracing.start(screenshots=True, snapshots=True)
        try:
            login(page, origin, 'org-admin')
            journey.assert_actor(context, 'org-admin')
            page.goto(origin + path)
            form = page.locator('#groupSettingsForm')
            expect(form).to_have_attribute('data-settings-initialized', '1')
            name = 'Infrastructure edited group'
            form.locator('input[name=name]').fill(name)
            with page.expect_response(lambda response: response.request.method == 'POST' and response.url == origin + path) as saved:
                form.get_by_role('button', name='Save changes', exact=True).click()
            assert saved.value.status == 200
            assert saved.value.request.headers['x-trellum-form'] == '1'
            assert saved.value.json()['ok'] is True
            status = form.locator('[data-settings-dirty-status]')
            expect(status).to_have_text('Group updated.')
            visible_geometry(status)
            journey.checkpoint('ajax-visible-feedback')
            journey.database(group.refresh_from_db)
            assert group.name == name
            journey.checkpoint('db-persistence')
            page.reload()
            expect(page.locator('#groupSettingsForm input[name=name]')).to_have_value(name)
            journey.checkpoint('reload-persistence')
            events = [row for row in journey.events() if row['route'] == 'route-297' and row['method'] == 'POST']
            assert len(events) == 1 and events[0]['action'] == 'action=edit'
            journey.assert_observed_actor(events[0])
            journey.record(event_ids=[events[0]['event_id']])
            with (Path(journey.unit.env['PORTAL_E2E_ARTIFACTS']) / 'browser-metrics.jsonl').open('a') as output:
                output.write(json.dumps({'node_id': journey.node_id, **browser_metrics(page)}) + '\n')
        finally:
            context.tracing.stop(path=str(Path(journey.unit.env['PORTAL_E2E_ARTIFACTS']) / f'{journey.correlation}-private-trace.zip'))
            browser.close()
