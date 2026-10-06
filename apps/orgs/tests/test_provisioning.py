"""The atomic organization-and-owner provisioning service.

``instance_operator=False`` creates an organization admin without granting
instance-wide access. That role boundary must remain explicit.
"""
import pytest

from apps.orgs.models import Organization, OrgMembership
from apps.orgs.provisioning import (
    OrgSlugTakenError,
    UserExistsError,
    provision_org_with_owner,
)

pytestmark = pytest.mark.django_db

BASE = {
    "email": "owner@demo.example",
    "name": "Owner",
    "password": "Sup3r-secret-pw",
    "org_name": "Demo",
    "org_slug": "demo",
}


def test_self_hosted_owner_is_an_instance_operator():
    user, org = provision_org_with_owner(**BASE, instance_operator=True)
    assert user.is_superuser and user.is_staff
    assert OrgMembership.objects.get(user=user, org=org).role == "admin"


def test_org_owner_administers_only_their_own_org():
    user, org = provision_org_with_owner(**BASE, instance_operator=False)
    assert not user.is_superuser
    assert not user.is_staff
    assert OrgMembership.objects.get(user=user, org=org).role == "admin"


def test_password_is_usable():
    user, _ = provision_org_with_owner(**BASE, instance_operator=False)
    assert user.check_password("Sup3r-secret-pw")


def test_email_and_slug_are_normalised():
    user, org = provision_org_with_owner(
        **{**BASE, "email": "  Owner@Demo.example  ", "org_slug": " DEMO "},
        instance_operator=False,
    )
    assert org.slug == "demo"
    assert user.email.endswith("@demo.example")


def test_duplicate_email_is_refused(make_user):
    make_user("owner@demo.example")
    with pytest.raises(UserExistsError):
        provision_org_with_owner(**BASE, instance_operator=False)
    assert not Organization.objects.filter(slug="demo").exists()


def test_duplicate_slug_is_refused(org):
    with pytest.raises(OrgSlugTakenError):
        provision_org_with_owner(**BASE, instance_operator=False)


def test_a_failure_leaves_no_half_built_tenant(org):
    """The org exists already, so the call must fail *after* validating and
    leave the user it would have created uncreated."""
    from django.contrib.auth import get_user_model

    with pytest.raises(OrgSlugTakenError):
        provision_org_with_owner(**BASE, instance_operator=True)
    assert not get_user_model().objects.filter(email="owner@demo.example").exists()
