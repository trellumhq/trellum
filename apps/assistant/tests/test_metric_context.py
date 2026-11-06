"""Registry disclosure follows conversation scope, not metric-name guesses."""
import json
import os

import pytest

from apps.assistant import metric_context
from apps.assistant.system_prompt import build_system_prompt
from apps.assistant.tools import AssistantToolbox

pytestmark = pytest.mark.django_db


@pytest.fixture
def registry(studio_tree):
    path = studio_tree.project_root / "metrics.yaml"
    path.write_text("""version: 1
private_project_metadata: NEVER_DISCLOSE
metrics:
  - name: revenue
    label: Canonical revenue
    description: Revenue after refunds
    owner: finance@example.test
    tags: [finance, canonical]
    agg: sum
    column: net_revenue
    sql: CANONICAL_SQL
  - name: private_unrelated
    description: PRIVATE_METRIC_MEANING
    owner: PRIVATE_OWNER
""", encoding="utf-8")
    return path


def test_project_summary_and_source_detail(studio_tree, org_admin, registry):
    toolbox = AssistantToolbox(studio_tree, actor=org_admin)
    blocks = build_system_prompt(toolbox)
    cached = blocks[0]["text"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    for value in ("Canonical revenue", "Revenue after refunds", "finance@example.test", '"canonical"'):
        assert value in cached
    assert '<data source="metrics.yaml">' in cached
    assert "CANONICAL_SQL" not in cached
    assert "NEVER_DISCLOSE" not in cached
    assert "Canonical revenue" not in blocks[1]["text"]
    assert "Prefer canonical definitions" in cached
    assert "explanation comes from that report" in cached
    detail = toolbox.execute("read_doc", {"path": "metrics.yaml"})
    assert "CANONICAL_SQL" in detail and '"agg": "sum"' in detail
    assert "NEVER_DISCLOSE" not in detail
    assert json.loads(detail.splitlines()[0])["owner"] == "finance@example.test"


def test_report_scope_never_opens_registry_even_after_full_prompt(
    studio_tree, org_admin, selected_viewer, report_row, registry, monkeypatch,
):
    full = AssistantToolbox(studio_tree, actor=org_admin)
    assert "PRIVATE_METRIC_MEANING" in build_system_prompt(full)[0]["text"]
    bound = AssistantToolbox(studio_tree, actor=selected_viewer, scope="report", report=report_row)

    def forbidden(*args):
        raise AssertionError("Report scope must not open or parse the registry")

    monkeypatch.setattr(metric_context, "_load", forbidden)
    cached = build_system_prompt(bound)[0]["text"]
    assert metric_context.UNAVAILABLE in cached
    # Even administrators remain bound to the conversation's report.
    admin_bound = AssistantToolbox(studio_tree, actor=org_admin, scope="report", report=report_row)
    assert metric_context.UNAVAILABLE in build_system_prompt(admin_bound)[0]["text"]
    for value in ("Canonical revenue", "finance@example.test", "PRIVATE_METRIC_MEANING", "PRIVATE_OWNER"):
        assert value not in cached
    alias = registry.parent / "reports" / report_row.slug / "registry-link"
    alias.symlink_to(registry)
    for path in ("metrics.yaml", "./metrics.yaml", "reports/../metrics.yaml",
                 f"reports/{report_row.slug}/registry-link"):
        result = bound.execute("read_doc", {"path": path})
        assert result.startswith("Error:")
        assert "PRIVATE_METRIC_MEANING" not in result
    # A forged full-scope toolbox still must satisfy current full access.
    forged = AssistantToolbox(studio_tree, actor=selected_viewer)
    assert "PRIVATE_METRIC_MEANING" not in build_system_prompt(forged)[0]["text"]
    assert forged.execute("read_doc", {"path": "metrics.yaml"}).startswith("Error:")


def test_permission_change_invalidates_full_registry_access(viewer, studio_tree, registry):
    from apps.reports.models import Report

    toolbox = AssistantToolbox(studio_tree, actor=viewer)
    assert "PRIVATE_METRIC_MEANING" in build_system_prompt(toolbox)[0]["text"]
    # Private content removes full visibility for studio viewers. An existing
    # toolbox and a fresh one both fail closed, regardless of earlier prompts.
    Report.objects.create(studio=studio_tree, slug="private", name="Private", audience=Report.AUDIENCE_PRIVATE)
    for box in (toolbox, AssistantToolbox(studio_tree, actor=viewer)):
        assert "PRIVATE_METRIC_MEANING" not in build_system_prompt(box)[0]["text"]
        assert box.execute("read_doc", {"path": "metrics.yaml"}).startswith("Error:")


def test_source_policy_hides_sql_but_keeps_summary(studio_tree, org_admin, registry):
    toolbox = AssistantToolbox(studio_tree, actor=org_admin, share_report_source=False)
    assert "Canonical revenue" in build_system_prompt(toolbox)[0]["text"]
    assert "turned off" in toolbox.execute("read_doc", {"path": "metrics.yaml"})
    alias = studio_tree.project_root / "reports" / "registry-link"
    alias.symlink_to(registry)
    assert "turned off" in toolbox.execute("read_doc", {"path": "reports/registry-link"})


@pytest.mark.parametrize("content", [
    "metrics: [", "metrics: not-a-list", "metrics: [42]",
    "metrics: [{name: x}, {name: x}]",
    "metrics: &metrics [*metrics]",
    "metrics: [{name: x, owner: {password: PRIVATE_SECRET}}]",
    "metrics: [{name: x, tags: [!!python/object:os.system {}]}]",
    "metrics: " + "[" * 20 + "PRIVATE_SECRET" + "]" * 20,
    "metrics: []\n#" + "x" * metric_context.MAX_FILE_BYTES,
    "metrics: [" + ",".join("{name: metric_%s}" % i for i in range(201)) + "]",
])
def test_unsafe_yaml_is_unavailable_without_echoing_contents(studio_tree, registry, content):
    registry.write_text(content)
    toolbox = AssistantToolbox(studio_tree)
    for result in (metric_context.registry_text(toolbox), toolbox.execute("read_doc", {"path": "metrics.yaml"})):
        assert result.startswith("Error:")
        assert "PRIVATE_SECRET" not in result
        assert metric_context.UNAVAILABLE in result


def test_registry_symlink_cannot_leak_other_files(studio_tree, registry, tmp_path):
    secret = tmp_path / "secret.yaml"
    secret.write_text("metrics: [{name: x, description: SECRET_CONTENT}]")
    registry.unlink()
    registry.symlink_to(secret)
    toolbox = AssistantToolbox(studio_tree)
    assert "SECRET_CONTENT" not in build_system_prompt(toolbox)[0]["text"]
    for path in ("metrics.yaml", "./metrics.yaml", "reports/../metrics.yaml", str(secret)):
        result = toolbox.execute("read_doc", {"path": path})
        assert result.startswith("Error:")
        assert "SECRET_CONTENT" not in result


def test_registry_allowlist_is_a_file_not_a_directory(studio_tree, registry):
    registry.unlink()
    registry.mkdir()
    (registry / "config.yaml").write_text("PRIVATE_SECRET")
    result = AssistantToolbox(studio_tree).execute("read_doc", {"path": "metrics.yaml/config.yaml"})
    assert result.startswith("Error:")
    assert "PRIVATE_SECRET" not in result


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires FIFO support")
def test_registry_fifo_is_rejected_before_open(studio_tree, registry, monkeypatch):
    registry.unlink()
    os.mkfifo(registry)

    def forbidden(*args):
        raise AssertionError("A FIFO must be rejected without opening it")

    monkeypatch.setattr(metric_context.os, "open", forbidden)
    assert metric_context.registry_text(AssistantToolbox(studio_tree)).startswith("Error:")


def test_registry_output_is_bounded_and_omissions_explicit(studio_tree, registry):
    registry.write_text("metrics:\n" + "".join(
        f"  - name: metric_{i}\n    description: " + "é" * 1000 + "\n" for i in range(20)
    ))
    toolbox = AssistantToolbox(studio_tree)
    for result in (metric_context.registry_text(toolbox), toolbox.execute("read_doc", {"path": "metrics.yaml"})):
        assert len(result.encode()) <= metric_context.MAX_OUTPUT_BYTES
        assert "truncated" in result
        assert "not known to be absent" in result


def test_no_registry_cache_survives_changes_or_project_switch(studio_tree, registry, tmp_path):
    from types import SimpleNamespace

    toolbox = AssistantToolbox(studio_tree)
    assert "Canonical revenue" in metric_context.registry_text(toolbox)
    registry.write_text("metrics: [{name: changed}]")
    assert "Canonical revenue" not in metric_context.registry_text(toolbox)
    # Same studio identity, different tree: no process-wide registry cache.
    other = AssistantToolbox(SimpleNamespace(
        pk=studio_tree.pk, org=studio_tree.org, slug=studio_tree.slug, project_root=tmp_path,
    ))
    assert "changed" not in metric_context.registry_text(other)
    registry.unlink()
    assert metric_context.UNAVAILABLE in build_system_prompt(toolbox)[0]["text"]


def test_mcp_registry_visibility(client, org, studio_tree, org_admin, selected_viewer, registry):
    from apps.assistant.tests.test_mcp import call, key_for

    url = f"/s/{org.slug}/{studio_tree.slug}/mcp"
    _, admin_key = key_for(org_admin, org)
    _, selected_key = key_for(selected_viewer, org)
    full = call(client, url, admin_key, "read_doc", {"path": "metrics.yaml"})
    assert not full["isError"]
    assert "CANONICAL_SQL" in str(full)
    limited = call(client, url, selected_key, "read_doc", {"path": "metrics.yaml"})
    assert limited["isError"]
    assert "PRIVATE_METRIC_MEANING" not in str(limited)
