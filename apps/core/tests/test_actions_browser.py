"""Live local HTTP polling plus synthetic lifecycle races; not portal E2E."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def endpoint():
    observations = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == '/':
                body = b'<p id="out">Waiting</p>'
            else:
                observations.append(self.path)
                body = json.dumps({'path': self.path}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if self.path != '/':
                time.sleep(0.08)  # Headers arrive before delayed body.
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', observations
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture(params=['chromium', 'webkit'])
def page(request, endpoint):
    with sync_playwright() as playwright:
        browser = getattr(playwright, request.param).launch(headless=True)
        page = browser.new_page()
        page.goto(endpoint[0])
        page.add_script_tag(content=(ROOT / 'static/actions.js').read_text())
        yield page
        browser.close()


def test_live_body_and_dependent_reads_share_one_flight(page, endpoint):
    result = page.evaluate('''async () => {
      let active = 0, peak = 0, commits = 0, poll;
      await new Promise(resolve => {
        poll = TrellumActions.createPoller({intervalMs: 10, timeoutMs: 2000,
          read: async ({signal, statusURL}) => {
            active++; peak = Math.max(peak, active);
            const status = await (await fetch(statusURL('/status'), {signal})).json();
            const history = await (await fetch(statusURL('/history'), {signal})).json();
            active--; return {status, history};
          }, isTerminal: () => commits === 1,
          onStatus: result => { commits++; document.querySelector('#out').textContent = result.history.path; if (commits === 2) resolve(); }
        });
        poll.start(); poll.refresh(); poll.refresh();
        setTimeout(() => {poll.refresh(); poll.refresh();}, 30);
      });
      await new Promise(resolve => setTimeout(resolve, 50));
      poll.stop(); return {peak, commits};
    }''')
    assert result == {'peak': 1, 'commits': 2}
    assert endpoint[1] == ['/status', '/history', '/status', '/history']
    assert page.locator('#out').inner_text() == '/history'


def test_timeout_ignored_signal_late_read_cannot_commit(page):
    result = page.evaluate('''async () => {
      let releases = [], commits = [], errors = [], poll;
      poll = TrellumActions.createPoller({intervalMs: 1000, timeoutMs: 30,
        read: () => new Promise(resolve => releases.push(resolve)),
        onStatus: result => commits.push(result), onError: error => errors.push(error.message),
        isTerminal: () => true});
      poll.start(); await new Promise(resolve => setTimeout(resolve, 70));
      poll.refresh(); await new Promise(resolve => setTimeout(resolve, 10));
      releases[1]('new'); await new Promise(resolve => setTimeout(resolve, 10));
      releases[0]('obsolete'); await new Promise(resolve => setTimeout(resolve, 20));
      poll.stop(); return {commits, errors};
    }''')
    assert result['commits'] == ['new']
    assert len(result['errors']) == 1
    assert 'unconfirmed' in result['errors'][0]


def test_pause_external_abort_and_currentness_invalidate_callbacks(page):
    result = page.evaluate('''async () => {
      let releases = [], commits = [], errors = [], current = true;
      const outer = new AbortController();
      const poll = TrellumActions.createPoller({signal: outer.signal, intervalMs: 15, timeoutMs: 200,
        isCurrent: () => current,
        read: ({signal}) => new Promise(resolve => releases.push({resolve, signal})),
        onStatus: result => commits.push(result), onError: error => errors.push(error.message)});
      const wait = () => new Promise(resolve => setTimeout(resolve, 30));
      poll.start(); await wait(); poll.pause(); releases[0].resolve('old'); await wait();
      const pausedCount = releases.length;
      poll.resume(); await wait(); current = false; releases[1].resolve('stale'); await wait();
      outer.abort(); const count = releases.length;
      releases.forEach(item => item.resolve('detached'));
      poll.resume(); poll.refresh(); poll.start(); await wait();
      return {commits, errors, pausedCount, count, finalCount: releases.length,
              aborted: releases.every(item => item.signal.aborted)};
    }''')
    assert result['commits'] == result['errors'] == []
    assert result['pausedCount'] == 1
    assert result['count'] == result['finalCount']
    assert result['aborted'] is True


def test_same_origin_status_lookup_rejects_unsafe_destinations(page):
    result = page.evaluate('''() => {
      const bad = ['https://foreign.test/status', '//foreign.test/status', 'javascript:alert(1)',
                   '/\\\\foreign.test/status', '/status#token', '/status\\nheader', null];
      return {valid: TrellumActions.statusURL('/status?revision=2'),
        rejected: bad.map(value => {try {TrellumActions.statusURL(value); return false;} catch (_) {return true;}})};
    }''')
    assert result['valid'].endswith('/status?revision=2')
    assert all(result['rejected'])


def test_read_failure_backoff_and_explicit_refresh(page):
    result = page.evaluate('''async () => {
      let calls = [], errors = 0;
      const poll = TrellumActions.createPoller({intervalMs: 30, maxIntervalMs: 60,
        read: () => {calls.push(performance.now()); throw new Error('Malformed status');},
        onStatus: () => {throw new Error('Must not commit');}, onError: () => errors++});
      poll.start(); await new Promise(resolve => setTimeout(resolve, 150));
      poll.stop(); return {errors, gaps: calls.slice(1).map((value, i) => value - calls[i])};
    }''')
    assert result['errors'] >= 2
    assert all(gap >= 45 for gap in result['gaps'])


def test_live_body_timeout_never_reports_success(page, endpoint):
    result = page.evaluate('''async () => {
      let commits = 0, error;
      const poll = TrellumActions.createPoller({intervalMs: 1000, timeoutMs: 25,
        read: async ({signal, statusURL}) => (await fetch(statusURL('/slow-body'), {signal})).json(),
        onStatus: () => commits++, onError: value => error = value.message});
      poll.start(); await new Promise(resolve => setTimeout(resolve, 120)); poll.stop();
      return {commits, error};
    }''')
    assert result['commits'] == 0
    assert 'unconfirmed' in result['error']
    assert endpoint[1] == ['/slow-body']


def test_refresh_coalesces_during_pending_read_and_terminal_stops(page):
    result = page.evaluate('''async () => {
      let release, calls = 0, commits = [];
      const poll = TrellumActions.createPoller({intervalMs: 10, timeoutMs: 1000,
        read: () => {calls++; return new Promise(resolve => release = resolve);},
        onStatus: value => commits.push(value), isTerminal: value => value === 'finished'});
      const wait = () => new Promise(resolve => setTimeout(resolve, 25));
      poll.start(); await wait(); poll.refresh(); poll.refresh(); poll.refresh();
      const pendingCalls = calls; release('running'); await wait();
      release('finished'); await wait(); await wait();
      poll.stop(); return {pendingCalls, calls, commits};
    }''')
    assert result == {'pendingCalls': 1, 'calls': 2, 'commits': ['running', 'finished']}


@pytest.mark.parametrize('callback', ['onError', 'isCurrent', 'isTerminal', 'onStatus'])
def test_callback_exception_stops_without_unhandled_rejection(page, callback):
    result = page.evaluate('''async callback => {
      let calls = 0, unhandled = [], errors = 0;
      addEventListener('unhandledrejection', event => {unhandled.push(String(event.reason)); event.preventDefault();});
      const options = {intervalMs: 10, timeoutMs: 100,
        read: () => {calls++; if (callback === 'onError') throw new Error('Read failed'); return {};},
        onStatus: () => {}, onError: () => errors++};
      options[callback] = () => {throw new Error('Callback failed');};
      const poll = TrellumActions.createPoller(options);
      poll.start(); await new Promise(resolve => setTimeout(resolve, 90));
      poll.refresh(); await new Promise(resolve => setTimeout(resolve, 30)); poll.stop();
      return {calls, unhandled, errors};
    }''', callback)
    assert result['calls'] == 1
    assert result['unhandled'] == []
    assert result['errors'] == (0 if callback == 'onError' else 1)
