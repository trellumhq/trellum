"""The recorder and the action registry: category resolution, outcome,
redaction, size cap, user-agent truncation, audit_system's shape, and
registry completeness.

Registry completeness is the load-bearing test here: it greps every call
site under apps/ for the action string it passes to
audit()/audit_system() and asserts each one is registered in
apps.core.audit_actions.ACTIONS. That is what keeps the taxonomy honest --
a call site added without a matching registry entry fails the suite instead
of quietly degrading to the prefix fallback in production.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings

from apps.core.audit import audit, audit_system
from apps.core.audit_actions import (
    ACTIONS,
    CATEGORIES,
    OUTCOMES,
    UnknownAuditAction,
    category_for,
)
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


# ── Registry completeness ────────────────────────────────────────────────

_AUDIT_CALL_RE = re.compile(r'audit\(\s*request,\s*"([a-zA-Z0-9_.]+)"')
_AUDIT_SYSTEM_CALL_RE = re.compile(r'audit_system\(\s*"([a-zA-Z0-9_.]+)"')

#: apps/reports/views.py's share-policy endpoint builds its action name from
#: a local variable (`audit(request, action, org=request.org, **changed)`),
#: so the regex sweep below -- which only sees literal string arguments --
#: cannot discover it. The three values that variable actually takes are
#: checked explicitly instead.
_DYNAMIC_CALL_SITE_ACTIONS = {"share_policy.enable", "share_policy.disable", "share_policy.update"}


def _used_actions() -> set[str]:
    root = Path(settings.BASE_DIR)
    found: set[str] = set()
    for top in ("apps",):
        for path in (root / top).rglob("*.py"):
            posix = path.as_posix()
            if "/tests/" in posix or path.name.startswith("test_"):
                continue  # test fixtures may use throwaway action strings
            text = path.read_text(encoding="utf-8")
            found.update(_AUDIT_CALL_RE.findall(text))
            found.update(_AUDIT_SYSTEM_CALL_RE.findall(text))
    return found | _DYNAMIC_CALL_SITE_ACTIONS


class TestRegistryCompleteness:
    def test_every_call_site_action_is_registered(self):
        missing = _used_actions() - set(ACTIONS)
        assert not missing, (
            f"audit()/audit_system() call site(s) use action(s) not in "
            f"apps.core.audit_actions.ACTIONS: {sorted(missing)}"
        )

    def test_every_registry_entry_has_a_valid_category(self):
        for name, spec in ACTIONS.items():
            assert spec.category in CATEGORIES, name

    def test_taxonomy_size_is_a_deliberate_number(self):
        """Changes here should be a deliberate registry edit, not a typo or
        an accidental duplicate key silently dropping an entry. The size
        assertion makes additions and removals deliberate.
        """
        assert len(ACTIONS) == 103
        reserved = [name for name, spec in ACTIONS.items() if spec.reserved]
        assert len(reserved) == 0


# ── Category resolution ──────────────────────────────────────────────────

class TestCategoryResolution:
    def test_registered_action_resolves_from_the_registry(self):
        assert category_for("member.invite") == "authz"
        assert category_for("auth.sso_denied") == "auth"
        assert category_for("report.view") == "access"
        assert category_for("org.retention_set") == "admin"

    def test_unregistered_action_raises_in_debug(self):
        with pytest.raises(UnknownAuditAction):
            category_for("nonsense.action_nobody_registered", debug=True)

    def test_unregistered_action_falls_back_in_production(self):
        # Prefix match: "widget.foo" -> no "widget" entry -> default.
        assert category_for("widget.foo", debug=False) == "system"
        # A known prefix still resolves even without an exact registry hit.
        assert category_for("auth.some_new_thing", debug=False) == "auth"

    def test_audit_raises_in_debug_for_an_unknown_action(self, rf, org, org_admin, settings):
        settings.DEBUG = True
        request = rf.get("/anything")
        request.user = org_admin
        request.org = org
        with pytest.raises(UnknownAuditAction):
            audit(request, "totally.unregistered", target=org)

    def test_audit_degrades_gracefully_when_not_in_debug(self, rf, org, org_admin, settings):
        settings.DEBUG = False
        request = rf.get("/anything")
        request.user = org_admin
        request.org = org
        row = audit(request, "totally.unregistered", target=org)
        assert row is not None
        assert row.category == "system"  # the default-fallback category


# ── Outcome ───────────────────────────────────────────────────────────────

class TestOutcome:
    def test_default_outcome_is_success(self, rf, org, org_admin):
        request = rf.get("/anything")
        request.user = org_admin
        request.org = org
        row = audit(request, "member.invite", target=org)
        assert row.outcome == "success"

    def test_outcome_override(self, rf, org, org_admin):
        request = rf.get("/anything")
        request.user = org_admin
        request.org = org
        row = audit(request, "auth.sso_denied", outcome="denied", target=None)
        assert row.outcome == "denied"

    def test_outcome_values_match_the_documented_vocabulary(self):
        assert set(OUTCOMES) == {"success", "denied", "failure"}


# ── Redaction ─────────────────────────────────────────────────────────────

class TestRedaction:
    def test_sensitive_keys_are_redacted(self, rf, org, org_admin):
        request = rf.get("/anything")
        request.user = org_admin
        request.org = org
        row = audit(
            request, "member.invite", target=org,
            password="hunter2", client_secret="s3cret", cookie="abc",
            api_key="k", credential="c", authorization="bearer x",
        )
        for key in ("password", "client_secret", "cookie", "api_key", "credential", "authorization"):
            assert row.metadata[key] == "[redacted]", key

    def test_require_password_flag_is_explicitly_allowlisted(self, rf, org, org_admin):
        """share_policy.* 's own policy flag (apps/reports/views.py's
        api_share_policy) -- contains "password" as a substring but is a
        boolean setting, never a credential."""
        request = rf.get("/anything")
        request.user = org_admin
        request.org = org
        row = audit(request, "share_policy.update", org=org, require_password=True)
        assert row.metadata["require_password"] is True

    def test_token_suffix_is_explicitly_allowlisted(self, rf, org, org_admin):
        """The documented precedent (apps/reports/views.py's share-link
        create/revoke calls) -- a suffix is not the secret."""
        request = rf.get("/anything")
        request.user = org_admin
        request.org = org
        row = audit(request, "share_link.create", target=org, token_suffix="a1b2c3")
        assert row.metadata["token_suffix"] == "a1b2c3"

    def test_nested_one_level_is_also_scrubbed(self, rf, org, org_admin):
        request = rf.get("/anything")
        request.user = org_admin
        request.org = org
        row = audit(
            request, "member.invite", target=org,
            detail={"password": "hunter2", "email": "a@b.com"},
        )
        assert row.metadata["detail"]["password"] == "[redacted]"
        assert row.metadata["detail"]["email"] == "a@b.com"

    def test_non_sensitive_keys_are_untouched(self, rf, org, org_admin):
        request = rf.get("/anything")
        request.user = org_admin
        request.org = org
        row = audit(request, "member.invite", target=org, email="a@b.com", role="admin")
        assert row.metadata == {"email": "a@b.com", "role": "admin"}


# ── Size cap ──────────────────────────────────────────────────────────────

class TestMetadataCap:
    def test_oversized_metadata_is_truncated(self, rf, org, org_admin):
        request = rf.get("/anything")
        request.user = org_admin
        request.org = org
        huge = {"blob": "x" * 10_000}
        row = audit(request, "member.invite", target=org, **huge)
        assert row.metadata.get("_truncated") is True
        assert "blob" not in row.metadata  # the one oversized key didn't fit

    def test_small_metadata_is_untouched(self, rf, org, org_admin):
        request = rf.get("/anything")
        request.user = org_admin
        request.org = org
        row = audit(request, "member.invite", target=org, email="a@b.com")
        assert row.metadata == {"email": "a@b.com"}
        assert "_truncated" not in row.metadata


# ── User agent ────────────────────────────────────────────────────────────

class TestUserAgent:
    def test_user_agent_is_captured(self, rf, org, org_admin):
        request = rf.get("/anything", HTTP_USER_AGENT="Mozilla/5.0 Test")
        request.user = org_admin
        request.org = org
        row = audit(request, "member.invite", target=org)
        assert row.user_agent == "Mozilla/5.0 Test"

    def test_user_agent_is_truncated_to_the_column_width(self, rf, org, org_admin):
        request = rf.get("/anything", HTTP_USER_AGENT="x" * 500)
        request.user = org_admin
        request.org = org
        row = audit(request, "member.invite", target=org)
        assert len(row.user_agent) == 256

    def test_missing_user_agent_is_blank(self, rf, org, org_admin):
        request = rf.get("/anything")
        request.META.pop("HTTP_USER_AGENT", None)
        request.user = org_admin
        request.org = org
        row = audit(request, "member.invite", target=org)
        assert row.user_agent == ""


# ── audit_system ──────────────────────────────────────────────────────────

class TestAuditSystem:
    def test_request_less_row_has_no_actor_impersonator_ip_or_ua(self, org):
        row = audit_system("retention.purge", org=org, removed={"runs": 3})
        assert row.actor_id is None
        assert row.impersonator_id is None
        assert row.ip is None
        assert row.user_agent == ""
        assert row.outcome == "success"
        assert row.category == "system"
        assert row.metadata == {"removed": {"runs": 3}}

    def test_org_none_is_allowed(self):
        row = audit_system("org.retention_set", retention_built_days=30)
        assert row.org_id is None
        assert row.category == "admin"
        assert row.metadata == {"retention_built_days": 30}

    def test_never_raises_on_a_write_failure(self, monkeypatch, org):
        def boom(**kwargs):  # noqa: ARG001
            raise RuntimeError("db exploded")

        monkeypatch.setattr(AuditLog.objects, "create", boom)
        assert audit_system("retention.purge", org=org) is None

    def test_audit_also_never_raises_on_a_write_failure(self, monkeypatch, rf, org, org_admin):
        def boom(**kwargs):  # noqa: ARG001
            raise RuntimeError("db exploded")

        monkeypatch.setattr(AuditLog.objects, "create", boom)
        request = rf.get("/anything")
        request.user = org_admin
        request.org = org
        assert audit(request, "member.invite", target=org) is None
