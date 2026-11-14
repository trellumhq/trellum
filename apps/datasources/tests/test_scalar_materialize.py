"""Portal-to-framework scalar contract, with synthetic values only."""
import json
from types import SimpleNamespace

import pytest
import yaml

from apps.datasources.materialize import materialize
from apps.datasources.models import DataSource, RepoDataSource
from apps.datasources.status import report_blockers
from apps.orgs.models import OrgAssistantConfig
from trellum.data import datasource_config
from trellum.report import ReportContext

pytestmark = pytest.mark.django_db


def report_for(studio, names):
    return SimpleNamespace(studio=studio, config={"data_sources": names})


def test_only_selected_report_receives_secret_and_replacement(studio_tree):
    value = "synthetic-scalar-contract-value"
    ds = DataSource.objects.create(studio=studio_tree, name="litellm_key", type="secret",
                                   credentials={"value": value})
    selected = report_for(studio_tree, ["litellm_key"])
    env = materialize(studio_tree, report=selected)
    assert env[f"{ds.env_prefix}_VALUE"] == value
    assert f"{ds.env_prefix}_VALUE" in json.loads(env["TRELLUM_SCALAR_SECRET_NAMES"])
    assert value not in (studio_tree.datasources_dir / "config.yaml").read_text()
    assert not list(studio_tree.datasources_dir.rglob("*litellm_key*"))
    assert materialize(studio_tree, report=report_for(studio_tree, [])) == {}
    ds.credentials = {"value": "replacement-synthetic-value"}
    ds.save()
    assert materialize(studio_tree, report=selected)[f"{ds.env_prefix}_VALUE"] == ds.credentials["value"]


def test_cleared_scalar_is_not_a_build_blocker_and_reader_can_catch(studio_tree, monkeypatch):
    ds = DataSource.objects.create(studio=studio_tree, name="litellm_key", type="secret",
                                   credentials={"value": ""})
    RepoDataSource.objects.create(studio=studio_tree, name=ds.name, type=ds.type,
                                  config={}, source_file="data-sources/config.yaml")
    report = report_for(studio_tree, [ds.name])
    assert report_blockers(report) == []
    env = materialize(studio_tree, report=report)
    assert env[f"{ds.env_prefix}_VALUE"] == ""
    monkeypatch.setenv(f"{ds.env_prefix}_VALUE", "")
    monkeypatch.setenv("FW_PROJECT_ROOT", str(studio_tree.project_root))
    datasource_config.invalidate_datasource_cache()
    try:
        ctx = ReportContext(report.config, "revenue-pulse", str(studio_tree.output_dir))
        with pytest.raises(ValueError, match="no value configured"):
            ctx.read_text(ds.name)
    finally:
        datasource_config.invalidate_datasource_cache()


def test_org_secret_reference_is_resolved_fresh_and_not_copied(studio_tree, other_org):
    assistant = OrgAssistantConfig.objects.create(org=studio_tree.org, api_key="synthetic-org-key")
    OrgAssistantConfig.objects.create(org=other_org, api_key="synthetic-other-org-key")
    ds = DataSource.objects.create(studio=studio_tree, name="litellm_key", type="secret",
                                   config={"secret_reference": "assistant_api_key"})
    report = report_for(studio_tree, [ds.name])
    assert materialize(studio_tree, report=report)[f"{ds.env_prefix}_VALUE"] == assistant.api_key
    ds.refresh_from_db()
    assert not ds.credentials
    assistant.api_key = "synthetic-rotated-org-key"
    assistant.save()
    assert materialize(studio_tree, report=report)[f"{ds.env_prefix}_VALUE"] == assistant.api_key
    contents = (studio_tree.datasources_dir / "config.yaml").read_text()
    assert "synthetic-" not in contents
    assert materialize(studio_tree, report=report_for(studio_tree, [])) == {}


@pytest.mark.parametrize("kind,value", [("string", "model-v1"), ("text", "one\ntwo"),
    ("number", "0"), ("boolean", "false"), ("json", '{"limit": 2}')])
def test_plain_scalars_live_in_environment_only(studio_tree, kind, value):
    ds = DataSource.objects.create(studio=studio_tree, name="scalar", type=kind,
                                   credentials={"value": value})
    env = materialize(studio_tree, report=report_for(studio_tree, [ds.name]))
    assert env[f"{ds.env_prefix}_VALUE"] == value
    assert "TRELLUM_SCALAR_SECRET_NAMES" not in env
    entry = yaml.safe_load((studio_tree.datasources_dir / "config.yaml").read_text())["sources"][ds.name]
    assert "value" not in entry
    assert entry["type"] == kind


def test_repo_cannot_grant_itself_organization_secret(studio_tree):
    OrgAssistantConfig.objects.create(org=studio_tree.org, api_key="synthetic-restricted-org-key")
    ds = DataSource.objects.create(studio=studio_tree, name="litellm_key", type="secret")
    RepoDataSource.objects.create(studio=studio_tree, name=ds.name, type=ds.type,
        config={"secret_reference": "assistant_api_key"}, source_file="data-sources/config.yaml")
    env = materialize(studio_tree, report=report_for(studio_tree, [ds.name]))
    assert env[f"{ds.env_prefix}_VALUE"] == ""
    assert "TRELLUM_SCALAR_SECRET_NAMES" not in env


def test_scalar_value_is_encrypted_in_database(studio_tree):
    from django.db import connection

    value = "synthetic-encryption-proof"
    ds = DataSource.objects.create(studio=studio_tree, name="litellm_key", type="secret",
                                   credentials={"value": value})
    with connection.cursor() as cursor:
        cursor.execute("SELECT credentials FROM datasources_datasource WHERE id = %s", [ds.pk])
        ciphertext = cursor.fetchone()[0]
    assert value not in str(ciphertext)
    ds.refresh_from_db()
    assert ds.credentials["value"] == value


@pytest.mark.heavy
@pytest.mark.parametrize("state", ["configured", "cleared", "unlisted"])
@pytest.mark.parametrize("inline", [False, True])
def test_revenue_pulse_real_build_contract(studio_tree, state, inline):
    """Exercise encrypted storage -> injection -> real process -> published files."""
    import os
    from pathlib import Path
    import subprocess
    import sys

    value = "synthetic-revenue-pulse-api-key" if state != "cleared" else ""
    ds = DataSource.objects.create(studio=studio_tree, name="litellm_key", type="secret",
                                   credentials={"value": value})
    declaration = {"name": ds.name, "type": "secret", "credentials": {"local": "UNUSED_LOCAL"}} if inline else ds.name
    report = report_for(studio_tree, [declaration] if state != "unlisted" else [])
    directory = studio_tree.reports_dir / "revenue-pulse"
    directory.mkdir(parents=True)
    (directory / "report.yaml").write_text(yaml.safe_dump({
        "slug": "revenue-pulse", "name": "Revenue Pulse", **report.config,
    }))
    (directory / "generator.py").write_text('''from trellum.report import BaseReport
class Report(BaseReport):
    def generate(self, ctx):
        try:
            value = ctx.read_text("litellm_key")
        except ValueError:
            value = "optional value unavailable"
        print(value)
        ctx.set_custom_output("<html>" + value + "</html>", {"value": value})
''')
    repo = Path(__file__).resolve().parents[3]
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRELLUM_DS_")}
    env.update(materialize(studio_tree, report=report))
    env.update(FW_PROJECT_ROOT=str(studio_tree.project_root), FW_UPDATE_CHECK="0",
               PYTHONPATH=str(repo))
    result = subprocess.run([sys.executable, "-m", "trellum.run", str(directory), "--no-serve"],
                            cwd=repo, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-1000:]
    output = studio_tree.output_dir / "revenue-pulse"
    assert (output / "index.html").is_file()
    data = json.loads((output / "data.json").read_text())
    if state == "configured":
        assert data["value"] == "[REDACTED]"
        assert value not in result.stdout + result.stderr
        assert value not in (output / "index.html").read_text()
        assert value not in (output / "data.json").read_text()
    else:
        assert data["value"] == "optional value unavailable"


def test_portal_secret_flag_cannot_be_downgraded_by_declaration(studio_tree):
    ds = DataSource.objects.create(studio=studio_tree, name="token", type="string",
        config={"secret": True}, credentials={"value": "synthetic-binding-secret"})
    RepoDataSource.objects.create(studio=studio_tree, name=ds.name, type="string",
        config={"secret": False}, source_file="data-sources/config.yaml")
    env = materialize(studio_tree, report=report_for(studio_tree, [ds.name]))
    assert f"{ds.env_prefix}_VALUE" in json.loads(env["TRELLUM_SCALAR_SECRET_NAMES"])
    config = yaml.safe_load((studio_tree.datasources_dir / "config.yaml").read_text())
    assert config["sources"][ds.name]["secret"] is True
