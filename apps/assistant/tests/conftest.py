"""Fixtures shared by the AI assistant suite."""
import pytest

from apps.core import roles
from apps.orgs.models import OrgAssistantConfig

FAKE_KEY = "sk-test-not-a-real-key"


@pytest.fixture
def viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("view@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


@pytest.fixture
def other_viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("view2@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


@pytest.fixture
def selected_viewer(make_user, org, studio_tree, report_row, settings):
    from apps.orgs.models import PermissionGroup, PermissionGroupGrant, PermissionGroupMembership
    from apps.reports.models import ReportPermissionGrant

    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    user = make_user("selected@demo.example", org=org)
    group = PermissionGroup.objects.create(org=org, name="Selected report viewers")
    grant = PermissionGroupGrant.objects.create(
        group=group,
        studio=studio_tree,
        role=roles.VIEWER,
        viewer_scope=PermissionGroupGrant.REPORT_SCOPE_SELECTED,
    )
    PermissionGroupMembership.objects.create(user=user, group=group)
    ReportPermissionGrant.objects.create(grant=grant, report=report_row)
    user.selected_report_grant = grant
    return user


@pytest.fixture
def prefix(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}/api/assistant"


@pytest.fixture
def make_assistant_config(db):
    def _make(org, **kwargs):
        defaults = {
            "enabled": True,
            "provider": "anthropic",
            "api_key": FAKE_KEY,
            "model": "claude-sonnet-4-6",
        }
        defaults.update(kwargs)
        cfg, _ = OrgAssistantConfig.objects.update_or_create(org=org, defaults=defaults)
        return cfg

    return _make


@pytest.fixture
def assistant_config(org, make_assistant_config):
    return make_assistant_config(org)
