"""``check_repo_changes`` (internal planning ticket #151): what the portal would publish, as a
read tool for developers -- in the panel and over MCP alike."""
import json

import pytest

from apps.accounts.models import ApiKey
from apps.assistant.tools import AssistantToolbox
from apps.core import roles
from apps.studios.models import StudioRepo

pytestmark = pytest.mark.django_db

PENDING = {
    "from": "a" * 40, "to": "b" * 40, "branch": "main",
    "rewritten": False, "initial": False, "full_listing": False,
    "commits": [
        {"sha": "b" * 40, "author": "Ada", "date": "2026-09-01T10:00:00+02:00",
         "message": "Tighten churn cohorts", "files": ["reports/churn/report.yaml"]},
    ],
    "commits_total": 1,
    "reports": {"added": ["retention"], "modified": ["churn"], "removed": ["funnel"]},
    "root_files": ["metrics.yaml"],
    "datasources": {"added": [], "removed": [], "changed": ["events"]},
    "warnings": ["2 report(s) will not be registered: this organization is limited to 5 reports."],
}


@pytest.fixture
def developer(make_user, org, studio_tree, grant_studio):
    user = make_user("dev@demo.example", org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
    return user


@pytest.fixture
def repo(studio_tree):
    return StudioRepo.objects.create(
        studio=studio_tree, repo_url="https://github.com/acme/analytics.git", branch="main",
        publish_mode="manual", remote_sha="b" * 40, pending_changes=PENDING,
    )


def names(box):
    return [s["name"] for s in box.schemas]


def check(studio, role=roles.DEVELOPER):
    return AssistantToolbox(studio, role=role).execute("check_repo_changes", {})


class TestVisibility:
    def test_developers_see_it_viewers_do_not(self, studio_tree):
        assert "check_repo_changes" in names(AssistantToolbox(studio_tree, role=roles.DEVELOPER))
        assert "check_repo_changes" in names(AssistantToolbox(studio_tree, role="org_admin"))
        assert "check_repo_changes" not in names(AssistantToolbox(studio_tree, role=roles.VIEWER))
        assert "check_repo_changes" not in names(AssistantToolbox(studio_tree))  # the evaluator's default

    def test_a_viewers_scripted_call_is_refused(self, studio_tree, repo):
        result = check(studio_tree, roles.VIEWER)
        assert result.startswith("Error") and "not available" in result
        assert "retention" not in result


class TestPayload:
    def test_pending_summary_is_readable(self, studio_tree, repo):
        text = check(studio_tree)
        assert "manual" in text and "bbbbbbbbbbbb" in text
        assert "reports added: retention" in text
        assert "reports modified: churn" in text and "reports removed: funnel" in text
        assert "data sources changed: events" in text
        assert "metrics.yaml" in text and "Tighten churn cohorts" in text
        assert "limited to 5 reports" in text
        assert "Last published: never" in text
        assert "publish_repo_changes" in text

    def test_nothing_pending_and_the_last_error(self, studio_tree, repo):
        repo.pending_changes, repo.last_error = {}, "boom"
        repo.save()
        text = check(studio_tree)
        assert "nothing" in text.lower() and "Last error: boom" in text
        assert "retention" not in text

    def test_unconfigured(self, studio_tree):
        assert "not configured" in check(studio_tree)

    def test_the_view_answers_the_same_payload(self, login, developer, org, studio_tree, repo):
        body = login(developer).get(f"/s/{org.slug}/{studio_tree.slug}/api/system/git/status").json()
        assert body["pending"] == PENDING and body["publish_mode"] == "manual"
        assert body["remote_sha"] == "b" * 40 and body["last_published"] is None

    def test_is_framed_as_repo_data(self, studio_tree):
        framed = AssistantToolbox(studio_tree, role=roles.DEVELOPER).frame("check_repo_changes", {}, "x")
        assert framed.startswith(f'<data source="repo:{studio_tree.slug}">')


class TestOverMcp:
    @staticmethod
    def rpc(client, org, studio, secret, method, params=None):
        resp = client.post(
            f"/s/{org.slug}/{studio.slug}/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}),
            content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {secret}",
        )
        assert resp.status_code == 200
        return resp.json()["result"]

    def test_a_developers_read_key_lists_and_calls_it(self, client, developer, org, studio_tree, repo):
        _, secret = ApiKey.mint(user=developer, org=org, name="agent", scopes=ApiKey.READ)
        tools = self.rpc(client, org, studio_tree, secret, "tools/list")["tools"]
        assert "check_repo_changes" in [t["name"] for t in tools]
        result = self.rpc(
            client, org, studio_tree, secret, "tools/call", {"name": "check_repo_changes", "arguments": {}}
        )
        assert result["isError"] is False
        assert "reports added: retention" in result["content"][0]["text"]

    def test_a_viewers_key_does_not_get_it(self, client, viewer, org, studio_tree, repo):
        _, secret = ApiKey.mint(user=viewer, org=org, name="agent", scopes=ApiKey.READ_WRITE)
        tools = self.rpc(client, org, studio_tree, secret, "tools/list")["tools"]
        assert "check_repo_changes" not in [t["name"] for t in tools]
        result = self.rpc(
            client, org, studio_tree, secret, "tools/call", {"name": "check_repo_changes", "arguments": {}}
        )
        assert result["isError"] is True and "retention" not in result["content"][0]["text"]
