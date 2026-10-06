"""The framework-contract adapter for data sources.

Before every report build the worker calls :func:`materialize`, which

1. rewrites ``<studio>/project/data-sources/config.yaml`` from the studio's
   DataSource rows — inline path entries for sqlite/file sources, a
   ``credentials: {local: TRELLUM_DS_<id>}`` reference for everything else, and
2. returns the ``TRELLUM_DS_<id>_<FIELD>`` env vars (decrypted) that the
   framework's LocalEnvResolver reads in the child process.

Nothing in the framework changes: it sees the same file format and the same
env-var convention it always has.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from apps.datasources.models import CREDENTIAL_KEYS, ENV_SUFFIXES, INLINE_TYPES
from apps.datasources.status import SourceState, effective_fields, source_states

#: Nothing to write for these: the build would be blocked before it started.
_OMITTED = (SourceState.NEEDS_CREDENTIALS, SourceState.NEEDS_UPLOAD, SourceState.UNKNOWN)


def materialize(studio) -> dict[str, str]:
    """Write config.yaml for the studio; return env vars for the child.

    A source the repository declares is written as declared -- its type and
    non-secret config -- with credentials from its binding; one whose binding
    is incomplete is left out. A binding with no declaration is written as it
    always was. Includes the organization's shared sources; a studio source
    with the same name wins (see sources_for_studio)."""
    sources: dict[str, dict] = {}
    env: dict[str, str] = {}

    for state in source_states(studio):
        if state.state in _OMITTED:
            continue
        ds, declaration = state.binding, state.declaration
        fields = effective_fields(declaration, ds)
        # "image" is portal vocabulary; the framework only knows file paths.
        entry: dict = {"type": "file" if state.type == "image" else state.type}
        description = fields.get("description") or (ds.description if ds else "")
        if description:
            entry["description"] = description

        if state.type in INLINE_TYPES:
            if fields.get("path"):
                if ds is not None and ds.org_id:
                    # An org file is shared: its bytes live outside any studio,
                    # so emit an ABSOLUTE path. The framework already handles
                    # those (os.path.join short-circuits; _resolve_sqlite_path
                    # checks isabs), so nothing downstream changes.
                    try:
                        entry["path"] = str(resolve_path(ds, fields["path"]))
                    except ValueError:
                        # One malformed source must not break every build in
                        # the studio; the framework reports the missing entry.
                        pass
                else:
                    # Resolved by the framework against FW_PROJECT_ROOT.
                    entry["path"] = fields["path"]
            if fields.get("upload"):
                entry["upload"] = True
        else:
            if declaration is not None:
                entry.update(
                    {
                        k: v
                        for k, v in (declaration.config or {}).items()
                        if k != "description" and k not in CREDENTIAL_KEYS
                    }
                )
            # A declaration that needs no binding (its fields are all inline
            # and none is a secret) is written as declared, with no prefix.
            if ds is not None:
                entry["credentials"] = {"local": ds.env_prefix}
                for key, value in fields.items():
                    suffix = ENV_SUFFIXES.get(str(key).lower())
                    if suffix is not None and str(value or "").strip() != "" and key != "upload":
                        env[f"{ds.env_prefix}_{suffix}"] = str(value)

        sources[state.name] = entry

    config_path = studio.datasources_dir / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as fh:
        fh.write(
            "# Managed by the portal — edits here are overwritten before every run.\n"
        )
        yaml.dump(
            {"sources": sources}, fh,
            default_flow_style=False, sort_keys=False, allow_unicode=True,
        )
    return env


# ── File-ish sources: where their bytes live ────────────────────────────────
# A studio source's path is relative to that studio's project root, which is
# also what the framework resolves against. An ORG source has no project root,
# so its path is relative to the organization's own data-sources directory and
# the materializer emits it absolute — one file, every studio, same bytes.


def storage_root(ds) -> Path:
    """Directory this source's stored ``path`` is relative to."""
    return ds.org.datasources_dir if ds.org_id else ds.studio.project_root


def default_upload_rel(ds) -> str:
    """Where a freshly uploaded file lands when nothing is stored yet."""
    return f"files/{ds.name}" if ds.org_id else f"data-sources/files/{ds.name}"


def resolve_path(ds, rel: str) -> Path:
    """Absolute path for a stored relative path, refusing to escape the root."""
    root = storage_root(ds).resolve()
    target = (root / rel).resolve()
    if root != target and root not in target.parents:
        where = "the organization's data-sources directory" if ds.org_id else (
            "the studio project root"
        )
        raise ValueError(f"data source path escapes {where}")
    return target


def shared_read_paths(studio) -> list[Path]:
    """Directories OUTSIDE the studio project that a build must be able to read.

    A sandboxed build only gets its own directories mounted, the studio project
    among them (apps/runner/sandbox.py). An organization-level file lives in the
    organization's own tree instead, so without this the absolute path in
    config.yaml would point at nothing inside the container.

    Only existing directories are returned: a missing one would fail the
    container start, and "the file is not there" is a better error from the
    framework, which names the source.
    """
    from apps.datasources.models import sources_for_studio

    roots: set[Path] = set()
    for ds in sources_for_studio(studio):
        if ds.org_id and ds.type in INLINE_TYPES and (ds.config or {}).get("path"):
            root = ds.org.datasources_dir.resolve()
            if root.is_dir():
                roots.add(root)
    return sorted(roots)


def stored_file(ds) -> Path | None:
    """Absolute path of the file this source currently points at.

    ``None`` when nothing is configured yet (a fresh uploaded source) or the
    stored path is unusable.
    """
    rel = ((ds.config or {}).get("path") or "").strip()
    if not rel:
        return None
    if os.path.isabs(rel):
        return Path(rel)
    try:
        return resolve_path(ds, rel)
    except ValueError:
        return None


def upload_rel_path(ds, filename: str = "") -> str:
    """Where an upload of ``filename`` lands, relative to :func:`storage_root`.

    A configured path is the destination, verbatim: it is what the admin chose,
    what reports resolve, and what the framework dispatches on — an upload is
    not permission to move it. Only when nothing is configured yet does the
    filename decide, and then only its extension, so a first upload of
    ``q3.xlsx`` gives ``…/<name>.xlsx`` rather than an extensionless file the
    framework cannot read.
    """
    rel = (ds.config or {}).get("path")
    if rel:
        return rel
    ext = os.path.splitext(filename)[1].lower()
    return default_upload_rel(ds) + ext


def extension_conflict(ds, filename: str) -> str:
    """Why this upload does not fit the configured path, or "".

    The framework picks its reader from the extension (``.csv`` -> read_csv,
    ``.xlsx`` -> read_excel), so writing an Excel file over ``budget.csv``
    produces a source that parses as garbage at build time instead of failing
    here, where a person is watching.
    """
    rel = (ds.config or {}).get("path")
    if not rel:
        return ""
    want = os.path.splitext(rel)[1].lower()
    got = os.path.splitext(filename or "")[1].lower()
    if not want or not got or want == got:
        return ""
    return (
        f"This source reads {os.path.basename(rel)}, but you picked a "
        f"{got} file. Upload a {want} file, or change the path first."
    )


def upload_target(ds, filename: str = "") -> Path:
    """Absolute path an uploaded file lands at."""
    return resolve_path(ds, upload_rel_path(ds, filename))
