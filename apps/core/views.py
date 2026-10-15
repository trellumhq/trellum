"""Instance-level endpoints: health, version, and identity.

These stay unauthenticated and unprefixed — the compose healthcheck and
already-built report pages depend on their exact paths.
"""
import re

from django.conf import settings
from django.db import connections
from django.http import JsonResponse
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables


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
            from apps.core.request_access import request_org_access
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
                role = request_org_access(request, org).role_for(studio)
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
    """Send the operator a test message and show only sanitized failure text."""
    from django.contrib import messages
    from django.shortcuts import redirect

    from apps.core.mail import send_test_email

    from apps.core.email_errors import EmailDeliveryError

    try:
        send_test_email(request.user.email)
    except EmailDeliveryError as exc:
        messages.error(request, f"Test email failed: {exc}")
    except Exception:  # noqa: BLE001 - never expose provider exceptions or secrets
        messages.error(request, "Test email failed. Check the delivery configuration and server logs.")
    else:
        messages.success(request, f"Test email sent to {request.user.email}.")
    return redirect(redirect_to)


@sensitive_post_parameters()
def system_page(request):
    """Diagnostics at the established health URL; accept legacy settings POSTs."""
    from django.shortcuts import render

    gate = _require_email_operator(request)
    if gate is not None:
        return gate
    if request.method == "POST":
        return instance_settings(request)

    from apps.core import releases
    import trellum_portal
    from apps.core import operator_observations as observations
    from apps.core.version import framework_tag, meta_schema_version, schema_state, source_url

    # The coordinator measures and static/operator-observations.js keeps the page
    # current. When there is nothing usable from the coordinator (never measured,
    # or stale with no live coordinator), the checks run here, as they always did.
    diagnostic = observations.snapshot("full-health")
    live = observations.available()
    if not diagnostic.get("checks") or (diagnostic["state"] != "observed" and not live):
        from apps.core.health import run_checks

        # No age: with a live coordinator the page asks for its measurement at once.
        diagnostic = {"checks": run_checks(), "state": "unmeasured"}
    return render(request, "core/system.html", {
        "checks": diagnostic.get("checks", []),
        "diagnostic": diagnostic,
        "diagnostic_age": observations.age_text(diagnostic.get("age_seconds")),
        "probe_live": live,
        "probe_nonce": observations.nonce(request.user.pk, "full-health"),
        "probe_operation": observations.current_operation("full-health"),
        "releases": releases.current(),
        "version": {
            "version": trellum_portal.__version__, "sha": settings.TRELLUM_VERSION_SHA,
            "build_time": settings.TRELLUM_VERSION_BUILD_TIME, "source_url": source_url(),
            "framework": {"tag": framework_tag(), "meta_schema": meta_schema_version()},
            "unapplied_migrations": schema_state()[1],
        },
    })


def _diagnostics_status_url(operation_id):
    from django.urls import reverse

    return reverse("system-diagnostics-status", args=[operation_id])


def operator_diagnostics_refresh(request):
    """Admit one coordinator measurement; the system page asks (GET) and posts by itself."""
    from django.http import HttpResponseNotAllowed
    from django.db import transaction
    from apps.core import operator_observations as observations
    from apps.core.audit import audit

    gate = _require_email_operator(request)
    if gate is not None:
        return gate
    if request.method == "GET":
        response = JsonResponse(observations.preflight(request.user.pk, "full-health", _diagnostics_status_url))
        response["Cache-Control"] = "no-store"
        return response
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    try:
        with transaction.atomic():
            entry, created = observations.admit(request.user.pk, "full-health", request.POST.get("request_key", ""))
            if created:
                audit(request, "operator.diagnostics.refresh", operation_id=entry["id"])
    except observations.ObservationError as exc:
        response = JsonResponse(observations.refusal(exc, request.user.pk, "full-health", _diagnostics_status_url), status=exc.status)
        response["Cache-Control"] = "no-store"
        return response
    response = JsonResponse({"ok": True, **observations.envelope(entry, _diagnostics_status_url(entry["id"]))}, status=202 if created else 200)
    response["Cache-Control"] = "no-store"
    return response


def operator_diagnostics_status(request, operation_id):
    from django.http import Http404, HttpResponseNotAllowed
    from apps.core import operator_observations as observations

    if not request.user.is_authenticated:
        return JsonResponse({"detail": "Authentication required"}, status=401)
    if not _is_current_operator(request):
        raise Http404
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    try:
        entry = observations.observation(operation_id, kind="full-health")
    except observations.ObservationError as exc:
        response = JsonResponse(observations.refusal(exc, request.user.pk, "full-health", _diagnostics_status_url), status=exc.status)
        response["Cache-Control"] = "no-store"
        return response
    response = JsonResponse({**observations.envelope(entry, _diagnostics_status_url(operation_id)), "snapshot": observations.snapshot("full-health"),
                             "next_nonce": observations.nonce(request.user.pk, "full-health") if entry["state"] in observations.TERMINAL else None})
    response["Cache-Control"] = "no-store"
    return response


@sensitive_post_parameters()
def instance_settings(request):
    """Instance configuration and security, shared with legacy /system POSTs."""
    from django.contrib import messages
    from django.http import Http404
    from django.shortcuts import redirect, render
    from django.urls import reverse

    from apps.core.audit import audit
    from apps.core.form_responses import (
        is_settings_request,
        settings_error,
        settings_success,
    )
    from apps.core.forms import (
        EmailSettingsForm,
        IdentitySettingsForm,
        InstanceSettingsForm,
        LimitsSettingsForm,
        NetworkingSettingsForm,
        SecuritySettingsForm,
    )
    from apps.core.models import EmailApiConnection, InstanceConfig
    from apps.reports import deliveries

    gate = _require_email_operator(request)
    if gate is not None:
        return gate

    instance = InstanceConfig.load()
    action = request.POST.get("action", "instance_settings")
    policy_access = deliveries.can_manage_instance_policy(request)
    if request.method == "POST" and action == "delivery_policy" and not policy_access:
        raise Http404
    policy_form = deliveries.DeliveryPolicyForm.for_target(
        instance, request.POST if request.method == "POST" and action == "delivery_policy" else None,
    ) if policy_access else None
    if request.method == "POST" and action == "delivery_policy":
        if policy_form.is_valid():
            values = deliveries.save_policy(request, instance, policy_form)
            if is_settings_request(request):
                return settings_success(request, "Delivery policy saved. Resume uses future occurrences only.", values=values)
            messages.success(request, "Delivery policy saved.")
            return redirect(reverse("instance-settings") + "#scheduled-deliveries")
        if is_settings_request(request):
            return settings_error(request, form=policy_form)
    section_forms = {
        "identity_settings": ("Identity settings saved.", IdentitySettingsForm),
        "networking_settings": ("Networking settings saved.", NetworkingSettingsForm),
        "email_settings": ("Email settings saved.", EmailSettingsForm),
        "limits_settings": ("Limits settings saved.", LimitsSettingsForm),
    }
    section = section_forms.get(action)
    form_type = section[1] if section else InstanceSettingsForm
    form = form_type(
        request.POST if request.method == "POST" and action in {*section_forms, "instance_settings"} else None,
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
        return redirect("instance-settings")
    if request.method == "POST" and action == "test_email":
        return send_test_email_and_report(request, redirect_to=reverse("instance-settings") + "#email-delivery")
    if request.method == "POST" and action in {*section_forms, "instance_settings"}:
        if form.is_valid():
            saved = form.save()
            audit(request, "instance.settings_update")
            if "test_email" in request.POST:
                messages.success(request, "Instance settings saved.")
                return send_test_email_and_report(request, redirect_to=reverse("instance-settings") + "#email-delivery")
            if is_settings_request(request):
                return settings_success(request, section[0] if section else "Instance settings saved.", values={
                    name: getattr(saved, name) for name in form.fields if name != "email_host_password"
                }, **({"refresh": ["email-connections"]} if "email_host" in form.fields else {}))
            messages.success(request, "Instance settings saved.")
            return redirect("instance-settings")
        if is_settings_request(request):
            return settings_error(request, form=form)
    if request.method == "POST" and action == "security_settings":
        if security_form.is_valid():
            security_form.save()
            from apps.accounts import session_policy

            session_policy.invalidate_policy_cache()
            audit(request, "instance.settings_update", section="security")
            if is_settings_request(request):
                return settings_success(request, "Security settings saved.")
            messages.success(request, "Security settings saved.")
            return redirect("instance-settings")
        if is_settings_request(request):
            return settings_error(request, form=security_form)
    if request.method == "POST" and action not in {*section_forms, "instance_settings", "security_settings", "delivery_policy"}:
        raise Http404
    if request.method == "POST":
        # Invalid ModelForms mutate their instance; summaries describe persisted settings.
        instance = InstanceConfig.load()

    return render(
        request,
        "core/instance_settings.html",
        {
            "form": form,
            "delivery_policy_form": policy_form,
            "delivery_policy_action": reverse("instance-settings"),
            "identity_form": form if action == "identity_settings" else IdentitySettingsForm(
                request.POST if request.method == "POST" and action == "instance_settings" else None,
                instance=instance,
            ),
            "networking_form": form if action == "networking_settings" else NetworkingSettingsForm(
                request.POST if request.method == "POST" and action == "instance_settings" else None,
                instance=instance,
            ),
            "email_form": form if action == "email_settings" else EmailSettingsForm(
                request.POST if request.method == "POST" and action == "instance_settings" else None,
                instance=instance,
            ),
            "limits_form": form if action == "limits_settings" else LimitsSettingsForm(
                request.POST if request.method == "POST" and action == "instance_settings" else None,
                instance=instance,
            ),
            "email_connections": EmailApiConnection.objects.defer("credentials").order_by("name"),
            "active_email_connection_id": instance.active_email_api_connection_id,
            "legacy_email_route": (
                "Saved SMTP" if instance.email_host else
                "Environment SMTP" if getattr(settings, "EMAIL_URL_CONFIGURED", False) else "Console only"
            ),
            "security_form": security_form,
            "sessions_invalidated_at": instance.sessions_invalidated_at,
        },
    )


def _require_email_operator(request):
    """Return a response for forbidden access, otherwise None."""
    from django.http import Http404
    from django.shortcuts import redirect

    if not request.user.is_authenticated:
        return redirect("login")
    if not _is_current_operator(request):
        raise Http404
    return None


@sensitive_post_parameters()
@sensitive_variables()
def email_connection_editor(request, connection_id=None):
    """Create/edit one saved connection; POST preview is network-free."""
    import json

    from django.contrib import messages
    from django.core.exceptions import ValidationError
    from django.db import transaction
    from django.http import Http404
    from django.shortcuts import get_object_or_404, redirect, render
    from django.urls import reverse

    from apps.core.audit import audit
    from apps.core.email_mapping import render_payload, synthetic_message_context
    from apps.core.forms import EmailApiConnectionForm
    from apps.core.form_responses import is_settings_request, settings_error, settings_success
    from apps.core.models import EmailApiConnection, InstanceConfig

    class ActiveCustomContractError(Exception):
        pass

    gate = _require_email_operator(request)
    if gate is not None:
        return gate
    instance = get_object_or_404(EmailApiConnection.objects.defer("credentials"), pk=connection_id) if connection_id else None
    copy_from = None
    if not instance and request.method == "GET" and request.GET.get("copy"):
        try:
            copy_id = int(request.GET["copy"])
        except ValueError:
            raise Http404 from None
        copy_from = get_object_or_404(EmailApiConnection.objects.defer("credentials"),
                                      pk=copy_id, provider="custom_https")
    form = EmailApiConnectionForm(request.POST if request.method == "POST" else None,
                                  instance=instance, copy_from=copy_from)
    preview = None
    preview_config = None
    if request.method == "POST" and form.is_valid():
        if request.POST.get("action") == "preview":
            if form.cleaned_data["provider"] != "custom_https":
                form.add_error(None, "Preview is available for Custom HTTPS connections.")
            else:
                try:
                    _, config, _ = form.build_profile(preview=True)
                    preview_config = config
                    kind = request.POST.get("kind", "simple")
                    if kind not in {"simple", "report"}:
                        raise ValidationError("Choose a preview type.")
                    preview = json.dumps(
                        render_payload(config["payload"], synthetic_message_context(kind)),
                        indent=2, ensure_ascii=False,
                    )
                except (ValidationError, ValueError):
                    form.add_error(None, "The custom contract could not be previewed. Check its mapping fields.")
        elif request.POST.get("action", "save") == "save":
            with transaction.atomic():
                if instance:
                    active_id = InstanceConfig.objects.select_for_update().get(pk=InstanceConfig.load().pk).active_email_api_connection_id
                    current = get_object_or_404(EmailApiConnection.objects.select_for_update(), pk=instance.pk)
                    form.instance = current
                else:
                    active_id = None
                    current = EmailApiConnection()
                try:
                    provider_config, custom_config, credentials = form.build_profile()
                    name = form.cleaned_data["name"].strip()
                    from_email = form.cleaned_data["from_email"]
                    if instance and current.provider == "custom_https" and (
                        active_id == current.pk
                        and current.custom_config != custom_config
                    ):
                        raise ActiveCustomContractError
                    contract_changed = not instance or (
                        current.from_email != from_email or current.provider_config != provider_config
                        or current.custom_config != custom_config or current.credentials != credentials
                    )
                    current.name = name
                    current.from_email = from_email
                    current.provider = form.cleaned_data["provider"]
                    current.provider_config = provider_config
                    current.custom_config = custom_config
                    current.credentials = credentials
                    if instance and contract_changed:
                        current.config_revision += 1
                    current.full_clean()
                    current.save()
                except ActiveCustomContractError:
                    form.add_error(None, "Create an inactive copy to change an active Custom HTTPS contract.")
                except ValidationError:
                    form.add_error(None, "Could not save connection. Check the fields and credential requirements.")
                else:
                    audit(request, "instance.email_connection_update" if instance else "instance.email_connection_create",
                          target_id=str(current.pk), provider=current.provider)
                    message = "Email connection saved. Select Use to activate it."
                    if is_settings_request(request):
                        return settings_success(request, message,
                            redirect_url=reverse("instance-settings") + "#email-delivery" if not instance else None,
                            values={"name": current.name, "from_email": current.from_email},
                            updates={"email-connection-name": current.name})
                    messages.success(request, message)
                    return redirect(reverse("instance-settings") + "#email-delivery")
        else:
            raise Http404
    if request.method == "POST" and form.errors and is_settings_request(request):
        return settings_error(request, form=form)
    return render(request, "core/email_connection_form.html", {
        "form": form, "connection": instance, "copy_from": copy_from,
        "preview": preview, "preview_config": preview_config,
    })


@sensitive_post_parameters()
@sensitive_variables()
def email_connection_action(request, connection_id=None, action=None):
    """Explicit selection, test and delete operations on saved connections."""
    from django.contrib import messages
    from django.db import transaction
    from django.http import Http404
    from django.shortcuts import get_object_or_404, redirect
    from django.urls import reverse
    from django.utils import timezone

    from apps.core.audit import audit
    from apps.core.email_errors import EmailDeliveryError
    from apps.core.email_providers import validate_api_profile
    from apps.core.form_responses import is_settings_request, settings_error, settings_success
    from apps.core.mail import resolve_delivery_connection, send_report_test_email, send_test_email
    from apps.core.models import EmailApiConnection, InstanceConfig

    gate = _require_email_operator(request)
    if gate is not None:
        return gate
    if request.method != "POST":
        raise Http404
    if action == "use-smtp" and connection_id is None:
        with transaction.atomic():
            config = InstanceConfig.objects.select_for_update().get(pk=InstanceConfig.load().pk)
            config.active_email_api_connection = None
            config.save(update_fields=["active_email_api_connection"])
        audit(request, "instance.email_connection_select", route="legacy")
        if is_settings_request(request):
            return settings_success(request, "SMTP / environment route selected.", refresh=["email-connections"])
        messages.success(request, "SMTP / environment route selected.")
        return redirect(reverse("instance-settings") + "#email-delivery")
    profile = get_object_or_404(EmailApiConnection, pk=connection_id)
    if action == "use":
        with transaction.atomic():
            config = InstanceConfig.objects.select_for_update().get(pk=InstanceConfig.load().pk)
            profile = get_object_or_404(EmailApiConnection.objects.select_for_update(), pk=profile.pk)
            try:
                validate_api_profile(profile.provider, profile.provider_config, profile.custom_config,
                                     profile.credentials)
            except Exception:  # noqa: BLE001 - validation details may contain operator input
                if is_settings_request(request):
                    return settings_error(request, "Connection is invalid. Review its settings before selecting it.")
                messages.error(request, "Connection is invalid. Review its settings before selecting it.")
                return redirect(reverse("instance-settings") + "#email-delivery")
            config.active_email_api_connection = profile
            config.save(update_fields=["active_email_api_connection"])
        audit(request, "instance.email_connection_select", target_id=str(profile.pk), provider=profile.provider)
        if is_settings_request(request):
            return settings_success(request, "Email connection selected for future mail.", refresh=["email-connections"])
        messages.success(request, "Email connection selected for future mail.")
    elif action == "delete":
        with transaction.atomic():
            config = InstanceConfig.objects.select_for_update().get(pk=InstanceConfig.load().pk)
            profile = get_object_or_404(EmailApiConnection.objects.select_for_update(), pk=profile.pk)
            if config.active_email_api_connection_id == profile.pk:
                messages.error(request, "Select another delivery route before deleting this connection.")
                return redirect(reverse("instance-settings") + "#email-delivery")
            audit(request, "instance.email_connection_delete", target_id=str(profile.pk), provider=profile.provider)
            profile.delete()
        messages.success(request, "Inactive email connection deleted.")
    elif action == "test":
        kind = request.POST.get("kind")
        if kind not in {"simple", "report"}:
            raise Http404
        revision = profile.config_revision
        outcome, category, status = "accepted", "", None
        try:
            connection = resolve_delivery_connection(api_profile=profile)
            revision = connection.snapshot.config_revision
            if kind == "report":
                send_report_test_email(request.user.email, connection=connection)
            else:
                send_test_email(request.user.email, connection=connection)
        except EmailDeliveryError as exc:
            outcome, category, status = exc.outcome, exc.category, exc.status
        except Exception:  # noqa: BLE001 - provider/SDK error text must not be exposed
            outcome, category = "unconfirmed", "unexpected_error"
        with transaction.atomic():
            current = EmailApiConnection.objects.select_for_update().filter(pk=profile.pk).first()
            if current:
                results = dict(current.last_test_results or {})
                previous = results.get(kind, {})
                if previous.get("revision", 0) <= revision:
                    results[kind] = {"revision": revision, "checked_at": timezone.now().isoformat(),
                                     "outcome": outcome, "category": category, "status": status}
                    current.last_test_results = results
                    current.save(update_fields=["last_test_results"])
        audit(request, "instance.email_connection_test", target_id=str(profile.pk),
              provider=profile.provider, kind=kind, delivery_outcome=outcome,
              outcome="success" if outcome == "accepted" else "failure")
        if outcome == "accepted":
            messages.success(request, f"{kind.title()} test accepted by the provider. Delivery is not guaranteed.")
        else:
            messages.error(request, f"{kind.title()} test: {outcome.replace('_', ' ')} ({category}).")
    else:
        raise Http404
    return redirect(reverse("instance-settings") + "#email-delivery")
