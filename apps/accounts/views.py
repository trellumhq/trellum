"""Authentication and account lifecycle: login, logout, first-run setup,
invitation acceptance."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from apps.core import roles
from apps.core.audit import audit
from apps.core.form_responses import is_settings_request, settings_success
from apps.core.permissions import require_org_role, visible_studios
from apps.orgs.models import OrgMembership, PermissionGroupMembership
from apps.studios.models import Studio, StudioMembership

from .forms import (
    InviteAcceptForm,
    LoginForm,
    MfaCodeForm,
    SetupAccountForm,
    SetupOrgForm,
    TotpConfirmForm,
    UserExistsError,
    create_user_account,
)
from .models import Invitation, User

#: How long the anonymous pre-auth stash (password already verified, second
#: factor pending) survives. See _start_mfa_stepup / mfa_verify_view.
MFA_STASH_TTL_SECONDS = 5 * 60
_MFA_STASH_KEYS = ("mfa_user_id", "mfa_started", "mfa_next", "mfa_login_method")


def login_view(request):
    """Email-first login.

    If the email's domain belongs to an org with enabled SSO:
    - password login forbidden for that org -> always redirect to its IdP;
    - otherwise redirect to its IdP only when no password was typed;
    - a directory (LDAP) org has nowhere to redirect to: the typed password
      is checked against the directory instead (_directory_login), falling
      back to the local password only where the org still allows one.
    Everyone else: plain password login, with a TOTP step-up between
    authenticate() and auth_login() for anyone with a confirmed device (see
    _start_mfa_stepup / mfa_verify_view below).
    """
    from apps.accounts import sso, throttle

    if request.user.is_authenticated:
        return redirect("/")
    if not User.objects.exists():
        return redirect("setup")

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip()
        password = form.cleaned_data["password"]
        # Org-mandated SSO beats everything: members of an enforcing org
        # never get password login (operators exempt — lockout safety).
        enforced = sso.enforced_config_for_user(email)
        cfg = enforced or sso.config_for_email_domain(email)
        directory = cfg is not None and cfg.is_ldap
        if not directory:
            if enforced is not None:
                return redirect(sso.provider_login_url(request, enforced))
            if cfg is not None and (not cfg.allow_password_login or not password):
                return redirect(sso.provider_login_url(request, cfg))
        elif not password:
            form.add_error("password", "Enter your directory password.")
            return render(request, "accounts/login.html", {"form": form})

        # Checked after the SSO redirects, so throttling can never strand a
        # member of an enforcing org: their route out is the IdP, not a
        # password, and it should stay open however many attempts were made.
        if throttle.is_throttled(email, request):
            # Same wording as a wrong password: telling an attacker they found
            # the limit tells them the limit exists and where.
            form.add_error(None, "Invalid email or password.")
            return render(request, "accounts/login.html", {"form": form})

        user = None
        if directory:
            user, stop = _directory_login(
                request, form, cfg, email, password,
                local_allowed=enforced is None and cfg.allow_password_login,
            )
            if stop is not None:
                return stop
        elif password:
            user = authenticate(request, username=email, password=password)
        if user is not None:
            if user.has_mfa:
                return _start_mfa_stepup(request, user, next_url=request.GET.get("next") or "/")
            auth_login(request, user)
            throttle.clear(email, request)
            return redirect(_safe_next(request.GET.get("next") or "/"))
        throttle.record_failure(email, request)
        form.add_error(None, "Invalid email or password.")
    return render(
        request, "accounts/login.html", {"form": form, "reason": request.GET.get("reason", "")}
    )


def _directory_login(request, form, cfg, email: str, password: str, *, local_allowed: bool):
    """The typed password checked against the org's directory, then the same
    membership rules as the OpenID Connect callback (adapters.admission).

    Returns ``(user, None)`` on success, ``(None, None)`` when the password
    was wrong -- against the directory, and against the local account too
    when ``local_allowed`` -- or ``(None, response)`` when the login stops
    here: a denial page, or an outage shown as a generic error.
    """
    from django.contrib.auth.signals import user_login_failed

    from apps.accounts import ldap, sso, throttle
    from apps.accounts.adapters import (
        REASON_DIRECTORY_ERROR,
        REASON_OUTSIDE_DOMAINS,
        admission,
        denied,
    )
    from apps.orgs import domains as domain_service

    try:
        info = ldap.authenticate(cfg, email, password)
    except ldap.LDAPError as exc:
        audit(
            request, "auth.sso_denied", outcome="failure", org=cfg.org,
            reason_code=REASON_DIRECTORY_ERROR, provider=ldap.PROVIDER,
            asserted_email=email, error=str(exc),
        )
        if User.objects.filter(email__iexact=email, is_superuser=True).exists():
            # Lockout safety, the same exemption operators have from
            # enforce_sso: their local password keeps working while the
            # directory is down, so someone can always get in and fix it.
            return authenticate(request, username=email, password=password), None
        # Counted like a wrong password: a slow or dead directory must not
        # be a free way to keep workers waiting on it.
        throttle.record_failure(email, request)
        form.add_error(
            None, "Sign-in is unavailable right now. Try again later or contact your administrator.",
        )
        return None, render(request, "accounts/login.html", {"form": form})
    if info is None:
        if local_allowed:
            # authenticate() fires user_login_failed itself when this fails.
            return authenticate(request, username=email, password=password), None
        # The signal Django's authenticate() fires, so auth.login_failed
        # rows cover directory rejections the same way.
        user_login_failed.send(sender=__name__, credentials={"username": email}, request=request)
        return None, None

    # The directory vouched for the entry; the address it carries names the
    # account and must sit inside this org's domains -- the OpenID Connect
    # callback's rule, or a directory could claim accounts at other domains.
    address = info["email"] or email
    if domain_service.normalise(address) not in domain_service.routable_domains(cfg):
        return None, denied(
            request, f"The account {address} is outside this organization's verified domains.",
            reason_code=REASON_OUTSIDE_DOMAINS, provider=ldap.PROVIDER,
            asserted_email=address, org=cfg.org,
        )
    user, refusal = admission(cfg, address)
    if refusal is not None:
        reason_code, message = refusal
        return None, denied(
            request, message, reason_code=reason_code, provider=ldap.PROVIDER,
            asserted_email=address, org=cfg.org,
        )
    if user is None:
        user = User.objects.create_user(email=address, name=info["name"])
        sso.provision_membership(user, cfg)
        audit(
            request, "member.provisioned", target=user, actor=user, org=cfg.org,
            provider=ldap.PROVIDER, via="sso_auto_provision",
        )
    elif not user.is_active:
        return None, None  # what ModelBackend says of a disabled account
    sso.sync_groups(user, cfg, {cfg.groups_claim: info["groups"]})
    # Read by apps.core.auth_events._logged_in (and carried across the MFA
    # step-up by _start_mfa_stepup) so auth.login says how the password was
    # checked.
    request._trellum_login_method = ldap.PROVIDER
    return user, None


def _safe_next(url: str) -> str:
    from django.utils.http import url_has_allowed_host_and_scheme

    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={None}):
        return url
    return "/"


def _start_mfa_stepup(request, user, *, next_url: str):
    """Password verified; the user is NOT logged in yet. Stash just enough
    to finish the login at /login/verify, keyed to an anonymous session that
    is cycled first against fixation (the pre-auth session and the
    post-verify session must not be the same key)."""
    request.session.cycle_key()
    request.session["mfa_user_id"] = user.pk
    request.session["mfa_started"] = timezone.now().timestamp()
    request.session["mfa_next"] = _safe_next(next_url)
    request.session["mfa_login_method"] = getattr(request, "_trellum_login_method", "")
    return redirect("mfa-verify")


def _clear_mfa_stash(request) -> None:
    for key in _MFA_STASH_KEYS:
        request.session.pop(key, None)


def mfa_verify_view(request):
    """Second half of a factored login: the code/recovery-code prompt.

    Only reachable via a valid pre-auth stash (see _start_mfa_stepup) --
    reaching this page at all already reveals the password was right, which
    is inherent to any second prompt (the first form's wording stays merged
    with wrong-password; see login_view).
    """
    from apps.accounts import mfa, throttle

    stash_user_id = request.session.get("mfa_user_id")
    started = request.session.get("mfa_started")
    stash_ok = bool(stash_user_id and started) and (
        timezone.now().timestamp() - started <= MFA_STASH_TTL_SECONDS
    )
    user = User.objects.filter(pk=stash_user_id, is_active=True).first() if stash_ok else None
    if user is None:
        _clear_mfa_stash(request)
        return redirect("login")

    form = MfaCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        # MFA guesses feed the same email+IP throttle counters as password
        # attempts -- a stolen password plus brute-forced 6 digits is
        # exactly the attack those counters exist for.
        if throttle.is_throttled(user.email, request):
            form.add_error(None, "That code didn't work.")
            return render(request, "accounts/mfa_verify.html", {"form": form})

        code = form.cleaned_data["code"]
        device = getattr(user, "totp_device", None)
        method = None
        if device is not None and device.confirmed_at is not None and mfa.verify_login_code(device, code):
            method = "totp"
        else:
            recovery = mfa.verify_recovery_code(user, code)
            if recovery is not None:
                method = "recovery"

        if method is not None:
            next_url = request.session.get("mfa_next") or "/"
            request._trellum_login_method = request.session.get("mfa_login_method", "")
            _clear_mfa_stash(request)
            # Read by apps.core.auth_events._logged_in so auth.login records
            # which factor completed the login, with no new receiver needed.
            request._trellum_mfa_method = method
            auth_login(request, user)
            throttle.clear(user.email, request)
            if method == "recovery":
                remaining = mfa.remaining_recovery_codes(user)
                audit(
                    request, "auth.mfa_recovery_used", target=user, actor=user,
                    remaining=remaining,
                )
                messages.warning(
                    request,
                    f"You signed in with a recovery code. {remaining} left — "
                    f"generate a new set from Account settings.",
                )
            return redirect(next_url)

        throttle.record_failure(user.email, request)
        audit(request, "auth.mfa_failed", outcome="denied", target=user, actor=user)
        form.add_error(None, "That code didn't work.")
    return render(request, "accounts/mfa_verify.html", {"form": form})


@require_POST
def logout_view(request):
    auth_logout(request)
    return redirect("login")


# --- First-run wizard -------------------------------------------------------
#
# Six steps, split across URLs so Back works and no step can trap the operator.
# The commit happens once, at the account step: organization details are held in
# the session (harmless), the password never is. Everything after the commit is
# optional polish that a logged-in operator can also reach later from /system.

#: Session key holding the organization details collected in step 3.
SETUP_SESSION_KEY = "trellum_setup"

#: The rail rendered across the top of every wizard page.
SETUP_STEPS = [
    ("welcome", "Welcome", "setup"),
    ("checks", "Readiness", "setup-checks"),
    ("organization", "Organization", "setup-organization"),
    ("account", "Your account", "setup-account"),
    ("settings", "Settings", "setup-settings"),
    ("done", "Done", "setup-done"),
]


def _setup_is_open() -> bool:
    """True while the instance has no users at all — the pre-commit half of the
    wizard. Deliberately not token-gated: set the portal up before exposing the
    port — see the install docs (apps.core.docs.docs_url)."""
    return not User.objects.exists()


def _step_context(current: str, **extra) -> dict:
    steps = []
    reached = False
    for key, label, url_name in SETUP_STEPS:
        if key == current:
            reached = True
        steps.append(
            {"key": key, "label": label, "url_name": url_name,
             "current": key == current, "done": not reached}
        )
    return {"setup_steps": steps, "step_key": current, **extra}


def _post_commit_gate(request):
    """Steps 5-6 run as the freshly created operator. Returns a redirect when
    the caller has no business being there, else None."""
    from apps.core.instance import setup_is_complete

    if not (request.user.is_authenticated and request.user.is_superuser):
        return redirect("login")
    if setup_is_complete():
        return redirect("/")
    return None


def setup_view(request):
    """Step 1 — welcome. Only reachable while the instance has no users."""
    if not _setup_is_open():
        return redirect("login")
    return render(request, "accounts/setup/welcome.html", _step_context("welcome"))


def setup_checks_view(request):
    """Step 2 — readiness. Advisory, never blocking: on a dev run the worker
    legitimately is not up yet, and refusing to continue would strand the
    operator on a machine that is otherwise fine."""
    if not _setup_is_open():
        return redirect("login")
    from apps.core.health import run_checks

    checks = [
        {**c, "hint": "" if c["ok"] else SETUP_CHECK_HINTS.get(c["label"], "")}
        for c in run_checks()
    ]
    return render(
        request,
        "accounts/setup/checks.html",
        _step_context(
            "checks", checks=checks, failures=[c for c in checks if not c["ok"]]
        ),
    )


#: What to actually do about a failed readiness check. Keyed by the labels
#: run_checks() emits (apps/core/health.py).
SETUP_CHECK_HINTS = {
    "database": "Postgres is unreachable. Check the db service and DATABASE_URL.",
    "SECRET_ENCRYPTION_KEY": (
        "Set SECRET_ENCRYPTION_KEY in .env and restart. Generate one with: python -c "
        '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    ),
    "data volume writable": "The /data volume is not writable — check the mount and its permissions.",
    "report storage": (
        "TRELLUM_STORAGE_BACKEND=s3 but the bucket is not reachable. Check "
        "TRELLUM_REPORTS_BUCKET, any TRELLUM_STORAGE_ENDPOINT_URL, and the credentials "
        "this process runs with. Unset TRELLUM_STORAGE_BACKEND to keep output on "
        "the data volume."
    ),
    "git": "Install git in the image; per-studio report repositories are cloned with it.",
    "framework": (
        "The trellum package could not be imported. It ships in this "
        "repository at trellum/ — check the image built from a complete tree."
    ),
    "worker": (
        "No worker has checked in yet. Reports will queue until one starts "
        "(docker compose up -d worker). Safe to continue."
    ),
}


def setup_organization_view(request):
    """Step 3 — the first organization. Stored in the session, not the DB."""
    if not _setup_is_open():
        return redirect("login")
    saved = request.session.get(SETUP_SESSION_KEY) or {}
    form = SetupOrgForm(request.POST or None, initial=saved)
    if request.method == "POST" and form.is_valid():
        request.session[SETUP_SESSION_KEY] = {
            "org_name": form.cleaned_data["org_name"],
            "org_slug": form.cleaned_data["org_slug"],
        }
        return redirect("setup-account")
    return render(request, "accounts/setup/organization.html", _step_context("organization", form=form))


def setup_account_view(request):
    """Step 4 — the operator account, and the wizard's single commit."""
    if not _setup_is_open():
        return redirect("login")
    saved = request.session.get(SETUP_SESSION_KEY) or {}
    if not saved.get("org_slug"):
        return redirect("setup-organization")

    from apps.orgs.provisioning import OrgSlugTakenError, provision_org_with_owner

    form = SetupAccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user, org = provision_org_with_owner(
                email=form.cleaned_data["email"],
                name=form.cleaned_data.get("name", ""),
                password=form.cleaned_data["password1"],
                org_name=saved["org_name"],
                org_slug=saved["org_slug"],
                instance_operator=True,
            )
        except UserExistsError:
            form.add_error("email", "An account with this email already exists.")
        except OrgSlugTakenError:
            # Raced, or the slug was taken between step 3 and now. Send them
            # back to the step that owns the field rather than error here.
            request.session.pop(SETUP_SESSION_KEY, None)
            messages.error(request, "That organization slug was just taken — pick another.")
            return redirect("setup-organization")
        else:
            request.session.pop(SETUP_SESSION_KEY, None)
            auth_login(request, user)
            audit(request, "instance.setup", target=org, org=org)
            return redirect("setup-settings")
    return render(
        request,
        "accounts/setup/account.html",
        _step_context("account", form=form, org_name=saved.get("org_name", "")),
    )


@sensitive_post_parameters()
def setup_settings_view(request):
    """Step 5 — instance settings. Entirely optional; Skip jumps to Done."""
    gate = _post_commit_gate(request)
    if gate is not None:
        return gate

    from apps.core.forms import InstanceSettingsForm
    from apps.core.models import InstanceConfig

    row = InstanceConfig.load()
    form = InstanceSettingsForm(request.POST or None, instance=row)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(request, "instance.settings_update")
        if "test_email" in request.POST:
            from apps.core.views import send_test_email_and_report

            return send_test_email_and_report(request, redirect_to="setup-settings")
        messages.success(request, "Instance settings saved.")
        return redirect("setup-done")
    return render(request, "accounts/setup/settings.html", _step_context("settings", form=form))


def setup_done_view(request):
    """Step 6 — recap. Stamps the instance as set up, which closes steps 5-6."""
    gate = _post_commit_gate(request)
    if gate is not None:
        return gate

    from apps.core.models import InstanceConfig

    if request.method == "POST":
        row = InstanceConfig.load()
        row.setup_completed_at = timezone.now()
        row.save()
        messages.success(request, "Setup complete — welcome to your portal.")
        return redirect("/")

    org = (
        OrgMembership.objects.filter(user=request.user)
        .select_related("org").order_by("pk").first()
    )
    from apps.core.instance import mail_is_configured

    return render(
        request,
        "accounts/setup/done.html",
        _step_context(
            "done",
            org=org.org if org else None,
            mail_configured=mail_is_configured(),
        ),
    )


def _apply_invitation(invite: Invitation, user: User) -> None:
    """Attach all roles carried by an invitation to a user (idempotent)."""
    OrgMembership.objects.get_or_create(
        user=user, org=invite.org, defaults={"role": invite.org_role or roles.ORG_MEMBER}
    )
    for group in invite.groups.all():
        if group.org_id == invite.org_id:
            PermissionGroupMembership.objects.get_or_create(user=user, group=group)
    for grant in invite.studio_grants or []:
        studio = Studio.objects.filter(org=invite.org, pk=grant.get("studio_id")).first()
        role = grant.get("role")
        if studio and role in dict(roles.STUDIO_ROLE_CHOICES):
            StudioMembership.objects.get_or_create(
                user=user, studio=studio, defaults={"role": role}
            )
    invite.accepted_at = timezone.now()
    invite.accepted_by = user
    invite.save(update_fields=["accepted_at", "accepted_by"])


def invite_accept_view(request, token: str):
    invite = Invitation.objects.filter(token=token).select_related("org").first()
    problem = None
    if invite is None:
        problem = "This invitation link is not valid."
    elif invite.is_accepted:
        problem = "This invitation has already been used."
    elif invite.is_expired:
        problem = "This invitation has expired. Ask your admin for a new one."
    elif not invite.org.is_active:
        problem = "This organization is not active."
    if problem:
        return render(request, "accounts/invite_invalid.html", {"problem": problem}, status=410)

    # Logged-in path: the invite must be for this account's email.
    if request.user.is_authenticated:
        if request.user.email.lower() != invite.email.lower():
            return render(
                request,
                "accounts/invite_invalid.html",
                {
                    "problem": (
                        f"This invitation is for {invite.email}, but you are logged in "
                        f"as {request.user.email}. Log out first to accept it."
                    )
                },
                status=403,
            )
        _apply_invitation(invite, request.user)
        audit(request, "member.join", target=request.user, org=invite.org, via="invite")
        return redirect(f"/orgs/{invite.org.slug}/")

    existing = User.objects.filter(email__iexact=invite.email).first()
    if existing is not None:
        # Account exists: require login, then come back here.
        return redirect(f"/login?next=/invite/{token}")

    form = InviteAcceptForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = create_user_account(
                    invite.email, form.cleaned_data.get("name", ""), form.cleaned_data["password1"]
                )
                _apply_invitation(invite, user)
        except UserExistsError:
            return redirect(f"/login?next=/invite/{token}")
        auth_login(request, user)
        audit(request, "member.join", target=user, org=invite.org, via="invite", created=True)
        messages.success(request, f"Welcome to {invite.org.name}!")
        return redirect(f"/orgs/{invite.org.slug}/")
    return render(request, "accounts/invite_accept.html", {"form": form, "invite": invite})


def account_view(request):
    """Own account: display name, password change, live sessions, MFA."""
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.forms import PasswordChangeForm

    from apps.accounts import mfa
    from apps.accounts import session_policy as sp
    from apps.accounts.models import UserSession

    if not request.user.is_authenticated:
        return redirect("login")

    password_form = PasswordChangeForm(request.user)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "profile":
            name = (request.POST.get("name") or "").strip()
            request.user.name = name
            request.user.save(update_fields=["name"])
            messages.success(request, "Profile updated.")
            return redirect("account")
        if action == "password":
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)  # keep them logged in
                audit(request, "account.password_change", target=user)
                messages.success(request, "Password changed.")
                return redirect("account")
        if action == "sign_out_others":
            sp.sign_out_everywhere(request)
            audit(request, "auth.session_revoked", target=request.user, scope="others")
            messages.success(request, "Every other session has been signed out.")
            return redirect("account")
        if action == "revoke_session":
            key = request.POST.get("session_key", "")
            current = request.session.session_key
            owned = UserSession.objects.filter(session_key=key, user=request.user).exists()
            if not owned:
                messages.error(request, "That session is not yours to sign out.")
            elif key == current:
                messages.error(
                    request, "That is your current session — use “Sign out” instead."
                )
            else:
                sp.revoke_session(key)
                audit(request, "auth.session_revoked", target=request.user, scope="one")
                messages.success(request, "Session signed out.")
            return redirect("account")

    sessions = list(UserSession.objects.filter(user=request.user).order_by("-last_seen"))
    return render(
        request,
        "accounts/account.html",
        {
            "password_form": password_form,
            "sessions": sessions,
            "current_session_key": request.session.session_key,
            "has_mfa": request.user.has_mfa,
            "recovery_codes_remaining": (
                mfa.remaining_recovery_codes(request.user) if request.user.has_mfa else 0
            ),
        },
    )


def account_delete_view(request):
    """Self-service erasure: the account's owner deletes it, everywhere.

    Runs the same ``apps.orgs.personal_data.erase`` an org admin's own
    "erase this person" button calls, once per membership -- but unlike the
    dormancy sweep's ``_erase_account`` it wraps the loop in one outer
    transaction (a web request cannot finish a half-done job tomorrow the way
    the nightly sweep can) and audits with the request as actor: actor ==
    target IS the self-service marker, alongside dormancy's None-actor and an
    admin erasure's admin.
    """
    from apps.core import impersonation
    from apps.orgs import personal_data

    if not request.user.is_authenticated:
        return redirect("login")
    if request.method != "POST":
        return redirect("account")
    user = request.user

    # Refusals first, mirroring member_erase. Impersonation before the
    # password check: an operator supporting an SSO account would sail past
    # it (no usable password), and deleting a person is the operator's job
    # under their own name, never through the user's button.
    if impersonation.is_impersonating(request):
        messages.error(request, "You are impersonating this account, so you cannot delete it.")
        return redirect("account")
    if user.is_operator:
        messages.error(
            request, "Instance operators cannot delete their own account here — use the admin site at /admin."
        )
        return redirect("account")

    # Sole admin anywhere: erasing them would leave that org adminless, the
    # same state member_erase's last-admin guard exists to prevent. Naming
    # the orgs is safe — they are the user's own.
    sole_admin_of = [
        m.org.name
        for m in OrgMembership.objects.filter(user=user, role=roles.ORG_ADMIN).select_related("org")
        if not OrgMembership.objects.filter(org=m.org, role=roles.ORG_ADMIN).exclude(pk=m.pk).exists()
    ]
    if sole_admin_of:
        messages.error(
            request,
            "You are the only admin of " + ", ".join(sorted(sole_admin_of))
            + " — hand the admin role to someone else first, or delete the "
            "organization from its settings if it is finished.",
        )
        return redirect("account")

    if request.POST.get("confirm_email", "") != user.email:
        messages.error(request, "Type your email address to confirm.")
        return redirect("account")
    if user.has_usable_password() and not user.check_password(request.POST.get("password", "")):
        messages.error(request, "Current password is incorrect.")
        return redirect("account")

    orgs = [
        m.org for m in OrgMembership.objects.filter(user=user).select_related("org")
    ] or [None]
    with transaction.atomic():
        for org in orgs:
            personal_data.erase(request, user, org)

    auth_logout(request)
    # Survives the logout: no MESSAGE_STORAGE override exists, so Django's
    # cookie-first FallbackStorage carries this onto the login page.
    messages.success(request, "Your account has been deleted.")
    return redirect("login")


def _instance_name() -> str:
    from apps.core.models import InstanceConfig

    return InstanceConfig.load().instance_name


def mfa_setup_view(request):
    """Enroll TOTP: show a QR code (+ manual key) and confirm one code.

    Re-enrollment (already confirmed) bounces to /account; a
    user with a merely-begun (unconfirmed) device sees the same QR again
    rather than a second, different one, unless ?restart=1 is passed.
    """
    from apps.accounts import mfa

    if not request.user.is_authenticated:
        return redirect("login")
    user = request.user
    if user.has_mfa:
        messages.info(request, "MFA is already enabled on your account.")
        return redirect("account")

    device = getattr(user, "totp_device", None)
    if device is None or request.GET.get("restart") == "1":
        device = mfa.begin_enrollment(user)

    form = TotpConfirmForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        codes = mfa.confirm_enrollment(device, form.cleaned_data["code"])
        if codes is None:
            form.add_error("code", "That code didn't match. Check the time on your device and try again.")
        else:
            audit(request, "auth.mfa_enrolled", target=user)
            request.session["mfa_just_generated_codes"] = codes
            messages.success(request, "MFA is now enabled on your account.")
            return redirect("mfa-recovery-codes")

    uri = mfa.provisioning_uri(user, device, issuer_name=_instance_name())
    return render(
        request,
        "accounts/mfa_setup.html",
        {"form": form, "secret": device.secret, "qr_svg": mfa.qr_svg(uri)},
    )


def mfa_recovery_codes_view(request):
    """One-time display of a just-(re)generated recovery-code set. There is
    no "show me my codes again" -- same posture as a password."""
    if not request.user.is_authenticated:
        return redirect("login")
    codes = request.session.pop("mfa_just_generated_codes", None)
    if not codes:
        return redirect("account")
    return render(request, "accounts/mfa_recovery_codes.html", {"codes": codes})


def mfa_disable_view(request):
    """Self-disable: current password (local accounts) AND a live factor.
    Refused outright while an applicable org/instance policy requires MFA."""
    from apps.accounts import mfa

    if not request.user.is_authenticated:
        return redirect("login")
    if request.method != "POST":
        return redirect("account")
    user = request.user
    if user.has_usable_password() and not user.check_password(request.POST.get("password", "")):
        messages.error(request, "Current password is incorrect.")
        return redirect("account")
    if mfa.policy_requires_mfa(request, user):
        messages.error(request, "Your organization requires MFA — it cannot be disabled.")
        return redirect("account")

    device = getattr(user, "totp_device", None)
    code = request.POST.get("code", "")
    verified = bool(
        device is not None and device.confirmed_at is not None and mfa.verify_login_code(device, code)
    ) or mfa.verify_recovery_code(user, code) is not None
    if not verified:
        messages.error(request, "Enter a valid authenticator code or recovery code to disable MFA.")
        return redirect("account")

    mfa.remove_device(user)
    audit(request, "auth.mfa_removed", target=user)
    messages.success(request, "MFA has been disabled on your account.")
    return redirect("account")


def mfa_regenerate_view(request):
    """Mint a fresh set of ten recovery codes, invalidating the old set."""
    from apps.accounts import mfa

    if not request.user.is_authenticated:
        return redirect("login")
    if request.method != "POST":
        return redirect("account")
    user = request.user
    if not user.has_mfa:
        messages.error(request, "Enroll MFA first.")
        return redirect("account")
    if user.has_usable_password() and not user.check_password(request.POST.get("password", "")):
        messages.error(request, "Current password is incorrect.")
        return redirect("account")
    codes = mfa.generate_recovery_codes(user)
    request.session["mfa_just_generated_codes"] = codes
    messages.success(request, "New recovery codes generated — your old codes no longer work.")
    return redirect("mfa-recovery-codes")


def me_api(request):
    """Session/identity endpoint used by portal.js."""
    if not request.user.is_authenticated:
        return JsonResponse({"authenticated": False}, status=401)
    from apps.core.permissions import get_effective

    orgs = []
    for m in request.user.org_memberships.select_related("org").filter(org__is_active=True):
        er = get_effective(request, m.org)
        orgs.append(
            {
                "slug": m.org.slug,
                "name": m.org.name,
                "org_admin": er.is_org_admin,
            }
        )
    return JsonResponse(
        {
            "authenticated": True,
            "email": request.user.email,
            "name": request.user.display_name,
            "is_superuser": request.user.is_superuser,
            "orgs": orgs,
        }
    )


# ── Personal API keys (internal planning ticket #002) ───────────────────────────────────────────

def api_keys_view(request):
    """/me/api-keys: the user's bearer tokens across every org they belong
    to. Create shows the secret once -- stashed in the session across the
    redirect so a refresh cannot mint a second key -- and revoke is final;
    rotation is create-new-then-revoke-old."""
    from django.shortcuts import get_object_or_404

    from apps.core.instance import base_url

    from .forms import ApiKeyCreateForm
    from .models import ApiKey

    if not request.user.is_authenticated:
        return redirect("login")

    form = ApiKeyCreateForm(user=request.user)
    if request.method == "POST":
        if request.POST.get("action") == "revoke":
            key = get_object_or_404(
                ApiKey, pk=request.POST.get("key_id"), user=request.user, revoked_at__isnull=True
            )
            key.revoke()
            audit(request, "apikey.revoke", target=key, org=key.org, name=key.name, prefix=key.prefix)
            messages.success(request, f"API key “{key.name}” revoked.")
            return redirect("my-api-keys")
        form = ApiKeyCreateForm(request.POST, user=request.user)
        if form.is_valid():
            key, secret = ApiKey.mint(user=request.user, **form.cleaned_data)
            audit(
                request, "apikey.create", target=key, org=key.org,
                name=key.name, prefix=key.prefix, scopes=key.scopes,
            )
            request.session["new_api_key"] = {"id": key.pk, "secret": secret}
            return redirect("my-api-keys")

    fresh = request.session.pop("new_api_key", None)
    new_key = ApiKey.objects.filter(pk=fresh["id"], user=request.user).first() if fresh else None
    return render(
        request,
        "accounts/api_keys.html",
        {
            "form": form,
            "can_create": form.fields["org"].queryset.exists(),
            "keys": ApiKey.objects.filter(user=request.user).select_related("org"),
            "new_key": new_key,
            "new_secret": fresh["secret"] if new_key else None,
            # The `setup portal` one-liner, once, for every studio the key can
            # reach (internal planning ticket #151): create a key, paste one command in the repo.
            "new_key_studios": visible_studios(request.user, new_key.org) if new_key else [],
            "portal_url": base_url(),
        },
    )


@require_org_role(roles.ORG_ADMIN)
def org_api_keys_view(request, org_slug):  # noqa: ARG001
    """Org settings -> API keys: every key in the org, revocable, plus the
    org-wide switch. Turning it off stops every key on its next request and
    keeps the rows for the trail."""
    from django.shortcuts import get_object_or_404

    from .models import ApiKey

    org = request.org
    if request.method == "POST":
        if request.POST.get("action") == "revoke":
            key = get_object_or_404(
                ApiKey, pk=request.POST.get("key_id"), org=org, revoked_at__isnull=True
            )
            key.revoke()
            audit(
                request, "apikey.revoke", target=key,
                name=key.name, prefix=key.prefix, owner=key.user.email,
            )
            messages.success(request, f"API key “{key.name}” ({key.user.email}) revoked.")
        else:
            org.api_keys_enabled = bool(request.POST.get("api_keys_enabled"))
            org.save(update_fields=["api_keys_enabled"])
            audit(request, "org.api_keys_set", target=org, enabled=org.api_keys_enabled)
            if is_settings_request(request):
                return settings_success(request, "API key policy saved.", api_keys_enabled=org.api_keys_enabled)
            messages.success(
                request,
                "API keys are on for this organization."
                if org.api_keys_enabled
                else "API keys are off for this organization — every key stops working now.",
            )
        return redirect(request.path)

    return render(
        request,
        "accounts/org_api_keys.html",
        {"org": org, "keys": ApiKey.objects.filter(org=org).select_related("user")},
    )
