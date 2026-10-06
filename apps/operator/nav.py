"""Operator-console navigation."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    label: str
    url_name: str


_ITEMS = (
    NavItem(label="Fleet", url_name="operator-fleet"),
    NavItem(label="Organizations", url_name="operator-orgs"),
)


def items() -> list[NavItem]:
    return list(_ITEMS)
