"""The one path by which an organization and its owner come into being.

Callers choose one of two explicit roles:

- ``/setup`` (the first-run wizard) and ``manage.py bootstrap`` create the
  **instance operator**: ``is_superuser=True`` plus org admin on the org they
  just created.
- ``instance_operator=False`` creates an ordinary user who administers only
  the new organization. It must never grant instance-wide operator access.

Both paths use this service so user, organization, and membership creation stay
atomic.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.core import roles

from .models import Organization, OrgMembership


class UserExistsError(Exception):
    """An account with this email already exists."""


class OrgSlugTakenError(ValueError):
    """An organization already owns this slug (slugs are immutable, so this
    is permanent — they key directory trees on the data volume)."""


@transaction.atomic
def provision_org_with_owner(
    *,
    email: str,
    name: str = "",
    password: str,
    org_name: str,
    org_slug: str,
    instance_operator: bool,
):
    """Create a user, an organization, and the membership binding them.

    Returns ``(user, org)``. Atomic: a failure anywhere leaves no half-built
    tenant behind.
    """
    User = get_user_model()

    email = email.strip()
    org_slug = org_slug.strip().lower()

    if User.objects.filter(email__iexact=email).exists():
        raise UserExistsError(email)
    if Organization.objects.filter(slug=org_slug).exists():
        raise OrgSlugTakenError(org_slug)

    create = User.objects.create_superuser if instance_operator else User.objects.create_user
    user = create(email=email, password=password, name=name)
    org = Organization.objects.create(slug=org_slug, name=org_name)
    OrgMembership.objects.create(user=user, org=org, role=roles.ORG_ADMIN)
    return user, org
