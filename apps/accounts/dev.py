"""Dev-only instant login: bypass the login form while testing.

The URL is registered ONLY when ``settings.DEBUG`` (see trellum_portal/urls.py)
and the view re-checks DEBUG per request, so this cannot exist in a
production deployment even if the module ships.

    /__dev__/login/                      pick a user or persona from a page
    /__dev__/login/?user=<email>         log in as an existing user
    /__dev__/login/?as=<persona>         create-or-get a canned user, log in
    ...&next=/s/sim-01/stress/           where to land afterwards

Personas (users are created as dev-<slug>@dev.local, password "dev"):

    superuser                      is_superuser
    operator                       operator-console flag
    org_admin:<org>                org admin of <org>
    member:<org>                   plain org member
    viewer:<org>/<studio>          org member + studio viewer
    developer:<org>/<studio>       org member + studio developer
    admin:<org>/<studio>           org member + studio admin

Referenced orgs/studios must already exist (seeders create them); membership
rows are added idempotently, so re-using a persona against a new studio just
widens that dev user's access.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model, login as auth_login
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.utils.html import format_html, format_html_join
from django.utils.http import url_has_allowed_host_and_scheme

from apps.core import roles

STUDIO_PERSONAS = {"viewer": roles.VIEWER, "developer": roles.DEVELOPER, "admin": roles.ADMIN}


def dev_login(request):
    if not settings.DEBUG:
        raise Http404

    # Login happens via GET for one-click convenience, so refuse drive-by
    # embeds from other sites (login CSRF against a developer's localhost).
    # Typed URLs ("none") and same-site links still work.
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return HttpResponse("cross-site requests are not allowed", status=403)

    email = request.GET.get("user", "").strip()
    persona = request.GET.get("as", "").strip()

    if email:
        user = get_user_model().objects.filter(email__iexact=email).first()
        if user is None:
            return HttpResponse(
                format_html("no user with email {}", email), status=404
            )
        return _login_and_go(request, user)
    if persona:
        return _login_and_go(request, _persona_user(persona))
    return _page(request)


def _login_and_go(request, user):
    auth_login(request, user)  # single ModelBackend: no backend arg needed
    target = request.GET.get("next", "/")
    if not url_has_allowed_host_and_scheme(target, allowed_hosts=None):
        target = "/"
    return redirect(target)


def _persona_user(persona: str):
    from apps.orgs.models import Organization, OrgMembership
    from apps.studios.models import Studio, StudioMembership

    kind, _, scope = persona.partition(":")
    slug = persona.replace(":", "-").replace("/", "-")
    user, created = get_user_model().objects.get_or_create(
        email=f"dev-{slug}@dev.local"
    )
    if created:
        user.set_password("dev")
        user.save()

    if kind == "superuser":
        user.is_superuser = True
        user.is_staff = True
        user.save()
        return user
    if kind == "operator":
        user.is_operator_flag = True
        user.save()
        return user

    if kind in ("org_admin", "member"):
        org = _get_or_404(Organization, slug=scope, what=f"org {scope!r}")
        role = roles.ORG_ADMIN if kind == "org_admin" else roles.ORG_MEMBER
        OrgMembership.objects.get_or_create(user=user, org=org, defaults={"role": role})
        return user

    if kind in STUDIO_PERSONAS:
        org_slug, _, studio_slug = scope.partition("/")
        studio = _get_or_404(
            Studio, org__slug=org_slug, slug=studio_slug,
            what=f"studio {org_slug}/{studio_slug}",
        )
        OrgMembership.objects.get_or_create(
            user=user, org=studio.org, defaults={"role": roles.ORG_MEMBER}
        )
        StudioMembership.objects.get_or_create(
            user=user, studio=studio, defaults={"role": STUDIO_PERSONAS[kind]}
        )
        return user

    raise Http404(f"unknown persona {persona!r}")


def _get_or_404(model, *, what: str, **lookup):
    obj = model.objects.filter(**lookup).first()
    if obj is None:
        raise Http404(f"{what} does not exist — seed it first")
    return obj


def _page(request):
    from apps.studios.models import Studio

    users = get_user_model().objects.order_by("email")[:100]
    user_rows = format_html_join(
        "\n",
        '<li><a href="?user={}&next={}">{}</a>{}</li>',
        (
            (
                u.email,
                request.GET.get("next", "/"),
                u.email,
                " — superuser" if u.is_superuser
                else (" — operator" if u.is_operator else ""),
            )
            for u in users
        ),
    )

    persona_links = ["superuser", "operator"]
    for studio in Studio.objects.select_related("org").order_by("org__slug", "slug")[:30]:
        persona_links.append(f"org_admin:{studio.org.slug}")
        for kind in STUDIO_PERSONAS:
            persona_links.append(f"{kind}:{studio.org.slug}/{studio.slug}")
    seen: list[str] = []
    for p in persona_links:
        if p not in seen:
            seen.append(p)
    persona_rows = format_html_join(
        "\n",
        '<li><a href="?as={}&next={}">{}</a></li>',
        ((p, request.GET.get("next", "/"), p) for p in seen),
    )

    return HttpResponse(format_html(
        "<!doctype html><title>Dev login</title>"
        "<body style='font-family:monospace;max-width:640px;margin:40px auto'>"
        "<h2>Dev login (DEBUG only)</h2>"
        "<h3>Existing users</h3><ul>{}</ul>"
        "<h3>Personas (created on first use, password &quot;dev&quot;)</h3><ul>{}</ul>"
        "</body>",
        user_rows,
        persona_rows,
    ))
