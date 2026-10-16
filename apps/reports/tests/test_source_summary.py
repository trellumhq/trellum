"""Source projection semantics, fencing, and bounded historical reconciliation."""
import json
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.reports.models import MetricCatalogState, Report, ReportSourceSummary
from apps.reports.source_reconciliation import run_batch
from apps.reports.source_summary import (
    MAX_IDLE_SECONDS, META_BYTES, acquire, begin_attempt, begin_expiry, definitions_changed,
    finish_attempt, finish_expiry, invalidate_reports, reconcile,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def isolated_mocked_remote_output_facts(monkeypatch):
    """These remote cases mock artifact bodies; facts share that fake boundary.

    Real HTTP/provider publication is exercised in test_source_summary_live.
    Never discover ambient SDK credentials for an otherwise mocked observation.
    """
    from apps.core import storage
    original = storage.summary_output_facts

    def facts(studio, slug, **kwargs):
        if storage.is_remote():
            return dict(has_output=False, html_entry='', has_live_queries=False,
                        output_digest='0' * 64)
        return original(studio, slug, **kwargs)

    monkeypatch.setattr(storage, 'summary_output_facts', facts)
    original_stamp = storage.summary_change_stamp
    monkeypatch.setattr(storage, 'summary_change_stamp',
                        lambda *args: None if storage.is_remote() else original_stamp(*args))


def report(studio_tree, slug="one", meta=None):
    row = Report.objects.create(studio=studio_tree, slug=slug)
    path = studio_tree.output_dir / slug
    path.mkdir(parents=True, exist_ok=True)
    if meta is not None:
        (path / "_meta.json").write_text(json.dumps(meta))
    return row


def metadata(*names, status="success"):
    return {"last_status": status, "last_run": timezone.now().isoformat(), "metrics_used": list(names)}


def due(row):
    ReportSourceSummary.objects.filter(report=row).update(next_check_at=timezone.now() - timedelta(seconds=1))


def test_historical_complete_dense_claims_and_generation(studio_tree):
    row = report(studio_tree, meta=metadata(*(f"m{i}" for i in range(2000))))
    result = run_batch()
    assert result["results"][0]["status"] == "ready"
    summary = ReportSourceSummary.objects.get(report=row)
    assert summary.quality == "observed"
    assert summary.claims.count() == 2000
    assert summary.claims.filter(metric_name="m1999", definition_hash=None, version=None).exists()
    assert MetricCatalogState.objects.get(studio=studio_tree).summary_revision == 1


def test_failure_retains_history_without_current_claim(studio_tree):
    row = report(studio_tree, meta=metadata("old"))
    reconcile(row.pk)
    begin_attempt(row.pk, "not-a-run")
    (studio_tree.output_dir / row.slug / "_meta.json").write_text(json.dumps(metadata("partial", status="error")))
    finish_attempt(row.pk, "not-a-run", "error")
    assert reconcile(row.pk)["status"] == "uncertain"
    assert list(row.source_summary.claims.values_list("metric_name", flat=True)) == ["old"]


def test_no_transaction_spans_storage_io(studio_tree, monkeypatch):
    from apps.core import storage
    original = storage.read_summary_artifact
    row = report(studio_tree, meta=metadata("m"))
    def observed(*args, **kwargs):
        assert not connection.in_atomic_block
        return original(*args, **kwargs)
    monkeypatch.setattr(storage, "read_summary_artifact", observed)
    assert reconcile(row.pk)["status"] == "ready"


def test_new_attempt_supersedes_inflight_observation(studio_tree, monkeypatch):
    from apps.reports import source_summary
    row = report(studio_tree, meta=metadata("old"))
    original = source_summary.inspect_source
    def race(*args):
        result = original(*args)
        begin_attempt(row.pk, "next")
        return result
    monkeypatch.setattr(source_summary, "inspect_source", race)
    assert reconcile(row.pk)["status"] == "superseded"
    assert row.source_summary.claims.count() == 0
    assert row.source_summary.state == "pending"


def test_oversized_and_malformed_never_empty_verified(studio_tree):
    row = report(studio_tree, meta=metadata("old"))
    reconcile(row.pk)
    due(row)
    path = studio_tree.output_dir / row.slug / "_meta.json"
    path.write_bytes(b"x" * (META_BYTES + 1))
    assert reconcile(row.pk)["status"] == "unavailable"
    assert row.source_summary.claims.count() == 1
    assert row.source_summary.failures == 1
    due(row)
    path.write_bytes(b"{")
    assert reconcile(row.pk)["status"] == "unavailable"
    row.refresh_from_db()
    assert row.source_summary.failures == 2


def test_batch_count_pool_and_provider_backoff(studio_tree):
    for i in range(30):
        report(studio_tree, str(i))
    assert run_batch(pools=["large"])["inspections"] == 0
    result = run_batch()
    assert result["inspections"] == 25
    assert run_batch()["inspections"] == 5
    with pytest.raises(ValueError):
        run_batch(limit=26)


def test_expiry_blocks_new_attempt_and_restart_repairs(studio_tree):
    row = report(studio_tree, meta=metadata("old"))
    reconcile(row.pk)
    operation = begin_expiry(row.pk)
    with pytest.raises(RuntimeError):
        begin_attempt(row.pk, "racing")
    assert acquire(row.pk) is None
    (studio_tree.output_dir / row.slug / "_meta.json").unlink()
    ReportSourceSummary.objects.filter(report=row).update(lease_until=timezone.now() - timedelta(seconds=1), next_check_at=timezone.now())
    assert reconcile(row.pk)["status"] == "expired"
    row.refresh_from_db()
    assert row.data_expired_at is not None
    assert row.source_summary.claims.count() == 1  # historical, not live


def test_expiry_marker_and_removed_generation(studio_tree):
    row = report(studio_tree, meta=metadata("m"))
    reconcile(row.pk)
    token = begin_expiry(row.pk)
    stamp = timezone.now()
    assert finish_expiry(row.pk, token, expired_at=stamp)
    row.refresh_from_db()
    assert row.data_expired_at == stamp
    assert row.source_summary.state == "expired"
    invalidate_reports([row.pk], "removed")
    row.refresh_from_db()
    assert row.source_summary.state == "removed"
    definitions_changed(studio_tree.pk)
    assert MetricCatalogState.objects.get(studio=studio_tree).definition_revision == 1


def test_local_gzip_decoded_limit(studio_tree):
    import gzip
    from apps.core.storage import read_summary_artifact
    row = report(studio_tree)
    path = studio_tree.output_dir / row.slug / "_meta.json"
    raw = json.dumps(metadata("m")).encode()
    path.write_bytes(gzip.compress(raw))
    assert read_summary_artifact(studio_tree, row.slug, "_meta.json", max_bytes=1024) == raw
    path.write_bytes(gzip.compress(b"x" * 1025))
    with pytest.raises(ValueError, match="decoded"):
        read_summary_artifact(studio_tree, row.slug, "_meta.json", max_bytes=1024)
    path.write_bytes(b"x" * 1025)
    with pytest.raises(ValueError, match="encoded"):
        read_summary_artifact(studio_tree, row.slug, "_meta.json", max_bytes=1024)


def test_missing_historical_rows_prioritized_over_due_existing(studio_tree):
    old = report(studio_tree, "existing", metadata("m"))
    reconcile(old.pk)
    due(old)
    new = report(studio_tree, "new", metadata("m"))
    assert run_batch(limit=1)["results"][0]["report_id"] == new.pk
    assert run_batch(limit=1)["results"][0]["report_id"] == old.pk


def test_generated_preview_is_bounded_and_canonical(studio_tree):
    row = report(studio_tree, "metrics", metadata("m"))
    row.tags = ["metrics"]
    row.save()
    data = {"components": {"c": {"type": "chart", "dataset_id": "d", "x": "date"}},
            "_metrics": {"m": {"agg": "sum", "column": "value", "component_ids": ["c"]}},
            "_ds_d": {"_cols": ["date", "value"], "_data": [[f"{i:03}", i] for i in range(100)]}}
    (studio_tree.output_dir / "metrics" / "data.json").write_text(json.dumps(data))
    assert reconcile(row.pk)["status"] == "ready"
    summary = row.source_summary
    points = summary.previews.get(metric_name="m").points.split()
    assert len(points) == 60
    assert points[0] == "0.0,34.0"
    assert points[-1] == "140.0,2.0"
    assert summary.preview_state == "ready"


def test_remote_missing_immutable_meta_is_unavailable(studio_tree, monkeypatch):
    from apps.core import storage
    row = report(studio_tree)
    monkeypatch.setattr(storage, "is_remote", lambda: True)
    monkeypatch.setattr(storage, "summary_build_identity", lambda *args: "published")
    monkeypatch.setattr(storage, "read_summary_artifact", lambda *args, **kwargs: None)
    assert reconcile(row.pk)["status"] == "unavailable"
    assert row.source_summary.state == "unavailable"
    assert row.source_summary.claims.count() == 0


def test_remote_served_identity_not_mutable_attempt(studio_tree, monkeypatch):
    from apps.core import storage
    row = report(studio_tree)
    served = metadata("served")
    attempt = metadata("partial", status="error")
    monkeypatch.setattr(storage, "is_remote", lambda: True)
    monkeypatch.setattr(storage, "summary_build_identity", lambda *args: "published")
    def read(*args, **kwargs):
        return json.dumps(attempt if kwargs.get("mutable_status") else served).encode()
    monkeypatch.setattr(storage, "read_summary_artifact", read)
    assert reconcile(row.pk)["status"] == "ready"
    summary = row.source_summary
    assert summary.source_identity == "published"
    assert summary.served_at < summary.attempt_at
    assert summary.attempt_status == "error"
    assert list(summary.claims.values_list("metric_name", flat=True)) == ["served"]


def test_remote_pointer_race_never_commits_mixed_claims(studio_tree, monkeypatch):
    from apps.core import storage
    row = report(studio_tree)
    monkeypatch.setattr(storage, "is_remote", lambda: True)
    pointers = iter(["old", "new"])
    monkeypatch.setattr(storage, "summary_build_identity", lambda *args: next(pointers))
    monkeypatch.setattr(storage, "read_summary_artifact", lambda *args, **kwargs: json.dumps(metadata("old")).encode())
    assert reconcile(row.pk)["status"] == "unavailable"
    assert row.source_summary.claims.count() == 0


def test_partial_expiry_failure_stays_uncertain_until_success(studio_tree):
    row = report(studio_tree, meta=metadata("old"))
    reconcile(row.pk)
    token = begin_expiry(row.pk)
    finish_expiry(row.pk, token)  # deletion failed after touching some assets
    assert reconcile(row.pk)["status"] == "uncertain"
    assert row.source_summary.operation.startswith("expiry:")
    due(row)
    assert reconcile(row.pk)["status"] == "uncertain"
    begin_attempt(row.pk, "new-run")
    (studio_tree.output_dir / row.slug / "_meta.json").write_text(json.dumps(metadata("new")))
    finish_attempt(row.pk, "new-run", "success")
    assert reconcile(row.pk)["status"] == "ready"
    assert list(row.source_summary.claims.values_list("metric_name", flat=True)) == ["new"]


def test_active_runs_do_not_starve_missing_catalog(studio_tree):
    from apps.runner.models import Run
    for i in range(25):
        row = report(studio_tree, f"busy{i}")
        Run.objects.create(report=row, studio=studio_tree, slug=row.slug, status=Run.RUNNING)
    idle = report(studio_tree, "idle", metadata("m"))
    assert run_batch(limit=1)["results"][0]["report_id"] == idle.pk


def test_failed_local_attempt_with_unchanged_success_meta_is_uncertain(studio_tree):
    row = report(studio_tree, meta=metadata("old"))
    reconcile(row.pk)
    begin_attempt(row.pk, "failed-run")
    # Subprocess can mutate some assets before it updates successful metadata.
    finish_attempt(row.pk, "failed-run", "error")
    assert reconcile(row.pk)["status"] == "uncertain"
    assert row.source_summary.claims.get().metric_name == "old"
    assert row.source_summary.quality == "observed"


def test_concurrent_inspectors_take_one_lease(studio_tree):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections
    row = report(studio_tree, meta=metadata("m"))
    # Make row creation deterministic; race the actual PostgreSQL lease locks.
    ReportSourceSummary.objects.create(report=row, next_check_at=timezone.now())
    barrier = threading.Barrier(2)
    def claim():
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            return acquire(row.pk)
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sum(result is not None for result in results) == 1


def test_batch_byte_and_time_admission(studio_tree, monkeypatch):
    from apps.reports import source_reconciliation as service
    for i in range(3):
        report(studio_tree, str(i))
    monkeypatch.setattr(service, "reconcile", lambda pk: {"status": "ready", "bytes": service.MAX_INSPECTION_BYTES})
    bounded = service.run_batch()
    assert bounded["inspections"] == 1
    assert bounded["bytes"] <= service.BATCH_BYTES
    clock = iter([0, 0, 6, 6])
    monkeypatch.setattr(service.time, "monotonic", lambda: next(clock))
    timed = service.run_batch()
    assert timed["inspections"] == 1
    assert timed["seconds"] == 6  # admission window, never falsely a hard5s cap


def test_schema_upgrade_preserves_reports_and_is_reversible(studio_tree):
    from django.db.migrations.executor import MigrationExecutor
    row = report(studio_tree)
    try:
        MigrationExecutor(connection).migrate([("reports", "0026_report_idle_refresh")])
        assert "reports_reportsourcesummary" not in connection.introspection.table_names()
        assert Report.objects.filter(pk=row.pk).exists()
        MigrationExecutor(connection).migrate([("reports", "0027_source_summaries")])
        assert "reports_reportsourcesummary" in connection.introspection.table_names()
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes("reports"))
    assert Report.objects.filter(pk=row.pk).exists()
    assert ReportSourceSummary.objects.count() == 0  # automatic worker bootstrap


def test_unchanged_projection_has_no_claim_or_preview_writes(studio_tree):
    from django.test.utils import CaptureQueriesContext
    row = report(studio_tree, meta=metadata(*(f"m{i}" for i in range(2000))))
    reconcile(row.pk)
    first_pk = row.source_summary.claims.get(metric_name="m0").pk
    revision = MetricCatalogState.objects.get(studio=studio_tree).summary_revision
    due(row)
    with CaptureQueriesContext(connection) as captured:
        assert reconcile(row.pk)["status"] == "ready"
    writes = [item["sql"] for item in captured if item["sql"].lstrip().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert not any("reports_reportmetricclaim" in query or "reports_reportmetricpreview" in query for query in writes)
    assert row.source_summary.claims.get(metric_name="m0").pk == first_pk
    assert MetricCatalogState.objects.get(studio=studio_tree).summary_revision == revision
    (studio_tree.output_dir / row.slug / "_meta.json").write_text(json.dumps(metadata("changed")))
    due(row)
    assert reconcile(row.pk)["status"] == "ready"
    assert list(row.source_summary.claims.values_list("metric_name", flat=True)) == ["changed"]


def test_unchanged_remote_generated_build_skips_preview_io(studio_tree, monkeypatch):
    from apps.core import storage
    row = report(studio_tree, "metrics")
    row.tags = ["metrics"]
    row.save()
    calls = []
    # A current workspace: a legacy one would queue its automatic rebuild, and
    # reconciliation then waits for that build.
    meta = {**metadata("m"), "metrics_workspace": {"version": 1}}
    data = {"components": {"c": {"type": "chart", "dataset_id": "d", "x": "date"}},
            "_metrics": {"m": {"agg": "sum", "column": "value", "component_ids": ["c"]}},
            "_ds_d": {"_cols": ["date", "value"], "_data": [["a", 1], ["b", 2]]}}
    monkeypatch.setattr(storage, "is_remote", lambda: True)
    monkeypatch.setattr(storage, "summary_build_identity", lambda *args: "published")
    def read(studio, slug, name, **kwargs):
        calls.append(name)
        return json.dumps(data if name == "data.json" else meta).encode()
    monkeypatch.setattr(storage, "read_summary_artifact", read)
    assert reconcile(row.pk)["status"] == "ready"
    first = row.source_summary.previews.get(metric_name="m").pk
    assert calls.count("data.json") == 1
    due(row)
    assert reconcile(row.pk)["status"] == "ready"
    assert calls.count("data.json") == 1
    assert row.source_summary.previews.get(metric_name="m").pk == first


@pytest.mark.parametrize("version", [-1, 2147483648])
def test_invalid_schema_bound_claim_version_backoff(studio_tree, version):
    row = report(studio_tree, meta=metadata("old"))
    reconcile(row.pk)
    bad = metadata({"id": "bad", "version": version})
    (studio_tree.output_dir / row.slug / "_meta.json").write_text(json.dumps(bad))
    due(row)
    assert reconcile(row.pk)["status"] == "unavailable"
    assert row.source_summary.failures == 1
    assert row.source_summary.next_check_at > timezone.now()
    assert row.source_summary.claims.get().metric_name == "old"


@pytest.mark.parametrize("build", ["../escape", "x" * 301])
def test_invalid_remote_pointer_schema_bounds_backoff(studio_tree, monkeypatch, build):
    from apps.core import storage
    row = report(studio_tree)
    monkeypatch.setattr(storage, "is_remote", lambda: True)
    monkeypatch.setattr(storage, "read_summary_artifact", lambda *args, **kwargs: json.dumps({"build": build}).encode())
    assert reconcile(row.pk)["status"] == "unavailable"
    assert row.source_summary.failures == 1
    assert row.source_summary.next_check_at > timezone.now()


@pytest.mark.parametrize("invalid", [0, False, "", {}, None], ids=["zero", "false", "empty-string", "empty-object", "null"])
@pytest.mark.parametrize("prior_claims", [False, True], ids=["fresh", "previous-ready"])
def test_explicit_falsey_claim_values_fail_closed(studio_tree, invalid, prior_claims):
    original = metadata("old")
    malformed = metadata()
    malformed["metrics_used"] = invalid
    row = report(studio_tree, meta=original if prior_claims else malformed)
    previous_identity = previous_digest = ""
    if prior_claims:
        assert reconcile(row.pk)["status"] == "ready"
        previous_identity = row.source_summary.source_identity
        previous_digest = row.source_summary.claim_digest
        (studio_tree.output_dir / row.slug / "_meta.json").write_text(json.dumps(malformed))
        due(row)
    assert reconcile(row.pk)["status"] == "unavailable"
    summary = ReportSourceSummary.objects.get(report=row)
    assert summary.state == "unavailable"
    assert summary.failures == 1
    assert summary.next_check_at > timezone.now()
    assert summary.source_identity == previous_identity
    assert summary.claim_digest == previous_digest
    assert list(summary.claims.values_list("metric_name", flat=True)) == (["old"] if prior_claims else [])
    assert summary.quality == ("observed" if prior_claims else "unknown")


@pytest.mark.parametrize("legacy_missing", [True, False], ids=["missing-legacy-key", "valid-empty-list"])
@pytest.mark.parametrize("prior_claims", [False, True], ids=["fresh", "previous-ready"])
def test_legacy_missing_claims_and_valid_empty_list_remain_valid(studio_tree, legacy_missing, prior_claims):
    empty = metadata()
    if legacy_missing:
        del empty["metrics_used"]
    row = report(studio_tree, meta=metadata("old") if prior_claims else empty)
    if prior_claims:
        assert reconcile(row.pk)["status"] == "ready"
        (studio_tree.output_dir / row.slug / "_meta.json").write_text(json.dumps(empty))
        due(row)
    assert reconcile(row.pk)["status"] == "ready"
    summary = ReportSourceSummary.objects.get(report=row)
    assert summary.quality == "observed"
    assert summary.failures == 0
    assert summary.claims.count() == 0


def test_remote_unbuilt_declaration_is_missing_without_data_read(studio_tree, monkeypatch):
    from apps.core import storage
    row = report(studio_tree, 'unbuilt-ab')
    row.config = {'ab_test': {}}
    row.save(update_fields=['config'])
    reads = []
    monkeypatch.setattr(storage, 'is_remote', lambda: True)
    monkeypatch.setattr(storage, 'summary_build_identity', lambda *args: '')

    def read(*args, **kwargs):
        reads.append(args[2])
        assert args[2] != 'data.json', 'No serving pointer: never read unpublished data'
        return None

    monkeypatch.setattr(storage, 'read_summary_artifact', read)
    result = reconcile(row.pk)
    summary = row.source_summary
    assert result['status'] == 'missing' and not result['projection_unavailable']
    assert summary.experiment_state == 'missing' and not summary.has_output
    assert summary.experiments.get().payload_state == 'missing'
    assert reads == ['_meta.json']


def test_schedule_longer_than_the_idle_cap_is_due(studio_tree):
    """Rows scheduled under an older, longer cap are re-observed instead of ageing into unknown."""
    row = report(studio_tree, meta=metadata("m"))
    reconcile(row.pk)
    ReportSourceSummary.objects.filter(report=row).update(next_check_at=timezone.now() + timedelta(seconds=3000), idle_checks=8)
    assert run_batch()["inspections"] == 1
    assert (ReportSourceSummary.objects.get(report=row).next_check_at - timezone.now()).total_seconds() <= MAX_IDLE_SECONDS
    # A failure back-off is a deliberate wait and stays untouched.
    ReportSourceSummary.objects.filter(report=row).update(next_check_at=timezone.now() + timedelta(seconds=3000), failures=2)
    assert run_batch()["inspections"] == 0


def test_unchanged_source_backs_off_and_change_resets(studio_tree):
    row = report(studio_tree, meta=metadata("m"))
    reconcile(row.pk)
    delays = []
    for _ in range(4):
        due(row)
        assert reconcile(row.pk)["status"] == "ready"
        summary = ReportSourceSummary.objects.get(report=row)
        delays.append((summary.next_check_at - timezone.now()).total_seconds())
    assert delays[0] < delays[-1] and all(b >= a - 1 for a, b in zip(delays, delays[1:]))
    # Capped inside the readers' five-minute freshness window.
    assert delays[-1] <= MAX_IDLE_SECONDS + 1 and delays[-1] > MAX_IDLE_SECONDS - 5
    # Idle catalog: nothing is eligible until the backed-off time.
    assert run_batch()["inspections"] == 0
    (studio_tree.output_dir / row.slug / "_meta.json").write_text(json.dumps(metadata("changed")))
    due(row)
    reconcile(row.pk)
    summary = ReportSourceSummary.objects.get(report=row)
    assert summary.idle_checks == 0
    assert (summary.next_check_at - timezone.now()).total_seconds() < 90
    # A new run resets the backoff immediately.
    due(row)
    reconcile(row.pk)
    due(row)
    reconcile(row.pk)
    assert ReportSourceSummary.objects.get(report=row).idle_checks == 2
    begin_attempt(row.pk, "op-1")
    assert ReportSourceSummary.objects.get(report=row).idle_checks == 0


def settle_mtimes(studio_tree, slug, age=3600):
    """Make the report's files old enough that a stamp is trusted (not racily clean)."""
    import os
    root = studio_tree.output_dir / slug
    stamp = timezone.now().timestamp() - age
    for path in [*root.iterdir(), root]:
        os.utime(path, (stamp, stamp))


@pytest.fixture
def inspections(monkeypatch):
    """Count full artifact inspections."""
    from apps.reports import source_summary
    calls = []
    original = source_summary.inspect_source
    monkeypatch.setattr(source_summary, "inspect_source", lambda *args: calls.append(args[0].pk) or original(*args))
    return calls


def test_unchanged_local_source_is_confirmed_without_reading_it(studio_tree, inspections):
    row = report(studio_tree, meta=metadata("m"))
    settle_mtimes(studio_tree, row.slug)
    assert reconcile(row.pk)["status"] == "ready"
    first = ReportSourceSummary.objects.get(report=row)
    revision = MetricCatalogState.objects.get(studio=studio_tree).summary_revision
    assert inspections == [row.pk] and first.change_stamp
    ReportSourceSummary.objects.filter(report=row).update(next_check_at=timezone.now() - timedelta(seconds=1),
                                                         observed_at=timezone.now() - timedelta(seconds=170))
    result = reconcile(row.pk)
    assert result["status"] == "ready" and result["bytes"] == 0 and result["unchanged"]
    assert inspections == [row.pk]  # nothing was read again
    again = ReportSourceSummary.objects.get(report=row)
    # Still fresh for every reader's five-minute window, still the same claims and schedule rules.
    assert again.observed_at > timezone.now() - timedelta(seconds=5)
    assert again.state == "ready" and again.idle_checks == 1 and again.failures == 0
    assert again.lease_token == "" and again.lease_until is None
    assert 0 < (again.next_check_at - timezone.now()).total_seconds() <= MAX_IDLE_SECONDS
    assert list(again.claims.values_list("metric_name", flat=True)) == ["m"]
    assert MetricCatalogState.objects.get(studio=studio_tree).summary_revision == revision


def test_recently_written_source_is_read_in_full_until_it_settles(studio_tree, inspections):
    row = report(studio_tree, meta=metadata("m"))  # written just now: inside the racy window
    reconcile(row.pk)
    due(row)
    reconcile(row.pk)
    assert inspections == [row.pk, row.pk]


def test_changed_source_invalidates_the_stamp(studio_tree, inspections):
    row = report(studio_tree, meta=metadata("old"))
    settle_mtimes(studio_tree, row.slug)
    reconcile(row.pk)
    (studio_tree.output_dir / row.slug / "_meta.json").write_text(json.dumps(metadata("new", "another")))
    settle_mtimes(studio_tree, row.slug, age=1800)
    due(row)
    reconcile(row.pk)
    assert inspections == [row.pk, row.pk]
    assert sorted(row.source_summary.claims.values_list("metric_name", flat=True)) == ["another", "new"]
    # A new output file changes the directory's stamp as well.
    settle_mtimes(studio_tree, row.slug)
    due(row)
    reconcile(row.pk)
    assert len(inspections) == 3
    due(row)
    reconcile(row.pk)
    assert len(inspections) == 3
    (studio_tree.output_dir / row.slug / "index.html").write_text("<html></html>")
    due(row)
    reconcile(row.pk)
    assert len(inspections) == 4 and ReportSourceSummary.objects.get(report=row).has_output


def test_new_attempt_or_changed_declaration_forces_a_full_read(studio_tree, inspections):
    row = report(studio_tree, meta=metadata("m"))
    settle_mtimes(studio_tree, row.slug)
    reconcile(row.pk)
    due(row)
    reconcile(row.pk)
    assert len(inspections) == 1
    begin_attempt(row.pk, "op-1")
    finish_attempt(row.pk, "op-1", "success")
    reconcile(row.pk)
    assert len(inspections) == 2
    settle_mtimes(studio_tree, row.slug, age=1800)
    due(row)
    reconcile(row.pk)  # re-stamped after the racy pass
    due(row)
    reconcile(row.pk)
    count = len(inspections)
    row.config = {"ab_test": [{"test_name": "t", "start_date": "2026-01-01"}]}
    row.save()
    due(row)
    reconcile(row.pk)
    assert len(inspections) == count + 1


def test_failed_or_unsettled_inspections_are_never_confirmed_by_stamp(studio_tree, inspections):
    row = report(studio_tree, meta=metadata("m"))
    (studio_tree.output_dir / row.slug / "_meta.json").write_text("{not json")
    settle_mtimes(studio_tree, row.slug)
    assert reconcile(row.pk)["status"] == "unavailable"
    assert ReportSourceSummary.objects.get(report=row).change_stamp is None
    due(row)
    reconcile(row.pk)
    assert len(inspections) == 2


def test_unchanged_remote_build_is_confirmed_from_etags(studio_tree, monkeypatch):
    from apps.core import storage
    row = report(studio_tree)
    reads = []
    etags = {"value": '["\\"a\\"", "\\"b\\""]'}
    monkeypatch.setattr(storage, "is_remote", lambda: True)
    monkeypatch.setattr(storage, "summary_change_stamp", lambda *args: (etags["value"], None))
    monkeypatch.setattr(storage, "summary_build_identity", lambda *args: reads.append("pointer") or "published")
    monkeypatch.setattr(storage, "read_summary_artifact",
                        lambda *args, **kwargs: reads.append(args[2]) or json.dumps(metadata("m")).encode())
    assert reconcile(row.pk)["status"] == "ready"
    full = list(reads)
    assert "pointer" in full and "_meta.json" in full
    for _ in range(3):
        due(row)
        assert reconcile(row.pk)["unchanged"]
    assert reads == full  # no pointer or artifact read at all
    etags["value"] = '["\\"c\\"", "\\"b\\""]'  # the serving pointer moved
    due(row)
    result = reconcile(row.pk)
    assert result["status"] == "ready" and "unchanged" not in result and len(reads) > len(full)
