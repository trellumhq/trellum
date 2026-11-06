"""Audit selectors preserve complete scoped choices without fetching history."""
from __future__ import annotations

import json
import os
from html import escape
from pathlib import Path

import pytest
from django.db import connection

from apps.core.audit_actions import ACTIONS, ActionSpec, emittable_actions, label_for
from apps.core.models import AuditLog
from apps.orgs.audit.views import _action_choices, _actor_choices, _base_queryset

pytestmark = pytest.mark.django_db


def test_action_choices_keep_every_used_emittable_label(org, other_org, monkeypatch, login, org_admin):
    monkeypatch.setitem(ACTIONS, "test.reserved", ActionSpec("system", "Reserved", reserved=True))
    names = list(emittable_actions())
    AuditLog.objects.bulk_create([
        AuditLog(org=org, action=name) for name in [*names, names[0], "test.reserved", "unknown.action"]
    ])
    expected = sorted(((name, label_for(name)) for name in names), key=lambda pair: pair[1])
    assert _action_choices(_base_queryset(org)) == expected
    assert _action_choices(_base_queryset(other_org)) == []
    # The helper must retain additional caller scope as well as the org.
    assert _action_choices(_base_queryset(org).filter(action=names[0])) == [
        (names[0], label_for(names[0]))
    ]
    response = login(org_admin).get(f"/orgs/{org.slug}/settings/audit")
    assert response.status_code == 200
    assert response.context["action_choices"] == expected
    html = response.content.decode()
    for name, label in expected:
        assert f"{escape(label)} ({name})</option>" in html


def test_actor_choices_include_historical_nonmembers_once(org, other_org, make_user):
    # An audit actor need not still have membership in the organization.
    historical = make_user("historical@example.test", org=other_org)
    stranger = make_user("stranger@example.test", org=other_org)
    AuditLog.objects.bulk_create([
        AuditLog(org=org, actor=historical, action="auth.login"),
        AuditLog(org=org, actor=historical, action="auth.logout"),
        AuditLog(org=org, action="auth.login_failed"),
        AuditLog(org=other_org, actor=stranger, action="auth.login"),
    ])
    assert _actor_choices(_base_queryset(org)) == [(historical.pk, historical.email)]


def _capture(call):
    statements = []

    def capture(execute, sql, params, many, context):
        statements.append((sql, params))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(capture):
        result = call()
    plans = []
    with connection.cursor() as cursor:
        for sql, params in statements:
            cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params)
            plans.append({"sql": sql, "params": params, "plan": cursor.fetchone()[0]})
    return result, plans


def _nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from _nodes(child)


def _scan_work(root):
    # Include bitmap index work, rechecks and index-only heap fetches: the
    # number of returned flags alone cannot detect a hidden history scan.
    scans = [node for node in _nodes(root) if "Scan" in node["Node Type"]]
    assert all(node.get("Relation Name", "core_auditlog") == "core_auditlog" for node in scans)
    return {
        "tuples": sum(
            (node["Actual Rows"] + node.get("Rows Removed by Filter", 0)
             + node.get("Rows Removed by Index Recheck", 0)) * node["Actual Loops"]
            for node in scans
        ),
        "heap_fetches": sum(node.get("Heap Fetches", 0) * node["Actual Loops"] for node in scans),
        "blocks": root.get("Shared Hit Blocks", 0) + root.get("Shared Read Blocks", 0),
    }


@pytest.mark.heavy
def test_action_selector_postgresql_history_growth(org, org_admin, other_org):
    """Measure exact helper SQL at 120/100120 rows, including absent actions.

    Includes an action with a large history only in a different tenant to
    verify that absent scoped choices do not scan that tenant's history.
    Actor output remains proportional to the complete historical actor set.
    """
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL execution-plan regression")
    records = []
    names = list(emittable_actions())
    for size in (120, 100120):
        previous = 0 if size == 120 else 120
        AuditLog.objects.bulk_create([
            AuditLog(org=org, actor=org_admin, action="run.enqueue", category="system")
            for _ in range(size - previous)
        ], batch_size=2000)
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE core_auditlog")
        base = _base_queryset(org)
        legacy, legacy_plans = _capture(lambda: list(base.values_list("action", flat=True).distinct()))
        assert len(legacy) == size  # timestamp ordering defeats DISTINCT(action)
        choices, plans = _capture(lambda: _action_choices(base))
        assert choices == [("run.enqueue", label_for("run.enqueue"))]
        assert len(plans) == 1
        root = plans[0]["plan"][0]["Plan"]
        assert root["Actual Rows"] == 1
        assert "LIMIT 1" in plans[0]["sql"]
        assert "DISTINCT" not in plans[0]["sql"]
        nodes = list(_nodes(root))
        # PostgreSQL can prefer sequential scans of the tiny table. At scale,
        # require indexed probes rather than a history scan hidden in one query.
        scans = [node for node in nodes if node.get("Relation Name") == "core_auditlog"]
        assert len(scans) == len(names) + 1
        work = _scan_work(root)
        assert work["tuples"] <= (len(names) + 1 if size > 120 else size * (len(names) + 1))
        assert work["heap_fetches"] <= len(names) + 1
        if size > 120:
            assert work["blocks"] <= 8 * (len(names) + 1)
            assert not any(node["Node Type"] in {"Sort", "Aggregate"} for node in nodes)
        actors, actor_plans = _capture(lambda: _actor_choices(base))
        assert actors == [(org_admin.pk, org_admin.email)]
        assert "created_at" not in actor_plans[0]["sql"]
        records.append({"size": size, "choices": choices, "plans": plans, "work": work, "legacy": legacy_plans, "actors": actor_plans})
    # An action used only in another org must never become an option here.
    AuditLog.objects.bulk_create([
        AuditLog(org=other_org, action="auth.login", category="auth") for _ in range(100000)
    ], batch_size=2000)
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE core_auditlog")
    for scoped_org, expected in ((org, "run.enqueue"), (other_org, "auth.login")):
        choices, plans = _capture(lambda: _action_choices(_base_queryset(scoped_org)))
        assert choices == [(expected, label_for(expected))]
        assert len(plans) == 1 and plans[0]["plan"][0]["Plan"]["Actual Rows"] == 1
        work = _scan_work(plans[0]["plan"][0]["Plan"])
        records.append({"size": 200120, "org": scoped_org.slug, "choices": choices, "plans": plans, "work": work})
        if directory := os.environ.get("AUDIT_PLAN_OUT"):
            Path(directory, "selector-plans.json").write_text(json.dumps(records, indent=2, default=str))
        assert work["tuples"] <= len(names) + 1
        assert work["heap_fetches"] <= len(names) + 1
        assert work["blocks"] <= 8 * (len(names) + 1)
