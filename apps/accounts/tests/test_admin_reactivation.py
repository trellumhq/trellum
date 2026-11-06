"""Django-admin reactivation resets only the old disable warning."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core import retention
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db
ACTION = "account.reactivate"


@pytest.fixture
def dormant(settings):
    settings.RETENTION = {
        **settings.RETENTION, "dormant_days": 730,
        "dormant_disable_days": 30, "dormant_erase_days": 60,
    }
    return User.objects.create_user(
        email="dormant@example.test", is_active=False,
        date_joined=timezone.now() - timedelta(days=1000),
        last_login=timezone.now().replace(microsecond=0) - timedelta(days=900),
        dormancy_warned_at=timezone.now() - timedelta(days=40),
    )


def save(user, actor, *, change=True):
    request = RequestFactory().post("/admin/accounts/user/")
    request.user = actor
    admin.site._registry[User].save_model(request, user, form=None, change=change)


def change_data(user):
    data = {"email": user.email, "name": user.name, "password": user.password,
            "date_joined_0": user.date_joined.strftime("%Y-%m-%d"),
            "date_joined_1": user.date_joined.strftime("%H:%M:%S"),
            "last_login_0": user.last_login.strftime("%Y-%m-%d"),
            "last_login_1": user.last_login.strftime("%H:%M:%S"),
            "dormancy_warned_at_0": user.dormancy_warned_at.strftime("%Y-%m-%d"),
            "dormancy_warned_at_1": user.dormancy_warned_at.strftime("%H:%M:%S"),
            "is_active": "on", "_save": "Save"}
    return data


def test_admin_change_survives_sweep_and_future_warning_has_full_grace(
    client, superuser, dormant,
):
    client.force_login(superuser)
    old_login = dormant.last_login
    url = reverse("admin:accounts_user_change", args=[dormant.pk])
    response = client.post(url, change_data(dormant))
    assert response.status_code == 302
    dormant.refresh_from_db()
    assert dormant.is_active
    assert dormant.dormancy_warned_at is None
    assert dormant.last_login == old_login
    row = AuditLog.objects.get(action=ACTION)
    assert row.actor_id == superuser.pk
    assert (row.target_type, row.target_id) == ("accounts.user", str(dormant.pk))
    assert row.category == "admin" and row.outcome == "success"
    assert row.metadata == {}
    assert retention.disable_dormant_accounts()[1] == 0
    with patch.object(retention, "_send_dormancy_warning") as mail:
        assert retention.warn_dormant_accounts()[1] == 1
        mail.assert_called_once()
    assert retention.disable_dormant_accounts()[1] == 0
    future = timezone.now() + timedelta(days=31)
    with patch.object(retention.timezone, "now", return_value=future):
        assert retention.disable_dormant_accounts()[1] == 1
    dormant.refresh_from_db()
    assert not dormant.is_active
    assert AuditLog.objects.filter(action=ACTION).count() == 1


@pytest.mark.parametrize("active", [False, True])
def test_unrelated_edit_preserves_warning_and_does_not_audit(dormant, superuser, active):
    User.objects.filter(pk=dormant.pk).update(is_active=active)
    dormant.refresh_from_db()
    warning = dormant.dormancy_warned_at
    dormant.name = "Updated name"
    save(dormant, superuser)
    dormant.refresh_from_db()
    assert dormant.dormancy_warned_at == warning
    assert not AuditLog.objects.filter(action=ACTION).exists()


@pytest.mark.parametrize("active", [False, True])
def test_creation_is_not_reactivation(superuser, active):
    warning = timezone.now()
    user = User(email="new@example.test", is_active=active, dormancy_warned_at=warning)
    save(user, superuser, change=False)
    user.refresh_from_db()
    assert user.is_active == active and user.dormancy_warned_at == warning
    assert not AuditLog.objects.filter(action=ACTION).exists()


def test_database_state_decides_transition_and_repeat_is_not_audited(dormant, superuser):
    # The form instance may have been loaded before retention disabled it.
    dormant.is_active = True
    save(dormant, superuser)
    # An unchanged active save is no further transition.
    dormant.refresh_from_db()
    dormant.name = "Second save"
    save(dormant, superuser)
    assert AuditLog.objects.filter(action=ACTION).count() == 1


def test_later_admin_failure_rolls_back_reactivation_and_audit(client, superuser, dormant):
    client.force_login(superuser)
    warning = dormant.dormancy_warned_at
    with patch.object(admin.site._registry[User], "save_related", side_effect=RuntimeError("later failure")):
        with pytest.raises(RuntimeError, match="later failure"):
            client.post(reverse("admin:accounts_user_change", args=[dormant.pk]), change_data(dormant))
    dormant.refresh_from_db()
    assert not dormant.is_active and dormant.dormancy_warned_at == warning
    assert not AuditLog.objects.filter(action=ACTION).exists()


def test_failed_audit_rolls_back_reactivation(dormant, superuser):
    warning = dormant.dormancy_warned_at
    dormant.is_active = True
    with patch("apps.accounts.admin.audit", return_value=None):
        with pytest.raises(RuntimeError, match="audit"):
            save(dormant, superuser)
    dormant.refresh_from_db()
    assert not dormant.is_active and dormant.dormancy_warned_at == warning


def test_stale_disable_candidate_cannot_undo_reactivation(dormant, superuser):
    User.objects.filter(pk=dormant.pk).update(is_active=True)
    queryset = retention._dormant_candidates()
    # Reactivate after the sweep has read its candidate, before its UPDATE.
    original_iter = type(queryset).__iter__
    fired = False

    def after_read(qs):
        nonlocal fired
        rows = list(original_iter(qs))
        if qs.model is User and rows and not fired:
            fired = True
            User.objects.filter(pk=dormant.pk).update(is_active=False)
            dormant.is_active = True
            save(dormant, superuser)
        return iter(rows)

    with patch.object(type(queryset), "__iter__", after_read):
        assert retention.disable_dormant_accounts()[1] == 0
    dormant.refresh_from_db()
    assert fired and dormant.is_active and dormant.dormancy_warned_at is None
    assert not AuditLog.objects.filter(action="retention.dormancy_disabled").exists()


def test_nonstaff_cannot_reactivate(client, member, dormant):
    client.force_login(member)
    response = client.post(reverse("admin:accounts_user_change", args=[dormant.pk]), change_data(dormant))
    assert response.status_code == 302
    dormant.refresh_from_db()
    assert not dormant.is_active
    assert not AuditLog.objects.filter(action=ACTION).exists()
