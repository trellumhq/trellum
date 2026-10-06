import pytest
from django.core.exceptions import ValidationError
from django.forms.models import model_to_dict

from apps.alerts.admin import AlertRuleAdminForm

pytestmark = pytest.mark.django_db


def test_historical_mismatched_rule_stops_before_reading_data(rule, studio2, monkeypatch):
    from apps.alerts import evaluator
    from apps.alerts.models import AlertRule, AlertRun
    from apps.core import storage

    AlertRule.objects.filter(pk=rule.pk).update(studio=studio2)
    rule.refresh_from_db()

    def unexpected_read(*args, **kwargs):
        pytest.fail("An inconsistent rule must not read report storage")

    monkeypatch.setattr(storage, "read_meta", unexpected_read)
    run = evaluator.evaluate(rule)
    assert run.status == AlertRun.STATUS_ERROR
    assert "do not match" in run.error


def test_rule_rejects_a_report_from_another_studio(rule, studio2):
    rule.pk = None
    rule.studio = studio2

    with pytest.raises(ValidationError, match="report must belong"):
        rule.save()


def test_rule_rejects_a_studio_from_another_org(rule, other_org):
    rule.pk = None
    rule.org = other_org

    with pytest.raises(ValidationError, match="studio must belong"):
        rule.save()


def test_admin_rejects_cross_org_recipient_users_and_groups(
    rule, make_user, make_group, other_org
):
    outsider = make_user("outsider@rival.example", org=other_org)
    outsider_group = make_group("Rival", org_=other_org)
    data = model_to_dict(rule)
    data.update(
        org=rule.org_id,
        studio=rule.studio_id,
        report=rule.report_id,
        created_by=rule.created_by_id,
        recipients=[outsider.pk],
        recipient_groups=[outsider_group.pk],
    )

    form = AlertRuleAdminForm(data=data, instance=rule)

    assert not form.is_valid()
    assert "recipients" in form.errors
    assert "recipient_groups" in form.errors
