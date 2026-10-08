from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import override_settings
import pytest

from apps.core import roles
from apps.studios.models import StudioMembership, StudioPreference

pytestmark = pytest.mark.django_db(transaction=True)


def test_existing_theme_moves_without_changing_membership_role(
    member, studio, grant_studio, make_user
):
    membership = grant_studio(member, studio, roles.VIEWER)
    with override_settings(MIGRATION_MODULES={}):
        loader = MigrationLoader(None)
    migration = loader.disk_migrations[("studios", "0013_studio_preference")]
    before = loader.project_state([("studios", "0012_studio_default_analysis_audience_and_more")])
    after = before.clone()
    states = [before]
    for operation in migration.operations:
        operation.state_forwards("studios", after)
        states.append(after.clone())

    old_membership = before.apps.get_model("studios", "StudioMembership")
    old_theme = old_membership._meta.get_field("theme")
    new_preference = after.apps.get_model("studios", "StudioPreference")
    try:
        with connection.schema_editor() as editor:
            if new_preference._meta.db_table in connection.introspection.table_names():
                editor.delete_model(new_preference)
            with connection.cursor() as cursor:
                columns = connection.introspection.get_table_description(
                    cursor, StudioMembership._meta.db_table
                )
            if not any(column.name == "theme" for column in columns):
                editor.add_field(old_membership, old_theme)
            migration.operations[0].database_forwards(
                "studios", editor, states[0], states[1]
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {old_membership._meta.db_table} SET theme = %s WHERE id = %s",
                    ["ocean", membership.pk],
                )
            migration.operations[1].database_forwards(
                "studios", editor, states[1], states[2]
            )
        assert StudioPreference.objects.get(user=member, studio=studio).theme == "ocean"
        membership.refresh_from_db()
        assert membership.role == roles.VIEWER

        # Previous-release ORM code can still read, update, and create
        # memberships with the retained legacy theme column.
        legacy_membership = old_membership.objects.get(pk=membership.pk)
        assert legacy_membership.theme == "ocean"
        legacy_membership.theme = "legacy-updated"
        legacy_membership.save(update_fields=["theme"])
        another_member = make_user("legacy@demo.example")
        created = old_membership.objects.create(
            user_id=another_member.pk,
            studio_id=studio.pk,
            role=roles.VIEWER,
            theme="legacy-created",
        )
        assert old_membership.objects.get(pk=created.pk).theme == "legacy-created"

        # Re-running a partially completed backfill creates missing rows and
        # leaves preferences already customized by users untouched.
        StudioPreference.objects.filter(user=member, studio=studio).update(theme="personal")
        with connection.schema_editor() as editor:
            migration.operations[1].database_forwards(
                "studios", editor, states[1], states[2]
            )
        assert StudioPreference.objects.get(user=member, studio=studio).theme == "personal"
        assert (
            StudioPreference.objects.get(user=another_member, studio=studio).theme
            == "legacy-created"
        )
    finally:
        with connection.schema_editor() as editor:
            if new_preference._meta.db_table in connection.introspection.table_names():
                editor.delete_model(new_preference)
            editor.create_model(StudioPreference)
