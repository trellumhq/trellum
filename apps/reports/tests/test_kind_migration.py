from importlib import import_module

import pytest
from django.apps import apps
from django.db import connection
from django.db.migrations.state import ProjectState


@pytest.mark.django_db(transaction=True)
def test_report_kind_migration_preserves_previous_release_compatibility(studio):
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
            migration.apply(state.clone(), editor)
        restored = True
        assert Report.objects.get(pk=row.pk).kind == "report"

        # The pre-kind model still reads, inserts and updates the upgraded table.
        historical_row = HistoricalReport.objects.get(pk=row.pk)
        historical_row.name = "Updated existing report"
        historical_row.save()
        assert Report.objects.get(pk=row.pk).name == "Updated existing report"
        created = HistoricalReport.objects.create(studio_id=studio.pk, slug="rollback-report")
        assert Report.objects.get(pk=created.pk).kind == "report"

        analysis = Report.objects.create(studio=studio, slug="analysis", kind="analysis")
        historical_analysis = HistoricalReport.objects.get(pk=analysis.pk)
        historical_analysis.name = "Updated analysis"
        historical_analysis.save()
        analysis.refresh_from_db()
        assert analysis.name == "Updated analysis"
        assert analysis.kind == "analysis"

        with connection.schema_editor(collect_sql=True) as editor:
            migration.apply(state.clone(), editor)
        sql = "\n".join(editor.collected_sql)
        assert "DEFAULT 'report'" in sql
        assert "DROP DEFAULT" not in sql
    finally:
        if not restored:
            with connection.schema_editor() as editor:
                editor.add_field(Report, Report._meta.get_field("kind"))
