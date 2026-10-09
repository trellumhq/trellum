"""Data source API (legacy shapes for portal.js) + management UI."""
from __future__ import annotations

import os

from django.contrib import messages
from django.db.models import Count
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.timesince import timesince
from django.views.decorators.http import require_POST

from apps.core import health, roles
from apps.core import instance as instance_config
from apps.core.audit import audit
from apps.core.form_responses import is_settings_request, settings_error, settings_success
from apps.core.permissions import require_org_role, require_studio_role
from apps.core.report_access import require_full_studio_visibility
from apps.datasources.forms import DataSourceForm
from apps.datasources.materialize import (
    extension_conflict,
    resolve_path,
    stored_file,
    upload_rel_path,
)
from apps.datasources.models import (
    CREDENTIAL_KEYS,
    INLINE_TYPES,
    TYPE_FIELDS,
    DataSource,
    RepoDataSource,
    sources_for_studio,
)
from apps.datasources.status import (
    SourceState,
    binding_state,
    blocked_reports,
    source_states,
)
from apps.datasources.testing import STALE_CHECK, check_binding, check_revision, fmt_size, rebuild_unblocked, run_state_check
from apps.orgs import quotas
from apps.orgs.views import paginate


def _resolve_source(studio, name: str) -> DataSource | None:
    """The source a report build would actually use for this name
    (studio shadows org — same rule as the materializer)."""
    for ds in sources_for_studio(studio):
        if ds.name == name:
            return ds
    return None


def _upload_target(studio, name: str) -> DataSource | None:
    """The studio row an upload for ``name`` lands on. A declared upload
    source has nothing to bind until its first file arrives, so the row is
    created here rather than asking for a form first."""
    ds = _resolve_source(studio, name)
    if ds is not None:
        return ds
    declaration = RepoDataSource.objects.filter(studio=studio, name=name, present=True).first()
    if declaration is None or not (declaration.config or {}).get("upload"):
        return None
    return DataSource.objects.create(
        studio=studio, name=name, type=declaration.type, config={"upload": True}
    )


def _filter_rows(rows: list[dict], query: str) -> list[dict]:
    """Name/type/description search over the merged source rows."""
    q = query.lower()
    return [
        r
        for r in rows
        if q in r["name"].lower()
        or q in (r["state"].type or "").lower()
        or q in r["description"].lower()
    ]


#: Source states the legacy ``configured`` status stands for.
_CONFIGURED = (SourceState.CONNECTED, SourceState.NOT_IN_REPO, SourceState.CHECKING)


def _source_info(ds: DataSource, state: SourceState) -> dict:
    """The legacy _handle_datasources_list entry shape (+scope), with
    ``status``/``status_detail`` read off the source's state."""
    info = {
        "name": ds.name,
        "type": state.type or ds.type,
        "description": ds.description,
        "upload": bool((ds.config or {}).get("upload")),
        "used_by": len(state.used_by),
        "scope": ds.scope,
    }
    # File-ish sources report on their FILE, at either scope — an org source's
    # bytes live in the organization's share rather than a studio project.
    if state.type in INLINE_TYPES:
        full = stored_file(ds)
        if full and full.is_file():
            stat = os.stat(full)
            info["status"] = "available"
            info["file_size"] = stat.st_size
            info["last_modified"] = stat.st_mtime
            info["status_detail"] = fmt_size(stat.st_size)
        else:
            info["status"] = "missing"
            info["status_detail"] = state.detail
    else:
        configured = state.state in _CONFIGURED and not state.missing
        info["status"] = "configured" if configured else "not_configured"
        info["status_detail"] = state.detail
    return info


def _states_by_name(studio) -> dict[str, SourceState]:
    return {s.name: s for s in source_states(studio)}


@require_studio_role(roles.VIEWER)
@require_full_studio_visibility
def api_list(request, org_slug, studio_slug):  # noqa: ARG001
    states = _states_by_name(request.studio)
    sources = [_source_info(ds, states[ds.name]) for ds in sources_for_studio(request.studio)]
    return JsonResponse({"sources": sources})


def _store_upload(request, ds: DataSource, name: str) -> JsonResponse:
    """Write one uploaded file for ``ds``. Shared by the studio and org routes.

    Three things this does that the bare chunk-loop it replaces did not:
    refuse what the instance and the org's quota do not allow, keep the stored
    path in step with the uploaded file's extension (the framework dispatches
    on it), and stage through ``.part`` so a failed 200 MB upload leaves the
    previous file intact instead of truncating it.
    """
    if not (ds.config or {}).get("upload"):
        return JsonResponse(
            {"ok": False, "error": f"Data source '{name}' does not allow uploads"}, status=403
        )
    files = list(request.FILES.values())
    if not files:
        return JsonResponse({"ok": False, "error": "No file found in request"}, status=400)
    upload = files[0]
    incoming = int(upload.size or 0)

    cap = instance_config.max_upload_bytes()
    if cap and incoming > cap:
        return JsonResponse(
            {
                "ok": False,
                "error": f"That file is {fmt_size(incoming)}; this portal accepts "
                         f"at most {fmt_size(cap)} per data source.",
            },
            status=413,
        )

    org = ds.owner_org
    current = stored_file(ds)
    replacing = current.stat().st_size if current and current.is_file() else 0
    decision = quotas.check_upload_allowed(org, incoming, replacing_bytes=replacing)
    if not decision.allowed:
        return JsonResponse({"ok": False, "error": decision.reason}, status=413)
    if incoming - replacing > max(0, health.free_bytes() - health.FREE_SPACE_RESERVE_BYTES):
        return JsonResponse(
            {"ok": False, "error": "Not enough free space on the portal's data volume."},
            status=507,
        )

    conflict = extension_conflict(ds, upload.name or "")
    if conflict:
        return JsonResponse({"ok": False, "error": conflict}, status=400)
    try:
        rel = upload_rel_path(ds, upload.name or "")
        target = resolve_path(ds, rel)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(target.name + ".part")
    written = 0
    try:
        with open(staged, "wb") as fh:
            for chunk in upload.chunks():
                written += len(chunk)
                # Content-Length is the client's claim; this is the fact.
                if cap and written > cap:
                    raise ValueError(f"upload exceeded {fmt_size(cap)}")
                fh.write(chunk)
        os.replace(staged, target)
    except Exception as exc:  # noqa: BLE001 - the live file must survive this
        staged.unlink(missing_ok=True)
        status = 413 if isinstance(exc, ValueError) else 500
        return JsonResponse({"ok": False, "error": str(exc)}, status=status)

    # An extension change relocates the file; drop what it replaced so the
    # storage quota does not count a file nothing points at any more.
    if current and current.is_file() and current != target:
        current.unlink(missing_ok=True)
    ds.config = {**(ds.config or {}), "path": rel}
    ds.last_check_at = ds.last_check_ok = None
    ds.last_check_error = ""
    ds.save(update_fields=["config", "updated_at", "last_check_at", "last_check_ok", "last_check_error"])

    size = target.stat().st_size
    audit(request, "datasource.upload", target=ds, name=name, size=size)
    check_binding(ds)
    state = binding_state(ds, getattr(request, "studio", None))
    badge, label = _BADGES.get(state.state, ("", state.state))
    return JsonResponse(
        {
            "ok": True,
            "filename": target.name,
            "size": size,
            "size_label": fmt_size(size),
            "uploaded_at": timezone.now().isoformat(),
            "source_id": ds.pk,
            "revision": check_revision(ds),
            "download_url": _file_urls(ds, {"status": "available", "upload": True}, True, studio=getattr(request, "studio", None))["download_url"],
            "badge": badge,
            "label": label,
            "detail": _status_detail(state),
        }
    )


def _download(request, ds: DataSource, name: str):
    if ds.type not in INLINE_TYPES:
        return JsonResponse(
            {"ok": False, "error": "Download only supported for file sources"}, status=400
        )
    target = stored_file(ds)
    if target is None:
        raise Http404
    if not target.is_file():
        return JsonResponse({"ok": False, "error": "File not found"}, status=404)
    # Only the actual-bytes-served path is audited -- a 400/404 above never
    # touched the file, so there is nothing to record as downloaded.
    audit(request, "datasource.download", target=ds, name=name)
    return FileResponse(open(target, "rb"), as_attachment=True, filename=target.name)


@require_studio_role(roles.DEVELOPER)
@require_POST
def api_upload(request, org_slug, studio_slug, name):  # noqa: ARG001
    ds = _upload_target(request.studio, name)
    if ds is None or ds.org_id is not None:
        # An org source's bytes are shared: only an org admin may replace them,
        # through the organization's own route.
        return JsonResponse({"ok": False, "error": f"Unknown data source: {name}"}, status=404)
    return _store_upload(request, ds, name)


@require_studio_role(roles.DEVELOPER)
@require_POST
def api_test(request, org_slug, studio_slug, name):  # noqa: ARG001
    ds = _resolve_source(request.studio, name)
    if ds is None:
        return JsonResponse({"ok": False, "error": f"Unknown data source: {name}"}, status=404)
    return _test_response(request, ds, name, request.studio)


def _test_response(request, ds, name, studio=None):
    revision = check_revision(ds, studio)
    if request.POST.get("revision") and request.POST["revision"] != revision:
        return JsonResponse({"ok": False, "stale": True, "detail": STALE_CHECK, "revision": revision}, status=409)
    ok, detail = check_binding(ds, studio, expected_revision=revision)
    audit(request, "datasource.test", target=ds, name=name, ok=ok)
    stale = getattr(ds, "_check_stale", False)
    return JsonResponse({"ok": ok, "detail": detail, "stale": stale, "revision": revision}, status=409 if stale else 200)


@require_studio_role(roles.DEVELOPER)
def api_download(request, org_slug, studio_slug, name):  # noqa: ARG001
    """Any source the studio resolves — including the org's shared files,
    which its reports read anyway."""
    ds = _resolve_source(request.studio, name)
    if ds is None:
        return JsonResponse({"ok": False, "error": f"Unknown data source: {name}"}, status=404)
    return _download(request, ds, name)


# ── Management UI ───────────────────────────────────────────────────────────

def _owned_file(ds: DataSource | None):
    """The file the portal owns for this source right now, if any.

    Resolve this BEFORE saving an edit: the returned Path is a snapshot, while
    the row it came from is about to change underneath.
    """
    if ds is None or not ds.is_uploaded:
        return None
    path = stored_file(ds)
    return path if path is not None and path.is_file() else None


def _discard_orphan(before, ds: DataSource | None) -> None:
    """Delete an uploaded file the source no longer points at.

    Switching a source to a repo path, or deleting it outright, would otherwise
    leave bytes on the data volume that nothing references and that still count
    against the org's storage quota.
    """
    if before is None:
        return
    after = _owned_file(ds) if ds is not None else None
    if after is not None and after == before:
        return
    try:
        before.unlink(missing_ok=True)
    except OSError:
        pass


def _file_urls(ds: DataSource, info: dict, can_manage: bool, studio=None) -> dict:
    """Per-row upload/download endpoints.

    The two halves resolve differently on purpose. WRITING a shared file always
    goes through the ORG route, so replacing it needs an org admin wherever the
    row is rendered. READING it goes through the page's own route: a studio
    developer may download every source their reports already read, without
    being an org admin.
    """
    org_slug = ds.owner_org.slug
    write_base = (
        f"/orgs/{org_slug}/settings/datasources/{ds.name}" if ds.org_id
        else f"/s/{org_slug}/{ds.studio.slug}/api/datasources/{ds.name}"
    )
    read_base = (
        f"/s/{org_slug}/{studio.slug}/api/datasources/{ds.name}" if studio is not None
        else write_base
    )
    return {
        "upload_url": f"{write_base}/upload" if (can_manage and info["upload"]) else "",
        "download_url": f"{read_base}/download" if info.get("status") == "available" else "",
    }


def _saved_message(ds: DataSource) -> str:
    """Saving an uploaded source is only half the job — say what is left."""
    where = "organization-level" if ds.org_id else "studio-level"
    if ds.is_uploaded and _owned_file(ds) is None and not ds.path_is_git_managed:
        return (
            f"Data source “{ds.name}” saved ({where}). Upload its file from the "
            f"table above — nothing needs to be committed to git."
        )
    return f"Data source “{ds.name}” saved ({where})."


def _editable_source(request, pk) -> DataSource:
    """A source this admin may edit from the studio page: the studio's own,
    or an org-level one when they are also an org admin."""
    ds = get_object_or_404(DataSource, pk=pk)
    if ds.studio_id == request.studio.pk:
        return ds
    if ds.org_id == request.org.pk and request.org_roles.is_org_admin:
        return ds
    raise Http404


#: Status badge per source state: (.ui-badge colour class, label). ``unknown``
#: has no row -- it is listed under "Referenced but not declared".
_BADGES = {
    SourceState.NEEDS_CREDENTIALS: ("fail", "Needs credentials"),
    SourceState.NEEDS_UPLOAD: ("warn", "Needs upload"),
    SourceState.CHECKING: ("info", "Checking…"),
    SourceState.CONNECTED: ("ok", "Connected"),
    SourceState.FAILING: ("fail", "Failing"),
    SourceState.NOT_IN_REPO: ("", "Not in repository"),
}
NEEDS_STATES = (SourceState.NEEDS_CREDENTIALS, SourceState.NEEDS_UPLOAD)
_TYPE_LABELS = dict(DataSource.TYPES)


def _status_detail(st: SourceState) -> str:
    """The muted text beside the badge."""
    if st.state == SourceState.NEEDS_CREDENTIALS:
        return st.detail.removeprefix("missing: ")
    if st.state == SourceState.NEEDS_UPLOAD:
        return "upload allowed"
    if st.state == SourceState.FAILING:
        since = f" · since {timesince(st.last_check_at)} ago" if st.last_check_at else ""
        return st.detail[:80] + since
    if st.state == SourceState.NOT_IN_REPO:
        return st.detail if st.missing else "portal-only"
    if st.state == SourceState.CHECKING:
        return ""
    return st.detail


def _credential_keys(type_: str) -> list[str]:
    return [k for k in TYPE_FIELDS.get(type_, []) if k in CREDENTIAL_KEYS]


def credential_fields(decl) -> list[str]:
    """The secrets the Configure form (and the assistant's card) asks for:
    the type's credential keys, the tunnel's only when the declaration
    tunnels."""
    cfg = decl.config or {}
    return [k for k in _credential_keys(decl.type) if cfg.get("ssh_host") or not k.startswith("ssh_")]


def persist_credentials(
    request, decl, secrets: dict, *, org_level: bool,
    new_connection_per_query: bool | None = None, **audit_meta,
) -> DataSource:
    """Persist credentials without connecting; blank values keep stored secrets."""
    studio, name = request.studio, decl.name
    if org_level and request.org_roles.is_org_admin:
        ds = DataSource.objects.filter(org=request.org, name=name).first() or DataSource(
            org=request.org, name=name
        )
    else:
        ds = DataSource.objects.filter(studio=studio, name=name).first()
        if ds is None:
            # A new studio row shadows the organization's: start from what the
            # studio has been connecting with, so a blank field keeps it.
            shared = DataSource.objects.filter(org=request.org, name=name).first()
            ds = DataSource(
                studio=studio, name=name,
                credentials=dict(shared.credentials or {}) if shared else None,
                config=(
                    {"new_connection_per_query": shared.config["new_connection_per_query"]}
                    if shared and "new_connection_per_query" in (shared.config or {})
                    else None
                ),
            )
    ds.type = decl.type
    config = dict(ds.config or {})
    if "new_connection_per_query" in TYPE_FIELDS.get(decl.type, []):
        if new_connection_per_query is not None:
            config["new_connection_per_query"] = new_connection_per_query
    else:
        config.pop("new_connection_per_query", None)
    ds.config = config
    stored = dict(ds.credentials or {})
    for key in _credential_keys(decl.type):
        value = str(secrets.get(key) or "").strip()
        if value:
            stored[key] = value  # blank = keep current
    ds.credentials = stored or None
    ds.updated_by = request.user
    ds.last_check_at = ds.last_check_ok = None
    ds.last_check_error = ""
    ds.save()
    audit(request, "datasource.update", target=ds, name=name, scope=ds.scope, **audit_meta)
    return ds


def save_credentials(
    request, decl, secrets: dict, *, org_level: bool,
    new_connection_per_query: bool | None = None, **audit_meta,
) -> tuple[bool, str]:
    """Keep the assistant and native form's synchronous save-and-test contract."""
    ds = persist_credentials(
        request, decl, secrets, org_level=org_level,
        new_connection_per_query=new_connection_per_query, **audit_meta,
    )
    name, studio = decl.name, request.studio
    ds._check_studio = studio

    ok, detail = run_state_check(binding_state(ds, studio))
    if not ok:
        return False, f"“{name}” saved but the connection test failed: {detail}"
    queued = rebuild_unblocked(ds, guard_check=True)
    if getattr(ds, "_check_stale", False):
        return False, f"“{name}” saved. {STALE_CHECK}"
    text = f"“{name}” connected."
    if queued:
        text += f" {queued} waiting report{'s' if queued != 1 else ''} ha{'ve' if queued != 1 else 's'} been queued to build."
    return True, text


def _row(request, st: SourceState, *, studio) -> dict:
    """One table row. ``studio`` is None on the organization page, where
    every row is the org's own binding."""
    ds = st.binding
    is_org_admin = request.org_roles.is_org_admin
    can_manage = ds is not None and (studio is None or ds.studio_id == studio.pk)
    badge, label = _BADGES.get(st.state, ("", st.state))
    info = _source_info(ds, st) if ds is not None else {"upload": False, "status": ""}
    urls = {"upload_url": "", "download_url": ""}
    if ds is not None:
        urls = _file_urls(ds, info, can_manage, studio=studio)
    elif studio is not None and st.type in INLINE_TYPES and (st.declaration.config or {}).get("upload"):
        # No binding yet: the studio route creates it on the first upload.
        urls["upload_url"] = f"/s/{request.org.slug}/{studio.slug}/api/datasources/{st.name}/upload"
    declared_in = ""
    if st.declaration is not None and not st.declaration.source_file.startswith("data-sources/"):
        declared_in = st.declaration.source_file.removeprefix("reports/")
    return {
        "state": st,
        "ds": ds,
        "name": st.name,
        # Element-id suffix: the row's pk where a binding exists (the tested JS
        # contract), else the name.
        "key": ds.pk if ds is not None else f"n-{st.name}",
        "type_label": _TYPE_LABELS.get(st.type, st.type or "—"),
        "badge": badge,
        "label": label,
        "detail": _status_detail(st),
        "description": ds.description if ds is not None else "",
        "declared_in": declared_in,
        "can_manage": can_manage,
        "manage_url": (
            f"/orgs/{request.org.slug}/settings/datasources?edit={ds.pk}"
            if ds is not None and ds.scope == "org" and is_org_admin and studio is not None
            else ""
        ),
        # The configure form applies to declared sources that take credentials.
        "configurable": st.declared and bool(_credential_keys(st.type)),
        "file_available": info.get("status") == "available",
        "used_by": len(st.used_by),
        **urls,
    }


def _configure_context(request, st: SourceState) -> dict:
    """What the "Configure <name>" form shows: the declaration read-only on
    the left, only the type's credential fields on the right."""
    decl = st.declaration
    cfg = decl.config or {}
    if decl.type in INLINE_TYPES:
        facts = [("Type", _TYPE_LABELS.get(decl.type, decl.type)), ("Path", cfg.get("path"))]
    else:
        facts = [
            ("Type", _TYPE_LABELS.get(decl.type, decl.type)),
            ("Host", cfg.get("host")),
            ("Port", cfg.get("port")),
            ("Database", cfg.get("database")),
            ("HTTP path", cfg.get("http_path")),
            ("Catalog", cfg.get("catalog")),
            ("SSH host", cfg.get("ssh_host")),
        ]
    keys = credential_fields(decl)

    # Widgets only -- the POST is read field by field in _configure.
    form = DataSourceForm(instance=st.binding, studio=request.studio, org=request.org, fixed_scope="studio")
    form.fields["new_connection_per_query"].widget.attrs["id"] = "configure-new-connection-per-query"
    form.initial["new_connection_per_query"] = effective_fields(decl, st.binding).get(
        "new_connection_per_query", False
    )
    shared = DataSource.objects.filter(org=request.org, name=st.name).first()
    return {
        "state": st,
        "binding": st.binding,
        "facts": [(k, v) for k, v in facts if v not in (None, "")],
        "declared_in": decl.source_file,
        "fields": [form[k] for k in keys],
        "new_connection_per_query": form["new_connection_per_query"],
        "supports_new_connection_per_query": (
            "new_connection_per_query" in TYPE_FIELDS.get(decl.type, [])
        ),
        "can_org": request.org_roles.is_org_admin,
        # Ticking "organization level" would overwrite shared credentials the
        # form is not showing (a studio row shadows them here).
        "warn_shared": shared is not None and (st.binding is None or shared.pk != st.binding.pk),
    }


def _configure(request, name: str):
    """Save the credentials posted for a declared source, at studio level or
    (when asked, by an org admin) at organization level, then test them and
    queue whatever the passing test unblocks."""
    studio = request.studio
    decl = get_object_or_404(RepoDataSource, studio=studio, name=name, present=True)
    if request.POST.get("action") == "remove_credentials":
        ds = DataSource.objects.filter(studio=studio, name=name).first()
        if ds is None:
            messages.info(request, "Shared credentials are managed in Organization settings.")
        else:
            audit(request, "datasource.delete", target=ds, name=name, scope="studio")
            ds.delete()
            messages.success(request, f"Credentials for “{name}” removed.")
        return redirect(request.path)

    if is_settings_request(request):
        ds = persist_credentials(
            request, decl, {k: request.POST.get(k) for k in _credential_keys(decl.type)},
            org_level=bool(request.POST.get("org_level")),
            new_connection_per_query=(
                request.POST.get("new_connection_per_query") in {"true", "on"}
                if (
                    "new_connection_per_query" in request.POST
                    and "new_connection_per_query" in TYPE_FIELDS.get(decl.type, [])
                ) else None
            ),
        )
        return _saved_response(request, ds, request.studio, configured=True)
    ok, text = save_credentials(
        request, decl, {k: request.POST.get(k) for k in _credential_keys(decl.type)},
        org_level=bool(request.POST.get("org_level")),
        new_connection_per_query=(
            request.POST.get("new_connection_per_query") in {"true", "on"}
            if (
                "new_connection_per_query" in request.POST
                and "new_connection_per_query" in TYPE_FIELDS.get(decl.type, [])
            ) else None
        ),
    )
    (messages.success if ok else messages.error)(request, text)
    return redirect(request.path)


def _banner(states: list[SourceState], studio) -> dict | None:
    """The page-top warning while declared sources wait for credentials."""
    needs = [s for s in states if s.state in NEEDS_STATES]
    if not needs:
        return None
    waiting = sum(
        1
        for blockers in blocked_reports(studio, states).values()
        if any(b.state in NEEDS_STATES for b in blockers)
    )
    return {
        "count": len(needs),
        "waiting": waiting,
        "noun": "a file" if all(s.state == SourceState.NEEDS_UPLOAD for s in needs) else "credentials",
        "sources": needs,
    }


def _saved_response(request, ds, studio=None, *, configured=False):
    base = (
        f"/orgs/{request.org.slug}/settings/datasources/" if ds.org_id
        else f"/s/{request.org.slug}/{ds.studio.slug}/api/datasources/"
    )
    # An org binding may be shadowed in this studio; test the actual saved row.
    test_studio = None if ds.org_id else studio
    state = binding_state(ds, test_studio)
    info = _source_info(ds, state)
    manage_path = (
        f"/orgs/{request.org.slug}/settings/datasources" if ds.org_id
        else f"/s/{request.org.slug}/{ds.studio.slug}/settings/datasources"
    )
    return settings_success(
        request, "Saved. Testing connection…", source_id=ds.pk, source_name=ds.name,
        test_url=base + ds.name + "/test", revision=check_revision(ds, test_studio),
        source_type=ds.type, type_label=_TYPE_LABELS.get(ds.type, ds.type),
        source_scope=ds.scope, description=ds.description,
        upload_url=base + ds.name + "/upload" if (ds.config or {}).get("upload") else "",
        values={"id": ds.pk},
        download_url=_file_urls(ds, info, True, studio=test_studio)["download_url"],
        edit_url=manage_path + f"?edit={ds.pk}", delete_url=manage_path,
        declared=state.declared, configured=configured,
        can_edit=not (state.declared and studio is not None),
        configure_url=(f"?configure={ds.name}#configure" if configured and ds.studio_id else ""),
        remove_credentials=bool(configured and ds.studio_id),
    )


@require_studio_role(roles.ADMIN)
def settings_page(request, org_slug, studio_slug):  # noqa: ARG001
    from apps.studios.models import StudioRepo

    # Org-wide sources are created and edited in Organization settings; this
    # page only creates studio-scoped ones and lists shared ones read-only.
    form_kwargs = dict(studio=request.studio, org=request.org, fixed_scope="studio")

    editing = None
    if request.GET.get("edit"):
        editing = _editable_source(request, request.GET["edit"])
        if editing.scope == "org":
            return redirect(
                f"/orgs/{request.org.slug}/settings/datasources?edit={editing.pk}"
            )
    if request.method == "POST" and request.POST.get("name") and request.POST.get("action") in (
        "configure", "remove_credentials"
    ):
        return _configure(request, request.POST["name"])
    if request.method == "POST":
        if request.POST.get("action") == "delete":
            ds = _editable_source(request, request.POST.get("id"))
            if ds.scope == "org":
                # Shared sources are read-only here — managed at org level.
                messages.info(request, "Shared sources are managed in Organization settings.")
                return redirect(f"/orgs/{request.org.slug}/settings/datasources")
            owned = _owned_file(ds)
            audit(request, "datasource.delete", target=ds, name=ds.name, scope=ds.scope)
            ds.delete()
            _discard_orphan(owned, None)
            messages.success(request, "Data source deleted.")
            return redirect(request.path)
        instance = None
        if request.POST.get("id"):
            instance = _editable_source(request, request.POST["id"])
            if instance.scope == "org":
                messages.info(request, "Shared sources are managed in Organization settings.")
                return redirect(
                    f"/orgs/{request.org.slug}/settings/datasources?edit={instance.pk}"
                )
        owned = _owned_file(instance)
        form = DataSourceForm(request.POST, instance=instance, **form_kwargs)
        if form.is_valid():
            ds = form.save(user=request.user)
            _discard_orphan(owned, ds)
            audit(request, "datasource.update", target=ds, name=ds.name, scope=ds.scope)
            if is_settings_request(request):
                return _saved_response(request, ds, request.studio)
            check_binding(ds, request.studio)
            messages.success(request, _saved_message(ds))
            return redirect(request.path)
        if is_settings_request(request):
            return settings_error(request, form=form)
        editing = instance
    else:
        form = DataSourceForm(instance=editing, **form_kwargs)

    states = source_states(request.studio)
    configure = None
    add_open = editing is not None or (request.method == "POST" and not form.is_valid())
    if request.GET.get("configure") and request.method == "GET":
        name = request.GET["configure"]
        st = next((s for s in states if s.name == name), None)
        if st is not None and st.declared and _credential_keys(st.type):
            configure = _configure_context(request, st)
        else:
            # Not declared (or a file source): the portal-only form, with the
            # name filled in, is the way to give the reports something to read.
            form = DataSourceForm(initial={"name": name}, **form_kwargs)
            add_open = True
    has_repo = StudioRepo.objects.filter(studio=request.studio, repo_url__gt="").exists()

    rows = [
        _row(request, st, studio=request.studio)
        for st in states
        if st.state != SourceState.UNKNOWN
    ]
    # source_states merges declarations, org- and studio-level bindings and
    # references, so this is a list, not a queryset — filter and page it in Python.
    q = (request.GET.get("q") or "").strip()
    if q:
        rows = _filter_rows(rows, q)
    total = len(rows)
    page = paginate(request, rows)

    return render(
        request,
        "datasources/settings.html",
        {
            "org": request.org,
            "studio": request.studio,
            "rows": page.object_list,
            "unknown": [s for s in states if s.state == SourceState.UNKNOWN],
            "banner": _banner(states, request.studio),
            "configure": configure,
            "has_repo": has_repo,
            "add_open": add_open or not has_repo,
            "form": form,
            "editing": editing,
            "type_fields": TYPE_FIELDS,
            "test_url_base": f"/s/{request.org.slug}/{request.studio.slug}/api/datasources/",
            "org_page": False,
            "max_upload_bytes": instance_config.max_upload_bytes(),
            "page": page,
            "q": q,
            "total": total,
        },
    )


@require_org_role(roles.ORG_ADMIN)
def org_settings_page(request, org_slug):  # noqa: ARG001
    """Organization settings → Data sources: shared connections every studio
    in the org can use (a studio source with the same name shadows them)."""
    form_kwargs = dict(studio=None, org=request.org, fixed_scope="org")

    editing = None
    if request.GET.get("edit"):
        editing = get_object_or_404(DataSource, org=request.org, pk=request.GET["edit"])
    if request.method == "POST":
        if request.POST.get("action") == "delete":
            ds = get_object_or_404(DataSource, org=request.org, pk=request.POST.get("id"))
            owned = _owned_file(ds)
            audit(request, "datasource.delete", target=ds, name=ds.name, scope="org")
            ds.delete()
            _discard_orphan(owned, None)
            messages.success(request, "Data source deleted.")
            return redirect(request.path)
        instance = None
        if request.POST.get("id"):
            instance = get_object_or_404(DataSource, org=request.org, pk=request.POST["id"])
        owned = _owned_file(instance)
        form = DataSourceForm(request.POST, instance=instance, **form_kwargs)
        if form.is_valid():
            ds = form.save(user=request.user)
            _discard_orphan(owned, ds)
            audit(request, "datasource.update", target=ds, name=ds.name, scope="org")
            if is_settings_request(request):
                return _saved_response(request, ds)
            check_binding(ds)
            messages.success(request, _saved_message(ds))
            return redirect(request.path)
        if is_settings_request(request):
            return settings_error(request, form=form)
        editing = instance
    else:
        form = DataSourceForm(instance=editing, **form_kwargs)

    # How many of the org's studios declare each name -- the "Used by" cell
    # here (the per-studio listing is a later ticket).
    declared_by = {
        r["name"]: r["n"]
        for r in RepoDataSource.objects.filter(studio__org=request.org, present=True)
        .values("name").annotate(n=Count("studio"))
    }
    rows = []
    for ds in DataSource.objects.filter(org=request.org).order_by("name"):
        row = _row(request, binding_state(ds), studio=None)
        row["declared_by"] = declared_by.get(ds.name, 0)
        rows.append(row)

    q = (request.GET.get("q") or "").strip()
    if q:
        rows = _filter_rows(rows, q)
    total = len(rows)
    page = paginate(request, rows)

    return render(
        request,
        "datasources/settings.html",
        {
            "org": request.org,
            "studio": None,
            "rows": page.object_list,
            "form": form,
            "editing": editing,
            "type_fields": TYPE_FIELDS,
            "test_url_base": f"/orgs/{request.org.slug}/settings/datasources/",
            "org_page": True,
            "max_upload_bytes": instance_config.max_upload_bytes(),
            "page": page,
            "q": q,
            "total": total,
        },
    )


@require_org_role(roles.ORG_ADMIN)
@require_POST
def api_org_test(request, org_slug, name):  # noqa: ARG001
    ds = DataSource.objects.filter(org=request.org, name=name).first()
    if ds is None:
        return JsonResponse({"ok": False, "error": f"Unknown data source: {name}"}, status=404)
    return _test_response(request, ds, name)


@require_org_role(roles.ORG_ADMIN)
@require_POST
def api_org_upload(request, org_slug, name):  # noqa: ARG001
    ds = DataSource.objects.filter(org=request.org, name=name).first()
    if ds is None:
        return JsonResponse({"ok": False, "error": f"Unknown data source: {name}"}, status=404)
    return _store_upload(request, ds, name)


@require_org_role(roles.ORG_ADMIN)
def api_org_download(request, org_slug, name):  # noqa: ARG001
    ds = DataSource.objects.filter(org=request.org, name=name).first()
    if ds is None:
        return JsonResponse({"ok": False, "error": f"Unknown data source: {name}"}, status=404)
    return _download(request, ds, name)
