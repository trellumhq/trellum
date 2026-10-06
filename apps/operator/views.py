"""The cross-organization operator console.

One flat tier above Organization. On a hosted instance that is the service
operator; in a self-hosted install it is the customer's own IT admin.

Every mutating action goes through ``apps.core.audit``.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core import impersonation
from apps.core.audit import audit
from apps.core.permissions import require_operator
from apps.orgs import quotas as quota_service
from apps.orgs.models import Organization, OrgMembership, OrgQuota
from apps.runner.models import Run, WorkerHeartbeat
from apps.studios.models import POOL_CHOICES, Studio


def _shell(extra: dict) -> dict:
    from apps.operator import nav

    return {"operator_nav": nav.items(), **extra}


@require_operator
def fleet(request):
    """Instance-wide health: workers, queue depth, recent failures."""
    workers = list(WorkerHeartbeat.alive().order_by("role", "worker_id"))
    queue = Run.objects.filter(status=Run.QUEUED).count()
    running = Run.objects.filter(status__in=(Run.STARTING, Run.RUNNING)).count()

    since = timezone.now() - timezone.timedelta(hours=24)
    day = Run.objects.filter(created_at__gte=since)
    by_status = {
        row["status"]: row["n"]
        for row in day.values("status").annotate(n=Count("id"))
    }
    recent_failures = (
        Run.objects.filter(
            status__in=(Run.ERROR, Run.OOM_KILLED, Run.TIMEOUT), created_at__gte=since
        )
        .select_related("studio", "studio__org")
        .order_by("-created_at")[:20]
    )
    heaviest = (
        Run.objects.filter(peak_memory_mb__isnull=False, created_at__gte=since)
        .select_related("studio", "studio__org")
        .order_by("-peak_memory_mb")[:10]
    )

    return render(
        request,
        "operator/fleet.html",
        _shell(
            {
                "workers": workers,
                "queue_depth": queue,
                "running": running,
                "by_status": by_status,
                "recent_failures": recent_failures,
                "heaviest": heaviest,
            }
        ),
    )


@require_operator
def orgs(request):
    """Every organization, with the numbers that matter for support."""
    rows = (
        Organization.objects.annotate(
            studio_count=Count("studios", distinct=True),
            member_count=Count("memberships", distinct=True),
        )
        .order_by("slug")
        .prefetch_related("quota")
    )
    active = {
        row["studio__org_id"]: row["n"]
        for row in Run.objects.filter(status__in=(Run.STARTING, Run.RUNNING))
        .values("studio__org_id")
        .annotate(n=Count("id"))
    }
    for org in rows:
        org.active_runs = active.get(org.pk, 0)
    return render(request, "operator/orgs.html", _shell({"orgs": rows}))


@require_operator
def org_detail(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    studios = Studio.objects.filter(org=org).annotate(
        report_count=Count("reports", filter=Q(reports__present_in_scan=True))
    )
    members = (
        OrgMembership.objects.filter(org=org)
        .select_related("user")
        .order_by("user__email")
    )
    recent = (
        Run.objects.filter(studio__org=org)
        .select_related("studio")
        .order_by("-created_at")[:20]
    )
    return render(
        request,
        "operator/org_detail.html",
        _shell(
            {
                "org": org,
                "studios": studios,
                "members": members,
                "recent_runs": recent,
                "usage": quota_service.usage_state(org),
                "quota": OrgQuota.objects.filter(org=org).first(),
                "quotas_enforced": settings.TRELLUM_QUOTAS_ENABLED,
                "can_impersonate": settings.TRELLUM_IMPERSONATION_ENABLED,
                "pool_choices": POOL_CHOICES,
            }
        ),
    )


@require_operator
@require_POST
def org_set_active(request, org_slug):
    """Suspend or restore an organization.

    Suspension is what the permission decorators already key on — every org URL
    404s for its members while `is_active` is false — so this needs no new
    enforcement path.
    """
    org = get_object_or_404(Organization, slug=org_slug)
    org.is_active = request.POST.get("is_active") == "1"
    org.save(update_fields=["is_active"])
    audit(request, "operator.org.active", target=org, org=org, is_active=org.is_active)
    messages.success(
        request, f"{org.slug} is now {'active' if org.is_active else 'suspended'}."
    )
    return redirect("operator-org-detail", org_slug=org.slug)


@require_operator
@require_POST
def org_set_quota(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    quota, _ = OrgQuota.objects.get_or_create(org=org)

    fields = (
        "max_concurrent_runs",
        "monthly_build_minutes",
        "max_studios",
        "max_reports",
        "max_memory_mb",
        "max_storage_mb",
    )
    changed = {}
    for field in fields:
        raw = (request.POST.get(field) or "").strip()
        try:
            value = max(0, int(raw or 0))
        except ValueError:
            messages.error(request, f"{field} must be a whole number.")
            return redirect("operator-org-detail", org_slug=org.slug)
        if getattr(quota, field) != value:
            changed[field] = value
        setattr(quota, field, value)
    quota.notes = (request.POST.get("notes") or "")[:400]
    quota.save()

    audit(request, "operator.org.quota", target=org, org=org, **changed)
    messages.success(request, "Quota saved. Zero means no limit.")
    return redirect("operator-org-detail", org_slug=org.slug)


@require_operator
@require_POST
def studio_set_pool(request, org_slug, studio_slug):
    """Move a studio to another runner pool.

    An operator decision, not a tenant one: the pool picks which runners may
    build the studio, so it is about fleet capacity rather than anything the
    org configures about itself.
    """
    org = get_object_or_404(Organization, slug=org_slug)
    studio = get_object_or_404(Studio, org=org, slug=studio_slug)
    pool = request.POST.get("pool") or ""
    valid = {choice for choice, _ in POOL_CHOICES}
    if pool not in valid:
        messages.error(request, f"Unknown pool {pool!r}.")
        return redirect("operator-org-detail", org_slug=org.slug)

    studio.pool = pool
    studio.save(update_fields=["pool"])
    audit(request, "operator.studio.pool", target=studio, org=org, pool=pool)
    messages.success(
        request,
        f"{studio.slug} now builds on the {pool} pool. Runs already queued keep "
        f"the pool they were created with.",
    )
    return redirect("operator-org-detail", org_slug=org.slug)


@require_operator
@require_POST
def impersonate(request, org_slug, user_id):
    org = get_object_or_404(Organization, slug=org_slug)
    membership = get_object_or_404(
        OrgMembership.objects.select_related("user"), org=org, user_id=user_id
    )
    try:
        detail = impersonation.start(request, membership.user)
    except impersonation.ImpersonationError as exc:
        messages.error(request, str(exc))
        return redirect("operator-org-detail", org_slug=org.slug)

    # The session already belongs to the target by this point — impersonation
    # .start() has called login(). `actor` is therefore the impersonated user
    # and `impersonator` is the operator, which is the same shape as every
    # other row written during the window.
    audit(
        request,
        "operator.impersonate.start",
        target=membership.user,
        org=org,
        target_email=membership.user.email,
        **detail,
    )
    messages.info(
        request,
        f"You are now acting as {membership.user.email}. "
        f"Everything you do is recorded against your operator account.",
    )
    return redirect("home")


@require_operator
@require_POST
def member_unlock(request, org_slug, user_id):
    """Clear a login lockout for one member -- email-scoped only, see
    apps.accounts.throttle.unlock's docstring for why that is deliberate."""
    from apps.accounts import throttle

    org = get_object_or_404(Organization, slug=org_slug)
    membership = get_object_or_404(
        OrgMembership.objects.select_related("user"), org=org, user_id=user_id
    )
    cleared = throttle.unlock(membership.user.email)
    audit(request, "auth.unlock", target=membership.user, org=org)
    if cleared:
        messages.success(request, f"Login lockout cleared for {membership.user.email}.")
    else:
        messages.info(request, f"{membership.user.email} was not locked out.")
    return redirect("operator-org-detail", org_slug=org.slug)


@require_operator
@require_POST
def member_reset_mfa(request, org_slug, user_id):
    """Lost-device recovery: clear a member's device + recovery codes and
    force re-enrollment by ending their current sessions."""
    from apps.accounts import mfa

    org = get_object_or_404(Organization, slug=org_slug)
    membership = get_object_or_404(
        OrgMembership.objects.select_related("user"), org=org, user_id=user_id
    )
    if not membership.user.has_mfa:
        messages.error(request, f"{membership.user.email} does not have MFA enrolled.")
        return redirect("operator-org-detail", org_slug=org.slug)
    mfa.remove_device(membership.user)
    membership.user.bump_auth_epoch()
    audit(request, "auth.mfa_reset", target=membership.user, org=org, by_admin=True)
    messages.success(
        request,
        f"MFA reset for {membership.user.email}. Their sessions were ended; "
        f"they will be asked to re-enroll next time a policy requires it.",
    )
    return redirect("operator-org-detail", org_slug=org.slug)


@require_POST
def impersonate_stop(request):
    """Available to the impersonated session itself, not to operators only —
    the whole point is that the person in the borrowed account can get out."""
    if not impersonation.is_impersonating(request):
        raise Http404
    target_email = request.user.email
    operator = impersonation.stop(request)
    if operator is not None:
        audit(request, "operator.impersonate.stop", target_email=target_email)
        messages.success(request, f"Back to {operator.email}.")
    return redirect("operator-fleet")
