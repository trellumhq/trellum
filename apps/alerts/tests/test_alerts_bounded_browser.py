"""Shared recipient lifecycle over local HTTP; live portal gate runs separately."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import pytest
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
@pytest.mark.parametrize('width', [1440, 390])
def test_remote_choices_keep_selected_nodes_search_and_failures(engine, width):
    state = {'fail': False, 'revoked': False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            if self.path == '/':
                body = b'<form><div data-recipient-picker data-picker-label="Recipients" data-recipient-source="/choices"><label>Recipients</label><div data-recipient-type="people"><label><input type="checkbox" name="recipients" value="99" checked>Person 099</label></div><div data-recipient-type="roles"><label><input type="checkbox" name="recipient_roles" value="everyone">Everyone</label></div><div data-recipient-type="groups"></div></div></form>'
            else:
                if state['fail']:
                    self.send_response(503); self.end_headers(); return
                term = query.get('q', [''])[0]; number = int(query.get('page', [1])[0])
                if term == 'slow':
                    time.sleep(.15)
                rows = [{'id': i, 'name': f'Person {i:03}', 'email': f'person{i:03}@example.test'} for i in range(1, 101)]
                rows = [row for row in rows if not state['revoked'] or row['id'] != 99]
                matches = [row for row in rows if term.casefold() in (row['name'] + row['email']).casefold()]
                selected = set(query.get('selected_people', [''])[0].split(','))
                start = (number - 1) * 25
                body = json.dumps({'members': matches[start:start+25], 'groups': [],
                    'selected': {'members': [row for row in rows if str(row['id']) in selected], 'groups': []},
                    'pagination': {'members': {'page': number, 'total': len(matches), 'has_previous': number > 1, 'has_next': start+25 < len(matches)},
                                   'groups': {'page': 1, 'total': 0, 'has_previous': False, 'has_next': False}}}).encode()
            self.send_response(200); self.send_header('Content-Type', 'text/html' if self.path == '/' else 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with sync_playwright() as pw:
            browser = getattr(pw, engine).launch(headless=True)
            page = browser.new_page(viewport={'width': width, 'height': 900})
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            for style in ['choice-picker.css', 'recipient-picker.css']:
                page.add_style_tag(content=(ROOT/'static'/style).read_text())
            for script in ['choice-picker.js', 'recipient-picker.js']:
                page.add_script_tag(content=(ROOT/'static'/script).read_text())
            field = page.locator('[data-recipient-picker]')
            assert field.locator('.ui-picker-trigger').count() == 1, errors
            page.evaluate("document.body.style.paddingTop='200px';document.body.style.zoom='2'")
            field.locator('.ui-picker-trigger').click()
            expect(field.locator('.ui-picker-pager')).to_contain_text('100 choices')
            assert field.locator('.recipient-picker-option').count() <= 27
            assert field.locator('.ui-picker-options').evaluate('el=>el.clientHeight') > 20
            bounds = field.locator('.ui-picker-popup').bounding_box()
            assert bounds['x'] >= -1 and bounds['x']+bounds['width'] <= width+2
            page.evaluate("document.body.style.zoom='';document.body.style.paddingTop=''")
            field.get_by_role('button', name='Next', exact=True).click()
            expect(field.locator('.ui-picker-pager')).to_contain_text('Page 2')
            field.locator('input[value="26"]').check()
            field.locator('.ui-picker-search').fill('slow')
            page.wait_for_timeout(230)
            field.locator('.ui-picker-search').fill('person100')
            expect(field.locator('input[value="100"]')).to_be_visible()
            field.locator('input[value="100"]').check()
            expect(field.locator('.recipient-picker-selected')).to_contain_text('Person 099')
            assert set(page.locator('form').evaluate("f => new FormData(f).getAll('recipients')")) == {'99', '26', '100'}
            state['fail'] = True
            field.locator('.ui-picker-search').fill('another')
            expect(field.locator('.ui-picker-message')).to_contain_text('Could not load recipients')
            assert set(page.locator('form').evaluate("f => new FormData(f).getAll('recipients')")) == {'99', '26', '100'}
            state['fail'] = False
            field.locator('.ui-picker-search').fill('person001')
            expect(field.locator('input[value="1"]')).to_be_visible()
            field.get_by_role('button', name='Done', exact=True).click()
            expect(field.locator('.ui-picker-trigger')).to_be_focused()
            page.evaluate('TrellumRecipients.init(document); TrellumRecipients.init(document)')
            assert field.locator('.ui-picker-trigger').count() == 1
            field.get_by_role('button', name='Remove Person 026', exact=True).click()
            assert set(page.locator('form').evaluate("f => new FormData(f).getAll('recipients')")) == {'99', '100'}
            state['revoked'] = True
            field.locator('.ui-picker-trigger').click()
            expect(field.locator('.recipient-picker-selected')).to_contain_text('Selected person is unavailable')
            assert 'Person 099' not in field.inner_text() and 'person099@example.test' not in field.inner_html()
            field.get_by_role('button', name='Done', exact=True).click()
            field.get_by_role('button', name='Remove Selected person is unavailable', exact=True).click()
            assert set(page.locator('form').evaluate("f => new FormData(f).getAll('recipients')")) == {'100'}
            browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
