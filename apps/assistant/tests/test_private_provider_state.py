"""Native continuation metadata stays server-side on outward session surfaces."""
import copy

import pytest

from apps.assistant.models import AssistantSession

pytestmark = pytest.mark.django_db

REASONING = "__PRIVATE_PROVIDER_REASONING__"
SIGNATURE = "__PRIVATE_PROVIDER_SIGNATURE__"
VISIBLE_TRANSCRIPT = [
    {"role": "user", "content": "Show the synthetic total."},
    {"role": "assistant", "content": [{"type": "text", "text": "The synthetic total is 42."}]},
]
VISIBLE_USAGE = {"input_tokens": 10, "output_tokens": 8, "cost_usd": 0.25}


@pytest.fixture(params=[
    {"reasoning_content": REASONING, "reasoning_details": [{"type": "reasoning.encrypted", "data": SIGNATURE}]},
    {"blocks": [{"type": "thinking", "thinking": REASONING, "signature": SIGNATURE}]},
    {"provider": "bedrock", "content": [{"reasoningContent": {"reasoningText": {"text": REASONING, "signature": SIGNATURE}}}]},
    {"provider": "vertex", "content": [{"text": REASONING, "thought": True, "thoughtSignature": SIGNATURE}]},
], ids=["compatible-reasoning", "anthropic-thinking", "bedrock-reasoning", "vertex-signature"])
def private_session(request, viewer, org, studio_tree):
    return AssistantSession.objects.create(
        user=viewer, org=org, studio=studio_tree, title="Synthetic totals",
        state={"transcript": copy.deepcopy(VISIBLE_TRANSCRIPT), "usage": dict(VISIBLE_USAGE),
               "_provider": {"identity": ["synthetic-provider", "synthetic-model"],
                             "messages": {"1": request.param}}},
    )


def assert_private_absent(response):
    text = response.content.decode()
    assert "_provider" not in text
    assert REASONING not in text
    assert SIGNATURE not in text


def test_session_detail_exposes_visible_transcript_without_native_state(login, viewer, prefix, private_session):
    response = login(viewer).get(f"{prefix}/sessions/{private_session.pk}")
    assert response.status_code == 200
    assert response.json()["transcript"] == VISIBLE_TRANSCRIPT
    assert response.json()["usage"] == VISIBLE_USAGE
    assert_private_absent(response)
    private_session.refresh_from_db()
    assert private_session.state["_provider"]["messages"]["1"]


def test_session_listing_keeps_summary_without_native_state(login, viewer, prefix, private_session):
    response = login(viewer).get(f"{prefix}/sessions")
    assert response.status_code == 200
    summary = response.json()["sessions"][0]
    assert summary["id"] == private_session.pk
    assert summary["message_count"] == 2
    assert summary["cost_usd"] == 0.25
    assert "transcript" not in summary
    assert_private_absent(response)


def test_org_personal_data_download_keeps_visible_transcript_without_native_state(login, org_admin, org, viewer, private_session):
    response = login(org_admin).get(f"/orgs/{org.slug}/settings/members/{viewer.pk}/export.json")
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    conversation = response.json()["activity"]["assistant_conversations"][0]
    assert conversation["title"] == private_session.title
    assert conversation["transcript"] == VISIBLE_TRANSCRIPT
    assert conversation["usage"] == VISIBLE_USAGE
    assert_private_absent(response)
