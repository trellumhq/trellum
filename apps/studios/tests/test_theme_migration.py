from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import override_settings
import pytest

from apps.core import roles
from apps.studios.models import StudioMembership, StudioPreference

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def migrated_preference(member, studio, grant_studio):
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
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {old_membership._meta.db_table} SET theme = %s WHERE id = %s",
                    ["ocean", membership.pk],
                )
            for index, operation in enumerate(migration.operations):
                operation.database_forwards("studios", editor, states[index], states[index + 1])
        yield before, migration, states, membership
    finally:
        with connection.schema_editor() as editor:
            if new_preference._meta.db_table in connection.introspection.table_names():
                editor.delete_model(new_preference)
            editor.create_model(StudioPreference)
            migration.operations[1].database_forwards("studios", editor, states[1], states[2])


def test_existing_theme_moves_without_changing_membership_role(
    migrated_preference, member, studio, make_user
):
    before, migration, states, membership = migrated_preference
    old_membership = before.apps.get_model("studios", "StudioMembership")
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
        migration.operations[-1].database_forwards(
            "studios", editor, states[-2], states[-1]
        )
    assert StudioPreference.objects.get(user=member, studio=studio).theme == "personal"
    assert (
        StudioPreference.objects.get(user=another_member, studio=studio).theme
        == "legacy-created"
    )


@pytest.mark.skipif(connection.vendor != "postgresql", reason="Portal upgrade compatibility requires PostgreSQL")
@pytest.mark.parametrize("parent", ["user", "studio"])
@pytest.mark.parametrize("delete_with", ["legacy_orm", "sql"])
def test_previous_release_deletion_cascades_to_preferences(
    migrated_preference, member, studio, parent, delete_with
):
    before, _, _, _ = migrated_preference
    preference = StudioPreference.objects.get(user=member, studio=studio)
    app_label, model_name, parent_id = (
        ("accounts", "User", member.pk) if parent == "user"
        else ("studios", "Studio", studio.pk)
    )
    old_parent = before.apps.get_model(app_label, model_name)
    assert "StudioPreference" not in {model.__name__ for model in before.apps.get_models()}
    if delete_with == "legacy_orm":
        old_parent.objects.get(pk=parent_id).delete()
    else:
        # Remove legacy children via the old collector, then issue the parent
        # DELETE directly so only the database can delete its preference.
        from django.db.models.deletion import Collector

        collector = Collector(using=connection.alias)
        collector.collect([old_parent.objects.get(pk=parent_id)])
        collector.data.pop(old_parent)
        collector.delete()
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {connection.ops.quote_name(old_parent._meta.db_table)} WHERE id = %s",
                [parent_id],
            )
    assert not StudioPreference.objects.filter(pk=preference.pk).exists()
    assert not old_parent.objects.filter(pk=parent_id).exists()
