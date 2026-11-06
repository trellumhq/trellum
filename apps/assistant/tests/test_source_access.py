"""Source grants never inherit report or administrator read authority."""
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.assistant.models import AssistantSession, SourceReadGrant
from apps.assistant.source_access import grants_for, policy_fingerprint, resolve_grant
from apps.core import roles
from apps.datasources.models import DataSource
from apps.orgs.models import OrgMembership, PermissionGroupMembership
from apps.studios.models import StudioMembership

pytestmark = pytest.mark.django_db


@pytest.fixture
def source_context(studio, org_admin, assistant_config):
    source = DataSource.objects.create(studio=studio, name="warehouse", type="postgres", config={"host": "synthetic.invalid"}, credentials={"user": "synthetic", "password": "test-only"})
    grant = SourceReadGrant.objects.create(studio=studio, datasource=source, enabled=True, relations=["analytics.sales"])
    grant.users.add(org_admin)
    session = AssistantSession.objects.create(org=studio.org, studio=studio, user=org_admin)
    return studio, org_admin, session, grant


def resolve(ctx):
    studio, actor, session, grant = ctx
    return resolve_grant(studio, actor, grant.pk, session=session)


def test_admin_needs_explicit_grant_membership(source_context):
    assert resolve(source_context).pk == source_context[3].pk
    source_context[3].users.clear()
    with pytest.raises(PermissionDenied):
        resolve(source_context)


@pytest.mark.parametrize("change", ["user", "org", "membership", "grant", "provider"])
def test_revocation_is_fresh(source_context, assistant_config, change):
    studio, user, _, grant = source_context
    assert resolve(source_context)
    if change == "user":
        type(user).objects.filter(pk=user.pk).update(is_active=False)
    elif change == "org":
        type(studio.org).objects.filter(pk=studio.org_id).update(is_active=False)
    elif change == "membership":
        OrgMembership.objects.filter(user=user, org=studio.org).delete()
    elif change == "provider":
        type(assistant_config).objects.filter(pk=assistant_config.pk).update(enabled=False)
    else:
        SourceReadGrant.objects.filter(pk=grant.pk).update(enabled=False)
    with pytest.raises(PermissionDenied):
        resolve(source_context)


def test_studio_membership_and_group_revocation(source_context, member, make_group, attach_group, grant_studio):
    studio, _, _, grant = source_context
    grant_studio(member, studio, roles.DEVELOPER)
    group = make_group("Readers")
    attach_group(member, group)
    grant.groups.add(group)
    session = AssistantSession.objects.create(org=studio.org, studio=studio, user=member)
    ctx = studio, member, session, grant
    assert resolve(ctx)
    StudioMembership.objects.filter(user=member, studio=studio).delete()
    with pytest.raises(PermissionDenied):
        resolve(ctx)
    grant_studio(member, studio, roles.DEVELOPER)
    PermissionGroupMembership.objects.filter(user=member).delete()
    with pytest.raises(PermissionDenied):
        resolve(ctx)


def test_persisted_full_conversation_required(source_context):
    studio, user, session, grant = source_context
    for absent in (None, AssistantSession(user=user, studio=studio, org=studio.org)):
        with pytest.raises(PermissionDenied, match="persisted studio conversation"):
            resolve_grant(studio, user, grant.pk, session=absent)
    # Simulate a stored report scope without building unrelated report fixtures.
    AssistantSession.objects.filter(pk=session.pk).update(scope="report")
    with pytest.raises(PermissionDenied, match="Start a studio conversation"):
        resolve(source_context)


def test_session_owner_and_tenant_boundaries(source_context, member, other_studio):
    studio, _, session, grant = source_context
    with pytest.raises(PermissionDenied):
        resolve_grant(studio, member, grant.pk, session=session)
    with pytest.raises(PermissionDenied):
        resolve_grant(other_studio, source_context[1], grant.pk, session=session)


@pytest.mark.parametrize("relations", [[], ["sales"], ["pg_catalog.pg_class"], ["pg_temp.x"], ["analytics.sales"] * 101, ["a.b", "a.b"], {"x": "y"}])
def test_relations_are_explicit_and_bounded(source_context, relations):
    grant = source_context[3]
    grant.relations = relations
    with pytest.raises(ValidationError):
        grant.save()


def test_source_and_principal_validation(source_context, other_studio, other_org, make_user, make_group):
    grant = source_context[3]
    other = DataSource.objects.create(studio=other_studio, name="other", type="postgres")
    grant.datasource = other
    with pytest.raises(ValidationError):
        grant.save()
    grant.refresh_from_db()
    outsider = make_user("outsider@example.test", org=other_org)
    group = make_group("other", org_=other_org)
    with pytest.raises(ValidationError), transaction.atomic():
        grant.users.add(outsider)
    with pytest.raises(ValidationError), transaction.atomic():
        grant.groups.add(group)
    with pytest.raises(ValidationError), transaction.atomic():
        outsider.source_read_grants.add(grant)
    with pytest.raises(ValidationError), transaction.atomic():
        group.source_read_grants.add(grant)


@pytest.mark.parametrize("change", ["relations", "revision", "credentials", "config", "provider", "principals"])
def test_policy_pin_changes(source_context, assistant_config, member, change):
    grant = resolve(source_context)
    before = policy_fingerprint(grant)
    if change in ("relations", "revision"):
        setattr(grant, change, ["analytics.other"] if change == "relations" else 2)
        grant.save()
    elif change in ("credentials", "config"):
        setattr(grant.datasource, change, {"password": "rotated"} if change == "credentials" else {"host": "changed.invalid"})
        grant.datasource.save()
    elif change == "provider":
        assistant_config.model = "changed-model"
        assistant_config.save()
    else:
        grant.users.add(member)
    after = policy_fingerprint(resolve(source_context))
    assert before != after
    assert "test-only" not in after
    assert len(after) == 64


def test_grant_list_has_no_per_row_queries(source_context):
    studio, user, session, grant = source_context
    for n in range(3):
        source = DataSource.objects.create(studio=studio, name=f"source{n}", type="postgres")
        extra = SourceReadGrant.objects.create(studio=studio, datasource=source, enabled=True, relations=["a.b"])
        extra.users.add(user)
    # Context reads have constant cost; rows and their principals are batched.
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    with CaptureQueriesContext(connection) as captured:
        rows = list(grants_for(studio, user, session=session)[:25])
        for row in rows:
            list(row.users.all())
            list(row.groups.all())
            row.datasource.name
    count = len(captured)
    with CaptureQueriesContext(connection) as captured:
        rows = list(grants_for(studio, user, session=session)[:1])
        list(rows[0].users.all())
        list(rows[0].groups.all())
    assert len(captured) == count


def test_ssh_and_connector_changes_fail_closed(source_context):
    grant = source_context[3]
    DataSource.objects.filter(pk=grant.datasource_id).update(config={"ssh_host": "blocked.invalid"})
    with pytest.raises(PermissionDenied):
        resolve(source_context)
    DataSource.objects.filter(pk=grant.datasource_id).update(config={}, type="mysql")
    with pytest.raises(PermissionDenied):
        resolve(source_context)


def test_org_source_is_shared_but_sibling_source_is_not(source_context, studio2):
    studio, user, session, _ = source_context
    shared = DataSource.objects.create(org=studio.org, name="shared", type="postgres")
    grant = SourceReadGrant.objects.create(studio=studio, datasource=shared, enabled=True, relations=["a.b"])
    grant.users.add(user)
    assert resolve_grant(studio, user, grant.pk, session=session).pk == grant.pk
    sibling = DataSource.objects.create(studio=studio2, name="sibling", type="postgres")
    grant.datasource = sibling
    with pytest.raises(ValidationError):
        grant.save()


def test_fingerprints_use_prefetched_policy(source_context):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    studio, user, session, _ = source_context
    rows = list(grants_for(studio, user, session=session)[:25])
    with CaptureQueriesContext(connection) as queries:
        for grant in rows:
            policy_fingerprint(grant)
    assert not queries
