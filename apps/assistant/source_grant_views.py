"""Admin-only source-read policy settings and bounded choice reads."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.assistant.models import SourceReadGrant
from apps.assistant.source_grant_forms import SourceGrantForm, source_choices, principal_choices
from apps.core import roles
from apps.core.audit import audit
from apps.core.choice_requests import choice_request_params, selected_by_id, search_page, choice_response, choice_id
from apps.core.form_responses import is_settings_request, settings_error, settings_success
from apps.core.permissions import require_studio_role


@require_studio_role(roles.ADMIN)
@require_http_methods(["GET", "POST"])
def settings_page(request, org_slug, studio_slug):
    studio = request.studio
    base = f"/s/{org_slug}/{studio_slug}/settings/assistant-sources"
    source_id = choice_id(request.POST.get("datasource") if request.method == "POST" else request.GET.get("source"))
    grant = SourceReadGrant.objects.filter(studio=studio, datasource_id=source_id).first() if source_id else None
    form = SourceGrantForm(request.POST if request.method == "POST" else None, studio=studio, grant=grant,
                           base_url=base, initial={"datasource": source_id})
    conflict = False
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                # Serialize creation too: the unique studio/source row may not exist yet.
                type(studio).objects.select_for_update().get(pk=studio.pk)
                current = SourceReadGrant.objects.select_for_update().filter(studio=studio, datasource=data["datasource"]).first()
                if data["revision"] != (current.revision if current else 0):
                    conflict = True
                    form.add_error(None, "This grant changed in another window. Reopen this source before saving your draft.")
                else:
                    current = current or SourceReadGrant(studio=studio, datasource=data["datasource"], revision=0)
                    current.enabled = data["enabled"]
                    current.relations = data["relations"]
                    current.revision += 1
                    if current.pk:
                        current.users.set(data["users"])
                        current.groups.set(data["groups"])
                    current.save()
                    current.users.set(data["users"])
                    current.groups.set(data["groups"])
                    audit(request, "assistant.source_grant.update", target=current, revision=current.revision,
                          source_id=current.datasource_id, enabled=current.enabled)
        except ValidationError as exc:
            form.add_error(None, exc)
        if not conflict and not form.errors:
            if is_settings_request(request):
                return settings_success(request, "Source access saved.", values={"revision": current.revision})
            messages.success(request, "Source access saved.")
            return redirect(base + f"?source={current.datasource_id}")
    if request.method == "POST" and is_settings_request(request):
        return settings_error(request, form=form, status=409 if conflict else 400)
    return render(request, "assistant/source_grants.html", {
        "org": request.org, "studio": studio, "form": form, "base_url": base,
        "has_sources": source_choices(studio).exists(),
    }, status=409 if conflict else 200)


@require_studio_role(roles.ADMIN)
@require_http_methods(["GET", "POST"])
def sources(request, org_slug, studio_slug):
    try:
        params = choice_request_params(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    rows = source_choices(request.studio)
    page = search_page(rows, str(params.get("q", ""))[:200], fields=("name",))
    return choice_response(page, lambda ds: {"value": str(ds.pk), "label": ds.name},
                           selected=selected_by_id(rows, params.get("selected")),
                           pagination={"page": 1, "per_page": 25, "total": page.paginator.count,
                                       "has_previous": False, "has_next": page.has_next()})


@require_studio_role(roles.ADMIN)
@require_http_methods(["GET", "POST"])
def principals(request, org_slug, studio_slug):
    try:
        params = choice_request_params(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    users, groups = principal_choices(request.studio)
    query = str(params.get("q", ""))[:200]
    payload = {"selected": {}, "pagination": {}}
    for key, rows, fields, selected_key in [
        ("members", users, ("name", "email"), "selected_people"),
        ("groups", groups, ("name",), "selected_groups"),
    ]:
        page = search_page(rows, query, fields=fields)
        raw = str(params.get(selected_key, ""))
        ids = [pk for value in raw.split(",")[:500] if (pk := choice_id(value))]
        def item(row):
            if key == "members":
                return {"id": row.pk, "name": row.name or row.email, "email": row.email}
            return {"id": row.pk, "name": row.name}
        payload[key] = [item(row) for row in page.object_list]
        payload["selected"][key] = [item(row) for row in rows.filter(pk__in=ids)[:500]]
        payload["pagination"][key] = {"page": 1, "per_page": 25, "total": page.paginator.count,
                                       "has_previous": False, "has_next": page.has_next()}
    response = JsonResponse(payload)
    response["Cache-Control"] = "private, no-store"
    return response
