"""Manage an organization's MFA requirement."""
from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render

from apps.core import roles
from apps.core.audit import audit
from apps.core.permissions import require_org_role
from apps.orgs.models import OrgSecurityPolicy

from .forms import SecurityPolicyForm


@require_org_role(roles.ORG_ADMIN)
def security_settings(request, org_slug):
    policy = OrgSecurityPolicy.objects.filter(org=request.org).first()

    if request.method == "POST":
        form = SecurityPolicyForm(request.POST, instance=policy, org=request.org)
        if form.is_valid():
            form.save()
            audit(
                request, "security_policy.update", target=request.org,
                require_mfa=form.instance.require_mfa,
                mfa_grace_days=form.instance.mfa_grace_days,
            )
            messages.success(request, "Security policy saved.")
            return redirect(request.path)
    else:
        form = SecurityPolicyForm(instance=policy, org=request.org)

    return render(
        request,
        "orgs/security.html",
        {"org": request.org, "form": form, "policy": policy},
    )
