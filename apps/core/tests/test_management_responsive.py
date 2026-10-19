"""Responsive contracts for the server-rendered management record lists.

These checks deliberately inspect the templates that users receive.  The
mobile presentation is CSS driven, so a missing data label is a rendering
regression even when the desktop table still looks correct.
"""
from pathlib import Path

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

ROOT = Path(__file__).resolve().parents[3]


TABLE_TEMPLATES = {
    "templates/orgs/members.html": ("Member", "Org role", "Groups", "Effective access", "Actions"),
    "templates/orgs/invites.html": ("Email", "Org role", "Invited", "Expires", "Link", "Actions"),
    "templates/orgs/studios.html": ("Studio", "Name &amp; description", "Members", "Reports", "Actions"),
    "templates/studios/members.html": ("User", "Role", "Actions"),
    "templates/accounts/account.html": ("Device", "IP", "Last seen", "Actions"),
    "templates/reports/analytics.html": ("Report", "Views", "Unique viewers", "Share views", "Last viewed", "Actions"),
    "templates/reports/experiments.html": ("Experiment", "Status", "Days", "Primary metric", "Lift", "Significance", "Actions"),
    "templates/reports/my_deliveries.html": ("Report", "Studio", "Creator", "Cadence", "Format", "Recipients", "Status", "Actions"),
}


@pytest.mark.parametrize(("template_name", "labels"), TABLE_TEMPLATES.items())
def test_management_record_templates_expose_mobile_labels(template_name, labels):
    source = (ROOT / template_name).read_text(encoding="utf-8")
    for label in labels:
        assert f'data-label="{label}"' in source, (template_name, label)


def test_delivery_template_renders_empty_state_without_table():
    # This is a real Django template render; no model or database is needed for
    # the empty branch and it protects the explicit table/empty-state contract.
    html = render_to_string(
        "reports/my_deliveries.html", {"schedule_rows": []},
        request=RequestFactory().get("/deliveries"),
    )
    assert "No scheduled deliveries match these filters." in html
    assert 'id="deliveryTable"' not in html
