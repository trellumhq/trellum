from importlib import import_module

import pytest
from django.apps import apps
from django.db import connection
from django.db.migrations.state import ProjectState


@pytest.mark.django_db(transaction=True)
def test_existing_reports_receive_report_kind(studio):
    from apps.reports.models import Report

    # Exercise the actual schema/data operation without replaying unrelated history.
    state = ProjectState.from_apps(apps)
    state.remove_field("reports", "report", "kind")
    migration = import_module("apps.reports.migrations.0022_report_kind").Migration("0022_report_kind", "reports")
    with connection.schema_editor() as editor:
        editor.remove_field(Report, Report._meta.get_field("kind"))
    restored = False
    try:
        HistoricalReport = state.apps.get_model("reports", "Report")
        row = HistoricalReport.objects.create(studio_id=studio.pk, slug="existing-report")
        with connection.schema_editor() as editor:
            migration.apply(state, editor)
        restored = True
        assert Report.objects.get(pk=row.pk).kind == "report"
    finally:
        if not restored:
            with connection.schema_editor() as editor:
                editor.add_field(Report, Report._meta.get_field("kind"))
