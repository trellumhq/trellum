"""Run the shipped assistant against synthetic source job/status streams."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect
from django.conf import settings
from apps.assistant.views import _widget_bundle

BASE = "http://reports.test/s/demo/casino"
JOB = "11111111-1111-1111-1111-111111111111"
STATUS = f"/s/demo/casino/api/assistant/sessions/7/source-queries/{JOB}"
EVIDENCE = {"kind": "source_query", "query_id": JOB, "source_id": 1, "assurance": "Exploratory query",
            "query": "SELECT total FROM analytics.sales", "executed_at": "2026-11-01T12:00:00Z",
            "columns": ["total", "unsafe"], "rows": [[42, '<img src=x onerror="window.pwned=1">' + "x" * 3000]],
            "truncated": True, "rows_returned": 1}


def stream(*events):
    return "".join("data: " + json.dumps(e) + "\n\n" for e in events)


@pytest.fixture
def source_browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


def mount(browser, *, history=False, malicious=False):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    posts, polls, cancellations = [], [], []
    pending = {"id": JOB, "status_url": STATUS if not malicious else "https://evil.invalid/steal"}
    # Accelerate only the job polling/backoff; leave UI/deadline timers intact.
    page.add_init_script("""const timer = window.setTimeout; window.setTimeout = (fn, ms, ...args) => timer(fn, ms >= 1000 && ms <= 10000 ? 30 : ms, ...args);""")
    if history:
        page.add_init_script("localStorage.setItem('trellum-assistant-sessions', '{}')")
    def respond(route):
        path = route.request.url.removeprefix("http://reports.test")
        if path == "/api/assistant/widget.js":
            route.fulfill(body=_widget_bundle(), content_type="text/javascript; charset=utf-8")
        elif "/available" in path:
            route.fulfill(json={"available": True, "suggestions": []})
        elif path.endswith("/sessions"):
            if route.request.method == "POST":
                route.fulfill(json={"id": 7, "scope": "full", "report": None})
            else:
                route.fulfill(json={"sessions": [{"id": 7, "title": "Source history", "updated_at": 1}]})
        elif path.endswith("/sessions/7"):
            route.fulfill(json={"id": 7, "scope": "full", "report": None, "source_pending": pending if history else None,
                                "transcript": [{"role": "user", "content": "Totals?"},
                                               {"role": "tool", "provenance": EVIDENCE},
                                               {"role": "assistant", "content": "From history"}]})
        elif path.endswith("/message"):
            body = route.request.post_data_json
            posts.append(body)
            if body.get("resume_source_query"):
                route.fulfill(body=stream({"type": "source_evidence", "evidence": EVIDENCE},
                    {"type": "tool_result", "provenance": EVIDENCE},
                    {"type": "text_delta", "text": "There are 42 sales."}, {"type": "done"}, {"type": "turn_end"}), content_type="text/event-stream")
            else:
                route.fulfill(body=stream({"type": "source_wait", **pending}, {"type": "turn_end"}), content_type="text/event-stream")
        elif path == STATUS:
            if route.request.method == "POST":
                cancellations.append(True)
                route.fulfill(json={"id": JOB, "state": "running", "cancel_requested": True}, status=202)
            else:
                polls.append(True)
                route.fulfill(json={"id": JOB, "state": "succeeded" if len(polls) > 1 else "running"}, status=200 if len(polls) > 1 else 202)
        elif route.request.resource_type == "document":
            route.fulfill(body='<!doctype html><html><head><meta charset="utf-8"><style>body{font-family:Arial,sans-serif}</style></head><body><script src="/api/assistant/widget.js"></script></body></html>', content_type="text/html")
        else:
            route.fulfill(status=204)
    page.route("**/*", respond)
    page.goto(BASE + "/reports")
    page.locator("#assistantAsk").click()
    return page, posts, polls, cancellations


def test_pending_success_resumes_once_with_structured_escaped_evidence(source_browser):
    page, posts, polls, _ = mount(source_browser)
    try:
        page.locator("#assistantInput").fill("Totals?")
        page.locator("#assistantSend").click()
        expect(page.locator(".assistant-msg.assistant")).to_contain_text("42 sales")
        assert posts == [{"message": "Totals?", "page": {"path": "/s/demo/casino/reports", "title": ""}}, {"resume_source_query": JOB}]
        assert len(polls) == 2
        assert page.locator(".assistant-msg.user").count() == 1
        assert page.locator(".assistant-source-evidence").count() == 1
        assert page.locator(".assistant-source-evidence img").count() == 0
        assert page.evaluate("window.pwned || 0") == 0
        expect(page.locator(".assistant-source-evidence")).to_contain_text("Truncated result")
        expect(page.locator(".assistant-source-table td").first).to_have_text("42")
        expect(page.locator("#assistantSend")).to_have_text("Send")
        artifacts = Path("/tmp/trellum-source-ui-review"); artifacts.mkdir(exist_ok=True)
        page.screenshot(path=str(artifacts / "source-evidence-desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        page.screenshot(path=str(artifacts / "source-evidence-mobile.png"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        page.close()


def test_invalid_model_status_url_is_never_fetched(source_browser):
    page, posts, polls, _ = mount(source_browser, malicious=True)
    try:
        page.locator("#assistantInput").fill("Totals?")
        page.locator("#assistantSend").click()
        expect(page.locator(".assistant-source-progress")).to_contain_text("invalid status address")
        assert not polls and len(posts) == 1
    finally:
        page.close()


def test_cancel_once_and_keep_actual_terminal_outcome(source_browser):
    page, posts, polls, cancellations = mount(source_browser)
    try:
        # Hold the first status read: cancelling must abort it, then reconcile.
        page.add_init_script("void 0")
        page.evaluate("""window.sourceReads=[]; const original=fetch; window.fetch=function(url, opts) {
          if(String(url).includes('/source-queries/') && (!opts || !opts.method)) {
            return new Promise((resolve,reject)=>{sourceReads.push({resolve,signal:opts.signal}); opts.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')));});
          } return original(url,opts);
        };""")
        page.locator("#assistantInput").fill("Totals?")
        page.locator("#assistantSend").click()
        page.wait_for_function("sourceReads.length===1")
        page.locator("#assistantSend").click()
        page.wait_for_function("sourceReads[0].signal.aborted")
        expect(page.locator(".assistant-source-progress")).to_contain_text("Waiting for the source to confirm")
        page.locator("#assistantSend").click()
        page.wait_for_function("sourceReads.length===2")
        page.evaluate("sourceReads[1].resolve(new Response(JSON.stringify({id:'" + JOB + "',state:'succeeded'})))")
        expect(page.locator(".assistant-msg.assistant")).to_contain_text("42 sales")
        assert len(cancellations) == 1
    finally:
        page.close()


def hold_status(page):
    page.evaluate("""window.sourceReads=[]; const original=fetch; window.fetch=function(url, opts) {
      if(String(url).includes('/source-queries/') && (!opts || !opts.method)) {
        return new Promise((resolve)=>sourceReads.push({resolve,signal:opts.signal}));
      } return original(url,opts);
    };""")


def test_hidden_panel_and_session_switch_abort_and_discard_stale_status(source_browser):
    page, posts, polls, _ = mount(source_browser)
    try:
        hold_status(page)
        page.locator('#assistantInput').fill('Totals?')
        page.locator('#assistantSend').click()
        page.wait_for_function('sourceReads.length===1')
        page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true}); document.dispatchEvent(new Event('visibilitychange'))")
        assert page.evaluate('sourceReads[0].signal.aborted')
        page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:false}); document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_function('sourceReads.length===2')
        page.locator('#assistantClose').click()
        assert page.evaluate('sourceReads[1].signal.aborted')
        page.locator('#assistantAsk').click()
        page.wait_for_function('sourceReads.length===3')
        page.locator('#assistantNew').click()
        assert page.evaluate('sourceReads[2].signal.aborted')
        page.evaluate("sourceReads.forEach(r=>r.resolve(new Response(JSON.stringify({id:'" + JOB + "',state:'succeeded'}))))")
        expect(page.locator('#assistantMessages')).to_contain_text('New conversation started')
        assert not page.locator('.assistant-source-evidence').count()
        assert len(posts) == 1
    finally:
        page.close()


def test_history_pending_resumes_without_a_new_user_bubble(source_browser):
    page, posts, polls, _ = mount(source_browser, history=True)
    try:
        page.locator('#assistantHistory').click()
        page.get_by_role('menuitem').first.click()
        expect(page.locator('.assistant-msg.assistant').last).to_contain_text('42 sales')
        assert posts == [{'resume_source_query': JOB}]
        assert page.locator('.assistant-msg.user').count() == 1
        assert page.locator('.assistant-source-evidence').count() == 1
    finally:
        page.close()


def test_lost_resume_response_reconciles_history_without_replaying_question(source_browser):
    page, posts, polls, _ = mount(source_browser)
    try:
        page.evaluate("""const original=fetch; window.fetch=function(url,opts) {
          if(String(url).endsWith('/message') && JSON.parse(opts.body).resume_source_query) {
            return Promise.reject(new TypeError('network lost'));
          } return original(url,opts);
        };""")
        page.locator('#assistantInput').fill('Totals?')
        page.locator('#assistantSend').click()
        expect(page.locator('.assistant-msg.assistant')).to_contain_text('From history')
        expect(page.locator('#assistantSend')).to_have_text('Send')
        assert len(posts) == 1
        assert page.locator('.assistant-source-evidence').count() == 1
    finally:
        page.close()


def test_resume_busy_retries_quietly_and_followup_read_uses_new_job(source_browser):
    page, posts, polls, _ = mount(source_browser)
    second = '22222222-2222-2222-2222-222222222222'
    resume_posts = []
    def messages(route):
        data = route.request.post_data_json
        if not data.get('resume_source_query'):
            route.fallback()
            return
        resume_posts.append(data)
        if len(resume_posts) == 1:
            route.fulfill(status=409, json={'error': 'Still running'})
        elif data['resume_source_query'] == JOB:
            route.fulfill(content_type='text/event-stream', body=stream(
                {'type': 'source_evidence', 'evidence': EVIDENCE},
                {'type': 'source_wait', 'id': second, 'status_url': STATUS.replace(JOB, second)},
                {'type': 'turn_end'}))
        else:
            route.fulfill(content_type='text/event-stream', body=stream(
                {'type': 'source_evidence', 'evidence': {**EVIDENCE, 'query_id': second}},
                {'type': 'text_delta', 'text': 'Investigation complete.'}, {'type': 'done'}, {'type': 'turn_end'}))
    page.route('**/message', messages)
    page.route('**/sessions/7', lambda route: route.fulfill(json={
        'id': 7, 'scope': 'full', 'source_pending': {'id': JOB, 'status_url': STATUS}, 'transcript': []}))
    page.route('**/source-queries/' + second, lambda route: route.fulfill(json={'id': second, 'state': 'succeeded'}))
    try:
        page.locator('#assistantInput').fill('Totals?')
        page.locator('#assistantSend').click()
        expect(page.locator('.assistant-msg.assistant')).to_contain_text('Investigation complete')
        assert [p['resume_source_query'] for p in resume_posts] == [JOB, JOB, second]
        assert len(posts) == 1
        assert page.locator('.assistant-msg.user').count() == 1
        assert page.locator('.assistant-source-evidence').count() == 2
    finally:
        page.close()


def test_revoked_conversation_stops_reconciliation(source_browser):
    page, posts, polls, _ = mount(source_browser)
    reads = []
    def revoked(route):
        reads.append(True)
        route.fulfill(status=403, json={"error": "Source access changed"})
    page.route('**/sessions/7', revoked)
    page.evaluate("""const original=fetch; window.fetch=function(url,opts) {
      if(String(url).endsWith('/message') && JSON.parse(opts.body).resume_source_query)
        return Promise.reject(new TypeError('network lost'));
      return original(url,opts);
    };""")
    try:
        page.locator('#assistantInput').fill('Totals?')
        page.locator('#assistantSend').click()
        expect(page.locator('.assistant-source-progress')).to_contain_text('no longer accessible')
        expect(page.locator('#assistantSend')).to_have_text('Send')
        page.wait_for_timeout(150)
        assert len(reads) == 1
        assert len(posts) == 1
    finally:
        page.close()


def test_approved_metric_card_uses_tool_values_and_shows_definition_context(source_browser):
    page, posts, polls, _ = mount(source_browser)
    metric = dict(kind="metric_query", evidence_id="metric-evidence-1", metric_id="revenue", rows=[[123.45]],
                  columns=["value"], unit="money", currency="EUR", completeness="complete", generated_at="2026-11-01T12:00:00Z",
                  period={"start": "2026-10-01", "end": "2026-11-01", "bounds": "[start,end)"}, timezone="Europe/Amsterdam",
                  report="sales", dataset_id="daily", build_id="build-123", fingerprint="v1:test", definition_hash="definition-1")
    page.route('**/message', lambda route: route.fulfill(content_type='text/event-stream', body=stream(
        {"type": "tool_result", "provenance": metric}, {"type": "text_delta", "text": "The table shows the calculated amount."},
        {"type": "done"}, {"type": "turn_end"})))
    try:
        page.locator('#assistantInput').fill('Revenue?')
        page.locator('#assistantSend').click()
        card = page.locator('.assistant-source-evidence')
        expect(card).to_contain_text('Approved calculation')
        expect(card).to_contain_text('EUR')
        expect(card.locator('td')).to_have_text('123.45')
        card.locator('summary').click()
        expect(card).to_contain_text('build-123')
        assert card.locator('img').count() == 0
    finally:
        page.close()
