"""Opt-in real worker/process verification against PostgreSQL and loopback S3.

Run with PORTAL_SOURCE_LIVE=1; remote additionally requires a disposable
PORTAL_SOURCE_LIVE_S3_URL endpoint (e.g. moto HTTP server). No production
credentials/config are inherited by child workers.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest
from django.db import connection
from django.utils import timezone

from apps.reports.models import ReportSourceSummary
from apps.reports.scan import sync_studio_registry
from apps.runner.models import Run
from apps.runner.services import enqueue

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.skipif(
    os.environ.get("PORTAL_SOURCE_LIVE") != "1", reason="explicit isolated live-worker opt-in required")]


def wait_until(predicate, *, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.2)
    raise AssertionError("live worker condition did not converge")


GENERATOR = '''from trellum import BaseReport
from trellum.components import KpiRow
class SummaryProbe(BaseReport):
    def generate(self, ctx):
        ctx.add_section("Overview", [KpiRow([{"metric": "m", "value": 7}])])
'''


@pytest.mark.parametrize("backend", ["local", "s3"])
def test_actual_worker_publish_failure_restart_and_freshness(studio_tree, settings, tmp_path, backend):
    root = Path(__file__).resolve().parents[3]
    assert (root / "manage.py").exists()
    endpoint = os.environ.get("PORTAL_SOURCE_LIVE_S3_URL", "")
    if backend == "s3":
        parsed = urlparse(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            pytest.fail("remote live gate requires an explicit disposable loopback HTTP provider")
    settings.TRELLUM_STORAGE_BACKEND = backend
    settings.TRELLUM_STORAGE_ENDPOINT_URL = endpoint
    settings.TRELLUM_STORAGE_ACCESS_KEY = "portal-c10a-test"
    settings.TRELLUM_STORAGE_SECRET_KEY = "portal-c10a-test"
    settings.TRELLUM_STORAGE_REGION = "us-east-1"
    settings.TRELLUM_REPORTS_BUCKET = "portal-c10a-" + uuid.uuid4().hex
    from apps.core import storage
    from apps.core.s3 import s3_client
    if backend == "s3":
        s3_client().create_bucket(Bucket=settings.TRELLUM_REPORTS_BUCKET)

    source = studio_tree.reports_dir / "probe"
    source.mkdir(parents=True)
    (source / "report.yaml").write_text('name: Summary probe\nslug: probe\ndescription: A deterministic report for isolated source summary publication verification.\n')
    generator = source / "generator.py"
    generator.write_text(GENERATOR)
    (studio_tree.project_root / "metrics.yaml").write_text('version: 1\nmetrics:\n  - name: m\n    label: Metric M\n    description: An isolated descriptive metric.\n    version: 1\n')
    sync_studio_registry(studio_tree)
    report = studio_tree.reports.get(slug="probe")

    # Existing builds are repaired automatically at worker startup, not CLI.
    from apps.reports.models import Report
    old = Report.objects.create(studio=studio_tree, slug="historical")
    old_output = studio_tree.output_dir / old.slug
    old_output.mkdir(parents=True)
    (old_output / "_meta.json").write_text(json.dumps({"last_run": timezone.now().isoformat(), "last_status": "success", "metrics_used": [f"old{i}" for i in range(2000)]}))
    if backend == "s3":
        storage.publish_build(studio_tree, old.slug, str(old_output), uuid.uuid4())

    old_ab = Report.objects.create(studio=studio_tree, slug="historical-ab", config={"ab_test": [{"test_name": "Alpha"}, {"test_name": "Missing"}]})
    ab_output = studio_tree.output_dir / old_ab.slug
    ab_output.mkdir(parents=True)
    (ab_output / "_meta.json").write_text(json.dumps({"last_status": "success", "metrics_used": []}))
    (ab_output / "index.html").write_text("historical AB")
    (ab_output / "data.json").write_text(json.dumps({"components": {}, "_scopeFiles": {"first": "data_ab.json"}}))
    (ab_output / "data_ab.json").write_text(json.dumps({"components": {"ab": {"type": "ab_compare", "header": {"test_name": "Alpha"}, "body": {"kpi_rows": [{"metric": "Revenue", "delta_pct": 4, "ci_pct": [1, 5]}]}}}}))
    if backend == "s3":
        storage.publish_build(studio_tree, old_ab.slug, str(ab_output), uuid.uuid4())

    overrides = {
        "DATABASES": {"default": {**connection.settings_dict, "CONN_MAX_AGE": 0}},
        "DATA_DIR": str(settings.DATA_DIR), "TRELLUM_STORAGE_BACKEND": backend,
        "TRELLUM_STORAGE_ENDPOINT_URL": endpoint,
        "TRELLUM_STORAGE_ACCESS_KEY": "portal-c10a-test", "TRELLUM_STORAGE_SECRET_KEY": "portal-c10a-test",
        "TRELLUM_STORAGE_REGION": "us-east-1", "TRELLUM_REPORTS_BUCKET": settings.TRELLUM_REPORTS_BUCKET,
        "TRELLUM_SANDBOX": "off", "TRELLUM_JOB_MEMORY_ENFORCE": False,
        "WORKER_CATCHUP": False, "CLEANUP_ENABLED": False, "TRELLUM_UPDATE_CHECK": False,
        "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    }
    config = tmp_path / "source_live_settings.py"
    config.write_text('from trellum_portal.settings.test import *\n' + '\n'.join(f'{key} = {value!r}' for key, value in overrides.items()) + '\n')
    pause = tmp_path / "inspection-pause.txt"
    entered = tmp_path / "inspection-entered.txt"
    pause.write_text("")
    with config.open("a") as stream:
        stream.write('from pathlib import Path\nimport time as _probe_time\nfrom apps.core import storage as _probe_summary\n')
        stream.write('_probe_inspect = _probe_summary.read_summary_artifact\n')
        stream.write(f'_probe_pause = Path({str(pause)!r})\n_probe_entered = Path({str(entered)!r})\n')
        stream.write(f'def _probe_inspection(studio, slug, name, **kwargs):\n    if slug == "probe" and _probe_pause.read_text() == "1":\n        _probe_entered.write_text("leased")\n        while _probe_pause.read_text() == "1":\n            _probe_time.sleep(.1)\n    return _probe_inspect(studio, slug, name, **kwargs)\n_probe_summary.read_summary_artifact = _probe_inspection\n')
    preserve = tmp_path / "preserve-success-metadata.txt"
    preserve.write_text("")
    with config.open("a") as stream:
        stream.write('from trellum import meta as _probe_meta\n_probe_error = _probe_meta.write_error\n')
        stream.write(f'_probe_preserve = Path({str(preserve)!r})\n')
        stream.write('def _probe_write_error(*args, **kwargs):\n    if _probe_preserve.read_text() != "1":\n        return _probe_error(*args, **kwargs)\n_probe_meta.write_error = _probe_write_error\n')
    fault = tmp_path / "publication-fault.txt"
    fault.write_text("")
    if backend == "s3":
        with config.open("a") as stream:
            stream.write('from pathlib import Path\nfrom apps.core import storage as _probe_storage\n')
            stream.write('_probe_put = _probe_storage._put_pointer\n')
            stream.write(f'_probe_fault = Path({str(fault)!r})\n')
            stream.write('def _probe_pointer(prefix, build):\n    mode = _probe_fault.read_text()\n    if mode == "before":\n        raise OSError("isolated before-pointer publication fault")\n    _probe_put(prefix, build)\n    if mode == "after":\n        raise OSError("isolated after-pointer publication fault")\n_probe_storage._put_pointer = _probe_pointer\n')
    config.chmod(0o600)
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ}
    env.update(DJANGO_SETTINGS_MODULE="source_live_settings", PYTHONPATH=f"{tmp_path}:{root}")
    processes = []
    logs = []
    def start():
        log = (tmp_path / f"worker-{backend}-{len(processes)}.log").open("w")
        logs.append(log)
        proc = subprocess.Popen([sys.executable, "manage.py", "runworker"], cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
        processes.append(proc)
        return proc
    def summary_ready(pk):
        return ReportSourceSummary.objects.filter(report_id=pk, state="ready").first()
    def build(expected):
        assert enqueue(report) == "queued"
        run = Run.objects.filter(report=report).latest("created_at")
        finished = wait_until(lambda: Run.objects.filter(pk=run.pk).exclude(status__in=Run.ACTIVE_STATUSES).first())
        assert finished.status == expected, finished.stderr_tail
        return finished
    evidence = {"backend": backend, "provider": "local" if backend == "local" else "disposable HTTP S3 protocol provider", "events": []}
    evidence["source_sha"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    evidence["source_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip())
    try:
        worker = start()
        historical = wait_until(lambda: summary_ready(old.pk))
        assert historical.claims.count() == 2000
        evidence["events"].append("automatic historical2000claim startup")
        historical_ab = wait_until(lambda: summary_ready(old_ab.pk))
        assert historical_ab.schema_version == 2 and historical_ab.runtime_state == "ready"
        assert historical_ab.has_output and historical_ab.html_entry == "index.html"
        assert historical_ab.experiment_state == "ready"
        assert historical_ab.experiments.get(ordinal=0).delta_pct == 4
        assert historical_ab.experiments.get(ordinal=1).payload_state == "missing"
        evidence["events"].append("automatic scoped declared AB and served runtime startup projection")
        success = build(Run.SUCCESS)
        summary = wait_until(lambda: summary_ready(report.pk))
        assert summary.claims.get(metric_name="m").version == 1
        assert summary.quality == "verified"
        assert summary.schema_version == 2 and summary.runtime_state == "ready"
        assert summary.has_output and summary.html_entry == "index.html"
        identity = summary.source_identity
        revision = summary.report.studio.metric_catalog_state.summary_revision
        evidence["events"].append("actual subprocess success and verified publication")

        # A second independent process sees the DB revision and claims directly.
        code = f'from apps.reports.models import ReportSourceSummary,MetricCatalogState;import json;s=ReportSourceSummary.objects.get(report_id={report.pk});print(json.dumps({{"identity":s.source_identity,"claims":s.claims.count(),"revision":MetricCatalogState.objects.get(studio_id={studio_tree.pk}).summary_revision}}))'
        other = subprocess.check_output([sys.executable, "manage.py", "shell", "-c", code], cwd=root, env=env, text=True)
        projection = json.loads(other.splitlines()[-1])
        assert projection["identity"] == identity and projection["claims"] == 1 and projection["revision"] >= revision
        evidence["events"].append("independent process generation and complete claims")

        if backend == "local":
            preserve.write_text("1")
            generator.write_text('from trellum import BaseReport\nfrom pathlib import Path\nimport os\nclass SummaryProbe(BaseReport):\n    def generate(self, ctx):\n        Path(ctx.output_dir, "index.html").write_text("partial isolated output")\n        os._exit(137)\n')
            build(Run.ERROR)
            wait_until(lambda: ReportSourceSummary.objects.filter(report=report, state="uncertain", attempt_status="error").first())
            assert json.loads((studio_tree.output_dir / "probe" / "_meta.json").read_text())["last_status"] == "success"
            assert ReportSourceSummary.objects.get(report=report).source_identity == identity
            evidence["events"].append("actual abrupt local partial write with suppressed error-metadata seam retains durable uncertainty")
            preserve.write_text("")

        generator.write_text('from trellum import BaseReport\nclass SummaryProbe(BaseReport):\n    def generate(self, ctx):\n        raise RuntimeError("deliberate isolated failure")\n')
        failure = build(Run.ERROR)
        state = "uncertain" if backend == "local" else "ready"
        failed_summary = wait_until(lambda: ReportSourceSummary.objects.filter(report=report, attempt_status="error", state=state).first())
        assert failed_summary.source_identity == identity
        assert failed_summary.claims.count() == 1
        assert failed_summary.attempt_at > summary.attempt_at
        evidence["events"].append("actual failure retains claims with distinct serving and attempt state")
        worker.terminate()
        worker.wait(timeout=15)
        worker = start()
        # Force due time rather than60s wait; only scheduling, not projection.
        ReportSourceSummary.objects.filter(report=report).update(next_check_at=timezone.now())
        wait_until(lambda: ReportSourceSummary.objects.filter(report=report, state=state, observed_at__gt=failed_summary.observed_at).first())
        evidence["events"].append("actual worker restart repairs without CLI")
        generator.write_text(GENERATOR)
        build(Run.SUCCESS)
        rebuilt = wait_until(lambda: ReportSourceSummary.objects.filter(report=report, state="ready", attempt_status="success").exclude(source_identity=identity).first())
        assert rebuilt.claims.count() == 1
        evidence["events"].append("successful rebuild restores verified current projection")
        # Kill during real background inspection after the lease committed.
        # Advance only lease expiry to avoid waiting180seconds in the suite.
        from datetime import timedelta
        pause.write_text("1")
        crash_run = build(Run.SUCCESS)
        wait_until(lambda: entered.exists())
        fenced = ReportSourceSummary.objects.get(report=report)
        assert fenced.state == "pending" and fenced.operation == str(crash_run.pk)
        assert fenced.lease_token and fenced.lease_until > timezone.now()
        worker.kill()
        worker.wait(timeout=10)
        ReportSourceSummary.objects.filter(report=report).update(lease_until=timezone.now() - timedelta(seconds=1))
        pause.write_text("")
        worker = start()
        resumed = wait_until(lambda: ReportSourceSummary.objects.filter(report=report, state="ready", operation=str(crash_run.pk)).exclude(source_identity=rebuilt.source_identity).first())
        assert resumed.quality == "verified" and resumed.claims.count() == 1
        evidence["events"].append("SIGKILL during leased pending projection; restart repairs automatically with explicit lease-expiry timing seam")
        rebuilt = resumed
        if backend == "s3":
            prior_identity = rebuilt.source_identity
            for mode, version in (("before", 2), ("after", 3)):
                fault.write_text(mode)
                (studio_tree.project_root / "metrics.yaml").write_text(f'version: 1\nmetrics:\n  - name: m\n    label: Metric M\n    description: An isolated descriptive metric.\n    version: {version}\n')
                sync_studio_registry(studio_tree)
                build(Run.ERROR)
                def boundary_summary():
                    candidate = ReportSourceSummary.objects.filter(report=report, state="ready", attempt_status="error").first()
                    if not candidate:
                        return None
                    matches = candidate.source_identity == prior_identity if mode == "before" else candidate.source_identity != prior_identity
                    return candidate if matches else None
                boundary = wait_until(boundary_summary)
                assert boundary.claims.get(metric_name="m").version == (1 if mode == "before" else version)
                evidence["events"].append(f"actual worker {mode}-pointer fault with authoritative uncached serving identity")
            fault.write_text("")
        # Real retention deletes worker-produced output and fences live claims.
        from apps.core import retention
        from datetime import timedelta
        settings.RETENTION = {**settings.RETENTION, "built_data_days": 1}
        Report.objects.filter(pk=report.pk).update(last_built_at=timezone.now() - timedelta(days=2))
        assert retention.purge_built_data()[1] == 1
        report.refresh_from_db()
        assert report.data_expired_at is not None
        expired = ReportSourceSummary.objects.get(report=report)
        assert expired.state == "expired" and expired.claims.count() == 1
        if backend == "s3":
            assert storage.summary_build_identity(studio_tree, report.slug) == ""
        else:
            assert not (studio_tree.output_dir / "probe").exists()
        evidence["events"].append("real retention expiry deletes output and atomically invalidates live projection")
        (tmp_path / f"result-{backend}.json").write_text(json.dumps(evidence, indent=2))
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        for log in logs:
            log.close()
        if backend == "s3":
            client = s3_client()
            objects = client.list_objects_v2(Bucket=settings.TRELLUM_REPORTS_BUCKET).get("Contents", [])
            if objects:
                client.delete_objects(Bucket=settings.TRELLUM_REPORTS_BUCKET, Delete={"Objects": [{"Key": item["Key"]} for item in objects]})
            client.delete_bucket(Bucket=settings.TRELLUM_REPORTS_BUCKET)
