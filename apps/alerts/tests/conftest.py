"""Fixtures for the alert evaluator: a built report, an owner, a rule, and
the assistant suite's scripted fake client."""
import pytest

from apps.alerts.models import AlertRule
from apps.assistant import llm
from apps.assistant.tests.test_message_sse import FakeClient, FakeResponse, ToolUseBlock
from apps.core import roles
from apps.orgs.models import OrgAssistantConfig


@pytest.fixture
def fake_llm(monkeypatch):
    """The assistant suite's scripted client, with ONE response queue shared
    across turns: the evaluator may call stream_turn twice (the forced
    ``decide``), and the original fixture hands each call a fresh copy."""
    calls = []

    def _install(*responses):
        client = FakeClient(responses, calls)
        monkeypatch.setattr(llm, "_anthropic_client", lambda config: client)
        return calls

    return _install


@pytest.fixture
def assistant_config(org):
    return OrgAssistantConfig.objects.create(
        org=org, enabled=True, provider="anthropic", api_key="sk-test-not-a-real-key",
        model="claude-sonnet-4-6",
    )


@pytest.fixture
def owner(make_user, org, studio_tree, grant_studio):
    user = make_user("owner@demo.example", org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
    return user


@pytest.fixture
def built(report_row, write_meta):
    """The report's last build succeeded."""
    write_meta(
        report_row.slug, last_status="success", last_run="2026-09-08T04:00:00+00:00",
        validation={"summary": {"fail": 0, "warn": 1}},
    )
    return report_row


@pytest.fixture
def rule(built, owner, org, studio_tree, assistant_config):
    return AlertRule.objects.create(
        org=org, studio=studio_tree, report=built, name="Revenue watch",
        instructions="Tell me if revenue drops.", created_by=owner,
    )


def decide(alert, title="Revenue fell", message="Revenue is down 30% on the week.",
           evidence=("gross_revenue 2026-09-07: 700 vs 1000 a week earlier",)):
    """A scripted turn that calls decide() straight away."""
    return FakeResponse(
        [ToolUseBlock("tu_decide", "decide", {
            "alert": alert, "title": title, "message": message, "evidence": list(evidence),
        })],
        stop_reason="tool_use",
    )
