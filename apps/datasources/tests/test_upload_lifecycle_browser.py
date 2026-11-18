"""Real rendered rows and HTTP handlers cover file states without navigation."""
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import pytest
from playwright.sync_api import expect, sync_playwright
from django.db import connections

from apps.datasources.models import DataSource, RepoDataSource


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_rendered_file_replace_and_clear(login, org_admin, studio_tree, width, monkeypatch):
    # Playwright callbacks run synchronously on this test thread.
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    ds = DataSource.objects.create(studio=studio_tree, name="litellm_key", type="file",
                                   config={"upload": True, "path": ""})
    DataSource.objects.create(studio=studio_tree, name="warehouse", type="postgres",
                              config={"host": "db.example", "database": "reports"})
    DataSource.objects.create(studio=studio_tree, name="quarterly_department_budget", type="file",
                              config={"upload": True, "path": "data-sources/files/quarterly_department_budget.xlsx"})
    RepoDataSource.objects.create(studio=studio_tree, name="quarterly_department_budget", type="file",
                                  config={"upload": True}, source_file="reports/quarterly-department-budget/report.yaml")
    client = login(org_admin)
    base = f"/s/{studio_tree.org.slug}/{studio_tree.slug}"
    url = base + "/settings/datasources"
    root = Path(__file__).resolve().parents[3]
    artifacts = root / ".artifacts" / "upload-states"
    artifacts.mkdir(parents=True, exist_ok=True)
    workbook = BytesIO()
    pd.DataFrame({"value": ["synthetic Excel upload"]}).to_excel(workbook, index=False)
    second = studio_tree.project_root / 'data-sources/files/quarterly_department_budget.xlsx'
    second.parent.mkdir(parents=True, exist_ok=True)
    second.write_bytes(workbook.getvalue())
    transport = {"pause": False, "pending": None, "fail": False}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
        page.add_init_script("localStorage.setItem('mgmt-theme', 'light')")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def dispatch(route):
            request = route.request
            path = urlparse(request.url).path
            headers = request.headers
            response = client.generic(
                request.method, path, data=request.post_data_buffer or b"",
                content_type=headers.get("content-type", "application/octet-stream"),
                HTTP_ACCEPT=headers.get("accept", ""),
                HTTP_X_REQUESTED_WITH=headers.get("x-requested-with", ""),
            )
            body = response.content
            connections.close_all()
            route.fulfill(status=response.status_code, content_type=response.get("Content-Type"), body=body)

        def serve(route):
            path = urlparse(route.request.url).path
            if path.startswith("/static/"):
                asset = root / "static" / path.removeprefix("/static/")
                route.fulfill(path=str(asset)) if asset.is_file() else route.fulfill(status=404, body="")
            elif path.startswith("/_vendor/"):
                asset = root / 'trellum/static/vendor' / path.removeprefix('/_vendor/')
                route.fulfill(path=str(asset)) if asset.is_file() else route.fulfill(status=404, body='')
            elif path == '/api/assistant/widget.js':
                route.fulfill(body='')
            elif path.endswith("/upload") and transport["pause"]:
                transport["pending"] = route
            elif path.endswith("/upload") and transport["fail"]:
                route.fulfill(status=500, json={"ok": False, "error": "The file could not be changed. The current file was kept."})
            else:
                dispatch(route)

        page.route("**/*", serve)
        page.goto("http://datasources.test" + url)
        row = page.locator('#ds-row-litellm_key')
        file_input = row.locator('.ds-file-input')
        name = row.locator('[data-ds-filename]')
        remove = row.get_by_role('button', name='Remove', exact=True)
        more = row.locator('[data-ds-more]')
        menu = row.locator('[data-ds-action-menu]')

        def capture(state):
            row.scroll_into_view_if_needed()
            # A card on mobile; the full multirow table on desktop.
            subject = page if state == 'more' and width < 768 else row if width < 768 else page.locator('.ds-table')
            for theme in ('light', 'dark'):
                page.evaluate('(theme) => document.documentElement.setAttribute("data-theme", theme)', theme)
                subject.screenshot(path=str(artifacts / f'{state}-{width}-{theme}.png'), animations='disabled')
            page.evaluate('document.documentElement.setAttribute("data-theme", "light")')

        def no_clipping():
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page.locator('.ui-table-scroll').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
            for control in row.locator('.ui-btn:visible').all():
                rect = control.bounding_box()
                assert rect['x'] >= 0 and rect['x'] + rect['width'] <= width + 1
                assert control.evaluate('(el) => getComputedStyle(el).fontSize') == '14px'
                if width < 768:
                    assert rect['height'] >= 44

        expect(name).to_have_text('No file uploaded')
        expect(remove).to_be_hidden()
        capture('empty')
        file_input.set_input_files({'name': 'litellm_key.xlsx', 'mimeType': 'application/octet-stream', 'buffer': workbook.getvalue()})
        expect(name).to_have_text('litellm_key.xlsx')
        expect(remove).to_be_enabled()
        expect(row.locator('[data-ds-upload-label]')).to_have_text('Replace')
        no_clipping()
        capture('uploaded')
        ds.refresh_from_db()
        original = studio_tree.project_root / ds.config['path']
        assert original.read_bytes() == workbook.getvalue()

        # Open via keyboard, traverse native controls, dismiss with Escape.
        more.focus()
        page.keyboard.press('Enter')
        expect(menu).to_be_visible()
        expect(more).to_have_attribute('aria-expanded', 'true')
        page.keyboard.press('Tab')
        assert menu.evaluate('(el) => el.contains(document.activeElement)')
        menu_rect = menu.bounding_box()
        assert menu_rect['x'] >= 0 and menu_rect['x'] + menu_rect['width'] <= width
        capture('more')
        page.keyboard.press('Escape')
        expect(menu).to_be_hidden()
        expect(more).to_be_focused()

        transport['pause'] = True
        file_input.set_input_files({'name': 'replacement.csv', 'mimeType': 'text/csv', 'buffer': b'value\nsynthetic CSV upload\n'})
        expect(file_input).to_be_disabled()
        expect(remove).to_be_disabled()
        expect(row.locator('.ds-progress')).to_be_visible()
        capture('replacing')
        assert transport['pending'] is not None
        transport['pause'] = False
        dispatch(transport.pop('pending'))
        expect(name).to_have_text('litellm_key.csv')
        expect(file_input).to_be_enabled()
        ds.refresh_from_db()
        replacement = studio_tree.project_root / ds.config['path']
        assert not original.exists()
        assert replacement.read_bytes() == b'value\nsynthetic CSV upload\n'
        no_clipping()
        capture('replaced')

        alias = DataSource.objects.create(studio=studio_tree, name="alias", type="file",
                                          config={"path": ds.config["path"]})
        file_input.set_input_files({'name': 'shared.csv', 'mimeType': 'text/csv', 'buffer': b'value\nchanged\n'})
        expect(row.locator('[data-ds-result]')).to_contain_text('Another data source uses this file')
        expect(file_input).to_be_enabled()
        expect(name).to_have_text('litellm_key.csv')
        assert replacement.read_bytes() == b'value\nsynthetic CSV upload\n'
        remove.click()
        expect(row.locator('[data-settings-dirty-status]')).to_contain_text('Another data source uses this file')
        expect(remove).to_be_enabled()
        assert replacement.read_bytes() == b'value\nsynthetic CSV upload\n'
        capture('shared-error')
        alias.delete()

        # An existing destination must not be overwritten by replacement.
        original.write_bytes(workbook.getvalue())
        file_input.set_input_files({'name': 'collision.xlsx', 'mimeType': 'application/octet-stream', 'buffer': workbook.getvalue()})
        expect(row.locator('[data-ds-result]')).to_contain_text('Another file already uses')
        assert original.read_bytes() == workbook.getvalue()
        assert replacement.read_bytes() == b'value\nsynthetic CSV upload\n'
        capture('collision')
        original.unlink()

        # A backend error keeps the file and usable replacement controls.
        transport['fail'] = True
        file_input.set_input_files({'name': 'failed.xlsx', 'mimeType': 'application/octet-stream', 'buffer': workbook.getvalue()})
        expect(row.locator('[data-ds-result]')).to_contain_text('The current file was kept')
        expect(file_input).to_be_enabled()
        expect(name).to_have_text('litellm_key.csv')
        capture('error')
        transport['fail'] = False

        # A dropped response is distinguished from a confirmed server failure.
        transport['pause'] = True
        file_input.set_input_files({'name': 'uncertain.csv', 'mimeType': 'text/csv', 'buffer': b'value\nuncertain\n'})
        expect(file_input).to_be_disabled()
        page.wait_for_function("document.querySelector('.ds-progress:not([hidden])') !== null")
        capture('pending')
        transport['pending'].abort('failed')
        expect(row.locator('[data-ds-result]')).to_contain_text('outcome unconfirmed')
        capture('unconfirmed')
        transport['pause'] = False

        transport['fail'] = True
        remove.click()
        expect(row.locator('[data-settings-dirty-status]')).to_contain_text('The current file was kept')
        expect(remove).to_be_enabled()
        assert replacement.exists()
        capture('remove-error')
        transport['fail'] = False

        # The removal request uses the shared form handler and the real endpoint.
        transport['pause'] = True
        remove.click()
        expect(remove).to_be_disabled()
        capture('removing')
        transport['pause'] = False
        dispatch(transport.pop('pending'))
        expect(name).to_have_text('No file uploaded')
        expect(remove).to_be_hidden()
        expect(row.locator('[data-ds-download]')).to_have_count(0)
        expect(row.locator('[data-ds-upload-label]')).to_have_text('Upload file')
        ds.refresh_from_db()
        assert ds.config['path'] == '' and not replacement.exists()
        assert DataSource.objects.filter(pk=ds.pk).exists()
        assert page.url.endswith(url)
        capture('removed')
        file_input.set_input_files({'name': 'again.xlsx', 'mimeType': 'application/octet-stream', 'buffer': workbook.getvalue()})
        expect(name).to_have_text('litellm_key.xlsx')
        expect(remove).to_be_enabled()
        expect(row.locator('[data-settings-dirty-status]')).to_be_empty()
        assert not errors
        connections.close_all()
        browser.close()
    connections.close_all()
