"""Synthetic question intents and permission/evidence integration; no model calls."""
import json
from dataclasses import asdict

import pytest
import yaml

from apps.assistant import metric_context
from apps.assistant.tools import AssistantToolbox
from trellum.testing.test_metric_execution import fixture, freeze_clock

pytestmark = pytest.mark.django_db


@pytest.fixture
def canonical(studio_tree, report_row, monkeypatch):
    freeze_clock(monkeypatch)
    metric, entry, payload = fixture()
    root = studio_tree.project_root
    definition = asdict(metric)
    definition.pop("definition_hash")
    definition.pop("date_col")
    (root / "metrics.yaml").write_text(yaml.safe_dump(dict(version=1, datasets={
        "warehouse_daily": dict(provider="fixture:daily", time=dict(column="day", grain="day"), dimensions=["platform"])
    }, metrics=[definition, dict(name="hidden", description="UNRELATED_PRIVATE_DEFINITION")])))
    out = studio_tree.output_dir / report_row.slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps(payload))
    meta = dict(last_status="success", metric_execution_build="build-1", last_run=entry["generated_at"])
    (out / "_meta.json").write_text(json.dumps(meta))
    return out, payload, meta


def args(**extra):
    return dict(slug="player-overview", dataset_id="report_output", metric_id="revenue",
                start="2026-10-01", end="2026-10-03", **extra)


def test_canonical_end_to_end_and_provenance(studio_tree, org_admin, canonical):
    box = AssistantToolbox(studio_tree, actor=org_admin)
    result = json.loads(box.execute("query_metric", args()))
    assert result["status"] == "answered" and result["rows"] == [[100]]
    assert result["provenance"] == box.provenance
    assert box.provenance["kind"] == "metric_query"
    assert box.provenance["build_id"] == "build-1"
    assert box.provenance["period"] == dict(start="2026-10-01", end="2026-10-03", bounds="[start,end)")
    assert {s["name"] for s in box.schemas} >= {"list_metrics", "get_metric_definition", "query_metric"}


def test_report_bound_never_reads_full_registry_or_source_grants(
    studio_tree, selected_viewer, report_row, canonical, monkeypatch,
):
    box = AssistantToolbox(studio_tree, actor=selected_viewer, scope="report", report=report_row)
    monkeypatch.setattr(metric_context, "_load", lambda *a, **k: pytest.fail("registry disclosure"))
    result = box.execute("list_metrics", {"slug": report_row.slug})
    assert "revenue" in result and "hidden" not in result
    assert json.loads(box.execute("query_metric", args()))["rows"] == [[100]]
    bad = args()
    bad["slug"] = "other"
    assert "100" not in box.execute("query_metric", bad)
    assert not {"list_data_sources", "describe_source", "query_source"} & {s["name"] for s in box.schemas}


def test_source_sharing_disabled_never_returns_sql(studio_tree, org_admin, canonical):
    box = AssistantToolbox(studio_tree, actor=org_admin, share_report_source=False)
    path = studio_tree.project_root / "metrics.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["metrics"][1]["sql"] = "PRIVATE_SQL"
    path.write_text(yaml.safe_dump(doc))
    call = {k: v for k, v in args().items() if k not in ("start", "end")}
    result = box.execute("get_metric_definition", call)
    assert json.loads(result)["status"] == "answered"
    assert "PRIVATE_SQL" not in result and "UNRELATED_PRIVATE_DEFINITION" not in result


@pytest.mark.parametrize("mutation", ["definition", "semantics", "revoked", "rebuild", "legacy", "chunks", "override", "failed"])
def test_unavailable_never_emits_success_or_provenance(studio_tree, org_admin, canonical, mutation, monkeypatch):
    out, payload, meta = canonical
    box = AssistantToolbox(studio_tree, actor=org_admin)
    if mutation in ("definition", "semantics"):
        path = studio_tree.project_root / "metrics.yaml"
        doc = yaml.safe_load(path.read_text())
        if mutation == "definition":
            doc["metrics"][0]["column"] = "different"
        else:
            doc["metrics"][0]["execution"]["timezone"] = "UTC"
        path.write_text(yaml.safe_dump(doc))
    elif mutation == "revoked":
        org_admin.is_active = False
        org_admin.save(update_fields=["is_active"])
    elif mutation == "rebuild":
        original = box.read_meta
        calls = []
        def changing(slug):
            calls.append(slug)
            return original(slug) if len(calls) == 1 else {**meta, "metric_execution_build": "build-2"}
        monkeypatch.setattr(box, "read_meta", changing)
    elif mutation == "legacy":
        payload.pop("_metric_execution")
    elif mutation == "chunks":
        payload["_chunk_manifests"] = {"report_output": {}}
    elif mutation == "override":
        payload["components"]["kpi"]["kpis"][0]["column"] = "count"
    elif mutation == "failed":
        meta["validation"] = {"summary": {"fail": 1}}
    (out / "data.json").write_text(json.dumps(payload))
    (out / "_meta.json").write_text(json.dumps(meta))
    result = box.execute("query_metric", args())
    assert '"answered"' not in result and box.provenance is None


# A deterministic routing/parameter contract, not a claim of model accuracy.
# Questions are fixture labels; expected tool calls are independently explicit.
INTENTS = [
    ("What was revenue over October 1 and 2?", {}, "answered", [[100]]),
    ("Revenue on iOS for those two days?", {"filters": {"platform": ["ios"]}}, "answered", [[40]]),
    ("Split that revenue by platform.", {"breakdown": ["platform"]}, "answered", [["android", 60], ["ios", 40]]),
    ("Revenue for a platform not present in this period?", {"filters": {"platform": ["web"]}}, "clarification_required", None),
    ("Split revenue by a dimension this artifact does not publish.", {"breakdown": ["country"]}, "clarification_required", None),
    ("Revenue for an unavailable date range?", {"start": "2026-09-01"}, "incomplete", None),
    ("Try another output dataset with the same columns.", {"dataset_id": "warehouse_daily"}, "unsupported", None),
]


@pytest.mark.parametrize("question,parameters,status,rows", INTENTS)
def test_question_intent_contract(studio_tree, org_admin, canonical, question, parameters, status, rows):
    box = AssistantToolbox(studio_tree, actor=org_admin)
    call = {**args(), **parameters}
    result = json.loads(box.execute("query_metric", call))
    assert result["status"] == status, question
    assert result.get("rows") == rows, question


def test_invalid_slug_cannot_escape_studio_output(studio_tree, org_admin, canonical, monkeypatch):
    box = AssistantToolbox(studio_tree, actor=org_admin)
    monkeypatch.setattr(box, "_report_output", lambda *a: pytest.fail("path traversal"))
    call = args()
    call["slug"] = "../../other-studio/report"
    assert json.loads(box.execute("query_metric", call))["status"] == "clarification_required"


def test_unknown_calculation_arguments_and_malformed_registry_are_safe(studio_tree, org_admin, canonical):
    box = AssistantToolbox(studio_tree, actor=org_admin)
    assert json.loads(box.execute("query_metric", args(sql="SELECT PRIVATE_SQL")))["status"] == "clarification_required"
    (studio_tree.project_root / "metrics.yaml").write_text("metrics: [ PRIVATE_PARSER_CONTENT: [")
    result = box.execute("query_metric", args())
    assert json.loads(result)["status"] == "unsupported"
    assert "PRIVATE_PARSER_CONTENT" not in result and box.provenance is None


def test_metric_evidence_is_persisted_and_revocation_blocks_history(studio_tree, viewer, canonical, assistant_config):
    from django.core.exceptions import PermissionDenied
    from apps.assistant.models import AssistantSession
    from apps.assistant.source_tools import check_history
    from apps.studios.models import StudioMembership
    session = AssistantSession.objects.create(user=viewer, studio=studio_tree, org=studio_tree.org)
    box = AssistantToolbox(studio_tree, actor=viewer, session=session)
    result = json.loads(box.execute("query_metric", args()))
    assert result["status"] == "answered"
    assert result["provenance"]["schema_version"] == 1
    assert len(result["provenance"]["evidence_id"]) == 64
    assert session.state["_metric_reports"] == ["player-overview"]
    check_history(session, actor=viewer, studio=studio_tree)
    StudioMembership.objects.filter(user=viewer, studio=studio_tree).delete()
    with pytest.raises(PermissionDenied):
        check_history(session, actor=viewer, studio=studio_tree)
