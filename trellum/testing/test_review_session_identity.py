"""Replies and workspace observations retain the identity of the picked-up work."""
from __future__ import annotations

import pytest

from trellum import cli
from trellum.cli.commands import review as review_cmd
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


def _pickup(st, slug="analysis-one"):
    st.submit({"slug": slug, "items": [], "note": "Check this"})
    return st.poll(0, slug)[1][0]["feedback_id"]


@pytest.mark.parametrize("slug", ["", "analysis-one"])
def test_delayed_legacy_reply_cannot_complete_replacement_session(slug):
    st = ReviewState()
    original_session = st.start()
    original_feedback = _pickup(st)
    st.end()
    current_session = st.start()
    current_feedback = _pickup(st)
    st.agent_scopes["analysis-one"]["seen"] -= 91
    with pytest.raises(ValueError, match="session"):
        st.reply("Old work finished", slug, session=original_session)
    with pytest.raises(ValueError, match="feedback"):
        st.reply("Old work finished", "analysis-one", original_feedback)
    st.reply("Legacy text still appears", slug)
    assert st.replies[-1]["feedback_ids"] == []
    assert st.activities[current_feedback]["state"] == "picked_up"
    assert st.status("analysis-one")["activities"][0]["display_state"] == "unknown"
    st.submit({"slug": "analysis-one", "items": [], "note": "Queued follow-up"})
    st.reply("Current work finished", slug, session=current_session)
    assert st.activities[current_feedback]["state"] == "completed"
    assert len(st.queue) == 1


@pytest.mark.parametrize("ending", ["end", "send-end", "workspace-send-end"])
def test_ended_workspace_keeps_analysis_scope_across_restart(workspace_server, ending):
    base, request, st = workspace_server
    session = start_workspace(request)
    headers = workspace_headers(base, session)
    feedback = _pickup(st)
    st.reply("Source only", "source-one")
    st.submit({"slug": "source-one", "note": "Other report", "items": []})
    if ending == "end":
        request("/_fw/review/end", {})
    elif ending == "send-end":
        request("/_fw/review/feedback", {"slug": "source-one", "end_session": True})
    else:
        assert request("/_fw/review/workspace/feedback", wsbody(session,
            request_id="final-workspace-send-01", note="Final request", items=[],
            end_session=True), headers=headers)[0] == 200
    # Final Send & End work remains eligible for real activity and completion.
    st.activity(session["session"], "analysis-one", feedback, "working", "Finishing final work")
    code, status = request("/_fw/review/workspace/state", headers=headers)
    assert code == 200 and status["ended"] and not status["active"]
    assert status["replies"] == []
    assert all(row["slug"] == "analysis-one" for row in status["activities"])
    assert status["queued"] == (1 if ending == "workspace-send-end" else 0)
    assert status["activities"][0]["feedback_id"] == feedback
    restarted = ReviewState()
    restarted.bind_dir(st._dir)
    assert restarted.analysis_slug == session["slug"]
    assert restarted.analysis_token == session["token"]
    st.reply("Final work finished", "analysis-one", session=session["session"])
    assert st.activities[feedback]["state"] == "completed"


def test_authenticated_workspace_read_rejects_replacement_and_missing_scope(workspace_server):
    base, request, st = workspace_server
    first = start_workspace(request)
    first_headers = workspace_headers(base, first)
    path = f"/_fw/review/workspace/state?session={first['session']}&analysis_slug={first['slug']}"
    st.end()
    second = st.start_analysis("analysis-one")
    _pickup(st)
    assert request(path, headers=first_headers)[0] == 403
    # An older client sending only its token must also be rejected.
    assert request("/_fw/review/workspace/state", headers=first_headers)[0] == 403
    headers = workspace_headers(base, second)
    assert request(path, headers=headers)[0] == 403  # new token with old session
    st.start_analysis("analysis-two")  # analysis replacement within the same review session
    assert request("/_fw/review/workspace/state", headers=headers)[0] == 403
    st.end_analysis()
    code, status = request("/_fw/review/workspace/state")
    assert code == 200 and status["activities"] == [] and status["queued"] == 0


def test_http_and_cli_reply_use_pickup_identity_without_current_session_lookup(workspace_server, monkeypatch, capsys):
    base, request, st = workspace_server
    first = start_workspace(request)
    _pickup(st)
    st.end()
    second = st.start_analysis("analysis-one")
    feedback = _pickup(st)
    st.submit({"slug": "analysis-one", "note": "Queued follow-up", "items": []})
    monkeypatch.setattr(review_cmd, "_find_our_server", lambda *_: int(base.rsplit(":", 1)[1]))
    stale = ["--session", str(first["session"]), "--report", "analysis-one"]
    assert cli.main(["review", "reply", "Old reply", *stale]) == 1
    assert cli.main(["review", "poll", "--reply", "Old reply", *stale, "--timeout", ".01"]) == 1
    assert st.activities[feedback]["state"] == "picked_up"
    assert len(st.queue) == 1  # a rejected acknowledgement must not drain new work
    assert cli.main(["review", "reply", "Legacy text", "--report", "analysis-one"]) == 0
    assert st.replies[-1]["feedback_ids"] == []
    assert request("/_fw/review/reply", {"text": "Bad session", "session": True})[0] == 409
    assert cli.main(["review", "reply", "Current reply", "--report", "analysis-one",
                     "--session", str(second["session"]), "--feedback", feedback]) == 0
    assert st.activities[feedback]["state"] == "completed"
    assert cli.main(["review", "poll", "--report", "analysis-one", "--timeout", ".01"]) == 0
    followup = st.last()[0]["feedback_id"]
    # poll --reply also forwards the identity returned by the preceding pickup.
    assert cli.main(["review", "poll", "--reply", "Follow-up done", "--report", "analysis-one",
                     "--session", str(second["session"]), "--feedback", followup,
                     "--timeout", ".01"]) == 2
    assert st.activities[followup]["state"] == "completed"
    output = capsys.readouterr()
    assert "reply not delivered" in output.err
    assert f"--report analysis-one --session {second['session']}" in output.out


def test_generated_feedback_identity_is_safe_as_a_cli_argument(workspace_server, monkeypatch):
    base, request, st = workspace_server
    session = start_workspace(request)
    monkeypatch.setattr("trellum.review.state.secrets.token_urlsafe", lambda _: "-abcdefghijklmnopqrstuvw")
    feedback = _pickup(st)
    assert feedback == "fb_-abcdefghijklmnopqrstuvw"
    monkeypatch.setattr(review_cmd, "_find_our_server", lambda *_: int(base.rsplit(":", 1)[1]))
    identity = ["--report", "analysis-one", "--session", str(session["session"]),
                "--feedback", feedback]
    assert cli.main(["review", "activity", "Checking selected style", *identity]) == 0
    assert st.activities[feedback]["state"] == "working"
    assert cli.main(["review", "reply", "Style updated", *identity]) == 0
    assert st.activities[feedback]["state"] == "completed"
