"""Bounded, provider-free Chromium timing of the real local review protocol."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def summarize(samples):
    result = {}
    for surface in sorted({s['surface'] for s in samples}):
        for mode in ('waiting', 'busy'):
            rows = [s for s in samples if s['surface'] == surface and s['mode'] == mode]
            if rows:
                result[f'{surface}/{mode}'] = {
                    key: {'median_ms': round(statistics.median(r['ms'][key] for r in rows), 3),
                          'max_ms': round(max(r['ms'][key] for r in rows), 3)}
                    for key in rows[0]['ms']}
    return result


def wait_for_chat_state(page, feedback_id, phase, text):
    # Observe each real animation frame instead of locator retry backoff, which
    # can add hundreds of milliseconds to an otherwise visible update.
    page.wait_for_function("""({id, phase, text}) => {
        const row = [...document.querySelectorAll('[data-feedback-id]')]
            .find(node => node.dataset.feedbackId === id);
        const visible = node => node && node.getClientRects().length > 0;
        if (!row) return false;
        if (phase === 'activity') {
            const summary = row.querySelector('.fw-review-activity-summary');
            return visible(summary) && summary.textContent === text;
        }
        const state = row.querySelector('.fw-review-activity-state');
        return visible(state) && state.textContent === 'Completed' &&
            [...document.querySelectorAll('.fw-review-message')].some(
                node => visible(node) && node.textContent === text);
    }""", arg={'id': feedback_id, 'phase': phase, 'text': text}, polling='raf')


def run(args):
    from playwright.sync_api import sync_playwright
    from trellum.review import http, inject
    from trellum.review.state import ReviewState
    from trellum.runner.execute import run_report
    from trellum.rendering.cdn import serve_vendor_request

    samples, builds, polling_checks = [], [], []
    with tempfile.TemporaryDirectory(prefix='trellum-timing-') as directory, ExitStack() as stack:
        root = Path(directory)
        stack.enter_context(patch.dict(os.environ, {'FW_PROJECT_ROOT': directory, 'FW_UPDATE_CHECK': '0'}))
        stack.enter_context(patch('trellum.project._project_root_override', None))
        # Keep personal settings out of this synthetic project.
        stack.enter_context(patch('pathlib.Path.home', return_value=root / 'home'))
        for slug, kind in (('timing-report', 'report'), ('timing-analysis', 'analysis')):
            source = root / 'reports' / slug
            source.mkdir(parents=True)
            (source / 'report.yaml').write_text(json.dumps({
                'slug': slug, 'kind': kind, 'name': 'Synthetic timing fixture',
                'theme': 'trellum light', 'studio': 'Example', 'category': 'Testing',
                'description': 'Deterministic synthetic timing fixture.', 'annotations': False}))
            if kind == 'analysis':
                (source / 'content.md').write_text('## Finding\n\nSynthetic timing fixture.\n')
            else:
                (source / 'generator.py').write_text(
                    'from trellum import BaseReport\n'
                    'class TimingReport(BaseReport):\n'
                    '    def generate(self, ctx):\n'
                    '        ctx.add_section("Synthetic timing fixture", [])\n')
            start = time.perf_counter()
            run_report(str(source))
            builds.append({'slug': slug, 'phase': 'initial_build', 'ms': (time.perf_counter()-start)*1000})

        output = str(root / 'output')
        state = ReviewState()
        state.bind_dir(output)
        stack.enter_context(patch.object(http, 'STATE', state))
        stack.enter_context(patch.object(inject, 'STATE', state))

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=output, **kw)

            def log_message(self, *_a):
                pass

            def do_GET(self):
                if not (http.handle_review_request(self, output) or serve_vendor_request(self)
                        or inject.maybe_serve_injected_html(self, output)):
                    super().do_GET()

            def do_POST(self):
                if not http.handle_review_request(self, output):
                    self.send_error(404)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stack.callback(thread.join, 5)
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'

        def request(path, body=None):
            data = None if body is None else json.dumps(body).encode()
            req = urllib.request.Request(base + '/_fw/review/' + path, data=data,
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=12) as response:
                return json.load(response)

        with sync_playwright() as pw, ThreadPoolExecutor(max_workers=1) as executor, ExitStack() as browsers:
            browser = pw.chromium.launch()
            browsers.callback(browser.close)
            browser_version = browser.version
            for surface, slug in (('report', 'timing-report'), ('analysis', 'timing-analysis')):
                if surface == 'report':
                    request('start', {})
                    url = base + '/' + slug + '/'
                else:
                    started = request('analysis/start', {'slug': slug})
                    url = base + '/_fw/review/workspace?fw_session=' + str(started['session'])
                page = browser.new_page(viewport={'width': 1360, 'height': 960})
                page.set_default_timeout(10000)
                errors = []
                status_requests = []
                def observe_status(request):
                    path = request.url.split('?', 1)[0]
                    if path.endswith('/_fw/review/status') or path.endswith('/_fw/review/workspace/state'):
                        status_requests.append(time.perf_counter())
                page.on('request', observe_status)
                page.on('pageerror', lambda error: errors.append(str(error)))
                # All fixture resources are local; never contact providers or CDNs.
                # Do not route local requests through Python: a synchronous
                # agent-future wait would otherwise stall browser status reads.
                page.route(re.compile(r'^https?://(?!' + re.escape(base.split('://')[1] + '/') + ')'),
                           lambda route: route.abort())
                page.goto(url, wait_until='load')
                page.locator('#fwrvChat' if surface == 'report' else '#queue-toggle').click()
                cadence = page.evaluate("""() => {
                    const p = window._fwReviewActivity.pollInterval;
                    const now = 1000;
                    return {
                        recentQueued: p([{state:'queued',submitted_at:999}], 2000, now),
                        expiredQueued: p([{state:'queued',submitted_at:909}], 2000, now),
                        active: p([{state:'working',display_state:'working'}], 2500, now),
                        stale: p([{state:'working',display_state:'unknown'}], 2500, now),
                        waiting: p([{state:'waiting'}], 2000, now),
                        completed: p([{state:'completed'}], 2500, now)
                    };
                }""")
                if cadence != {'recentQueued': 500, 'expiredQueued': 2000, 'active': 500,
                               'stale': 2500, 'waiting': 2000, 'completed': 2500}:
                    raise AssertionError(f'{surface} cadence state bounds incorrect: {cadence}')
                expected_idle = 2000 if surface == 'report' else 2500
                deadline = time.perf_counter() + 5
                idle_gap_ms = None
                while idle_gap_ms is None and time.perf_counter() < deadline:
                    idle_gaps = [(status_requests[i] - status_requests[i-1]) * 1000
                                 for i in range(1, len(status_requests))]
                    idle_gap_ms = next((gap for gap in idle_gaps
                                        if expected_idle - 150 <= gap <= expected_idle + 700), None)
                    if idle_gap_ms is None:
                        page.wait_for_timeout(25)
                if idle_gap_ms is None:
                    raise AssertionError(f'{surface} idle polling gaps out of bounds: {idle_gaps}')
                before_hidden = len(status_requests)
                page.evaluate("window.dispatchEvent(new Event('pagehide'))")
                page.wait_for_timeout(2700)
                hidden_suppressed = len(status_requests) == before_hidden
                if not hidden_suppressed:
                    raise AssertionError(f'{surface} polled after pagehide')
                resumed_at = time.perf_counter()
                page.evaluate("window.dispatchEvent(new Event('pageshow'))")
                deadline = time.perf_counter() + 1
                while len(status_requests) == before_hidden and time.perf_counter() < deadline:
                    page.wait_for_timeout(20)
                resume_ms = (status_requests[-1] - resumed_at) * 1000 if len(status_requests) > before_hidden else None
                if resume_ms is None or resume_ms > 800:
                    raise AssertionError(f'{surface} did not resume status polling promptly: {resume_ms}')
                polling_checks.append({'surface': surface, 'idle_gap_ms': round(idle_gap_ms, 3),
                                       'hidden_suppressed': hidden_suppressed,
                                       'resume_ms': round(resume_ms, 3)})
                for mode in ('waiting', 'busy'):
                    for index in range(args.samples):
                        marks = {}
                        ack = threading.Event()

                        def agent():
                            if mode == 'busy':
                                if not ack.wait(10):
                                    raise TimeoutError('feedback acknowledgement missing')
                                time.sleep(args.busy_ms / 1000)
                            marks['poll_start'] = time.perf_counter()
                            result = request(f'poll?timeout=8&slug={slug}')
                            marks['pickup'] = time.perf_counter()
                            assert result['status'] == 'feedback', result
                            batch, = result['batches']
                            marks['identity'] = {k: batch[k] for k in ('session', 'slug', 'feedback_id')}
                            body = dict(marks['identity'], state='working', summary='Synthetic agent activity')
                            marks['activity_send'] = time.perf_counter()
                            request('activity', body)
                            marks['activity_ack'] = time.perf_counter()

                        future = executor.submit(agent)
                        if mode == 'waiting':
                            deadline = time.perf_counter() + 5
                            while request('status?slug=' + slug)['agent']['state'] != 'listening':
                                if time.perf_counter() > deadline:
                                    raise TimeoutError('agent did not enter long poll')
                                time.sleep(.01)
                        note = page.locator('#fwrvNote' if surface == 'report' else '#chat-note')
                        note.fill(f'Synthetic {surface} {mode} sample {index}')
                        endpoint = '/_fw/review/' + ('feedback' if surface == 'report' else 'workspace/feedback')
                        marks['send'] = time.perf_counter()
                        with page.expect_response(lambda r: r.url.split('?')[0] == base + endpoint
                                                  and r.request.method == 'POST') as received:
                            page.locator('#fwrvSend' if surface == 'report' else '#send').click()
                        response = received.value
                        assert response.ok, response.text()
                        acknowledged = response.json()
                        marks['ack'] = time.perf_counter()
                        ack.set()
                        future.result(timeout=12)
                        identity = marks['identity']
                        assert acknowledged['feedback_id'] == identity['feedback_id']
                        wait_for_chat_state(page, identity['feedback_id'], 'activity', 'Synthetic agent activity')
                        marks['activity_visible'] = time.perf_counter()
                        next_status = next((stamp for stamp in status_requests
                                            if stamp >= marks['activity_ack']), None)
                        fast_status_ms = None if next_status is None else (next_status - marks['activity_ack']) * 1000
                        if fast_status_ms is None or fast_status_ms > 800:
                            raise AssertionError(f'{surface} active status cadence exceeded 800 ms: {fast_status_ms}')
                        reply = f'Synthetic completion {surface} {mode} {index}'
                        marks['reply_send'] = time.perf_counter()
                        request('reply', dict(identity, text=reply))
                        marks['reply_ack'] = time.perf_counter()
                        wait_for_chat_state(page, identity['feedback_id'], 'completion', reply)
                        marks['completion_visible'] = time.perf_counter()
                        pairs = {'send_to_ack': ('send', 'ack'), 'ack_to_pickup': ('ack', 'pickup'),
                                 'send_to_pickup': ('send', 'pickup'),
                                 'pickup_to_activity_submission': ('pickup', 'activity_send'),
                                 'activity_submission_to_visible': ('activity_send', 'activity_visible'),
                                 'activity_ack_to_visible': ('activity_ack', 'activity_visible'),
                                 'reply_to_visible_completion': ('reply_send', 'completion_visible'),
                                 'reply_ack_to_visible_completion': ('reply_ack', 'completion_visible')}
                        samples.append({'surface': surface, 'mode': mode, 'index': index, **identity,
                                        'ms': {**{k: round((marks[b]-marks[a])*1000, 3)
                                                   for k, (a,b) in pairs.items()},
                                               'activity_ack_to_next_status': round(fast_status_ms, 3)},
                                        'milestones_monotonic_s': {k: v for k,v in marks.items() if k != 'identity'}})
                        print(f'{surface}/{mode}/{index}: complete', flush=True)
                assert not errors, errors
                page.close()
            if args.rebuild:
                for slug in ('timing-report', 'timing-analysis'):
                    start = time.perf_counter()
                    run_report(str(root / 'reports' / slug))
                    builds.append({'slug': slug, 'phase': 'simple_rebuild', 'ms': (time.perf_counter()-start)*1000})
        return {'schema_version': 1, 'provider': 'deterministic synthetic agent; no model/provider calls',
                'chromium': browser_version, 'python': sys.version,
                'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'clock': 'time.perf_counter; real Chromium timers; no page.clock',
                'busy_delay_ms': args.busy_ms, 'samples_per_surface_per_mode': args.samples,
                'samples': samples, 'summary': summarize(samples), 'builds_separate': builds,
                'polling_checks': polling_checks,
                'limitations': ['No external editor, provider, model, reasoning or tool-dispatch timing.',
                    'ack_to_pickup is signed: a parked agent may receive feedback before the browser sees acknowledgement.',
                    'Visibility measurements include Playwright observation overhead; activity is held visible before reply.',
                    'Busy mode delays pickup after acknowledgement; it simulates an unavailable agent, not model latency.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, choices=range(1, 11), default=3)
    parser.add_argument('--busy-ms', type=int, choices=range(0, 5001), default=600)
    parser.add_argument('--deadline', type=int, choices=range(10, 601), default=180)
    parser.add_argument('--output', type=Path, default=Path('/tmp/review-response-baseline.json'))
    parser.add_argument('--rebuild', action='store_true')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.worker:
        command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], '--worker']
        process = subprocess.Popen(command, cwd=ROOT, start_new_session=True)
        try:
            return process.wait(timeout=args.deadline)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            print('Timing run exceeded its deadline; no successful result recorded.', file=sys.stderr)
            return 2
    result = run(args)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    for group, metrics in result['summary'].items():
        print(group + ' (median/max ms): ' + '; '.join(
            f"{key}={value['median_ms']:.3f}/{value['max_ms']:.3f}"
            for key, value in metrics.items()))
    print(f'JSON: {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
