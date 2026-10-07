"""Instance-level endpoints: health, version, and identity.

These stay unauthenticated and unprefixed — the compose healthcheck and
already-built report pages depend on their exact paths.
"""
import re

from django.conf import settings
from django.db import connections
from django.http import JsonResponse


def healthz(request):  # noqa: ARG001
    try:
        with connections["default"].cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as exc:  # pragma: no cover - only on infra failure
        return JsonResponse({"status": "error", "database": str(exc)}, status=503)
    return JsonResponse({"status": "ok"})


#: A studio-scoped page's own URL, e.g. "/s/acme/casino/r/funnel/". Matched
#: against ``?path=`` (below) rather than the request's own path, because
#: this endpoint's path is itself fixed/unprefixed (see the module
#: docstring) and so cannot carry org/studio the way every OTHER
#: studio-scoped API route does.
_STUDIO_PATH_RE = re.compile(r"/s/(?P<org_slug>[^/]+)/(?P<studio_slug>[^/]+)/")


def auth_me(request):
    """Tiny identity probe for already-built report pages.

    Read by ``trellum/static/js/data_loader.js``'s admin-only filter-health
    badge check. Unauthenticated *by design*, same as healthz/version above
    — a baked report page carries no CSRF dance for it — and always a plain
    200 so a normal call is never itself console noise: an anonymous
    viewer, a non-admin, or a page this endpoint cannot place in a studio
    all just resolve to ``is_admin: false``, the same as a logged-out
    viewer gets.

    The endpoint's own path is fixed and unprefixed, so it cannot carry
    org/studio in its URL the way a studio-scoped API route does. The
    caller instead reports which page it's asking from via ``?path=``
    (``window.location.pathname``, e.g. ``/s/<org>/<studio>/r/<slug>/``) —
    a missing or unparseable path degrades the same as a logged-out viewer,
    never an error.
    """
    is_admin = False
    if request.user.is_authenticated:
        match = _STUDIO_PATH_RE.search(request.GET.get("path") or "")
        if match:
            from apps.core import roles
            from apps.core.permissions import effective_roles
            from apps.orgs.models import Organization
            from apps.studios.models import Studio

            org = Organization.objects.filter(
                slug=match.group("org_slug"), is_active=True
            ).first()
            studio = (
                Studio.objects.filter(org=org, slug=match.group("studio_slug")).first()
                if org is not None else None
            )
            if studio is not None:
                role = effective_roles(request.user, org).role_for(studio)
                is_admin = roles.at_least(role, roles.ADMIN)
    return JsonResponse({"is_admin": is_admin})


def version(request):  # noqa: ARG001
    """Unauthenticated by design (see module docstring), so identity only.

    `schema` is the one-word answer to "did the migration actually apply?" that
    scripts/upgrade.sh asserts on. The *names* of unapplied migrations are
    operator detail and stay on /system.
    """
    from apps.core.version import version_info

    return JsonResponse(version_info())


def _is_current_operator(request) -> bool:
    """Operator access for the current identity, excluding borrowed sessions."""
    from apps.core import impersonation

    return bool(
        request.user.is_authenticated
        and getattr(request.user, "is_operator", False)
        and not impersonation.is_impersonating(request)
    )


def system_health(request):
    """Bounded health summary for the persistent operator shell."""
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "Authentication required"}, status=401)
    if not _is_current_operator(request):
        from django.http import Http404

        raise Http404

    from apps.core.health import quick_health_summary

    response = JsonResponse(quick_health_summary())
    response["Cache-Control"] = "no-store"
    return response


def send_test_email_and_report(request, *, redirect_to: str):
    """Send the operator a test message and flash the outcome.

    Shared by the first-run wizard and /system: the button exists to surface a
    misconfiguration, so the exception text is what gets shown.
    """
    from django.contrib import messages
    from django.shortcuts import redirect

    from apps.core.mail import send_test_email

    try:
        send_test_email(request.user.email)
    except Exception as exc:  # noqa: BLE001 - showing it is the whole point
        messages.error(request, f"Test email failed: {exc}")
    else:
        messages.success(request, f"Test email sent to {request.user.email}.")
    return redirect(redirect_to)


def system_page(request):
    """Instance health and settings for the operator: doctor checks, the
    instance configuration, security policy, workers, version."""
    from django.contrib import messages
    from django.shortcuts import redirect, render

    from apps.core.audit import audit
    from apps.core.forms import InstanceSettingsForm, SecuritySettingsForm
    from apps.core.health import run_checks
    from apps.core.models import InstanceConfig
    from apps.core.version import version_info
    from apps.runner.models import Run, WorkerHeartbeat

    if not request.user.is_authenticated:
        return redirect("login")
    # `is_operator`, not `is_superuser`: this is the page support staff are
    # given the operator flag in order to read, and the rest of the operator
    # console already gates on it (apps/core/permissions.require_operator).
    # 404 rather than 403 — the page is not advertised to people without it.
    if not _is_current_operator(request):
        from django.http import Http404

        raise Http404

    instance = InstanceConfig.load()
    # The same form the first-run wizard uses. Without it here, SMTP set during
    # setup — or skipped there — would be unreachable for the rest of the
    # instance's life.
    action = request.POST.get("action", "instance_settings")
    form = InstanceSettingsForm(
        request.POST if request.method == "POST" and action == "instance_settings" else None,
        instance=instance,
    )
    security_form = SecuritySettingsForm(
        request.POST if request.method == "POST" and action == "security_settings" else None,
        instance=instance,
    )
    if request.method == "POST" and action == "sign_everyone_out":
        from apps.accounts import session_policy

        session_policy.sign_everyone_out()
        audit(request, "auth.session_revoked", scope="instance")
        messages.success(request, "Every session on this instance has been signed out.")
        return redirect("system")
    if request.method == "POST" and action == "instance_settings":
        if not form.is_valid():
            for errors in form.errors.values():
                messages.error(request, "; ".join(errors))
            return redirect("system")
        form.save()
        audit(request, "instance.settings_update")
        if "test_email" in request.POST:
            return send_test_email_and_report(request, redirect_to="system")
        messages.success(request, "Instance settings saved.")
        return redirect("system")
    if request.method == "POST" and action == "security_settings":
        if not security_form.is_valid():
            for errors in security_form.errors.values():
                messages.error(request, "; ".join(errors))
            return redirect("system")
        security_form.save()
        from apps.accounts import session_policy

        session_policy.invalidate_policy_cache()
        audit(request, "instance.settings_update", section="security")
        messages.success(request, "Security settings saved.")
        return redirect("system")

    recent_runs = (
        Run.objects.select_related("studio", "studio__org")
        .order_by("-created_at")[:20]
    )
    from apps.core import releases

    return render(
        request,
        "core/system.html",
        {
            "checks": run_checks(),
            "releases": releases.current(),
            "form": form,
            "security_form": security_form,
            "sessions_invalidated_at": instance.sessions_invalidated_at,
            "workers": WorkerHeartbeat.alive(),
            "recent_runs": recent_runs,
            # Operators get the detailed payload: which migrations are missing
            # is exactly what you need when an upgrade half-landed, and it is
            # the one audience entitled to it.
            "version": version_info(detailed=True),
        },
    )
