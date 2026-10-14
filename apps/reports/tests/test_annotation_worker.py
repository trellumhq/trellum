"""Workspace identity proof; PostgreSQL races require the actual worker gate."""
import tempfile
from pathlib import Path
from django.test import SimpleTestCase, override_settings
from apps.reports.annotation_reconciliation import WorkspaceUnavailable, workspace_identity


class WorkspaceIdentityTests(SimpleTestCase):
    def test_restart_retains_identity(self):
        with tempfile.TemporaryDirectory() as root, override_settings(DATA_DIR=root):
            first = workspace_identity()
            self.assertEqual(workspace_identity(), first)
            self.assertEqual((Path(root) / ".annotation-workspace").stat().st_mode & 0o777, 0o600)

    def test_corrupt_marker_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as root, override_settings(DATA_DIR=root):
            marker = Path(root) / ".annotation-workspace"
            marker.write_text("corrupt")
            with self.assertRaises(WorkspaceUnavailable):
                workspace_identity()
            self.assertEqual(marker.read_text(), "corrupt")


# Forks exercise real database leases and filesystem reads in separate processes.
# Child targets are module-level to keep ownership of their connections explicit.
def _marker_process(root, ready, results):
    from django.conf import settings
    settings.DATA_DIR = root
    ready.wait(10)
    try:
        results.put(("ok", workspace_identity()))
    except Exception as exc:
        results.put(("error", type(exc).__name__))


def _observer_process(root, source_id, channel, *, block=False, renewal_interval=None):
    from django.conf import settings
    from django.db import connections
    from apps.reports import annotation_reconciliation as reconciliation
    settings.DATA_DIR = root
    connections.close_all()
    if renewal_interval is not None:
        reconciliation.LEASE_RENEW_SECONDS = renewal_interval
    original = reconciliation._read
    def intercepted(path):
        value = original(path)
        channel.send("read")
        if not channel.poll(15):
            raise RuntimeError("test parent did not release observation")
        channel.recv()
        return value
    try:
        if block:
            reconciliation._read = intercepted
        reconciliation.observe(source_id)
        channel.send("done")
    except Exception as exc:
        channel.send(("error", type(exc).__name__))
    finally:
        connections.close_all()
        channel.close()


def _actual_worker_process(root, channel, *, block=False):
    from django.conf import settings
    from django.db import connections
    from apps.reports import annotation_reconciliation as reconciliation
    settings.DATA_DIR = root
    connections.close_all()
    original = reconciliation._read
    def intercepted(path):
        value = original(path)
        channel.send("read")
        if not channel.poll(20):
            raise RuntimeError("test worker was not released")
        channel.recv()
        return value
    if block:
        reconciliation._read = intercepted
    channel.send("started")
    thread = reconciliation.start_for_worker(is_runner=True, pools=["standard"])
    try:
        if channel.poll(30):
            channel.recv()
    finally:
        thread.stop()
        connections.close_all()
        channel.close()


def _registration_process(root, studio_id, ready, results):
    from django.conf import settings
    from django.db import connections
    from apps.studios.models import Studio
    from apps.runner.gitsync import studio_sync_lock
    from apps.reports.annotation_reconciliation import authoritative_handoff
    settings.DATA_DIR = root
    connections.close_all()
    ready.wait(10)
    try:
        studio = Studio.objects.get(pk=studio_id)
        with studio_sync_lock(studio_id) as locked:
            won = authoritative_handoff(studio, unowned_only=True) if locked else False
        results.put(("ok", won))
    except Exception as exc:
        results.put(("error", type(exc).__name__))
    finally:
        connections.close_all()


import multiprocessing
import os
import signal
import time
from datetime import timedelta
from unittest.mock import patch
from django.db import connection, connections
from django.test import TransactionTestCase
from django.utils import timezone
from apps.orgs.models import Organization
from apps.studios.models import Studio, StudioRepo
from apps.reports.models import AnnotationCatalogState, AnnotationEvent, AnnotationSourceState, Report
from apps.reports.annotation_projection import sync_report_declarations
from apps.reports.annotation_reconciliation import (
    AnnotationThread, authoritative_handoff, backfill_reports, observe, proven_git_upgrade,
)
from apps.runner.gitsync import studio_sync_lock


class AnnotationPostgresTests(TransactionTestCase):
    """These gates intentionally require PostgreSQL, not SQLite emulation."""
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("actual PostgreSQL annotation gate")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root_a = Path(self.temporary.name) / "a"
        self.root_b = Path(self.temporary.name) / "b"
        self.root_a.mkdir()
        self.root_b.mkdir()
        self.settings_override = override_settings(DATA_DIR=str(self.root_a))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.org = Organization.objects.create(slug="annotation-gate", name="Annotation gate")
        self.studio = Studio.objects.create(org=self.org, slug="studio", name="Studio")
        self.studio.project_root.mkdir(parents=True)
        self.path = self.studio.project_root / "events.yaml"
        self.write("first")
        self.handoff()
        self.source = AnnotationSourceState.objects.get(studio=self.studio, kind="global")
        observe(self.source.pk)
        self.context = multiprocessing.get_context("fork")
        self.children = []
        self.addCleanup(self.reap)

    def reap(self):
        for child in self.children:
            if child.is_alive():
                child.kill()
            child.join(5)

    def write(self, label, *, path=None):
        (path or self.path).write_text(f'events: [{{date: 1999-01-01, label: {label}}}]\n')

    def handoff(self, *, unowned_only=False):
        with studio_sync_lock(self.studio.pk) as locked:
            self.assertTrue(locked)
            return authoritative_handoff(self.studio, unowned_only=unowned_only)

    def source_state(self):
        self.source.refresh_from_db()
        return self.source

    def labels(self):
        return list(AnnotationEvent.objects.filter(source=self.source).order_by("ordinal").values_list("label", flat=True))

    def launch(self, target, *args, **kwargs):
        connections.close_all()
        child = self.context.Process(target=target, args=args, kwargs=kwargs)
        child.start()
        self.children.append(child)
        return child

    def receive(self, channel, expected):
        self.assertTrue(channel.poll(20), f"child did not send {expected}")
        self.assertEqual(channel.recv(), expected)

    def test_atomic_publication_rollback_preserves_previous_relation(self):
        self.write("second")
        previous = self.source_state().digest
        with patch("apps.reports.models.AnnotationEvent.objects.bulk_create", side_effect=RuntimeError("injected insert failure")):
            with self.assertRaises(RuntimeError):
                observe(self.source.pk)
        self.assertEqual(self.labels(), ["first"])
        self.assertEqual(self.source_state().digest, previous)
        self.assertIsNotNone(self.source.lease_until)

    def test_unchanged_observation_only_refreshes_freshness(self):
        catalog = AnnotationCatalogState.objects.get(studio=self.studio)
        revision = catalog.revision
        generation = self.source_state().published_generation
        observe(self.source.pk)
        catalog.refresh_from_db()
        self.assertEqual(catalog.revision, revision)
        self.assertEqual(self.source_state().published_generation, generation)

    def test_manual_defaults_empty_delete_recreate_preserve_report_source(self):
        report = Report.objects.create(studio=self.studio, slug="report", name="Report", config={"annotations": {"events": [{"date": "2020-01-01", "label": "report"}]}})
        sync_report_declarations(report)
        self.path.write_text('type_defaults: {release: false}\nevents: [{date: 1999-01-01, label: changed}]')
        observe(self.source.pk)
        self.assertFalse(AnnotationEvent.objects.get(source=self.source).visible_default)
        self.path.write_text("events: []")
        observe(self.source.pk)
        self.assertEqual(self.labels(), [])
        self.path.unlink()
        observe(self.source.pk)
        self.assertEqual(self.source_state().status, "missing")
        self.write("recreated")
        observe(self.source.pk)
        self.assertEqual(self.labels(), ["recreated"])
        self.assertEqual(AnnotationEvent.objects.get(source__report=report).label, "report")

    def test_parse_error_retains_labeled_last_success(self):
        self.path.write_text("events: [")
        observe(self.source.pk)
        self.assertEqual(self.source_state().status, "parse-error")
        self.assertEqual(self.source.error_code, "parse-error")
        self.assertEqual(self.labels(), ["first"])
        self.assertLess(self.source.published_generation, self.source.desired_generation)

    def test_nonowner_missing_and_expired_lease_never_transfer(self):
        with override_settings(DATA_DIR=str(self.root_b)):
            foreign = workspace_identity()
            AnnotationSourceState.objects.filter(pk=self.source.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
            observe(self.source.pk)
        self.assertNotEqual(self.source_state().owner_workspace_id, foreign)
        self.assertEqual(self.labels(), ["first"])

    def test_a_b_a_fences_original_a_in_another_process(self):
        parent, child_channel = self.context.Pipe()
        child = self.launch(_observer_process, str(self.root_a), self.source.pk, child_channel, block=True)
        self.receive(parent, "read")
        original_generation = self.source_state().ownership_generation
        with override_settings(DATA_DIR=str(self.root_b)):
            self.handoff()
        self.handoff()
        self.assertEqual(self.source_state().ownership_generation, original_generation + 2)
        parent.send("continue")
        self.receive(parent, "done")
        child.join(5)
        self.assertEqual(child.exitcode, 0)
        self.assertEqual(self.source_state().status, "pending")
        self.assertEqual(self.labels(), ["first"])

    def test_expired_lease_rejects_late_process_publication(self):
        self.write("late")
        parent, child_channel = self.context.Pipe()
        child = self.launch(_observer_process, str(self.root_a), self.source.pk, child_channel, block=True)
        self.receive(parent, "read")
        AnnotationSourceState.objects.filter(pk=self.source.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        parent.send("continue")
        self.receive(parent, "done")
        child.join(5)
        self.assertEqual(self.labels(), ["first"])
        observe(self.source.pk)
        self.assertEqual(self.labels(), ["late"])

    def test_file_replacement_and_partial_write_are_unavailable(self):
        from apps.reports import annotation_reconciliation as reconciliation
        original = reconciliation.declaration
        def changed(raw):
            result = original(raw)
            self.write("replaced")
            return result
        with patch.object(reconciliation, "declaration", side_effect=changed):
            observe(self.source.pk)
        self.assertEqual(self.source_state().status, "unavailable")
        self.assertEqual(self.labels(), ["first"])

    def test_report_backfill_uses_current_locked_snapshot(self):
        report = Report.objects.create(studio=self.studio, slug="snapshot", name="Snapshot", config={"annotations": {"events": [{"date": "2020-01-01", "label": "old"}]}})
        original = Report.objects.select_for_update
        def changed(*args, **kwargs):
            Report.objects.filter(pk=report.pk).update(config={"annotations": {"events": [{"date": "2020-01-01", "label": "new"}]}})
            return original(*args, **kwargs)
        with patch.object(Report.objects, "select_for_update", side_effect=changed):
            backfill_reports()
        self.assertEqual(AnnotationEvent.objects.get(source__report=report).label, "new")

    def test_worker_sigkill_expiry_and_restart_observe_same_owner(self):
        self.write("after-restart")
        AnnotationSourceState.objects.filter(pk=self.source.pk).update(next_check_at=timezone.now())
        parent, child_channel = self.context.Pipe()
        child = self.launch(_actual_worker_process, str(self.root_a), child_channel, block=True)
        self.receive(parent, "started")
        self.receive(parent, "read")
        lease_owner = self.source_state().owner_workspace_id
        self.assertIsNotNone(self.source.lease_until)
        os.kill(child.pid, signal.SIGKILL)
        child.join(5)
        self.assertEqual(child.exitcode, -signal.SIGKILL)
        self.assertEqual(self.labels(), ["first"])
        AnnotationSourceState.objects.filter(pk=self.source.pk).update(lease_until=timezone.now() - timedelta(seconds=1), next_check_at=timezone.now())
        parent2, child_channel2 = self.context.Pipe()
        restarted = self.launch(_actual_worker_process, str(self.root_a), child_channel2)
        self.receive(parent2, "started")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.labels() == ["after-restart"]:
                break
            time.sleep(0.05)
        self.assertEqual(self.labels(), ["after-restart"])
        self.assertEqual(self.source_state().owner_workspace_id, lease_owner)
        parent2.send("stop")
        restarted.join(10)
        self.assertEqual(restarted.exitcode, 0)

    def test_two_process_marker_creation_has_one_durable_winner(self):
        root = Path(self.temporary.name) / "shared"
        root.mkdir()
        event = self.context.Event()
        results = self.context.Queue()
        children = [self.launch(_marker_process, str(root), event, results) for _ in range(2)]
        event.set()
        first, second = results.get(timeout=15), results.get(timeout=15)
        self.assertEqual(first[0], "ok")
        self.assertEqual(first, second)
        for child in children:
            child.join(5)
            self.assertEqual(child.exitcode, 0)

    def test_pool_and_coordinator_do_not_observe_foreign_source(self):
        from apps.reports.annotation_reconciliation import start_for_worker
        self.assertIsNone(start_for_worker(is_runner=False, pools=[]))
        self.write("not-in-pool")
        AnnotationSourceState.objects.filter(pk=self.source.pk).update(next_check_at=timezone.now())
        worker = AnnotationThread(["large"])
        worker.batch()
        self.assertEqual(self.labels(), ["first"])

    def test_fifo_symlink_and_directory_are_unavailable(self):
        self.path.unlink()
        os.mkfifo(self.path)
        observe(self.source.pk)
        self.assertEqual(self.source_state().status, "unavailable")
        self.path.unlink()
        outside = Path(self.temporary.name) / "outside.yaml"
        self.write("outside", path=outside)
        self.path.symlink_to(outside)
        observe(self.source.pk)
        self.assertEqual(self.source_state().status, "unavailable")
        self.path.unlink()
        self.path.mkdir()
        observe(self.source.pk)
        self.assertEqual(self.source_state().status, "unavailable")
        self.assertEqual(self.labels(), ["first"])

    def test_corrupt_marker_does_not_claim_or_erase_source(self):
        (self.root_a / ".annotation-workspace").write_text("corrupt")
        original_owner = self.source_state().owner_workspace_id
        with self.assertRaises(WorkspaceUnavailable):
            observe(self.source.pk)
        self.assertEqual(self.source_state().owner_workspace_id, original_owner)
        self.assertEqual(self.labels(), ["first"])

    def test_report_backfill_and_scan_ignore_unrelated_heavy_keys(self):
        config = {"queries": "x" * 200000, "studio": None, "annotations": {"unrelated": "y" * 200000, "events": [{"date": "2020-01-01", "label": "selected"}]}}
        report = Report.objects.create(studio=self.studio, slug="narrow", name="Narrow", config=config)
        with patch("apps.reports.annotation_projection.EXPANDED_BYTES", 1000):
            backfill_reports()
            first = AnnotationEvent.objects.get(source__report=report)
            sync_report_declarations(report)
        second = AnnotationEvent.objects.get(source__report=report)
        self.assertEqual((first.label, first.scope), (second.label, second.scope))
        self.assertEqual(second.scope, "None")

    def test_oversized_report_backfill_publishes_catalog_status(self):
        report = Report.objects.create(studio=self.studio, slug="oversized", name="Oversized", config={"annotations": {"events": [{"date": "2020-01-01", "label": "x" * (16 * 1024 * 1024)}]}})
        previous = AnnotationCatalogState.objects.get(studio=self.studio).revision
        backfill_reports()
        source = AnnotationSourceState.objects.get(report=report)
        self.assertEqual((source.status, source.error_code), ("oversized", "oversized"))
        self.assertGreater(AnnotationCatalogState.objects.get(studio=self.studio).revision, previous)
        self.assertFalse(AnnotationEvent.objects.filter(source=source).exists())
        worker = AnnotationThread(["standard"])
        for _ in range(4):
            worker.batch()
        source.refresh_from_db()
        self.assertEqual(source.failures, 1)

    def test_removed_report_is_not_backfilled(self):
        report = Report.objects.create(studio=self.studio, slug="removed", name="Removed", present_in_scan=False, config={"annotations": {"events": [{"date": "2020-01-01"}]}})
        backfill_reports()
        self.assertFalse(AnnotationSourceState.objects.filter(report=report).exists())

    def test_fenced_renewal_keeps_long_observation_alive(self):
        self.write("renewed")
        parent, child_channel = self.context.Pipe()
        child = self.launch(_observer_process, str(self.root_a), self.source.pk, child_channel, block=True, renewal_interval=0.05)
        self.receive(parent, "read")
        token = self.source_state().lease_token
        AnnotationSourceState.objects.filter(pk=self.source.pk).update(lease_until=timezone.now() + timedelta(seconds=1))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.source_state().lease_until > timezone.now() + timedelta(seconds=100):
                break
            time.sleep(0.02)
        self.assertGreater(self.source_state().lease_until, timezone.now() + timedelta(seconds=100))
        self.assertEqual(self.source.lease_token, token)
        parent.send("continue")
        self.receive(parent, "done")
        child.join(5)
        self.assertEqual(self.labels(), ["renewed"])

    def test_normal_git_upgrade_and_manual_registration(self):
        import subprocess
        from django.core.management import call_command
        checkout = self.studio.repo_dir
        checkout.mkdir(parents=True)
        def git(*args):
            return subprocess.check_output(["git", "-C", str(checkout), *args], stderr=subprocess.DEVNULL).decode().strip()
        git("init", "--initial-branch=main")
        git("config", "user.name", "Annotation fixture")
        git("config", "user.email", "fixture@example.invalid")
        (checkout / "events.yaml").write_bytes(self.path.read_bytes())
        git("add", "events.yaml")
        git("commit", "-m", "Add fixture declarations")
        sha = git("rev-parse", "HEAD")
        repo = StudioRepo.objects.create(studio=self.studio, repo_url="https://example.invalid/reports.git", path="reports", auth_method="none", last_synced_sha=sha)
        AnnotationSourceState.objects.filter(pk=self.source.pk).update(owner_workspace_id="", ownership_generation=0)
        self.assertTrue(proven_git_upgrade(self.studio))
        observe(self.source.pk)
        self.assertEqual(self.labels(), ["first"])
        self.assertFalse(proven_git_upgrade(self.studio))
        # Git omission must not adopt/clear a retained manual project file.
        git("rm", "events.yaml")
        git("commit", "-m", "Remove fixture declaration")
        repo.last_synced_sha = git("rev-parse", "HEAD")
        repo.save(update_fields=["last_synced_sha"])
        AnnotationSourceState.objects.filter(pk=self.source.pk).update(owner_workspace_id="")
        self.write("manual")
        self.assertFalse(proven_git_upgrade(self.studio))
        self.assertEqual(self.source_state().owner_workspace_id, "")
        call_command("reconcile_annotations", f"{self.org.slug}/{self.studio.slug}", register_local=True)
        self.assertEqual(self.labels(), ["manual"])

    def test_git_wrong_head_and_wrong_materialized_blob_are_unknown(self):
        import subprocess
        checkout = self.studio.repo_dir
        checkout.mkdir(parents=True)
        def git(*args):
            return subprocess.check_output(["git", "-C", str(checkout), *args], stderr=subprocess.DEVNULL).decode().strip()
        git("init", "--initial-branch=main")
        git("config", "user.name", "Fixture")
        git("config", "user.email", "fixture@example.invalid")
        self.write("git", path=checkout / "events.yaml")
        git("add", "events.yaml")
        git("commit", "-m", "Fixture")
        repo = StudioRepo.objects.create(studio=self.studio, repo_url="https://example.invalid/reports.git", auth_method="none", last_synced_sha="0" * 40)
        AnnotationSourceState.objects.filter(pk=self.source.pk).update(owner_workspace_id="")
        self.assertFalse(proven_git_upgrade(self.studio))
        repo.last_synced_sha = git("rev-parse", "HEAD")
        repo.save(update_fields=["last_synced_sha"])
        self.assertFalse(proven_git_upgrade(self.studio))
        self.assertEqual(self.source_state().owner_workspace_id, "")


    def test_same_bytes_republish_incompatible_schema_and_preserve_failure_history(self):
        from apps.reports.annotation_projection import SCHEMA_VERSION
        old_generation = self.source_state().published_generation
        digest = self.source.digest
        AnnotationSourceState.objects.filter(pk=self.source.pk).update(schema_version=SCHEMA_VERSION + 1)
        observe(self.source.pk)
        self.assertEqual(self.source_state().schema_version, SCHEMA_VERSION)
        self.assertEqual(self.source.digest, digest)
        self.assertGreater(self.source.published_generation, old_generation)
        self.path.write_text("events: [")
        AnnotationSourceState.objects.filter(pk=self.source.pk).update(schema_version=SCHEMA_VERSION + 1)
        observe(self.source.pk)
        self.assertEqual(self.source_state().schema_version, SCHEMA_VERSION + 1)
        self.assertEqual(self.source.status, "parse-error")
        self.assertEqual(self.labels(), ["first"])

    def test_incompatible_report_projection_is_backfilled(self):
        from apps.reports.annotation_projection import SCHEMA_VERSION
        report = Report.objects.create(studio=self.studio, slug="version", name="Version", config={"annotations": {"events": [{"date": "2020-01-01"}]}})
        sync_report_declarations(report)
        AnnotationSourceState.objects.filter(report=report).update(schema_version=SCHEMA_VERSION + 1, attempted_schema_version=SCHEMA_VERSION + 1)
        backfill_reports()
        self.assertEqual(AnnotationSourceState.objects.get(report=report).schema_version, SCHEMA_VERSION)

    def test_corrupt_marker_import_still_imports_datasources(self):
        import io
        from django.core.management import call_command
        source = Path(self.temporary.name) / "import"
        (source / "reports" / "imported").mkdir(parents=True)
        (source / "reports" / "imported" / "report.yaml").write_text("name: Imported\n")
        (source / "reports" / "imported" / "generator.py").write_text("def generate(): return []\n")
        (source / "data-sources").mkdir()
        (source / "data-sources" / "config.yaml").write_text("{}")
        (self.root_a / ".annotation-workspace").write_text("corrupt")
        stderr = io.StringIO()
        with patch("apps.datasources.importer.import_yaml", return_value=([], [])) as imported:
            call_command("import_project", f"{self.org.slug}/{self.studio.slug}", str(source), stdout=io.StringIO(), stderr=stderr)
        imported.assert_called_once()
        self.assertIn("coverage remains unknown", stderr.getvalue())
        self.assertTrue(Report.objects.filter(studio=self.studio, slug="imported").exists())


    def test_two_owners_unowned_registration_has_one_winner(self):
        AnnotationSourceState.objects.filter(pk=self.source.pk).update(owner_workspace_id="", ownership_generation=0)
        event = self.context.Event()
        results = self.context.Queue()
        children = [self.launch(_registration_process, str(root), self.studio.pk, event, results) for root in (self.root_a, self.root_b)]
        event.set()
        answers = [results.get(timeout=15), results.get(timeout=15)]
        self.assertEqual([answer[0] for answer in answers], ["ok", "ok"])
        self.assertEqual(sum(answer[1] for answer in answers), 1)
        self.assertEqual(self.source_state().ownership_generation, 1)
        for child in children:
            child.join(5)
            self.assertEqual(child.exitcode, 0)

    def test_concurrent_growth_is_admitted_after_report_lock(self):
        report = Report.objects.create(studio=self.studio, slug="growth", name="Growth", config={"annotations": {"events": [{"date": "2020-01-01"}]}})
        original = Report.objects.select_for_update
        def grow(*args, **kwargs):
            Report.objects.filter(pk=report.pk).update(config={"annotations": {"events": [{"date": "2020-01-01", "label": "x" * (16 * 1024 * 1024)}]}})
            return original(*args, **kwargs)
        with patch.object(Report.objects, "select_for_update", side_effect=grow):
            backfill_reports()
        self.assertEqual(AnnotationSourceState.objects.get(report=report).status, "oversized")
        self.assertFalse(AnnotationEvent.objects.filter(source__report=report).exists())

    def test_concurrent_removal_during_candidate_discovery_is_not_published(self):
        report = Report.objects.create(studio=self.studio, slug="removed-race", name="Removed race", config={"annotations": {"events": [{"date": "2020-01-01"}]}})
        original = Report.objects.select_for_update
        def remove(*args, **kwargs):
            Report.objects.filter(pk=report.pk).update(present_in_scan=False)
            return original(*args, **kwargs)
        with patch.object(Report.objects, "select_for_update", side_effect=remove):
            backfill_reports()
        self.assertFalse(AnnotationSourceState.objects.filter(report=report).exists())

    def test_cancelled_owned_observation_cannot_publish(self):
        import threading
        cancelled = threading.Event()
        cancelled.set()
        self.write("cancelled")
        observe(self.source.pk, cancelled=cancelled)
        self.assertEqual(self.labels(), ["first"])

    def test_owned_100000_events_are_published_without_partial_rows(self):
        self.path.write_bytes(b'events:\n' + b'- {date: 1999-01-01, label: event}\n' * 100000)
        observe(self.source.pk)
        self.assertEqual(self.source_state().status, "ready")
        self.assertEqual(self.source.row_count, 100000)
        self.assertEqual(AnnotationEvent.objects.filter(source=self.source).count(), 100000)
        self.path.write_bytes(b'events:\n' + b'- {date: 1999-01-01, label: event}\n' * 100001)
        observe(self.source.pk)
        self.assertEqual(self.source_state().status, "oversized")
        self.assertEqual(AnnotationEvent.objects.filter(source=self.source).count(), 100000)

    def test_registry_scan_projects_unquoted_yaml_dates(self):
        import datetime
        from apps.reports.scan import sync_studio_registry
        directory = self.studio.reports_dir / "dated"
        directory.mkdir(parents=True)
        (directory / "generator.py").write_text("def generate(): return []\n")
        (directory / "report.yaml").write_text(
            "name: Dated\nannotations:\n  events:\n"
            "  - {date: 2020-01-01, end_date: 2020-01-03, label: dated}\n")
        sync_studio_registry(self.studio)
        report = Report.objects.get(studio=self.studio, slug="dated")
        self.assertEqual(report.config["annotations"]["events"][0]["date"], "2020-01-01")
        event = AnnotationEvent.objects.get(source__report=report)
        self.assertEqual(event.start_date, datetime.date(2020, 1, 1))
        self.assertEqual(event.end_date, datetime.date(2020, 1, 3))
        self.assertEqual(event.label, "dated")

    def test_registry_declaration_write_rolls_back_with_report_update(self):
        from apps.reports.scan import sync_studio_registry
        directory = self.studio.reports_dir / "atomic"
        directory.mkdir(parents=True)
        (directory / "generator.py").write_text("def generate(): return []\n")
        (directory / "report.yaml").write_text('name: Atomic\nannotations:\n  events: [{date: 2020-01-01, label: atomic}]\n')
        with patch("apps.reports.annotation_projection.sync_report_declarations", side_effect=RuntimeError("injected projection failure")), self.assertRaises(RuntimeError):
            sync_studio_registry(self.studio)
        self.assertFalse(Report.objects.filter(studio=self.studio, slug="atomic").exists())


    def test_failed_legacy_backfill_obeys_backoff_without_starving_good_report(self):
        from apps.reports.annotation_projection import SCHEMA_VERSION
        bad = Report.objects.create(studio=self.studio, slug="bad-legacy", name="Bad legacy", config={"annotations": {"events": {}}})
        good = Report.objects.create(studio=self.studio, slug="good-legacy", name="Good legacy", config={"annotations": {"events": [{"date": "2020-01-01", "label": "good"}]}})
        worker = AnnotationThread(["standard"])
        worker.batch()
        bad_source = AnnotationSourceState.objects.get(report=bad)
        self.assertEqual((bad_source.status, bad_source.failures), ("parse-error", 1))
        self.assertEqual(bad_source.schema_version, 0)
        self.assertEqual(bad_source.attempted_schema_version, SCHEMA_VERSION)
        worker.batch()
        self.assertEqual(AnnotationEvent.objects.get(source__report=good).label, "good")
        for _ in range(5):
            worker.batch()
        bad_source.refresh_from_db()
        self.assertEqual(bad_source.failures, 1)
        # Once due, exactly one admitted retry advances the bounded backoff.
        AnnotationSourceState.objects.filter(pk=bad_source.pk).update(next_check_at=timezone.now() - timedelta(seconds=1))
        worker.report_cursor = 0
        worker.batch()
        bad_source.refresh_from_db()
        self.assertEqual(bad_source.failures, 2)
        self.assertGreater(bad_source.next_check_at, timezone.now() + timedelta(seconds=100))
        worker.report_cursor = 0
        worker.batch()
        bad_source.refresh_from_db()
        self.assertEqual(bad_source.failures, 2)

    def test_new_parser_version_retries_failure_without_claiming_last_success(self):
        from apps.reports.annotation_projection import SCHEMA_VERSION
        report = Report.objects.create(studio=self.studio, slug="old-attempt", name="Old attempt", config={"annotations": []})
        sync_report_declarations(report)
        source = AnnotationSourceState.objects.get(report=report)
        self.assertEqual(source.schema_version, 0)
        AnnotationSourceState.objects.filter(pk=source.pk).update(attempted_schema_version=SCHEMA_VERSION + 1, next_check_at=timezone.now() + timedelta(hours=1))
        backfill_reports()
        source.refresh_from_db()
        self.assertEqual(source.attempted_schema_version, SCHEMA_VERSION)
        self.assertEqual(source.schema_version, 0)
        self.assertEqual(source.failures, 2)

    def test_explicit_scan_change_ignores_background_retry_backoff(self):
        report = Report.objects.create(studio=self.studio, slug="explicit-change", name="Explicit change", config={"annotations": []})
        sync_report_declarations(report)
        source = AnnotationSourceState.objects.get(report=report)
        self.assertGreater(source.next_check_at, timezone.now())
        report.config = {"annotations": {"events": [{"date": "2020-01-01", "label": "fixed"}]}}
        report.save(update_fields=["config"])
        sync_report_declarations(report)
        source.refresh_from_db()
        self.assertEqual((source.status, source.failures), ("ready", 0))
        self.assertEqual(AnnotationEvent.objects.get(source=source).label, "fixed")

    def test_corrupt_workspace_marker_does_not_block_db_report_backfill(self):
        report = Report.objects.create(studio=self.studio, slug="db-only", name="DB only", config={"annotations": {"events": [{"date": "2020-01-01", "label": "canonical"}]}})
        original_owner = self.source_state().owner_workspace_id
        (self.root_a / ".annotation-workspace").write_text("corrupt")
        self.write("must-not-observe")
        AnnotationSourceState.objects.filter(pk=self.source.pk).update(next_check_at=timezone.now())
        worker = AnnotationThread(["standard"])
        with patch("apps.reports.annotation_reconciliation.proven_git_upgrade") as upgrade, patch("apps.reports.annotation_reconciliation.observe") as global_observe:
            worker.batch()
        upgrade.assert_not_called()
        global_observe.assert_not_called()
        self.assertEqual(AnnotationEvent.objects.get(source__report=report).label, "canonical")
        self.assertEqual(self.source_state().owner_workspace_id, original_owner)
        self.assertEqual(self.labels(), ["first"])
