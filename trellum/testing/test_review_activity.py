"""Feedback activity observations, scope boundaries and conversation updates."""
from __future__ import annotations

import pytest

from trellum import cli
from trellum.cli.commands import review as review_cmd
from trellum.review import state as state_module
from trellum.review.state import ReviewState
from trellum.testing.test_analysis_workspace import (
    start_workspace,
    workspace_headers,
    wsbody,
)
from trellum.testing.test_analysis_workspace import (  # noqa: F401
    workspace_project as workspace_project,
)
from trellum.testing.test_analysis_workspace import (
    workspace_server as workspace_server,
)
from trellum.testing.test_review import _http
from trellum.testing.test_review import review_server as review_server
from trellum.testing.test_workspace_journey import (
    workspace_browser_project as workspace_browser_project,
)


def batch(slug="alpha", note="Please check this"):
    return {"slug": slug, "note": note, "items": []}


def test_activity_transitions_do_not_complete_or_consume_followups():
    st = ReviewState()
    session = st.start()
    st.submit(batch())
    first = st.status("alpha")["activities"][0]
    assert first["state"] == "queued"
    _, picked = st.poll(0, "alpha")
    feedback = picked[0]["feedback_id"]
    assert st.status("alpha")["activities"][0]["state"] == "picked_up"
    st.submit(batch(note="And check this too"))
    st.activity(session, "alpha", feedback, "working", "Checking the date filter")
    now = st.activities[feedback]["updated_at"]
    st.activity(session, "alpha", feedback, "working", "Checking the date filter")
    assert st.activities[feedback]["updated_at"] == now
    assert st.status("alpha")["queued"] == 1
    assert st.status()["replies"] == []
    st.activity(session, "alpha", feedback, "rebuilding", "Building the report")
    st.reply("Updated and rebuilt", "alpha", feedback)
    rows = st.status("alpha")["activities"]
    assert [row["state"] for row in rows] == ["completed", "queued"]
    assert st.status()["replies"][0]["feedback_ids"] == [feedback]
    assert st.status()["queued"] == 1


def test_scope_staleness_restart_and_heartbeat_are_independent(tmp_path):
    st = ReviewState()
    st.bind_dir(str(tmp_path))
    session = st.start()
    st.submit(batch())
    st.submit(batch("beta"))
    _, picked = st.poll(0, "alpha")
    feedback = picked[0]["feedback_id"]
    st.activity(session, "alpha", feedback, "working", "Checking the chart")
    meaningful = st.activities[feedback]["updated_at"]
    assert st.status("beta")["agent"]["state"] == "absent"
    assert len(st.status("beta")["activities"]) == 1
    st.agent_scopes["alpha"]["seen"] -= 91
    assert st.status("alpha")["agent"]["state"] == "absent"
    assert st.status("alpha")["activities"][0]["display_state"] == "unknown"
    st.poll(0, "idle-report")
    assert st.status()["agent"]["state"] == "absent"
    st.poll(0, "alpha")
    assert st.status("alpha")["activities"][0]["display_state"] == "working"
    assert st.activities[feedback]["updated_at"] == meaningful
    restarted = ReviewState()
    restarted.bind_dir(str(tmp_path))
    assert restarted.status("alpha")["activities"][0]["display_state"] == "unknown"
    with pytest.raises(ValueError):
        restarted.activity(session + 1, "alpha", feedback, "working", "Checking")
    with pytest.raises(ValueError):
        restarted.activity(session, "beta", feedback, "working", "Checking")
    restarted.reply("Done", "beta")
    assert restarted.activities[feedback]["state"] == "working"


def test_activity_bounds_and_submission_identity_survive_reconnect(tmp_path, monkeypatch):
    monkeypatch.setattr(state_module, "_MAX_ACTIVITY", 2)
    st = ReviewState()
    st.bind_dir(str(tmp_path))
    session = st.start()
    first = batch()
    assert st.submit_once(first, "feedback-request-001") == (1, False)
    st.poll(0, "alpha")
    feedback = first["feedback_id"]
    st.submit(batch())
    with pytest.raises(ValueError, match="limit"):
        st.submit(batch())
    st.reply("Done", "alpha", feedback)
    st.submit(batch())
    assert len(st.activities) == 2 and feedback not in st.activities
    second = ReviewState()
    second.bind_dir(str(tmp_path))
    assert second.submit_once(batch(), "feedback-request-001") == (2, True)
    with pytest.raises(FileExistsError):
        second.submit_once(batch(note="Different"), "feedback-request-001")
    assert len(second.queue) == 2
    assert second.session == session


@pytest.mark.parametrize("state", ["waiting", "failed"])
def test_waiting_failure_and_end_are_explicit(state):
    st = ReviewState()
    session = st.start()
    st.submit(batch())
    _, picked = st.poll(0)
    feedback = picked[0]["feedback_id"]
    st.activity(session, "alpha", feedback, state, "Need a date range" if state == "waiting" else "Build failed")
    assert st.status()["activities"][0]["display_state"] == state
    st.end()
    assert st.status()["activities"][0]["display_state"] == ("failed" if state == "failed" else "ended")


def test_workspace_protocol_activity_and_cli_are_scoped(workspace_server, monkeypatch, capsys):
    base, request, st = workspace_server
    session = start_workspace(request)
    headers = workspace_headers(base, session)
    payload = wsbody(session, request_id="feedback-request-000001", note="Check this", items=[])
    code, accepted = request("/_fw/review/workspace/feedback", payload, headers=headers)
    assert code == 200
    feedback = accepted["feedback_id"]
    _, duplicate = request("/_fw/review/workspace/feedback", payload, headers=headers)
    assert duplicate["feedback_id"] == feedback and duplicate["duplicate"]
    activity = {"session": session["session"], "slug": "analysis-one", "feedback_id": feedback,
                "state": "working", "summary": "Checking the evidence"}
    assert request("/_fw/review/activity", activity)[0] == 409  # not picked up yet
    st.poll(0, "analysis-one")
    assert request("/_fw/review/activity", dict(activity, session=session["session"]+1))[0] == 409
    assert request("/_fw/review/activity", dict(activity, slug="source-one"))[0] == 409
    assert request("/_fw/review/activity", dict(activity, summary="x"*501))[0] == 409
    assert request("/_fw/review/activity", dict(activity, summary="x"*10000))[0] == 413
    assert request("/_fw/review/activity", activity, headers={"Origin": "https://foreign.invalid"})[0] == 403
    assert request("/_fw/review/activity", activity)[0] == 200
    monkeypatch.setattr(review_cmd, "_find_our_server", lambda *a, **k: int(base.rsplit(":", 1)[1]))
    assert cli.main(["review", "activity", "Building the analysis", "--state", "rebuilding",
                     "--report", "analysis-one", "--session", str(session["session"]),
                     "--feedback", feedback]) == 0
    assert "unfinished" in capsys.readouterr().out
    st.submit(batch("source-one"))
    _, status = request("/_fw/review/workspace/state?session="+str(session["session"])+"&analysis_slug=analysis-one", headers=headers)
    assert [a["feedback_id"] for a in status["activities"]] == [feedback]
    assert status["activities"][0]["state"] == "rebuilding"
    assert status["replies"] == []
    assert request("/_fw/review/reply", {"slug": "source-one", "feedback_id": feedback, "text": "Wrong scope"})[0] == 409
    assert request("/_fw/review/reply", {"slug": "analysis-one", "feedback_id": feedback, "text": "Built"})[0] == 200
    st.end()
    assert len(st.status("analysis-one")["activities"]) == 1


def _wait_activity(page, feedback, state):
    page.wait_for_function("([id,state]) => {const row=document.querySelector('[data-feedback-id=\"'+id+'\"]');return row && row.dataset.activityState===state;}", arg=[feedback,state])


def test_standalone_conversation_pickup_followup_stale_completion_and_reconnect(review_server):
    from playwright.sync_api import sync_playwright

    from trellum.review.state import STATE

    STATE.reset_for_tests()
    base = review_server["base"]
    _http("POST", base+"/_fw/review/start", {})
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width":1366,"height":900}, reduced_motion="reduce")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base+"/alpha/index.html")
            page.click("#fwrvChat")
            page.fill("#fwrvNote", "Check the date filter")
            page.click("#fwrvSend")
            page.locator('[data-activity-state="queued"]').wait_for()
            first = STATE.status("alpha")["activities"][0]["feedback_id"]
            _wait_activity(page, first, "queued")
            STATE.poll(0, "alpha")
            _wait_activity(page, first, "picked_up")
            indicator = page.locator('[data-activity-state="picked_up"] .fw-review-activity-state')
            page.emulate_media(reduced_motion="no-preference")
            assert indicator.evaluate("el => getComputedStyle(el, '::before').animationName") == "fw-review-working-spin"
            page.emulate_media(reduced_motion="reduce")
            assert indicator.evaluate("el => getComputedStyle(el, '::before').animationName") == "none"
            page.emulate_media(reduced_motion="no-preference")
            STATE.activity(STATE.session, "alpha", first, "working", "Checking the selected date filter")
            page.get_by_text("Checking the selected date filter", exact=True).wait_for()
            page.fill("#fwrvNote", "Also check the totals")
            page.click("#fwrvSend")
            page.locator('[data-activity-state="queued"]').wait_for()
            followup = STATE.status("alpha")["activities"][1]["feedback_id"]
            page.fill("#fwrvNote", "Keep this unfinished draft")
            page.locator("#fwrvNote").focus()
            page.evaluate("window.activityRow=document.querySelector('[data-feedback-id]')")
            STATE.agent_scopes["alpha"]["seen"] -= 91
            _wait_activity(page, first, "unknown")
            assert page.locator("#fwrvNote").input_value() == "Keep this unfinished draft"
            assert page.locator("#fwrvNote").evaluate("el=>el===document.activeElement")
            assert page.evaluate("window.activityRow===document.querySelector('[data-feedback-id]')")
            STATE.reply("Date filter checked", "alpha", first)
            _wait_activity(page, first, "completed")
            _wait_activity(page, followup, "queued")
            assert page.locator(".fw-review-activity-time").first.inner_text().startswith("Elapsed ")
            page.reload()
            page.locator('[data-activity-state="completed"]').wait_for()
            assert page.locator(".fw-review-request").count() == 2
            assert page.locator("#fwrvNote").input_value() == "Keep this unfinished draft"
            assert page.locator("#fwrvNote").evaluate("el=>el===document.activeElement")
            assert len(STATE.queue) == 1
            assert errors == []
        finally:
            browser.close()
            STATE.reset_for_tests()


def test_workspace_activity_preserves_drafts_focus_and_reader_position(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    base, st, _ = workspace_browser_project
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width":1366,"height":900})
            page.goto(base+"/_fw/review/workspace")
            page.click("#queue-toggle")
            page.fill("#chat-note", "Check the evidence")
            page.click("#send")
            page.locator('[data-activity-state="queued"]').wait_for()
            _, picked = st.poll(0, "decision")
            first = picked[0]["feedback_id"]
            _wait_activity(page, first, "picked_up")
            page.fill("#chat-note", "A follow-up")
            page.click("#send")
            page.locator('[data-activity-state="queued"]').wait_for()
            page.fill("#chat-note", "Retain this draft")
            page.locator("#chat-note").focus()
            # Long replies put the shared conversation scroller away from its end.
            for _ in range(8):
                st.reply("Long reply " + "evidence "*80, "decision", first)
            page.wait_for_function("document.querySelectorAll('.fw-review-message').length===8")
            page.evaluate("document.querySelector('.fw-queue-scroll').scrollTop=70")
            before = page.locator(".fw-queue-scroll").evaluate("el=>el.scrollTop")
            _, picked = st.poll(0, "decision")
            followup = picked[0]["feedback_id"]
            st.activity(st.session, "decision", followup, "working", "Checking the follow-up")
            page.get_by_text("Checking the follow-up", exact=True).wait_for()
            assert page.locator("#chat-note").input_value() == "Retain this draft"
            assert page.locator("#chat-note").evaluate("el=>el===document.activeElement")
            assert abs(page.locator(".fw-queue-scroll").evaluate("el=>el.scrollTop")-before) < 2
            st.agent_scopes["decision"]["seen"] -= 91
            _wait_activity(page, followup, "unknown")
            inactive = page.locator('[data-activity-state="unknown"] .fw-review-activity-state')
            assert inactive.evaluate("el => getComputedStyle(el, '::before').animationName") == "none"
            st.reply("Follow-up checked", "decision", followup)
            _wait_activity(page, followup, "completed")
            assert page.locator(".fw-review-request").count() == 2
        finally:
            browser.close()


def test_unconfirmed_standalone_send_reconciles_without_resubmission(review_server):
    from playwright.sync_api import sync_playwright

    from trellum.review.state import STATE

    STATE.reset_for_tests()
    base = review_server["base"]
    _http("POST", base+"/_fw/review/start", {})
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            writes = []
            def lose_response(route):
                writes.append(route.request.post_data_json)
                route.fetch()  # The write commits, but its acknowledgement is lost.
                route.abort()
            page.route("**/_fw/review/feedback", lose_response)
            page.goto(base+"/alpha/index.html")
            page.click("#fwrvChat")
            page.fill("#fwrvNote", "Accepted despite the lost response")
            page.click("#fwrvSend")
            page.locator('[data-activity-state="queued"]').wait_for()
            page.wait_for_function("document.getElementById('fwrvNote').value === ''")
            assert len(writes) == 1
            assert len(STATE.queue) == 1
            page.reload()
            page.locator('[data-activity-state="queued"]').wait_for()
            assert page.locator(".fw-review-request").count() == 1
            assert len(writes) == 1 and len(STATE.queue) == 1
        finally:
            browser.close()
            STATE.reset_for_tests()


def test_workspace_send_invalidates_held_state_and_serializes_refresh(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    base, st, _ = workspace_browser_project
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 900})
            page.goto(base + "/_fw/review/workspace")
            page.wait_for_function("document.getElementById('fw-workspace-analysis').src.includes('/decision/')")
            # Hold actual response bodies on both sides of the send. The second
            # hold exposes any transient removal by the obsolete observation.
            page.evaluate("""() => {
                const original = window.fetch;
                const reads = window.heldState = {release: [], inflight: 0, max: 0};
                window.fetch = function(url, options) {
                    if (!String(url).includes('/workspace/state')) return original(url, options);
                    reads.inflight++; reads.max = Math.max(reads.max, reads.inflight);
                    return original(url, options).then(async response => {
                        const body = await response.text();
                        return new Promise(resolve => reads.release.push(() => {
                            reads.inflight--;
                            resolve(new Response(body, {status: response.status}));
                        }));
                    });
                };
            }""")
            page.wait_for_function("window.heldState.release.length === 1")
            page.click("#queue-toggle")
            page.fill("#chat-note", "Keep this acknowledged request visible")
            page.click("#send")
            page.wait_for_function("document.getElementById('chat-note').value === ''")
            feedback = st.queue[0]["feedback_id"]
            _wait_activity(page, feedback, "queued")
            assert page.evaluate("window.heldState.release.length") == 1
            page.evaluate("window.heldState.release[0]()")
            page.wait_for_function("window.heldState.release.length === 2")
            _wait_activity(page, feedback, "queued")
            assert page.evaluate("window.heldState.max") == 1
            page.evaluate("window.heldState.release[1]()")
            st.poll(0, "decision")
            page.wait_for_function("window.heldState.release.length === 3")
            # A hidden page invalidates the held observation and resumes with
            # one fresh read when visible, without applying the stale pickup.
            page.evaluate("""() => {
                Object.defineProperty(document, 'hidden', {configurable: true, value: true});
                document.dispatchEvent(new Event('visibilitychange'));
                window.heldState.release[2]();
            }""")
            _wait_activity(page, feedback, "queued")
            page.evaluate("""() => {
                Object.defineProperty(document, 'hidden', {configurable: true, value: false});
                document.dispatchEvent(new Event('visibilitychange'));
            }""")
            page.wait_for_function("window.heldState.release.length === 4")
            page.evaluate("window.heldState.release[3]()")
            _wait_activity(page, feedback, "picked_up")
            assert page.evaluate("window.heldState.max") == 1
        finally:
            browser.close()
