"""Real report/source boundaries and outward history, using synthetic providers."""
import copy
import json

import pytest

from apps.assistant import answer_review as review
from apps.assistant import llm, source_queries
from apps.assistant.models import AssistantSession
from apps.assistant.tests.test_answer_review import (
    rendered,
    run,
    submission,
    submit_response,
)
from apps.assistant.tests.test_message_sse import frames
from apps.assistant.tests.test_metric_tools import args as metric_args
from apps.assistant.tests.test_metric_tools import canonical  # noqa: F401
from apps.assistant.tests.test_provider_runtime import response, tool
from apps.assistant.tests.test_source_access import source_context  # noqa: F401
from apps.assistant.tests.test_source_queries import executor  # noqa: F401
from apps.assistant.tests.test_tools import built  # noqa: F401
from apps.assistant.tools import AssistantToolbox

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("kind", ["canonical", "published"])
def test_real_report_executor_reconciles_before_answer(kind, canonical, built, studio_tree, viewer, monkeypatch):  # noqa: F811
    session = AssistantSession.objects.create(user=viewer, org=studio_tree.org, studio=studio_tree)
    box = AssistantToolbox(studio_tree, actor=viewer, session=session)
    # These fixtures share an output directory; restore the canonical artifact
    # when that executor is selected.
    if kind == "canonical":
        out, payload, meta = canonical
        (out / "data.json").write_text(json.dumps(payload))
        (out / "_meta.json").write_text(json.dumps(meta))
        name, args, expected, column = "query_metric", metric_args(), 100, "value"
    else:
        name, args, expected, column = "query_report_data", {
            "slug": "player-overview", "dataset_id": "revenue_daily", "metrics": [{"column": "revenue"}]}, 350, "revenue"
    def final(state):
        entry = state["transcript"][-1]
        if not entry.get("provenance"):
            return submit_response({"mode": "limitation"})
        args = submission(entry)
        args["claims"][0].update(expected=expected, column=column, scope="complete_total")
        return submit_response(args)
    events, state, _, _ = run(monkeypatch, [[response([tool(args, name=name, id="real-read")])], final],
                              state=session.state, box=box)
    assert f'{column}: {expected}' in rendered(events), state["transcript"]
    assert state["_answer_review"]["status"] == "accepted"
    assert state["_metric_reports"] == ["player-overview"]
    assert ("Canonical executor calculation" in rendered(events)) == (kind == "canonical")


def test_report_all_null_sum_is_missing_and_empty_filter_has_no_cells(built, studio_tree, viewer):  # noqa: F811
    path = built / "data.json"
    payload = json.loads(path.read_text())
    for row in payload["_ds_revenue_daily"]["_data"]:
        row[-1] = None
    path.write_text(json.dumps(payload))
    box = AssistantToolbox(studio_tree, actor=viewer)
    args = {"slug": "player-overview", "dataset_id": "revenue_daily", "metrics": [{"column": "revenue"}]}
    assert not box.execute("query_report_data", args).startswith("Error")
    assert box.provenance["rows"] == [[None]]
    assert not box.execute("query_report_data", {**args, "filters": {"platform": ["missing"]}}).startswith("Error")
    assert box.provenance["rows"] == []


def test_nonfinite_report_result_cannot_poison_persisted_evidence(built, studio_tree, viewer):  # noqa: F811
    path = built / "data.json"
    payload = json.loads(path.read_text())
    payload["_ds_revenue_daily"]["_data"][0][-1] = float("inf")
    path.write_text(json.dumps(payload))
    box = AssistantToolbox(studio_tree, actor=viewer)
    result = box.execute("query_report_data", {
        "slug": "player-overview", "dataset_id": "revenue_daily",
        "metrics": [{"column": "revenue"}],
    })
    assert result.startswith("Error:")
    assert box.provenance is None


def test_empty_report_evidence_still_obeys_byte_bound(built, studio_tree, viewer):  # noqa: F811
    path = built / "data.json"
    payload = json.loads(path.read_text())
    payload["_ds_revenue_daily"]["_cols"][-1] = "x" * 65536
    path.write_text(json.dumps(payload))
    box = AssistantToolbox(studio_tree, actor=viewer)
    result = box.execute("query_report_data", {
        "slug": "player-overview", "dataset_id": "revenue_daily",
        "filters": {"platform": ["missing"]},
    })
    assert "evidence exceeds" in result
    assert box.provenance is None


def test_source_pause_resume_reconnect_and_history_exclude_drafts(source_context, executor, login, monkeypatch):  # noqa: F811
    studio, user, session, grant = source_context
    draft = "__REJECTED_SOURCE_DRAFT__ 654321"
    calls = []
    phase = "pending"
    def adapter(config, messages, system, schemas, **kwargs):
        calls.append(copy.deepcopy(messages))
        if phase == "pending":
            if len(calls) <= 2:
                yield {"type": "text_delta", "text": draft}
                yield response([{"type": "text", "text": draft}])
            else:
                yield {"type": "text_delta", "text": draft}
                yield response([{"type": "text", "text": draft},
                                tool({"source_id": grant.pk, "sql": "SELECT count(*) FROM analytics.sales"},
                                     name="query_source", id="durable-source"),
                                tool({"mode": "analysis", "rejected_draft": draft}, name=review.NAME, id="deferred-submit")])
        elif phase == "resume":
            source = next(e for e in reversed(messages) if e.get("name") == "query_source")
            args = submission(source)
            args["claims"][0]["column"] = "total"
            yield from submit_response(args)
        else:
            yield from submit_response({"mode": "clarification", "clarify": ["period"], "text": "Which reporting period should I use?"})
    monkeypatch.setattr(llm, "_adapter", lambda provider: adapter)
    base = f"/s/{studio.org.slug}/{studio.slug}/api/assistant/sessions/{session.pk}"
    client = login(user)
    pending_events = frames(client.post(base + "/message", data=json.dumps({"message": "Count sales"}), content_type="application/json"))
    assert not any(name == "text_delta" for name, _ in pending_events)
    pending = next(data for name, data in pending_events if name == "source_wait")
    session.refresh_from_db()
    question = session.state["_answer_review"]["question"]
    assert session.state["_answer_review"]["attempts"] == 2
    assert draft not in json.dumps(session.state["transcript"]) + json.dumps(pending_events)
    assert not session.state.get("_continuation_error")
    detail = client.get(base).json()
    assert detail["source_pending"]["id"] == pending["id"]
    assert draft not in json.dumps(detail)
    assert review.NAME not in json.dumps(detail["transcript"])
    assert source_queries.process_next(worker_id="answer-review-synthetic")
    phase = "resume"
    events = frames(client.post(base + "/message", data=json.dumps({"resume_source_query": pending["id"]}), content_type="application/json"))
    text = "".join(data["text"] for name, data in events if name == "text_delta")
    assert 'total: 42' in text
    assert any(name == "source_evidence" for name, _ in events)
    session.refresh_from_db()
    assert session.state["_answer_review"]["question"] == question
    assert session.state["_answer_review"]["attempts"] == 2
    assert session.state["_answer_review"]["status"] == "accepted"
    detail = client.get(base).json()
    assert draft not in json.dumps(detail)
    assert detail["transcript"][-1]["content"][0]["text"] == text
    phase = "new"
    frames(client.post(base + "/message", data=json.dumps({"message": "What about later?"}), content_type="application/json"))
    session.refresh_from_db()
    assert session.state["_answer_review"]["question"] != question
    assert session.state["_answer_review"]["attempts"] == 0
    grant.users.clear()
    assert client.get(base).status_code == 403


def test_private_submission_protocol_is_excluded_from_session_and_personal_export(login, org_admin, viewer, org, studio_tree):
    from apps.orgs import personal_data
    session = AssistantSession.objects.create(user=viewer, org=org, studio=studio_tree,
        state={"transcript": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "review", "name": review.NAME, "input": {}}], "_internal": True},
            {"role": "tool", "tool_use_id": "review", "name": review.NAME, "input": {}, "result": "Internal correction", "_internal": True},
            {"role": "user", "content": "Internal correction", "_internal": True},
            {"role": "assistant", "content": [{"type": "text", "text": review.MESSAGES["greeting"]}]},
        ], "_answer_review": {"question": "private", "checks": {"note": "private assessment"}},
        "_provider": {"messages": {"1": {"reasoning_content": "private draft"}}}})
    detail = login(viewer).get(f"/s/{org.slug}/{studio_tree.slug}/api/assistant/sessions/{session.pk}").json()
    exported = personal_data.export(viewer, org, actor=org_admin)["activity"]["assistant_conversations"][0]
    for result in (detail, exported):
        serialized = json.dumps(result)
        assert "private draft" not in serialized and "private assessment" not in serialized
        assert "Internal correction" not in serialized and review.NAME not in serialized
        assert len(result["transcript"]) == 2


@pytest.mark.parametrize("boundary", ["usage", "tool_use", "source_wait", "error"])
def test_view_never_flushes_partial_text_at_progress_or_error(boundary, login, viewer, org, studio_tree, assistant_config, monkeypatch):
    session = AssistantSession.objects.create(user=viewer, org=org, studio=studio_tree)
    def stream(*a, **kw):
        yield {"type": "text_delta", "text": "__PARTIAL_DRAFT__"}
        yield {"type": boundary}
    monkeypatch.setattr(llm, "stream_turn", stream)
    events = frames(login(viewer).post(f"/s/{org.slug}/{studio_tree.slug}/api/assistant/sessions/{session.pk}/message",
                                    data=json.dumps({"message": "Hello"}), content_type="application/json"))
    assert "__PARTIAL_DRAFT__" not in json.dumps(events)


def test_report_evidence_preserves_float_precision(built, studio_tree, viewer):  # noqa: F811
    path = built / "data.json"
    payload = json.loads(path.read_text())
    value = 1.2345678901234567
    payload["_ds_revenue_daily"]["_data"] = [["2026-03-01", "ios", value]]
    path.write_text(json.dumps(payload))
    box = AssistantToolbox(studio_tree, actor=viewer)
    box.execute("query_report_data", {"slug": "player-overview", "dataset_id": "revenue_daily"})
    assert box.provenance["rows"][0][-1] == value
