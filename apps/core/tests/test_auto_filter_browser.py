"""Filters and pickers apply without an Apply, Go or Search button."""
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
PAGE = '''<form method="get" data-auto-filter>
<label>Search <input type="search" name="q" value="{q}"></label>
<label>Enabled <select name="enabled"><option value="">All</option><option value="true">Enabled</option></select></label>
<label><input type="checkbox" name="mine" value="1"> Mine</label></form>'''


def test_selects_apply_at_once_and_text_after_a_pause():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        seen = []
        def serve(route):
            query = parse_qs(urlsplit(route.request.url).query)
            seen.append(query)
            route.fulfill(content_type='text/html', body=PAGE.format(q=query.get('q', [''])[0]))
        page.route('http://filters.test/**', serve)
        page.goto('http://filters.test/list')
        script = (ROOT / 'static/auto-filter.js').read_text()
        page.add_script_tag(content=script)
        assert page.get_by_role('button').count() == 0
        page.locator('[name=enabled]').select_option('true')
        page.wait_for_url('**enabled=true')
        page.add_script_tag(content=script)
        page.locator('[name=mine]').check()
        page.wait_for_url('**mine=1**')
        # Typing waits for a pause (about 400 ms) and keeps the caret in the box.
        page.add_script_tag(content=script)
        field = page.locator('[name=q]')
        field.click()
        field.press_sequentially('abc', delay=60)
        before = len(seen)
        page.wait_for_timeout(200)
        assert len(seen) == before
        page.wait_for_url('**q=abc**')
        assert len(seen) == before + 1
        # The reloaded page puts the caret back where the visitor was typing.
        page.add_script_tag(content=script)
        field = page.locator('[name=q]')
        expect(field).to_be_focused()
        assert field.input_value() == 'abc'
        browser.close()
