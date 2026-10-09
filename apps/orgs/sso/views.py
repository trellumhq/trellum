"""Configure an organization's identity provider and domain claims."""
from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts import ldap
from apps.accounts.sso import redirect_uri_for_org
from apps.core import roles
from apps.core.audit import audit
from apps.core.form_responses import is_settings_request, settings_error, settings_success
from apps.core.permissions import require_org_role
from apps.orgs import domains as domain_service
from apps.orgs.models import OrgDomain, OrgSSOConfig

from .forms import SSOConfigForm


@require_org_role(roles.ORG_ADMIN)
def sso_settings(request, org_slug):  # noqa: ARG001
    cfg = OrgSSOConfig.objects.filter(org=request.org).first()
    action = request.POST.get("action", "")

    if request.method == "POST" and action == "claim-domain":
        try:
            record, created = domain_service.claim(
                request.org, request.POST.get("domain", "")
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            audit(request, "sso.domain.claim", target=request.org, domain=record.domain)
            messages.success(
                request,
                f"Publish a TXT record at {record.dns_record_name} with the value "
                f"{record.dns_record_value}, then press Verify."
                if created
                else f"{record.domain} was already claimed by this organization.",
            )
        return redirect(request.path)

    if request.method == "POST" and action == "verify-domain":
        record = get_object_or_404(
            OrgDomain, org=request.org, pk=request.POST.get("domain_id")
        )
        ok, message = domain_service.verify(record)
        audit(
            request, "sso.domain.verify", target=request.org,
            domain=record.domain, verified=ok,
        )
        (messages.success if ok else messages.error)(request, message)
        return redirect(request.path)

    if request.method == "POST" and action == "remove-domain":
        record = get_object_or_404(
            OrgDomain, org=request.org, pk=request.POST.get("domain_id")
        )
        audit(request, "sso.domain.remove", target=request.org, domain=record.domain)
        record.delete()
        messages.success(request, "Domain claim removed.")
        return redirect(request.path)

    if request.method == "POST" and action == "test-ldap":
        if cfg is None or not cfg.is_ldap:
            messages.error(request, "Save a directory configuration first.")
            return redirect(request.path)
        try:
            result = ldap.test_connection(cfg)
        except ldap.LDAPError as exc:
            audit(request, "sso.ldap.test", target=request.org, outcome="failure", error=str(exc))
            messages.error(request, f"Directory connection failed: {exc}")
        else:
            audit(request, "sso.ldap.test", target=request.org)
            messages.success(request, result)
        return redirect(request.path)

    if request.method == "POST":
        form = SSOConfigForm(request.POST, instance=cfg, org=request.org)
        if form.is_valid():
            form.save()
            audit(
                request, "sso.update", target=request.org,
                enabled=form.instance.enabled, auth_method=form.instance.auth_method,
            )
            if is_settings_request(request):
                return settings_success(request, "SSO settings saved.", values={"client_secret": "", "ldap_bind_password": ""}, refresh=["sso-directory-test", "sso-unclaimed-domains"])
            doc = form.discovery
            messages.success(
                request,
                "SSO settings saved."
                + (
                    f" {form.instance.issuer_url} answered: authorization endpoint "
                    f"{doc['authorization_endpoint']}, token endpoint {doc['token_endpoint']}."
                    if doc else ""
                ),
            )
            return redirect(request.path)
        if is_settings_request(request):
            return settings_error(request, form=form)
    else:
        form = SSOConfigForm(instance=cfg, org=request.org)

    claimed = list(OrgDomain.objects.filter(org=request.org).order_by("domain"))
    routable = set(domain_service.routable_domains(cfg)) if cfg else set()
    unclaimed = sorted(
        {domain_service.normalise(d) for d in ((cfg.email_domains if cfg else []) or [])}
        - {r.domain for r in claimed}
    )
    return render(
        request,
        "orgs/sso.html",
        {
            "org": request.org,
            "form": form,
            "cfg": cfg,
            "redirect_uri": redirect_uri_for_org(request.org),
            "domain_verification_required": domain_service.enforcement_enabled(),
            "claimed_domains": claimed,
            "routable_domains": sorted(routable),
            "unclaimed_domains": unclaimed,
        },
    )
