"""The cross-organization operator console.

One flat tier above Organization. On a hosted instance that is the service
operator; in a self-hosted install it is the customer's own IT admin.

Every mutating action goes through ``apps.core.audit``.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.core import impersonation
from apps.core.audit import audit
from apps.core.form_responses import is_settings_request, settings_error, settings_success
from apps.core.permissions import require_operator
from apps.operator import bounded
from apps.orgs.models import Organization, OrgMembership, OrgQuota
from apps.reports.models import Report
from apps.runner.models import Run, WorkerHeartbeat
from apps.studios.models import POOL_CHOICES, Studio


def _shell(extra: dict) -> dict:
    from apps.operator import nav

    return {"operator_nav": nav.items(), **extra}


@require_operator
def fleet(request):
    """Instance-wide health: workers, queue depth, recent failures."""
    workers, workers_more = bounded.capped(
        WorkerHeartbeat.alive()
        .only("id", "worker_id", "role", "running_count", "max_concurrent",
              "memory_budget_mb", "reserved_memory_mb", "last_beat_at")
        .order_by("role", "worker_id", "pk")
    )
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
        .only("id", "created_at", "slug", "status", "peak_memory_mb",
              "studio__id", "studio__slug", "studio__org__id", "studio__org__slug")
        .order_by("-created_at", "-pk")[:20]
    )
    heaviest = (
        Run.objects.filter(peak_memory_mb__isnull=False, created_at__gte=since)
        .select_related("studio", "studio__org")
        .only("id", "slug", "memory_limit_mb", "peak_memory_mb",
              "studio__id", "studio__slug", "studio__org__id", "studio__org__slug")
        .order_by("-peak_memory_mb", "-pk")[:10]
    )

    return render(
        request,
        "operator/fleet.html",
        _shell(
            {
                "workers": workers,
                "workers_more": workers_more,
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
    rows, more = bounded.capped(
        Organization.objects.only("id", "slug", "name", "is_active").order_by("slug", "pk")
    )
    ids = [org.pk for org in rows]
    studio_counts = dict(Studio.objects.filter(org_id__in=ids)
                         .values("org_id").annotate(n=Count("pk")).values_list("org_id", "n"))
    member_counts = dict(OrgMembership.objects.filter(org_id__in=ids)
                         .values("org_id").annotate(n=Count("pk")).values_list("org_id", "n"))
    active = {
        row["studio__org_id"]: row["n"]
        for row in Run.objects.filter(studio__org_id__in=ids, status__in=(Run.STARTING, Run.RUNNING))
        .values("studio__org_id")
        .annotate(n=Count("id"))
    }
    for org in rows:
        org.studio_count = studio_counts.get(org.pk, 0)
        org.member_count = member_counts.get(org.pk, 0)
        org.active_runs = active.get(org.pk, 0)
    return render(request, "operator/orgs.html", _shell({"orgs": rows, "orgs_more": more}))


@require_operator
def org_detail(request, org_slug):
    org = get_object_or_404(Organization, slug=org_slug)
    studios, studios_more = bounded.capped(
        Studio.objects.filter(org=org).only("id", "slug", "name", "pool").order_by("slug", "pk")
    )
    report_counts = dict(Report.objects.filter(studio_id__in=[studio.pk for studio in studios], present_in_scan=True)
                         .values("studio_id").annotate(n=Count("pk")).values_list("studio_id", "n"))
    for studio in studios:
        studio.report_count = report_counts.get(studio.pk, 0)
    members, members_more = bounded.capped(
        OrgMembership.objects.filter(org=org)
        .select_related("user")
        .only("id", "user_id", "role", "user__id", "user__email",
              "user__is_operator_flag", "user__is_superuser")
        .order_by("user__email", "pk")
    )
    recent = (
        Run.objects.filter(studio__org=org)
        .select_related("studio")
        .only("id", "created_at", "slug", "status", "peak_memory_mb", "studio__id", "studio__slug")
        .order_by("-created_at", "-pk")[:20]
    )
    quota = OrgQuota.objects.filter(org=org).first()
    quota_fields = (
        "max_concurrent_runs",
        "monthly_build_minutes",
        "max_studios",
        "max_reports",
        "max_memory_mb",
        "max_storage_mb",
    )
    quota_values = getattr(request, "quota_values", None) or {
        field: getattr(quota, field, 0) for field in quota_fields
    }
    quota_values["notes"] = (
        getattr(quota, "notes", "") if getattr(request, "quota_values", None) is None
        else request.quota_values["notes"]
    )
    return render(
        request,
        "operator/org_detail.html",
        _shell(
            {
                "org": org,
                "studios": studios,
                "studios_more": studios_more,
                "members": members,
                "members_more": members_more,
                "recent_runs": recent,
                "usage": bounded.usage(org),
                "quota": quota,
                "quota_values": quota_values,
                "quota_errors": getattr(request, "quota_errors", {}),
                "quotas_enforced": settings.TRELLUM_QUOTAS_ENABLED,
                "can_impersonate": settings.TRELLUM_IMPERSONATION_ENABLED,
                "pool_choices": POOL_CHOICES,
            }
        ),
    )


@require_operator
@require_GET
def org_usage(request, org_slug):
    """The last uploaded-files measurement; the organization page polls this quietly."""
    from apps.core import operator_observations as observations
    if impersonation.is_impersonating(request):
        raise Http404
    org = get_object_or_404(Organization, slug=org_slug)
    response = JsonResponse(observations.poll(observations.usage_key(org.pk)))
    response["Cache-Control"] = "no-store"
    return response


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
    errors = {}
    values = {}
    for field in fields:
        raw = (request.POST.get(field) or "").strip()
        try:
            value = max(0, int(raw or 0))
        except ValueError:
            errors[field] = ["Enter a whole number."]
            values[field] = request.POST.get(field, "")
            continue
        values[field] = value
        if getattr(quota, field) != value:
            changed[field] = value
        setattr(quota, field, value)
    notes = (request.POST.get("notes") or "")[:400]
    values["notes"] = notes
    if errors:
        if is_settings_request(request):
            return settings_error(request, "Please correct the highlighted fields.", errors=errors)
        request.quota_values = {field: request.POST.get(field, "") for field in fields}
        request.quota_values["notes"] = request.POST.get("notes", "")
        request.quota_errors = errors
        response = org_detail(request, org_slug)
        response.status_code = 400
        return response
    quota.notes = notes
    quota.save()

    audit(request, "operator.org.quota", target=org, org=org, **changed)
    if is_settings_request(request):
        return settings_success(
            request,
            "Quota saved. Zero means no limit.",
            values=values,
            refresh=["quota-usage"],
        )
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
