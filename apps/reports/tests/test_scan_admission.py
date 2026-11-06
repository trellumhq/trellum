"""Repository admission rejects new restrictions without changing stored audiences."""
import pytest

from apps.reports.models import OrgSharePolicy, Report
from apps.reports.scan import ReportAdmissionRejected, RegistrySyncWarning, sync_studio_registry

pytestmark = pytest.mark.django_db


def block(settings, cause):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = cause != "readiness"
    settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-external" if cause == "edge-external" else "proxy"


@pytest.mark.parametrize("cause", ["readiness", "edge-external"])
@pytest.mark.parametrize("kind", ["report", "analysis"])
@pytest.mark.parametrize("source", ["initial", "default"])
def test_new_private_rejected_without_blocking_valid_entries(
    studio_tree, write_report, settings, cause, kind, source,
):
    block(settings, cause)
    OrgSharePolicy.objects.create(org=studio_tree.org, share_links_enabled=False, embed_links_enabled=False)
    config = {"kind": kind}
    if source == "initial":
        config["initial_audience"] = "private"
    else:
        field = f"default_{kind}_audience"
        type(studio_tree).objects.filter(pk=studio_tree.pk).update(**{field: "private"})
    directory = write_report("a-rejected", **config)
    if kind == "analysis":
        (directory / "content.md").write_text("# Finding\n", encoding="utf-8")
    write_report("z-valid", initial_audience="studio")
    missing = Report.objects.create(studio=studio_tree, slug="removed", audience="private")
    with pytest.raises(RegistrySyncWarning) as error:
        sync_studio_registry(studio_tree)
    assert "a-rejected" in str(error.value)
    assert "Private audience is unavailable" in str(error.value)
    assert not studio_tree.reports.filter(slug="a-rejected").exists()
    assert studio_tree.reports.get(slug="z-valid").audience == "studio"
    missing.refresh_from_db()
    assert missing.audience == "private" and not missing.present_in_scan
    assert not OrgSharePolicy.objects.get(org=studio_tree.org).share_links_enabled


@pytest.mark.parametrize("cause", ["readiness", "edge-external"])
@pytest.mark.parametrize("audience", ["studio", "private"])
@pytest.mark.parametrize("initial", ["studio", "private"])
def test_removed_and_readded_rows_keep_database_audience(
    studio_tree, write_report, settings, cause, audience, initial,
):
    block(settings, cause)
    row = Report.objects.create(studio=studio_tree, slug="returned", audience=audience)
    sync_studio_registry(studio_tree)
    row.refresh_from_db()
    assert not row.present_in_scan
    directory = write_report("returned", name="Updated", kind="analysis", initial_audience=initial)
    (directory / "content.md").write_text("# Updated\n", encoding="utf-8")
    assert sync_studio_registry(studio_tree) == 1
    row.refresh_from_db()
    assert row.audience == audience and row.present_in_scan
    assert row.name == "Updated" and row.kind == "analysis"


def test_disabled_external_sharing_does_not_block_internal_private_creation(
    studio_tree, write_report, settings,
):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"
    policy = OrgSharePolicy.objects.create(org=studio_tree.org, share_links_enabled=False, embed_links_enabled=False)
    write_report("private", initial_audience="private")
    assert sync_studio_registry(studio_tree) == 1
    assert studio_tree.reports.get().audience == "private"
    policy.refresh_from_db()
    assert not policy.share_links_enabled and not policy.embed_links_enabled


def test_rejections_do_not_spend_quota_and_both_causes_are_reported(
    studio_tree, write_report, settings,
):
    from apps.orgs.models import OrgQuota

    block(settings, "readiness")
    settings.TRELLUM_QUOTAS_ENABLED = True
    OrgQuota.objects.create(org=studio_tree.org, max_reports=1)
    write_report("a-rejected", initial_audience="private")
    write_report("b-valid")
    write_report("c-over-quota")
    with pytest.raises(ReportAdmissionRejected) as error:
        sync_studio_registry(studio_tree)
    assert "a-rejected" in str(error.value) and "limited to 1 reports" in str(error.value)
    assert list(studio_tree.reports.values_list("slug", flat=True)) == ["b-valid"]


def test_rejection_diagnostics_are_bounded(studio_tree, write_report, monkeypatch, settings):
    block(settings, "readiness")
    directory = write_report("stub")
    monkeypatch.setattr("trellum.runner.scan_report_configs", lambda *a, **k: [
        {"dir": str(directory), "slug": f"private-{i:03}-" + "x" * 1000, "config": {"initial_audience": "private"}}
        for i in range(100)
    ])
    with pytest.raises(ReportAdmissionRejected) as error:
        sync_studio_registry(studio_tree)
    assert "100 new item(s)" in str(error.value) and "97 more" in str(error.value)
    assert len(str(error.value).encode()) < 2000
    assert not studio_tree.reports.exists()


@pytest.mark.parametrize("audience", ["studio", "private"])
def test_invalid_initial_value_on_existing_row_does_not_revalidate_audience(
    studio_tree, write_report, monkeypatch, settings, audience,
):
    block(settings, "readiness")
    existing = Report.objects.create(studio=studio_tree, slug="existing", audience=audience)
    # The framework normally rejects malformed YAML before this boundary. If
    # a caller already supplied it, initial_audience remains creation-only.
    monkeypatch.setattr("trellum.runner.scan_report_configs", lambda *a, **k: [
        {"dir": str(write_report("existing")), "slug": "existing", "config": {"initial_audience": {"invalid": True}, "name": "Updated"}},
        {"dir": str(write_report("valid")), "slug": "valid", "config": {}},
    ])
    assert sync_studio_registry(studio_tree) == 2
    existing.refresh_from_db()
    assert existing.audience == audience and existing.name == "Updated"
    assert studio_tree.reports.get(slug="valid").audience == "studio"


def test_row_deleted_after_admission_snapshot_cannot_be_recreated_private(
    studio_tree, write_report, monkeypatch, settings,
):
    from apps.reports import scan

    block(settings, "readiness")
    existing = Report.objects.create(studio=studio_tree, slug="deleted", audience="studio")
    write_report("deleted", initial_audience="private")
    write_report("valid")
    original = scan._apply_report_cap

    def delete_after_snapshot(studio, entries):
        existing.delete()
        return original(studio, entries)

    monkeypatch.setattr(scan, "_apply_report_cap", delete_after_snapshot)
    with pytest.raises(ReportAdmissionRejected):
        sync_studio_registry(studio_tree)
    assert not studio_tree.reports.filter(slug="deleted").exists()
    assert studio_tree.reports.get(slug="valid").audience == "studio"


def test_import_project_surfaces_rejection_and_completes_valid_import(
    studio_tree, settings, tmp_path,
):
    from io import StringIO
    from django.core.management import call_command

    block(settings, "readiness")
    source = tmp_path / "import"
    for slug, audience in (("a-private", "private"), ("z-valid", "studio")):
        directory = source / "reports" / slug
        directory.mkdir(parents=True)
        (directory / "report.yaml").write_text(f"slug: {slug}\ninitial_audience: {audience}\n", encoding="utf-8")
        (directory / "generator.py").write_text("# report\n", encoding="utf-8")
    stdout, stderr = StringIO(), StringIO()
    call_command("import_project", str(studio_tree), str(source), stdout=stdout, stderr=stderr)
    assert "1 report(s) registered" in stdout.getvalue()
    assert "a-private" in stderr.getvalue() and "TRELLUM_REPORT_SCOPED_ACCESS_READY" in stderr.getvalue()
    assert list(studio_tree.reports.values_list("slug", flat=True)) == ["z-valid"]
