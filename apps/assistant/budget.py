"""AI assistant spend caps.

The legacy portal tracked one global per-user cap in
``output/chat_budget.json``. Here the caps belong to the org that owns the
API key (``OrgAssistantConfig.monthly_budget_usd`` / ``per_user_budget_usd``) and
the ledger is :class:`apps.assistant.models.LlmUsage`, keyed by calendar month.

A cap left blank means "no cap" — the same behaviour the legacy
``CHAT_BUDGET_DISABLED`` escape hatch gave a single-tenant install.
"""
from __future__ import annotations

from decimal import Decimal

from apps.assistant.models import LlmUsage, month_start


def _caps(org):
    from apps.orgs.models import OrgAssistantConfig

    cfg = OrgAssistantConfig.objects.filter(org=org).first()
    if cfg is None:
        return None, None
    return cfg.monthly_budget_usd, cfg.per_user_budget_usd


def _remaining(cap, spent) -> float | None:
    if cap is None:
        return None
    return round(float(max(Decimal("0"), Decimal(cap) - spent)), 4)


def usage_state(org, user) -> dict:
    """Budget snapshot for one user. Superset of the legacy shape.

    Legacy keys (``cost_usd`` / ``cap_usd`` / ``remaining_usd`` / ``blocked``
    / ``disabled``) describe the requesting user; the ``org_*`` keys expose
    the organization-wide pool the same month.
    """
    org_cap, user_cap = _caps(org)
    user_spend = LlmUsage.user_month_total(org, user)
    org_spend = LlmUsage.month_total(org)
    blocked, _ = _blocked_reason(org_cap, user_cap, org_spend, user_spend)
    return {
        "month": month_start().isoformat(),
        "cost_usd": round(float(user_spend), 4),
        "cap_usd": float(user_cap) if user_cap is not None else None,
        "remaining_usd": _remaining(user_cap, user_spend),
        "blocked": blocked,
        "disabled": org_cap is None and user_cap is None,
        "org_cost_usd": round(float(org_spend), 4),
        "org_cap_usd": float(org_cap) if org_cap is not None else None,
        "org_remaining_usd": _remaining(org_cap, org_spend),
    }


def _blocked_reason(org_cap, user_cap, org_spend, user_spend) -> tuple[bool, str]:
    if user_cap is not None and user_spend >= Decimal(user_cap):
        return True, (
            f"Budget cap reached: ${user_spend:.2f} / ${Decimal(user_cap):.2f}. "
            "Contact an admin to raise your cap."
        )
    if org_cap is not None and org_spend >= Decimal(org_cap):
        return True, (
            f"Organization budget cap reached: ${org_spend:.2f} / "
            f"${Decimal(org_cap):.2f} this month. Contact an org admin to "
            "raise the cap."
        )
    return False, ""


def precheck(org, user) -> tuple[bool, str]:
    """(allowed, reason) — call BEFORE every LLM request."""
    org_cap, user_cap = _caps(org)
    if org_cap is None and user_cap is None:
        return True, ""
    blocked, reason = _blocked_reason(
        org_cap, user_cap, LlmUsage.month_total(org), LlmUsage.user_month_total(org, user)
    )
    return (not blocked), reason


def record(org, user, cost_usd) -> Decimal:
    """Book a charge; returns the user's new month-to-date total."""
    return LlmUsage.add_cost(org, user, cost_usd)
