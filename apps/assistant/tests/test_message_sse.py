"""The turn endpoint: SSE event protocol, tool loop, persistence, spend."""
import json
from decimal import Decimal

import pytest

from apps.assistant import llm
from apps.assistant.models import AssistantSession, LlmUsage

pytestmark = pytest.mark.django_db


# ── Fake Anthropic client ───────────────────────────────────────────────────

class FakeUsage:
    def __init__(self, input_tokens=1000, output_tokens=500):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text

    def model_dump(self):
        return {"type": "text", "text": self.text}


class ToolUseBlock:
    type = "tool_use"

    def __init__(self, id, name, input):  # noqa: A002
        self.id, self.name, self.input = id, name, input

    def model_dump(self):
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


class FakeResponse:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = FakeUsage()


class FakeStream:
    """Stands in for ``client.messages.stream(...)``'s context manager.

    Text arrives in several chunks, as it does from the real API, so the
    suite exercises the multi-frame path rather than a single-frame one that
    would pass even if streaming were reverted.
    """

    CHUNK = 8

    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        for block in self._response.content:
            if getattr(block, "type", None) != "text":
                continue
            text = block.text or ""
            for i in range(0, len(text), self.CHUNK):
                yield text[i : i + self.CHUNK]

    def get_final_message(self):
        return self._response


class FakeMessages:
    def __init__(self, responses, calls):
        self._responses = list(responses)
        self.calls = calls

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return FakeStream(self._responses.pop(0))


class FakeClient:
    def __init__(self, responses, calls):
        self.messages = FakeMessages(responses, calls)


@pytest.fixture
def fake_llm(monkeypatch):
    """Install a canned Anthropic response sequence; returns the call log."""
    calls = []

    def _install(*responses):
        monkeypatch.setattr(
            llm, "_anthropic_client", lambda config: FakeClient(responses, calls)
        )
        return calls

    return _install


@pytest.fixture
def session(viewer, org, studio_tree):
    return AssistantSession.objects.create(user=viewer, org=org, studio=studio_tree)


def post_message(client, prefix, session, text="how was revenue last week?", **extra):
    payload = {"message": text}
    payload.update(extra)
    return client.post(
        f"{prefix}/sessions/{session.pk}/message",
        data=json.dumps(payload),
        content_type="application/json",
    )


def drain(response) -> str:
    """Consume a streaming response (the generator only runs when read)."""
    return b"".join(response.streaming_content).decode("utf-8")


def frames(response) -> list[tuple[str, dict]]:
    """Parse an SSE body into [(event name, data dict), ...]."""
    raw = drain(response)
    out = []
    for chunk in raw.split("\n\n"):
        if not chunk.strip() or chunk.startswith(":"):  # ": keepalive" comments
            continue
        lines = chunk.split("\n")
        name = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        out.append((name, data))
    return out


class TestEventProtocol:
    def test_plain_answer_stream(self, login, viewer, prefix, session, assistant_config, fake_llm):
        fake_llm(FakeResponse([TextBlock("Revenue was $5.00.")]))
        resp = post_message(login(viewer), prefix, session)

        assert resp.status_code == 200
        assert resp["Content-Type"] == "text/event-stream"
        assert resp["Cache-Control"] == "no-cache"
        assert resp["X-Accel-Buffering"] == "no"

        events = frames(resp)
        names = [name for name, _ in events]
        # Text arrives BEFORE usage, because usage is only known once the
        # model has finished and the text is forwarded as it is produced.
        # More than one text_delta is the point: a reply is not held back
        # until it is complete.
        assert names[0] == "turn_start"
        assert names[-3:] == ["usage", "done", "turn_end"]
        assert names.count("text_delta") > 1
        assert set(names) == {"turn_start", "text_delta", "usage", "done", "turn_end"}

        by_type = dict(events)
        assert by_type["turn_start"]["session_id"] == session.pk
        assert by_type["turn_start"]["model"] == "claude-sonnet-4-6"
        streamed = "".join(d["text"] for n, d in events if n == "text_delta")
        assert streamed == "Revenue was $5.00."
        assert by_type["done"]["stop_reason"] == "end_turn"
        assert by_type["turn_end"]["session_id"] == session.pk

    def test_frames_are_well_formed_sse(self, login, viewer, prefix, session, assistant_config, fake_llm):
        fake_llm(FakeResponse([TextBlock("hi")]))
        raw = drain(post_message(login(viewer), prefix, session))
        assert raw.startswith("event: turn_start\ndata: {")
        assert raw.endswith("\n\n")
        for chunk in [c for c in raw.split("\n\n") if c.strip()]:
            lines = chunk.split("\n")
            assert len(lines) == 2
            assert lines[0].startswith("event: ")
            assert lines[1].startswith("data: ")

    def test_tool_loop_emits_tool_events(
        self, login, viewer, prefix, session, assistant_config, fake_llm, report_row
    ):
        fake_llm(
            FakeResponse(
                [ToolUseBlock("tu_1", "list_reports", {})], stop_reason="tool_use"
            ),
            FakeResponse([TextBlock("There is one report: Player Overview.")]),
        )
        events = frames(post_message(login(viewer), prefix, session))
        names = [name for name, _ in events]
        # The first response is a tool call with no prose, so its usage lands
        # before any text; the second streams its answer and reports usage
        # after it.
        assert names[:4] == ["turn_start", "usage", "tool_use", "tool_result"]
        assert names[-3:] == ["usage", "done", "turn_end"]
        assert set(names[4:-3]) == {"text_delta"}
        tool_result = [d for n, d in events if n == "tool_result"][0]
        assert tool_result["is_error"] is False
        assert report_row.slug in tool_result["excerpt"]
        assert f"/s/demo/casino/r/{report_row.slug}/" in tool_result["excerpt"]
        # Every tool_result carries provenance; list_reports has none.
        assert tool_result["provenance"] is None
        session.refresh_from_db()
        tool_entry = [e for e in session.state["transcript"] if e["role"] == "tool"][0]
        assert "provenance" in tool_entry

    def test_query_provenance_rides_the_frame(
        self, login, viewer, prefix, session, assistant_config, fake_llm, report_row, studio_tree
    ):
        out = studio_tree.output_dir / report_row.slug
        out.mkdir(parents=True, exist_ok=True)
        (out / "data.json").write_text(json.dumps({"_ds_d": {
            "_cols": ["date", "n"], "_data": [["2026-03-01", 1]], "_dict": {},
        }}), encoding="utf-8")
        (out / "_meta.json").write_text(json.dumps({
            "slug": report_row.slug, "last_run": "2026-03-02T04:00:00Z", "last_status": "success",
        }), encoding="utf-8")
        fake_llm(
            FakeResponse([ToolUseBlock("tu_1", "query_report_data",
                                       {"slug": report_row.slug, "dataset_id": "d"})],
                         stop_reason="tool_use"),
            FakeResponse([TextBlock("1 row.")]),
        )
        events = frames(post_message(login(viewer), prefix, session))
        prov = [d for n, d in events if n == "tool_result"][0]["provenance"]
        assert prov["report"] == report_row.slug
        assert prov["built_at"] == "2026-03-02T04:00:00Z"
        assert prov["link"] == f"/s/demo/casino/r/{report_row.slug}/"
        assert prov["rows_total"] == prov["rows_after"] == prov["rows_returned"] == 1

    def test_llm_error_becomes_error_event_not_500(
        self, login, viewer, prefix, session, assistant_config, monkeypatch
    ):
        class Boom:
            @property
            def messages(self):
                raise RuntimeError("kaboom")

        monkeypatch.setattr(llm, "_anthropic_client", lambda config: Boom())
        events = frames(post_message(login(viewer), prefix, session))
        assert [n for n, _ in events] == ["turn_start", "error", "turn_end"]
        err = dict(events)["error"]
        # The exception is logged server-side; the stream gets a user-safe line.
        assert err["code"] == "provider"
        assert "kaboom" not in err["message"]
        assert "RuntimeError" not in err["message"]

    def test_org_admins_get_the_technical_detail_viewers_do_not(
        self, login, viewer, org_admin, org, studio_tree, prefix, session,
        make_assistant_config, monkeypatch,
    ):
        # The incident: a gateway that speaks the wrong dialect makes the SDK
        # trip an AssertionError with no message. A viewer sees the safe line
        # only; the admin who set the URL also sees what broke, and where.
        make_assistant_config(org, base_url="https://gw.example.com/v1")

        class Boom:
            @property
            def messages(self):
                raise AssertionError()

        monkeypatch.setattr(llm, "_anthropic_client", lambda config: Boom())
        viewer_err = dict(frames(post_message(login(viewer), prefix, session)))["error"]
        assert viewer_err["code"] == "provider" and "detail" not in viewer_err

        admin_session = AssistantSession.objects.create(user=org_admin, org=org, studio=studio_tree)
        admin_err = dict(frames(post_message(login(org_admin), prefix, admin_session)))["error"]
        assert admin_err["message"] == viewer_err["message"]
        assert admin_err["detail"] == "AssertionError (gw.example.com)"

    def test_tool_results_reach_the_model_framed_as_data(
        self, login, viewer, prefix, session, assistant_config, fake_llm, report_row
    ):
        calls = fake_llm(
            FakeResponse([ToolUseBlock("tu_1", "list_reports", {})], stop_reason="tool_use"),
            FakeResponse([TextBlock("ok")]),
        )
        events = frames(post_message(login(viewer), prefix, session))
        sent = calls[1]["messages"][-1]["content"][0]["content"]
        assert sent.startswith('<data source="studio:') and sent.rstrip().endswith("</data>")
        # ... while the widget's excerpt stays the plain table.
        assert not dict(events)["tool_result"]["excerpt"].startswith("<data")


class TestReportBoundTurn:
    def test_forbidden_tool_result_and_context_never_reach_model_or_transcript(
        self, login, selected_viewer, org, studio_tree, prefix, report_row,
        assistant_config, fake_llm,
    ):
        (studio_tree.project_root / "assistant.md").write_text(
            "GLOBAL BRIEFING SECRET", encoding="utf-8"
        )
        session = AssistantSession.objects.create(
            user=selected_viewer, org=org, studio=studio_tree,
            scope=AssistantSession.SCOPE_REPORT, report=report_row,
        )
        calls = fake_llm(
            FakeResponse([
                ToolUseBlock("tu_1", "query_report_data", {
                    "slug": "private-report", "dataset_id": "secret",
                })
            ], stop_reason="tool_use"),
            FakeResponse([TextBlock("That report is outside this conversation.")]),
        )

        response = post_message(
            login(selected_viewer), prefix, session,
            page={"path": f"/s/demo/casino/r/{report_row.slug}/", "title": "Allowed"},
        )
        events = frames(response)
        tool_result = [data for name, data in events if name == "tool_result"][0]
        assert tool_result["is_error"] is True
        assert "cannot access" in tool_result["excerpt"]

        system = json.dumps(calls[0]["system"])
        sent_back = json.dumps(calls[1]["messages"])
        assert "GLOBAL BRIEFING SECRET" not in system
        assert "private-report" not in system
        assert "GLOBAL BRIEFING SECRET" not in sent_back
        session.refresh_from_db()
        assert "GLOBAL BRIEFING SECRET" not in json.dumps(session.state["transcript"])

    def test_other_report_page_requires_a_new_conversation(
        self, login, selected_viewer, org, studio_tree, prefix, report_row,
        assistant_config, fake_llm,
    ):
        session = AssistantSession.objects.create(
            user=selected_viewer, org=org, studio=studio_tree,
            scope=AssistantSession.SCOPE_REPORT, report=report_row,
        )
        calls = fake_llm(FakeResponse([TextBlock("must not run")]))
        response = post_message(
            login(selected_viewer), prefix, session,
            page={"path": "/s/demo/casino/r/private-report/", "title": "Private"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "report_context_changed"
        assert calls == []


class TestDeadline:
    def test_slow_tool_hits_the_deadline_and_the_partial_transcript_survives(
        self, login, viewer, prefix, session, assistant_config, fake_llm, settings, monkeypatch
    ):
        import time as _time

        from apps.assistant import tools

        settings.ASSISTANT_TURN_DEADLINE_S = 1
        monkeypatch.setattr(
            tools.AssistantToolbox, "execute",
            lambda self, name, args: _time.sleep(1.5) or "slow result",
        )
        fake_llm(
            FakeResponse([ToolUseBlock("tu_1", "list_reports", {})], stop_reason="tool_use"),
            FakeResponse([TextBlock("never reached")]),
        )
        events = frames(post_message(login(viewer), prefix, session))
        assert [n for n, _ in events] == ["turn_start", "usage", "tool_use", "error", "turn_end"]
        err = dict(events)["error"]
        assert err["code"] == "deadline"
        assert err["message"] == "Stopped after 1 seconds. Ask a narrower question."
        session.refresh_from_db()
        assert [e["role"] for e in session.state["transcript"]] == ["user", "assistant"]

    def test_a_provider_failure_after_the_deadline_is_reported_as_the_deadline(self):
        # The provider timed out because we told it to: that is the deadline,
        # not a provider error. Before the deadline it is the provider's.
        expired, live = llm._Clock(lambda: -0.1, 8), llm._Clock(lambda: 30.0, 8)
        assert llm._call_failed(expired, TimeoutError())["code"] == "deadline"
        assert llm._call_failed(live, TimeoutError())["code"] == "provider"

    def test_sdk_clients_leave_retrying_to_the_turn_loop(self):
        # With SDK retries on, one 8 s timeout becomes three and overruns the
        # deadline; the loops already retry rate limits and 5xx themselves.
        from types import SimpleNamespace

        cfg = SimpleNamespace(api_key="k", base_url="")
        assert llm._openai_client(cfg).max_retries == 0
        assert llm._anthropic_client(cfg).max_retries == 0


class TestPersistenceAndSpend:
    def test_transcript_and_title_persisted(
        self, login, viewer, prefix, session, assistant_config, fake_llm
    ):
        fake_llm(FakeResponse([TextBlock("Revenue was $5.00.")]))
        drain(post_message(login(viewer), prefix, session, text="how was revenue?"))
        session.refresh_from_db()
        assert session.title == "how was revenue?"
        roles_seen = [e["role"] for e in session.state["transcript"]]
        assert roles_seen == ["user", "assistant"]
        assert session.state["usage"]["input_tokens"] == 1000
        assert session.state["usage"]["output_tokens"] == 500

    def test_page_context_stored_but_not_in_message_text(
        self, login, viewer, prefix, session, assistant_config, fake_llm
    ):
        calls = fake_llm(FakeResponse([TextBlock("ok")]))
        drain(post_message(
            login(viewer), prefix, session, text="what is this?",
            page={"path": "/s/demo/casino/r/player-overview/", "title": "Player Overview"},
        ))
        session.refresh_from_db()
        entry = session.state["transcript"][0]
        assert entry["content"] == "what is this?"  # transcript stays clean
        assert "Player Overview" in entry["page"]
        # ... but the model does see it.
        sent = calls[0]["messages"][-1]["content"]
        assert "currently viewing" in json.dumps(sent)

    def test_chart_context_reaches_the_model(
        self, login, viewer, prefix, session, assistant_config, fake_llm
    ):
        calls = fake_llm(FakeResponse([TextBlock("ok")]))
        drain(post_message(
            login(viewer), prefix, session, text="why the dip?",
            page={"path": "/s/demo/casino/r/player-overview/", "title": "Player Overview",
                  "chart": {"section_id": "s1", "title": "Revenue by day",
                            "kind": "LineChart", "dataset_id": "revenue_daily"}},
        ))
        sent = json.dumps(calls[0]["messages"][-1]["content"])
        assert "asking about chart 'Revenue by day' (LineChart, dataset revenue_daily)" in sent

    def test_report_binding_does_not_follow_page_path(
        self, login, viewer, prefix, session, assistant_config, fake_llm, report_row
    ):
        fake_llm(FakeResponse([TextBlock("ok")]))
        drain(post_message(
            login(viewer), prefix, session, text="explain this",
            page={"path": f"/s/demo/casino/r/{report_row.slug}/index.html", "title": "x"},
        ))
        session.refresh_from_db()
        assert session.scope == AssistantSession.SCOPE_FULL
        assert session.report is None

    def test_cost_recorded_to_the_ledger(
        self, login, viewer, org, prefix, session, assistant_config, fake_llm
    ):
        fake_llm(FakeResponse([TextBlock("ok")]))
        events = frames(post_message(login(viewer), prefix, session))
        usage = dict(events)["usage"]
        # 1000 in @ $3/MTok + 500 out @ $15/MTok = $0.0105
        assert usage["cost_delta_usd"] == 0.0105
        assert LlmUsage.user_month_total(org, viewer) == Decimal("0.0105")

    def test_unpriced_model_still_answers_without_recording_unknown_spend(
        self, login, viewer, org, prefix, session, make_assistant_config, fake_llm
    ):
        make_assistant_config(org, model="claude-future-9")
        fake_llm(FakeResponse([TextBlock("ok")]))
        events = frames(post_message(login(viewer), prefix, session))
        usage = dict(events)["usage"]
        assert usage["cost_delta_usd"] is None
        assert usage["session_cost_usd"] is None
        assert LlmUsage.user_month_total(org, viewer) == Decimal("0")
        session.refresh_from_db()
        assert session.usage["input_tokens"] > 0
        assert session.to_summary()["cost_usd"] is None

    def test_second_turn_accumulates(
        self, login, viewer, org, prefix, session, assistant_config, fake_llm
    ):
        fake_llm(FakeResponse([TextBlock("a")]), FakeResponse([TextBlock("b")]))
        client = login(viewer)
        drain(post_message(client, prefix, session))
        drain(post_message(client, prefix, session, text="and now?"))
        assert LlmUsage.user_month_total(org, viewer) == Decimal("0.0210")
        session.refresh_from_db()
        assert len(session.state["transcript"]) == 4


class TestGuards:
    def test_missing_message_400(self, login, viewer, prefix, session, assistant_config):
        resp = post_message(login(viewer), prefix, session, text="   ")
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing 'message'"

    def test_unavailable_503(self, login, viewer, prefix, session, make_assistant_config, org):
        make_assistant_config(org, enabled=False)
        resp = post_message(login(viewer), prefix, session)
        assert resp.status_code == 503
        assert "disabled" in resp.json()["error"]

    def test_budget_cap_blocks_the_turn_402(
        self, login, viewer, org, prefix, session, make_assistant_config, fake_llm
    ):
        make_assistant_config(org, per_user_budget_usd=Decimal("0.01"))
        LlmUsage.add_cost(org, viewer, 0.02)
        calls = fake_llm(FakeResponse([TextBlock("should never run")]))
        resp = post_message(login(viewer), prefix, session)
        assert resp.status_code == 402
        assert "Budget cap reached" in resp.json()["error"]
        assert calls == []  # no API call was made

    def test_org_cap_blocks_the_turn(
        self, login, viewer, other_viewer, org, prefix, session, make_assistant_config
    ):
        make_assistant_config(org, monthly_budget_usd=Decimal("1.00"))
        LlmUsage.add_cost(org, other_viewer, 1)
        resp = post_message(login(viewer), prefix, session)
        assert resp.status_code == 402
        assert "Organization budget cap reached" in resp.json()["error"]

    def test_cap_reached_mid_turn_stops_the_loop(
        self, login, viewer, org, prefix, session, make_assistant_config, fake_llm, report_row
    ):
        """One user message can drive many API calls — the cap applies to each."""
        make_assistant_config(org, per_user_budget_usd=Decimal("0.005"))
        calls = fake_llm(
            FakeResponse([ToolUseBlock("tu_1", "list_reports", {})], stop_reason="tool_use"),
            FakeResponse([TextBlock("never reached")]),
        )
        events = frames(post_message(login(viewer), prefix, session))
        names = [n for n, _ in events]
        assert names == ["turn_start", "usage", "error", "turn_end"]
        assert "Budget cap reached" in dict(events)["error"]["message"]
        assert len(calls) == 1

    def test_a_second_turn_while_one_streams_is_409(
        self, login, viewer, prefix, session, assistant_config, fake_llm
    ):
        fake_llm(FakeResponse([TextBlock("a")]), FakeResponse([TextBlock("b")]))
        client = login(viewer)
        first = post_message(client, prefix, session)  # not drained: still in flight
        second = post_message(client, prefix, session, text="again?")
        assert second.status_code == 409
        assert second.json() == {
            "error": "A reply is still streaming in another tab.", "code": "busy"
        }
        drain(first)  # the turn ends and releases the claim
        assert post_message(client, prefix, session, text="now?").status_code == 200

    def test_other_users_session_404(self, login, other_viewer, prefix, session, assistant_config):
        assert post_message(login(other_viewer), prefix, session).status_code == 404

    def test_unauthenticated_401(self, client, prefix, session):
        assert post_message(client, prefix, session).status_code == 401

    def test_get_not_allowed(self, login, viewer, prefix, session, assistant_config):
        assert login(viewer).get(f"{prefix}/sessions/{session.pk}/message").status_code == 405
