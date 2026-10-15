"""Mounted at /s/<org>/<studio>/alerts (trellum_portal.urls studio_patterns).

The studio routes carry no trailing slash, so the index is ``alerts`` and
the rest hang off a nested ``/`` include: same paths as spelling them
``/new``, ``/<id>`` here, without Django's urls.W002.
"""
from django.urls import include, path

from apps.alerts import views

urlpatterns = [
    path("", views.alerts_page, name="studio-alerts"),
    path("/", include([
        path("report-choices", views.report_choices, name="studio-alert-report-choices"),
        path("recipient-choices", views.recipient_choices, name="studio-alert-recipient-choices"),
        path("new", views.rule_form, name="studio-alert-new"),
        path("<int:rule_id>", views.rule_detail, name="studio-alert"),
        path("<int:rule_id>/edit", views.rule_form, name="studio-alert-edit"),
        path("<int:rule_id>/test", views.rule_test, name="studio-alert-test"),
        path("<int:rule_id>/toggle", views.rule_toggle, name="studio-alert-toggle"),
        path("<int:rule_id>/delete", views.rule_delete, name="studio-alert-delete"),
        path("<int:rule_id>/reconcile", views.rule_reconcile, name="studio-alert-reconcile"),
    ])),
]
