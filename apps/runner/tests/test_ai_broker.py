"""Live broker and report subprocesses use a local synthetic provider only."""
from contextlib import contextmanager
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.assistant.models import LlmUsage
from apps.orgs.models import OrgAssistantConfig
from apps.runner.ai_broker import AIBroker, _complete
from apps.runner.executor import Executor, child_env
from apps.runner.models import Run
from trellum.ai import ReportAI, ReportAIError, unix_socket_address


@pytest.fixture
def provider():
    requests = []
    state = {"text": '{"diagnosis":"ok"}', "status": 200, "delay": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            time.sleep(state["delay"])
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {"id": "synthetic", "object": "chat.completion", "model": body["model"],
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": state["text"]}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 100}}
            try:
                self.wfile.write(json.dumps(response).encode())
            except BrokenPipeError:
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}/v1", requests=requests, state=state)
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def saved(org, provider):
    return OrgAssistantConfig.objects.create(org=org, enabled=True, provider="litellm",
        base_url=provider.url, model="saved-model", api_key="synthetic-org-secret",
        price_in_per_mtok=Decimal("50"), price_out_per_mtok=Decimal("50"))


@contextmanager
def live_broker(studio, monkeypatch, requested_by=None):
    with tempfile.TemporaryDirectory(prefix="tai-") as directory:
        run = SimpleNamespace(pk="run", studio=studio, requested_by_id=requested_by)
        broker = AIBroker(run, directory).start()
        for key, value in broker.child_env.items():
            monkeypatch.setenv(key, value)
        try:
            yield broker
        finally:
            broker.close()
        assert not Path(broker.path).exists()


@pytest.mark.parametrize("replacement", ["directory", "symlink", "missing"])
def test_close_tolerates_tenant_replacing_socket(tmp_path, replacement):
    run = SimpleNamespace(pk="run", studio=SimpleNamespace(org_id="org"), requested_by_id=None)
    broker = AIBroker(run, tmp_path).start()
    path = Path(broker.path)
    target = tmp_path / "keep"
    target.mkdir()
    marker = target / "marker"
    marker.write_text("preserve this file")
    path.unlink()
    if replacement == "directory":
        path.mkdir()
        (path / "marker").write_text("preserve this directory")
    elif replacement == "symlink":
        path.symlink_to(target, target_is_directory=True)

    broker.close()
    broker.close()

    assert not broker._thread.is_alive()
    assert broker._listener.fileno() == -1
    assert marker.read_text() == "preserve this file"
    if replacement == "directory":
        assert (path / "marker").read_text() == "preserve this directory"
    else:
        assert not path.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("output_tokens", [0, None])
def test_rejected_vertex_answer_is_metered_once_and_stops_at_org_cap(
    org, org_admin, monkeypatch, output_tokens
):
    from google.genai import types
    from apps.assistant import cloud

    OrgAssistantConfig.objects.create(
        org=org, enabled=True, provider="vertex", auth_mode="service_account",
        cloud_credentials={"service_account": {"type": "service_account"}},
        model="saved-model", monthly_budget_usd=Decimal("0.01"),
        price_in_per_mtok=Decimal("10"), price_out_per_mtok=Decimal("20"),
    )
    client = Mock()
    client.models.generate_content.return_value = types.GenerateContentResponse(
        candidates=[],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1000, candidates_token_count=output_tokens,
        ),
    )
    monkeypatch.setattr(cloud, "_vertex_client", lambda *args: client)

    with pytest.raises(ReportAIError) as caught:
        _complete(org.pk, {"system": "system", "user": "facts"})
    assert caught.value.code == "invalid_response"
    assert LlmUsage.month_total(org) == Decimal("0.01")
    assert LlmUsage.user_month_total(org, org_admin) == 0
    assert not LlmUsage.objects.filter(org=org).exists()
    with pytest.raises(ReportAIError) as caught:
        _complete(org.pk, {"system": "system", "user": "facts"})
    assert caught.value.code == "budget"
    assert client.models.generate_content.call_count == 1
    assert LlmUsage.month_total(org) == Decimal("0.01")


@pytest.mark.django_db
def test_broker_prices_the_selected_override(org, monkeypatch):
    from apps.assistant import llm

    OrgAssistantConfig.objects.create(
        org=org, enabled=True, provider="openai", api_key="synthetic-org-secret",
        model="unpriced-saved-model", monthly_budget_usd=Decimal("1"),
    )
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content="ok", tool_calls=[]), finish_reason="stop"
        )],
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100),
    )
    monkeypatch.setattr(llm, "_openai_client", lambda *args: client)
    response = _complete(org.pk, {"system": "system", "user": "facts", "model": "gpt-4o"})
    assert response["result"]["model"] == "gpt-4o"
    assert LlmUsage.month_total(org) == Decimal("0.0035")
    assert client.chat.completions.create.call_args.kwargs["model"] == "gpt-4o"


@pytest.mark.heavy
@pytest.mark.django_db(transaction=True)
def test_saved_connection_reload_and_org_only_spend(studio, org_admin, saved, provider, monkeypatch):
    saved.monthly_budget_usd = Decimal("0.03")
    saved.per_user_budget_usd = Decimal("0.01")
    saved.save()
    LlmUsage.add_cost(studio.org, org_admin, Decimal("0.01"))
    with live_broker(studio, monkeypatch, org_admin.pk):
        result = ReportAI().complete(system="Return JSON", user="facts", response_format="json", max_tokens=8000)
        assert result.text == '{"diagnosis":"ok"}'
        assert result.model == "saved-model"
        saved.model = "changed-model"
        saved.base_url = provider.url + "/changed"
        saved.config_revision += 1
        saved.save()
        assert ReportAI().complete(system="system", user="facts").model == "changed-model"
        assert LlmUsage.user_month_total(studio.org, org_admin) == Decimal("0.01")
        assert LlmUsage.month_total(studio.org) == Decimal("0.03")
        with pytest.raises(ReportAIError) as caught:
            ReportAI().complete(system="system", user="facts")
        assert caught.value.code == "budget"
    assert len(provider.requests) == 2
    assert provider.requests[0][0] == "/v1/chat/completions"
    assert provider.requests[1][0] == "/v1/changed/chat/completions"
    assert provider.requests[0][1]["response_format"] == {"type": "json_object"}


@pytest.mark.heavy
@pytest.mark.django_db(transaction=True)
def test_unattended_and_invalid_json_are_metered(studio, saved, provider, monkeypatch):
    with live_broker(studio, monkeypatch):
        for _ in range(2):
            ReportAI().complete(system="system", user="facts")
        provider.state["text"] = "not json"
        with pytest.raises(ReportAIError) as caught:
            ReportAI().complete(system="system", user="facts", response_format="json")
        assert caught.value.code == "invalid_response"
    assert LlmUsage.month_total(studio.org) == Decimal("0.03")
    assert not LlmUsage.objects.filter(org=studio.org).exists()


@pytest.mark.heavy
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("failure", ["success", "disabled", "budget", "provider", "invalid_response"])
def test_caught_failure_still_publishes(failure, studio_tree, queued_run, saved, provider, settings, caplog):
    # Exercise the real Executor -> framework generator -> socket -> SDK path.
    if failure == "disabled":
        saved.enabled = False
    elif failure == "budget":
        saved.monthly_budget_usd = Decimal("0")
    elif failure == "provider":
        provider.state["status"] = 500
    elif failure == "invalid_response":
        provider.state["text"] = "not json"
    saved.save()
    settings.TRELLUM_JOB_MEMORY_ENFORCE = False
    generator = studio_tree.reports_dir / queued_run.slug / "generator.py"
    generator.write_text('''from trellum.report import BaseReport
from trellum.ai import ReportAIError
class Diagnosis(BaseReport):
    def generate(self, ctx):
        import os
        assert "LITELLM_API_KEY" not in os.environ
        assert "synthetic-org-secret" not in str(dict(os.environ))
        assert not (__import__("pathlib").Path(".secrets/litellm-api-key")).exists()
        try:
            ctx.ai.complete(system="system", user="facts", response_format="json")
            ctx.sections.append({"title": "success", "components": []})
        except ReportAIError as error:
            ctx.sections.append({"title": error.code, "components": []})
        return {"title": "Diagnosis", "sections": ctx.sections}
''')
    executor = Executor("test-ai", default_timeout=45)
    assert executor.start_run(queued_run)
    proc = executor._procs[queued_run.pk]
    token = proc.ai_broker.child_env["TRELLUM_AI_TOKEN"]
    deadline = time.monotonic() + 40
    while executor._procs and time.monotonic() < deadline:
        executor.tick()
        time.sleep(0.1)
    queued_run.refresh_from_db()
    assert queued_run.status == Run.SUCCESS, queued_run.stderr_tail
    artifacts = "".join(p.read_text(errors="replace") for p in (studio_tree.output_dir / queued_run.slug).rglob("*") if p.is_file())
    assert failure in artifacts
    for secret in (token, "synthetic-org-secret", "Authorization"):
        assert secret not in artifacts + queued_run.stdout_tail + queued_run.stderr_tail + caplog.text
    assert not Path(proc.ai_broker.path).exists()


@pytest.mark.heavy
@pytest.mark.django_db(transaction=True)
def test_report_subprocess_no_provider_credentials(studio, saved, monkeypatch):
    with live_broker(studio, monkeypatch) as broker:
        env = child_env(studio, broker.child_env)
        assert saved.api_key not in str(env) and saved.base_url not in str(env)
        code = '''import os
from trellum.report import ReportContext
assert 'LITELLM_API_KEY' not in os.environ
assert not __import__('pathlib').Path('.secrets/litellm-api-key').exists()
ctx = ReportContext({}, 'example', '.')
print(ctx.ai.complete(system='system', user='facts').text)
'''
        result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == '{"diagnosis":"ok"}'
        assert saved.api_key not in result.stdout + result.stderr


@pytest.mark.django_db
def test_bad_token_and_validation_never_dispatch(studio, monkeypatch):
    with live_broker(studio, monkeypatch) as broker:
        calls = []
        monkeypatch.setattr(broker, "_dispatch", lambda request: calls.append(request))
        for request, code in [({"token": "wrong"}, "unavailable"), ({"token": broker._token, "system": "", "user": "facts"}, "invalid_request")]:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                with unix_socket_address(broker.path) as address:
                    connection.connect(address)
                connection.sendall(json.dumps(request).encode() + b"\n")
                assert json.loads(connection.recv(65536))["error"]["code"] == code
        assert not calls


@pytest.mark.heavy
@pytest.mark.django_db(transaction=True)
def test_timeout_terminates_provider_worker(studio, saved, provider, monkeypatch):
    provider.state["delay"] = 10
    with live_broker(studio, monkeypatch) as broker:
        with pytest.raises(ReportAIError) as caught:
            ReportAI().complete(system="system", user="facts", timeout=2)
        assert caught.value.code == "timeout"
        deadline = time.monotonic() + 3
        while broker._active is not None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert broker._active is None
        assert LlmUsage.month_total(studio.org) == 0


@pytest.mark.docker_sandbox
@pytest.mark.heavy
@pytest.mark.django_db(transaction=True)
def test_socket_completion_from_networkless_uid_10001(studio, saved, monkeypatch, settings):
    import docker
    from docker.types import Mount

    client = docker.from_env()
    image = os.environ.get("TRELLUM_TEST_SANDBOX_IMAGE", settings.TRELLUM_SANDBOX_IMAGE)
    try:
        client.ping()
        client.images.get(image)
    except docker.errors.DockerException as error:
        pytest.skip(f"Local sandbox image/daemon unavailable: {type(error).__name__}")
    try:
        with live_broker(studio, monkeypatch) as broker:
            directory = Path(broker.path).parent
            directory.chmod(0o755)
            code = '''import os
from pathlib import Path
from trellum.report import ReportContext
assert 'LITELLM_API_KEY' not in os.environ
assert 'synthetic-org-secret' not in str(dict(os.environ))
assert not Path('/app/.secrets/litellm-api-key').exists()
ctx = ReportContext({}, 'example', '/tmp')
print(ctx.ai.complete(system='system', user='facts').text)
'''
            output = client.containers.run(image, ["python3", "-c", code], remove=True,
                user="10001:10001", read_only=True, network_mode="none",
                cap_drop=["ALL"], security_opt=["no-new-privileges"],
                environment=broker.child_env, mem_limit="512m", pids_limit=128,
                mounts=[Mount(str(directory), str(directory), type="bind"),
                        Mount("/app/trellum", str(Path(__file__).resolve().parents[3] / "trellum"), type="bind", read_only=True)],
                tmpfs={"/tmp": "rw,nosuid,size=64m"})
            assert output.decode().strip() == '{"diagnosis":"ok"}'
            assert saved.api_key.encode() not in output
            assert broker._token.encode() not in output
    finally:
        client.close()


@pytest.mark.heavy
@pytest.mark.django_db(transaction=True)
def test_concurrent_call_is_rejected_and_close_stops_worker(studio, saved, provider, monkeypatch):
    provider.state["delay"] = 5
    errors = []
    with live_broker(studio, monkeypatch) as broker:
        def first_call():
            try:
                ReportAI().complete(system="system", user="facts")
            except ReportAIError as error:
                errors.append(error.code)

        caller = threading.Thread(target=first_call)
        caller.start()
        deadline = time.monotonic() + 10
        while not provider.requests and time.monotonic() < deadline:
            time.sleep(0.05)
        assert provider.requests
        with pytest.raises(ReportAIError) as caught:
            ReportAI().complete(system="system", user="other facts")
        assert caught.value.code == "invalid_request"
        broker.close()
        caller.join(2)
        assert not caller.is_alive()
        assert broker._active is None
        assert len(provider.requests) == 1
        assert errors
