import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import ServerLogEvent
from apps.core.server_logs import event_from_record
from apps.runner.models import Run

pytestmark = pytest.mark.django_db


@pytest.fixture
def operator(superuser):
    return superuser


@pytest.fixture
def event():
    def create(message="hello", **kwargs):
        import logging
        record = logging.LogRecord("runner", logging.INFO, "", 0, message, (), None)
        value = event_from_record(record)
        value.update(kwargs)
        return ServerLogEvent.objects.create(**value)
    return create


URLS = ["/operator/logs/", "/operator/logs/events/", "/operator/logs/export/",
        f"/operator/logs/runs/{uuid.uuid4()}/"]


def test_operator_page_renders_real_shell_and_valid_filter_defaults(login, operator):
    response = login(operator).get("/operator/logs/")
    assert response.status_code == 200
    html = response.content.decode()
    assert 'data-events-url="/operator/logs/events/"' in html
    assert 'value="INFO" selected' in html
    assert 'href="/operator/logs/"' in html
    assert response["Cache-Control"] == "no-store"


@pytest.mark.parametrize("url", URLS)
def test_access_and_impersonation(client, login, member, operator, url):
    response = client.get(url)
    assert response.status_code in (302, 401)
    assert response["Cache-Control"] == "no-store"
    assert login(member).get(url).status_code == 404
    login(operator)
    session = client.session
    session["impersonator_id"] = operator.pk
    session["impersonation_expires_at"] = (timezone.now() + timedelta(minutes=5)).isoformat()
    session.save()
    response = client.get(url)
    assert response.status_code == 404 and response["Cache-Control"] == "no-store"


def test_cursor_order_and_empty_catchup(login, operator, event):
    rows = [event(str(i)) for i in range(5)]
    client = login(operator)
    initial = client.get("/operator/logs/events/", {"limit": 2}).json()
    assert [row["id"] for row in initial["entries"]] == [rows[4].id, rows[3].id]
    assert initial["has_more"] and initial["next_after"] == rows[4].id
    older = client.get("/operator/logs/events/", {"before": initial["next_before"], "limit": 2}).json()
    assert [row["id"] for row in older["entries"]] == [rows[2].id, rows[1].id]
    newer = client.get("/operator/logs/events/", {"after": rows[0].id, "limit": 2}).json()
    assert [row["id"] for row in newer["entries"]] == [rows[1].id, rows[2].id]
    assert newer["next_after"] == rows[2].id and newer["has_more"]
    empty = client.get("/operator/logs/events/", {"after": rows[4].id}).json()
    assert empty["entries"] == [] and empty["next_after"] == rows[4].id


@pytest.mark.parametrize("params", [
    {"limit": 201}, {"limit": 0}, {"limit": "x"}, {"before": -1},
    {"before": 1, "after": 2}, {"after": 2**63}, {"level": "bad"},
    {"since": "yesterday"}, {"since": "2026-10-08T00:00:00"},
    {"run_id": "bad"}, {"q": "x" * 201}, {"worker_id": "x" * 101},
    {"since": "2026-10-09T00:00:00Z", "until": "2026-10-08T00:00:00Z"},
])
def test_invalid_filters(login, operator, params):
    response = login(operator).get("/operator/logs/events/", params)
    assert response.status_code == 400 and response["Cache-Control"] == "no-store"


def test_combined_filters_and_redaction(login, operator, event):
    run_id = str(uuid.uuid4())
    row = event("unique password=hunter2", level="ERROR", service="worker", worker_id="w1",
                run_id=run_id, report_slug="alpha", request_id="r1")
    event("unique", level="INFO")
    response = login(operator).get("/operator/logs/events/", {
        "q": "unique", "level": "WARNING", "service": "worker", "worker_id": "w1",
        "run_id": run_id, "report_slug": "alpha", "request_id": "r1",
        "since": (row.timestamp - timedelta(seconds=1)).isoformat(),
        "until": (row.timestamp + timedelta(seconds=1)).isoformat(),
    })
    entries = response.json()["entries"]
    assert len(entries) == 1 and entries[0]["id"] == row.id
    assert "hunter2" not in response.content.decode()


def test_export_cap_and_scrubbing(login, operator, event):
    row = event("token=private")
    row.pk = None
    ServerLogEvent.objects.bulk_create([ServerLogEvent(**{
        field.name: getattr(row, field.name) for field in ServerLogEvent._meta.fields if field.name != "id"
    }) for _ in range(1001)])
    response = login(operator).get("/operator/logs/export/")
    lines = response.content.decode().splitlines()
    assert len(lines) == 1000 and response["X-Log-Export-Truncated"] == "true"
    assert "private" not in response.content.decode()
    assert json.loads(lines[0])["message"] == "token=[redacted]"


def test_run_details_are_scoped_and_bounded(login, operator, report_row, tmp_path):
    run = Run.objects.create(report=report_row, studio=report_row.studio, slug=report_row.slug,
                             stdout_tail="<script>token=private</script>", stderr_tail="x" * 60_000)
    response = login(operator).get(f"/operator/logs/runs/{run.id}/")
    data = response.json()["run"]
    assert data["id"] == str(run.id) and data["org"] == run.studio.org.slug
    assert len(data["stderr"]) == 50_000 and data["output_truncated"]
    assert "private" not in data["stdout"] and data["output_note"]
    assert login(operator).get(f"/operator/logs/runs/{uuid.uuid4()}/").status_code == 404
    (tmp_path / "stdout.log").write_text("active api_key=hidden")
    run.log_dir = str(tmp_path)
    run.status = Run.RUNNING
    run.save()
    active = login(operator).get(f"/operator/logs/runs/{run.id}/").json()["run"]
    assert active["output_source"] == "active file tails"
    assert active["stdout"] == "active api_key=[redacted]"
