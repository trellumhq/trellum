"""Scoped audience/source parity and explicit single-report I/O failures."""
from types import SimpleNamespace
from datetime import timedelta
import json
import os

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import roles, storage
from apps.datasources.models import DataSource, RepoDataSource
from apps.orgs.models import PermissionGroupGrant, PermissionGroupMembership
from apps.reports.catalog_indicators import catalog_indicators_payload, scoped_subscription_counts, authorized_ids
from apps.reports.catalog_details import catalog_details_payload, _descriptor_meta, DetailChanged
from apps.reports.models import EmailSchedule, Report, ReportPermissionGrant, ReportSourceSummary
from apps.reports.notify import user_in_schedule_audience
from apps.runner.models import Run
from apps.studios.models import StudioMembership

pytestmark = pytest.mark.django_db


def request(user, studio):
    return SimpleNamespace(user=user, studio=studio, org=studio.org)


@pytest.mark.parametrize("role", [None, roles.ADMIN, roles.DEVELOPER, roles.VIEWER])
def test_scoped_subscription_aggregate_matches_each_schedule_audience(studio, org_admin, make_group, attach_group, role):
    report = Report.objects.create(studio=studio, slug="one")
    if role:
        StudioMembership.objects.create(studio=studio, user=org_admin, role=role)
    group = make_group("audience")
    attach_group(org_admin, group)
    schedules = []
    for chips, direct, grouped in [([], False, False), (["everyone"], False, False), (["admins"], False, False), (["developers"], False, False), ([], True, False), ([], False, True), (["everyone", "admins", "developers"], True, True)]:
        s = EmailSchedule.objects.create(report=report, recipient_roles=chips)
        if direct:
            s.recipients.add(org_admin)
        if grouped:
            s.recipient_groups.add(group)
        schedules.append(s)
    disabled = EmailSchedule.objects.create(report=report, recipient_roles=["everyone"], enabled=False)
    member_groups = set(PermissionGroupMembership.objects.filter(user=org_admin, group__org=studio.org).values_list("group_id", flat=True))
    expected = sum(user_in_schedule_audience(org_admin, s, user_role=role, user_group_ids=member_groups) for s in EmailSchedule.objects.filter(pk__in=[s.pk for s in schedules]).prefetch_related("recipients", "recipient_groups"))
    req = request(org_admin, studio)
    assert scoped_subscription_counts(req, authorized_ids(req, [report.pk])) == {report.pk: expected}
    disabled.delete()


def test_indicators_are_page_scoped_and_never_read_storage(studio, studio2, org_admin, monkeypatch):
    visible = Report.objects.create(studio=studio, slug="one")
    other = Report.objects.create(studio=studio2, slug="other")
    EmailSchedule.objects.create(report=other, recipient_roles=["everyone"])
    def forbidden(*args, **kwargs):
        raise AssertionError("Indicators must not read artifacts")
    monkeypatch.setattr(storage, "read_meta", forbidden)
    monkeypatch.setattr(storage, "read_summary_artifact", forbidden)
    with CaptureQueriesContext(connection) as queries:
        payload = catalog_indicators_payload(request(org_admin, studio), [visible.pk, other.pk])
    assert set(payload["reports"]) == {visible.pk}
    assert payload["reports"][visible.pk]["subscriptions"]["count"] == 0
    assert payload["reports"][visible.pk]["waiting"] is False
    assert len(queries) <= 14
    org_admin.is_active = False
    assert catalog_indicators_payload(request(org_admin, studio), [visible.pk])["reports"] == {}


def test_current_source_configuration_clears_old_block_and_disabled_wait(studio, org_admin):
    now = timezone.now()
    report = Report.objects.create(studio=studio, slug="one", config={"data_sources": ["db"]})
    RepoDataSource.objects.create(studio=studio, name="db", type="postgres", config={"host": "host", "database": "db"})
    ReportSourceSummary.objects.create(report=report, state="uncertain", observed_at=now, attempt_at=now, attempt_status="error", attempt_blocked_by=["db"])
    req = request(org_admin, studio)
    first = catalog_indicators_payload(req, [report.pk], now=now)["reports"][report.pk]
    assert first["waiting"] is True and first["blocked_by"] == ["db"]
    # A current binding clears the wait immediately, independently of artifact.
    binding = DataSource.objects.create(studio=studio, name="db", type="postgres", config={"host": "host", "database": "db"}, credentials={"user": "reader", "password": "never-emitted"})
    result = catalog_indicators_payload(req, [report.pk], now=now)
    assert result["reports"][report.pk]["waiting"] is False
    assert "never-emitted" not in json.dumps(result)
    binding.delete()
    report.disabled = True; report.save(update_fields=["disabled"])
    assert catalog_indicators_payload(req, [report.pk], now=now)["reports"][report.pk]["waiting"] is False
    report.config = {}; report.save(update_fields=["config"])
    assert catalog_indicators_payload(req, [report.pk], now=now)["reports"][report.pk]["blocked_by"] == []


def test_latest_failed_run_source_attribution_wins_and_large_tail_is_unknown(studio, org_admin):
    now = timezone.now()
    report = Report.objects.create(studio=studio, slug="one", config={"data_sources": ["db"]})
    RepoDataSource.objects.create(studio=studio, name="db", type="postgres", config={})
    ReportSourceSummary.objects.create(report=report, state="ready", quality="observed", observed_at=now, attempt_status="error", attempt_error="plain build error")
    run = Run.objects.create(report=report, studio=studio, slug=report.slug, status="error", stderr_tail="Data source 'db': unavailable")
    req = request(org_admin, studio)
    assert catalog_indicators_payload(req, [report.pk], now=now)["reports"][report.pk]["last_error"] == run.stderr_tail
    run.stderr_tail = "x"*4097; run.save(update_fields=["stderr_tail"])
    result = catalog_indicators_payload(req, [report.pk], now=now)["reports"][report.pk]
    assert result["source_status"] == "unavailable" and result["waiting"] is None


@pytest.mark.parametrize("config", [{"data_sources": ["s"]*33}, {"data_sources": ["x"*17000]}, {"data_sources": [False]}, {"data_sources": {"name": "x"}}])
def test_reference_overflow_and_invalid_shape_are_not_healthy(studio, org_admin, config):
    report = Report.objects.create(studio=studio, slug="one", config=config)
    result = catalog_indicators_payload(request(org_admin, studio), [report.pk])["reports"][report.pk]
    assert result["source_status"] in ("oversized", "unavailable") and result["waiting"] is None


def test_overlarge_source_is_admitted_before_secret_decryption(studio, org_admin, monkeypatch):
    report = Report.objects.create(studio=studio, slug="one", config={"data_sources": ["db"]})
    DataSource.objects.create(studio=studio, name="db", type="postgres", config={"padding": "x"*70000}, credentials={"password": "secret"})
    from apps.core.crypto import EncryptedJSONField
    def forbidden(*args, **kwargs):
        raise AssertionError("Oversized binding must not decrypt")
    monkeypatch.setattr(EncryptedJSONField, "from_db_value", forbidden)
    result = catalog_indicators_payload(request(org_admin, studio), [report.pk])["reports"][report.pk]
    assert result["source_status"] == "unavailable" and result["waiting"] is None


def test_local_detail_pages_accepted_large_metadata_and_observed_quality(studio_tree, org_admin):
    report = Report.objects.create(studio=studio_tree, slug="one")
    directory = studio_tree.output_dir / report.slug; directory.mkdir()
    checks = [{"id": f"check{i}", "level": "warn", "message": "message"+str(i)} for i in range(60)]
    (directory / "_meta.json").write_text(json.dumps({"validation": {"checks": checks}, "ignored": "x"*200000}))
    result = catalog_details_payload(request(org_admin, studio_tree), report.pk, {"page": "2"})
    assert result["state"] == "ready" and result["quality"] == "observed"
    assert len(result["entries"]) == 25 and result["pagination"]["total"] == 60
    assert result["entries"][0]["id"] == "check25"
    assert "ignored" not in result and len(json.dumps(result).encode()) < 128*1024


def test_local_symlink_and_partial_write_are_explicit(studio_tree, org_admin, tmp_path, monkeypatch):
    report = Report.objects.create(studio=studio_tree, slug="one")
    directory = studio_tree.output_dir / report.slug; directory.mkdir()
    target = tmp_path / "target.json"; target.write_text("{}")
    path = directory / "_meta.json"; path.symlink_to(target)
    assert catalog_details_payload(request(org_admin, studio_tree), report.pk, {})["state"] == "unavailable"
    path.unlink(); path.write_text("{}")
    original = os.fstat
    calls = []
    def race(fd):
        calls.append(fd)
        if len(calls) == 2:
            path.write_text('{"partial":true}')
        return original(fd)
    monkeypatch.setattr(os, "fstat", race)
    with pytest.raises(DetailChanged):
        _descriptor_meta(studio_tree, report.slug)


def test_local_path_replacement_keeps_descriptor_observation_without_atomic_claim(studio_tree, org_admin, monkeypatch):
    report = Report.objects.create(studio=studio_tree, slug="one")
    directory = studio_tree.output_dir / report.slug; directory.mkdir()
    path = directory / "_meta.json"; path.write_text("{}")
    replacement = directory / "replacement"; replacement.write_text('{"new":true}')
    original = os.fstat
    calls = []
    def replace(fd):
        value = original(fd); calls.append(fd)
        if len(calls) == 1:
            replacement.replace(path)
        return value
    monkeypatch.setattr(os, "fstat", replace)
    result = catalog_details_payload(request(org_admin, studio_tree), report.pk, {})
    # Replacement may change the opened inode's ctime/link state and be rejected;
    # otherwise the descriptor's earlier bytes remain only an observed snapshot.
    assert result["state"] == "changed" or (result["state"] == "ready" and result["quality"] == "observed")


def test_remote_detail_pointer_race_never_publishes_data(studio, org_admin, monkeypatch):
    report = Report.objects.create(studio=studio, slug="one")
    pointers = iter(["old", "new"])
    monkeypatch.setattr(storage, "is_remote", lambda: True)
    monkeypatch.setattr(storage, "summary_build_identity", lambda *a: next(pointers))
    monkeypatch.setattr(storage, "read_summary_artifact", lambda *a, **kw: b'{"validation":{"checks":[{"id":"old"}]}}')
    result = catalog_details_payload(request(org_admin, studio), report.pk, {})
    assert result["state"] == "changed" and result["entries"] == []


@pytest.mark.parametrize("ids", [[True], ["1"], list(range(1, 66)), [-1]])
def test_bad_indicator_id_batch_rejected_before_database_work(studio, org_admin, ids):
    with CaptureQueriesContext(connection) as queries, pytest.raises(ValueError):
        catalog_indicators_payload(request(org_admin, studio), ids)
    assert len(queries) == 0


@pytest.mark.parametrize("params", [{"q": "x"*1001}, {"section": "secret"}, {"entry": "bad"}, {"tag_index": "9"*11}, {"text_page": "0"}, {"metadata": "2"}, {"field": "credentials"}])
def test_bad_detail_inputs_rejected_before_database_or_artifact_work(studio, org_admin, params):
    with CaptureQueriesContext(connection) as queries, pytest.raises(ValueError):
        catalog_details_payload(request(org_admin, studio), 1, params)
    assert len(queries) == 0


def test_selected_private_schedule_scope_and_revocation(studio, member, make_group, attach_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    chosen = Report.objects.create(studio=studio, slug="chosen", audience="private")
    hidden = Report.objects.create(studio=studio, slug="hidden", audience="private", config={"data_sources": ["private-source"]})
    group = make_group("selected", grants=[(studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
    grant.save(update_fields=["viewer_scope"])
    access = ReportPermissionGrant.objects.create(grant=grant, report=chosen)
    attach_group(member, group)
    EmailSchedule.objects.create(report=chosen, recipient_roles=["everyone"])
    EmailSchedule.objects.create(report=hidden, recipient_roles=["everyone"])
    req = request(member, studio)
    result = catalog_indicators_payload(req, [chosen.pk, hidden.pk])
    assert set(result["reports"]) == {chosen.pk}
    assert result["reports"][chosen.pk]["subscriptions"]["count"] == 1
    assert "private-source" not in json.dumps(result)
    access.delete()
    assert catalog_indicators_payload(req, [chosen.pk, hidden.pk])["reports"] == {}
