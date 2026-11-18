"""LlmUsage ledger, cap enforcement and the budget endpoint."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from apps.assistant import budget
from apps.assistant.models import LlmUsage, ReportLlmUsage, month_start

pytestmark = pytest.mark.django_db


class TestLedger:
    def test_add_cost_accumulates(self, org, viewer):
        LlmUsage.add_cost(org, viewer, 0.01)
        LlmUsage.add_cost(org, viewer, 0.0250)
        assert LlmUsage.user_month_total(org, viewer) == Decimal("0.0350")
        assert LlmUsage.objects.filter(org=org, user=viewer).count() == 1

    def test_sub_hundredth_of_a_cent_is_still_booked(self, org, viewer):
        """A cheap model's single call is well under $0.0001; it used to round to nothing."""
        LlmUsage.add_cost(org, viewer, 0.00003)
        assert LlmUsage.user_month_total(org, viewer) == Decimal("0.000030")

    def test_add_cost_ignores_zero_and_negative(self, org, viewer):
        LlmUsage.add_cost(org, viewer, 0)
        LlmUsage.add_cost(org, viewer, -1)
        assert LlmUsage.objects.count() == 0
        assert LlmUsage.user_month_total(org, viewer) == Decimal("0")

    def test_month_rollover_keys_separate_rows(self, org, viewer):
        march = datetime(2026, 3, 15, tzinfo=timezone.utc)
        april = datetime(2026, 4, 1, tzinfo=timezone.utc)
        LlmUsage.add_cost(org, viewer, 1, when=march)
        LlmUsage.add_cost(org, viewer, 2, when=april)
        assert LlmUsage.objects.count() == 2
        assert LlmUsage.user_month_total(org, viewer, when=march) == Decimal("1.0000")
        assert LlmUsage.user_month_total(org, viewer, when=april) == Decimal("2.0000")
        assert set(LlmUsage.objects.values_list("month", flat=True)) == {
            date(2026, 3, 1), date(2026, 4, 1)
        }

    def test_month_total_sums_users(self, org, viewer, other_viewer):
        LlmUsage.add_cost(org, viewer, 1.5)
        LlmUsage.add_cost(org, other_viewer, 2.5)
        assert LlmUsage.month_total(org) == Decimal("4.0000")
        assert LlmUsage.user_month_total(org, viewer) == Decimal("1.5000")

    def test_other_org_spend_is_separate(self, org, other_org, viewer):
        LlmUsage.add_cost(org, viewer, 3)
        assert LlmUsage.month_total(other_org) == Decimal("0")


class TestEnforcement:
    def test_uncapped_allows(self, org, viewer, assistant_config):
        LlmUsage.add_cost(org, viewer, 999)
        assert budget.precheck(org, viewer) == (True, "")

    def test_per_user_cap_blocks(self, org, viewer, other_viewer, make_assistant_config):
        make_assistant_config(org, per_user_budget_usd=Decimal("1.00"))
        LlmUsage.add_cost(org, viewer, 1.0)
        allowed, reason = budget.precheck(org, viewer)
        assert allowed is False
        assert "Budget cap reached" in reason
        # ... and only for the user who spent it.
        assert budget.precheck(org, other_viewer)[0] is True

    def test_org_cap_blocks_everyone(self, org, viewer, other_viewer, make_assistant_config):
        make_assistant_config(org, monthly_budget_usd=Decimal("5.00"))
        LlmUsage.add_cost(org, other_viewer, 5)
        allowed, reason = budget.precheck(org, viewer)
        assert allowed is False
        assert "Organization budget cap reached" in reason

    def test_under_cap_allows(self, org, viewer, make_assistant_config):
        make_assistant_config(org, per_user_budget_usd=Decimal("1.00"))
        LlmUsage.add_cost(org, viewer, 0.99)
        assert budget.precheck(org, viewer)[0] is True

    def test_last_months_spend_does_not_block(self, org, viewer, make_assistant_config):
        make_assistant_config(org, per_user_budget_usd=Decimal("1.00"))
        LlmUsage.add_cost(org, viewer, 50, when=datetime(2020, 1, 5, tzinfo=timezone.utc))
        assert budget.precheck(org, viewer)[0] is True


class TestBudgetEndpoint:
    def test_shape(self, login, viewer, org, prefix, make_assistant_config):
        make_assistant_config(
            org, monthly_budget_usd=Decimal("100.00"), per_user_budget_usd=Decimal("10.00")
        )
        LlmUsage.add_cost(org, viewer, 2.5)
        body = login(viewer).get(f"{prefix}/budget").json()
        assert body["cost_usd"] == 2.5
        assert body["cap_usd"] == 10.0
        assert body["remaining_usd"] == 7.5
        assert body["blocked"] is False
        assert body["disabled"] is False
        assert body["org_cost_usd"] == 2.5
        assert body["org_cap_usd"] == 100.0
        assert body["month"] == month_start().isoformat()

    def test_uncapped_reports_disabled(self, login, viewer, prefix, assistant_config):
        body = login(viewer).get(f"{prefix}/budget").json()
        assert body["disabled"] is True
        assert body["cap_usd"] is None
        assert body["blocked"] is False

    def test_blocked_flag(self, login, viewer, org, prefix, make_assistant_config):
        make_assistant_config(org, per_user_budget_usd=Decimal("1.00"))
        LlmUsage.add_cost(org, viewer, 1.5)
        assert login(viewer).get(f"{prefix}/budget").json()["blocked"] is True

    def test_requires_auth(self, client, prefix):
        assert client.get(f"{prefix}/budget").status_code == 401

    def test_requires_studio_access(self, login, member, prefix):
        assert login(member).get(f"{prefix}/budget").status_code == 404


class TestReportBudget:
    def test_report_cost_shares_org_total_but_never_user_total(self, org, viewer, other_viewer):
        LlmUsage.add_cost(org, viewer, 1)
        budget.report_record(org, Decimal("0.25"))
        budget.report_record(org, Decimal("0.50"))
        assert LlmUsage.month_total(org) == Decimal("1.75")
        assert LlmUsage.user_month_total(org, viewer) == Decimal("1")
        assert LlmUsage.user_month_total(org, other_viewer) == Decimal("0")
        assert ReportLlmUsage.objects.filter(org=org).count() == 1
        assert not LlmUsage.objects.filter(user__isnull=True).exists()

    def test_reports_ignore_personal_cap(self, org, viewer, make_assistant_config):
        make_assistant_config(org, per_user_budget_usd=Decimal("1"))
        LlmUsage.add_cost(org, viewer, 1)
        assert not budget.precheck(org, viewer)[0]
        assert budget.report_precheck(org) == (True, "")

    def test_report_spend_does_not_block_personal_cap(self, org, viewer, make_assistant_config):
        make_assistant_config(org, per_user_budget_usd=Decimal("1"))
        budget.report_record(org, 10)
        assert budget.precheck(org, viewer) == (True, "")

    def test_shared_org_cap_blocks_reports_and_chats(self, org, viewer, make_assistant_config):
        make_assistant_config(org, monthly_budget_usd=Decimal("1"))
        assert budget.report_precheck(org) == (True, "")
        budget.report_record(org, 1)
        allowed, reason = budget.report_precheck(org)
        assert not allowed
        assert "Organization budget cap reached" in reason
        assert not budget.precheck(org, viewer)[0]

    def test_chat_spend_blocks_report_at_org_cap(self, org, viewer, make_assistant_config):
        make_assistant_config(org, monthly_budget_usd=Decimal("1"))
        LlmUsage.add_cost(org, viewer, 1)
        assert not budget.report_precheck(org)[0]

    def test_reports_ignore_failed_usage_probe(self, org, make_assistant_config):
        cfg = make_assistant_config(org, monthly_budget_usd=Decimal("1"))
        cfg.connection_check = {
            "revision": cfg.config_revision,
            "checks": {"usage": {"ok": False, "message": "Test connection first"}},
        }
        cfg.save(update_fields=["connection_check"])
        assert budget.report_precheck(org) == (True, "")

    def test_report_ledger_months_and_orgs_are_separate(self, org, other_org):
        march = datetime(2026, 3, 15, tzinfo=timezone.utc)
        april = datetime(2026, 4, 1, tzinfo=timezone.utc)
        ReportLlmUsage.add_cost(org, 1, when=march)
        ReportLlmUsage.add_cost(org, 2, when=april)
        assert LlmUsage.month_total(org, when=march) == Decimal("1")
        assert LlmUsage.month_total(org, when=april) == Decimal("2")
        assert LlmUsage.month_total(other_org, when=march) == Decimal("0")

    def test_report_zero_negative_and_small_costs(self, org):
        budget.report_record(org, 0)
        budget.report_record(org, -1)
        assert not ReportLlmUsage.objects.exists()
        budget.report_record(org, Decimal("0.00003"))
        assert LlmUsage.month_total(org) == Decimal("0.000030")


@pytest.mark.django_db(transaction=True)
def test_concurrent_report_charges_share_one_month_row(org):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import connections

    ready = Barrier(2)

    def charge():
        connections.close_all()
        try:
            ready.wait(timeout=10)
            budget.report_record(org, Decimal("0.25"))
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(charge)
        second = pool.submit(charge)
        first.result(timeout=20)
        second.result(timeout=20)
    assert ReportLlmUsage.objects.filter(org=org).count() == 1
    assert LlmUsage.month_total(org) == Decimal("0.50")
