"""Analyses reuse report identity, grants, protected assets and sharing."""
import pytest
import json
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core import roles
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import OrgSharePolicy, Report, ReportPermissionGrant, ShareLink
from apps.reports.scan import build_registry_payload, sync_studio_registry

pytestmark = pytest.mark.django_db


@pytest.fixture
def analysis(studio_tree, write_report):
    directory = write_report("quarterly-analysis", kind="analysis", name="Quarterly analysis", category="Strategy", tags=["quarterly"])
    (directory / "generator.py").unlink()
    (directory / "content.md").write_text("# Quarterly analysis\n", encoding="utf-8")
    row = Report.objects.create(
        studio=studio_tree, slug="quarterly-analysis", name="Quarterly analysis",
        kind=Report.KIND_ANALYSIS, category="Strategy", tags=["quarterly"],
    )
    out = studio_tree.output_dir / row.slug
    (out / "evidence").mkdir(parents=True)
    (out / "index.html").write_text(
        '<html><head></head><body><h1>Quarterly analysis</h1>'
        '<img src="evidence/revenue.png"></body></html>', encoding="utf-8",
    )
    (out / "evidence" / "revenue.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return row


@pytest.fixture
def article_reader(member, analysis, make_group, attach_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    group = make_group("Article audience", grants=[(analysis.studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
    grant.save(update_fields=["viewer_scope"])
    ReportPermissionGrant.objects.create(grant=grant, report=analysis)
    attach_group(member, group)
    return member


@pytest.fixture
def presentation(analysis, monkeypatch):
    from trellum.runner.execute import run_report

    root = analysis.studio.project_root
    directory = analysis.studio.reports_dir / analysis.slug
    (directory / "report.yaml").write_text(json.dumps({
        "kind": "analysis", "format": "presentation", "name": analysis.name,
        "slug": analysis.slug, "theme": "trellum light",
    }), encoding="utf-8")
    (directory / "slides.yaml").write_text(json.dumps({"version": 1, "slides": [
        {"id": "finding", "layout": "image-text", "title": "Frozen finding",
         "text": "Evidence stays fixed.", "image": "evidence/revenue.png", "alt": "Revenue evidence"},
    ]}), encoding="utf-8")
    from PIL import Image
    (directory / "evidence").mkdir(exist_ok=True)
    Image.new("RGB", (600, 300), "white").save(directory / "evidence/revenue.png")
    monkeypatch.setenv("FW_PROJECT_ROOT", str(root))
    monkeypatch.setattr("trellum.project._project_root_override", None)
    run_report(str(directory))
    (analysis.studio.output_dir / analysis.slug / "evidence/revenue.json").write_text(
        '{"captured_at":"2026-10-12T12:00:00Z"}', encoding="utf-8",
    )
    return analysis


def test_presentation_uses_own_audience_for_html_png_and_metadata(
    login, article_reader, presentation, report_row,
):
    prefix = f"/s/{presentation.studio.org.slug}/{presentation.studio.slug}"
    client = login(article_reader)
    html = client.get(f"{prefix}/r/{presentation.slug}/index.html")
    assert html.status_code == 200
    assert b'fw-presentation' in html.content and b'id="slide-finding"' in html.content
    assert b'Fullscreen unavailable' in html.content
    for asset in ("evidence/revenue.png", "evidence/revenue.json", "_meta.json"):
        assert client.get(f"{prefix}/r/{presentation.slug}/{asset}").status_code == 200
    assert client.get(f"{prefix}/r/{report_row.slug}/index.html").status_code == 404
    presentation.permission_group_grants.all().delete()
    for asset in ("index.html", "evidence/revenue.png", "evidence/revenue.json", "_meta.json"):
        assert client.get(f"{prefix}/r/{presentation.slug}/{asset}").status_code == 404


@pytest.mark.parametrize("blocked", ("expires_at", "revoked_at", "policy"))
def test_shared_presentation_protects_document_and_every_evidence_asset(client, presentation, blocked):
    policy = OrgSharePolicy.objects.create(org=presentation.studio.org, share_links_enabled=True)
    link = ShareLink.objects.create(report=presentation)
    entry = client.get(f"/share/{link.token}/")
    assert entry.status_code == 200 and b'id="slide-finding"' in entry.content
    for asset in ("index.html", "evidence/revenue.png", "evidence/revenue.json", "_meta.json"):
        assert client.get(f"/share/{link.token}/{asset}").status_code == 200
    if blocked == "policy":
        policy.share_links_enabled = False
        policy.save()
    else:
        setattr(link, blocked, timezone.now() - timezone.timedelta(seconds=1))
        link.save()
    for asset in ("", "index.html", "evidence/revenue.png", "evidence/revenue.json", "_meta.json"):
        assert client.get(f"/share/{link.token}/{asset}").status_code == 410


def test_kind_defaults_to_report(studio_tree):
    row = Report.objects.create(studio=studio_tree, slug="legacy")
    row.refresh_from_db()
    assert row.kind == Report.KIND_REPORT


def test_sync_kind_transition_keeps_identity_and_grant(
    studio_tree, analysis, article_reader, monkeypatch,
):
    # Defensive sync must clear a stale cron even if the scanner supplies one.
    import trellum.runner
    config = {"kind": "analysis", "name": "Edited", "schedule": {"cron": "0 7 * * *"}}
    monkeypatch.setattr(trellum.runner, "scan_report_configs", lambda *a, **kw: [
        {"slug": analysis.slug, "config": config},
    ])
    studio_tree.reports_dir.mkdir(exist_ok=True)
    sync_studio_registry(studio_tree)
    analysis.refresh_from_db()
    assert (analysis.kind, analysis.schedule_cron, analysis.schedule_timezone) == ("analysis", "", "UTC")
    assert analysis.permission_group_grants.count() == 1
    config.pop("kind")
    sync_studio_registry(studio_tree)
    analysis.refresh_from_db()
    assert (analysis.kind, analysis.schedule_cron) == ("report", "0 7 * * *")
    assert analysis.permission_group_grants.count() == 1


def test_analyses_page_and_registry_reuse_catalog(login, article_reader, analysis):
    prefix = f"/s/{analysis.studio.org.slug}/{analysis.studio.slug}"
    client = login(article_reader)
    response = client.get(prefix + "/analyses")
    assert response.status_code == 200
    assert response.context["console_page_title"] == "Analyses"
    assert '"initial_view": "analyses"' in response.content.decode()
    assert "Search analyses" in response.content.decode()
    report_page = client.get(prefix + "/")
    assert report_page.context["console_page_title"] == "Reports"
    payload = client.get(prefix + "/api/registry").json()
    assert [(r["slug"], r["kind"]) for r in payload["reports"]] == [(analysis.slug, "analysis")]
    assert payload["reports"][0]["category"] == "Strategy"
    assert payload["reports"][0]["tags"] == ["quarterly"]


def test_article_and_png_access_do_not_grant_source_access(
    login, article_reader, analysis, report_row,
):
    prefix = f"/s/{analysis.studio.org.slug}/{analysis.studio.slug}"
    client = login(article_reader)
    assert client.get(f"{prefix}/r/{analysis.slug}/").status_code == 302
    entry = client.get(f"{prefix}/r/{analysis.slug}/index.html")
    assert entry.status_code == 200
    assert b'data-content-kind="analysis"' in entry.content
    png = client.get(f"{prefix}/r/{analysis.slug}/evidence/revenue.png")
    assert png.status_code == 200
    assert b"".join(png.streaming_content).startswith(b"\x89PNG")
    assert client.get(f"{prefix}/r/{report_row.slug}/").status_code == 404
    assert client.get(f"{prefix}/r/{report_row.slug}/data.json").status_code == 404
    assert client.get(f"{prefix}/api/datasources").status_code == 403
    shell = client.get(f"{prefix}/api/report-shell?report={analysis.slug}")
    assert shell.status_code == 200
    assert shell.context["console_active"] == "analysis"
    # Search lives in each page; the bar only offers the jump list for this studio.
    assert b"Search analyses" not in shell.content and b'id="searchInput"' not in shell.content
    assert f'data-jump-source="{prefix}/api/jump"' in shell.content.decode()


def test_grant_revocation_hides_article_and_evidence(login, article_reader, analysis):
    prefix = f"/s/{analysis.studio.org.slug}/{analysis.studio.slug}"
    client = login(article_reader)
    analysis.permission_group_grants.all().delete()
    for asset in ("", "index.html", "evidence/revenue.png"):
        assert client.get(f"{prefix}/r/{analysis.slug}/{asset}").status_code == 404


def test_cross_studio_and_org_article_access_denied(
    login, article_reader, analysis, studio2, other_studio,
):
    client = login(article_reader)
    for target in (studio2, other_studio):
        Report.objects.create(studio=target, slug=analysis.slug, kind="analysis")
        prefix = f"/s/{target.org.slug}/{target.slug}"
        for suffix in ("", "evidence/revenue.png"):
            assert client.get(f"{prefix}/r/{analysis.slug}/{suffix}").status_code == 404


@pytest.mark.parametrize("blocked", ("expires_at", "revoked_at", "policy"))
def test_sharing_article_and_png_obey_existing_policy(client, analysis, blocked):
    policy = OrgSharePolicy.objects.create(org=analysis.studio.org, share_links_enabled=True)
    link = ShareLink.objects.create(report=analysis)
    assert client.get(f"/share/{link.token}/").status_code == 200
    response = client.get(f"/share/{link.token}/evidence/revenue.png")
    assert response.status_code == 200
    assert b"".join(response.streaming_content).startswith(b"\x89PNG")
    if blocked == "policy":
        policy.share_links_enabled = False
        policy.save()
    else:
        setattr(link, blocked, timezone.now() - timezone.timedelta(seconds=1))
        link.save()
    for asset in ("", "evidence/revenue.png"):
        assert client.get(f"/share/{link.token}/{asset}").status_code == 410


def test_live_queries_and_alert_rules_reject_analysis(login, org_admin, analysis):
    from apps.alerts.models import AlertRule
    prefix = f"/s/{analysis.studio.org.slug}/{analysis.studio.slug}"
    response = login(org_admin).post(
        f"{prefix}/api/reports/{analysis.slug}/live-query", data="{}", content_type="application/json",
    )
    assert response.status_code == 400
    assert "Analyses" in response.json()["error"]
    with pytest.raises(ValidationError, match="Analyses"):
        AlertRule.objects.create(
            org=analysis.studio.org, studio=analysis.studio, report=analysis,
            name="Unsupported", created_by=org_admin,
        )


def test_registry_never_labels_analysis_live(analysis, monkeypatch):
    from apps.core import storage
    monkeypatch.setattr(storage, "live_query_index", lambda studio: {analysis.slug})
    assert build_registry_payload(analysis.studio)["reports"][0]["live"] is False


def test_report_to_analysis_stops_existing_alert_rule_endpoints(login, org_admin, report_row):
    from apps.alerts.models import AlertRule
    rule = AlertRule.objects.create(
        org=report_row.studio.org, studio=report_row.studio, report=report_row,
        name="Before transition", created_by=org_admin,
    )
    Report.objects.filter(pk=report_row.pk).update(kind="analysis")
    prefix = f"/s/{report_row.studio.org.slug}/{report_row.studio.slug}/alerts/{rule.pk}"
    client = login(org_admin)
    assert client.get(prefix).status_code == 404
    assert client.post(prefix + "/test").status_code == 404
    assert client.post(prefix + "/toggle").status_code == 404


def test_analysis_cron_never_registers_or_enqueues(analysis, monkeypatch):
    from apps.runner.management.commands import runworker
    from apps.runner.management.commands.runworker import _SchedulerManager, _enqueue_scheduled
    from apps.runner.models import Run
    # The job normally runs outside the test's enclosing DB transaction.
    monkeypatch.setattr(runworker, "close_old_connections", lambda: None)
    analysis.schedule_cron = "0 7 * * *"
    analysis.save(update_fields=["schedule_cron"])
    manager = _SchedulerManager()
    manager.start()
    try:
        assert f"report-{analysis.pk}" not in {job.id for job in manager._scheduler.get_jobs()}
    finally:
        manager.shutdown()
    _enqueue_scheduled(analysis.pk)
    assert not Run.objects.filter(report=analysis).exists()
