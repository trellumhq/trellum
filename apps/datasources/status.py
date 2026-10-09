"""What state each of a studio's data sources is in, from the repository's
declaration (``RepoDataSource``), the portal's credential binding
(``DataSource``) and the reports that reference it. Pure: ORM reads only, no
disk, no connections -- ``testing.run_check`` is what changes a check result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from django.db.models import Q
from django.utils.timesince import timesince

from apps.datasources.models import (
    CREDENTIAL_KEYS,
    INLINE_TYPES,
    REQUIRED_FIELDS,
    DataSource,
    RepoDataSource,
    missing_required,
    sources_for_studio,
)
from apps.datasources.testing import UNTESTABLE

#: First line of a run blocked by pre-flight (apps.runner.executor); the
#: ops surfaces and the unblock step recognise a blocked run by it.
WAITING_PREFIX = "Waiting for data source '"
#: First line the executor's failure attribution puts above a build error.
FAILING_PREFIX = "Data source '"


def source_failure(text: str | None) -> tuple[str, str] | None:
    """``(name, message)`` when ``text`` opens with a failure attribution
    line (``Data source '<name>': <message>``), else None."""
    first = (text or "").splitlines()[0] if text else ""
    if not first.startswith(FAILING_PREFIX):
        return None
    name, sep, message = first[len(FAILING_PREFIX):].partition("': ")
    return (name, message.strip()) if sep else None


def build_error(meta: dict, run) -> str:
    """The error to show for a report. Attribution rewrites the failed run's
    ``stderr_tail`` but only the runner's local ``_meta.json``, so with a
    remote store the web tier may hold the un-attributed text: the report's
    latest run wins when it failed and names a source the meta does not."""
    error = meta.get("last_error") or ""
    if (
        run is not None and run.status == "error" and run.stderr_tail
        and source_failure(run.stderr_tail) and not source_failure(error)
    ):
        return run.stderr_tail
    return error


def blocked_names(meta: dict, run) -> list[str]:
    """Sources the report is waiting for. ``_meta.json`` carries them under
    ``blocked_by`` while its last outcome is the blocked run; a blocked run is
    not published to a remote store, so the latest run's tail is read when
    the meta has none."""
    if meta.get("last_status") == "error" and meta.get("blocked_by"):
        return list(meta["blocked_by"])
    if run is not None and run.status == "error":
        return [
            line[len(WAITING_PREFIX):].split("'", 1)[0]
            for line in (run.stderr_tail or "").splitlines()
            if line.startswith(WAITING_PREFIX)
        ]
    return []


def latest_runs(studio) -> dict[str, object]:
    """slug -> the studio's most recent ``Run`` for that report, one query
    (DISTINCT ON, as apps.runner.stats does)."""
    from apps.runner.models import Run

    return {
        r.slug: r
        for r in Run.objects.filter(studio=studio)
        .order_by("slug", "-created_at").distinct("slug")
        .only("slug", "status", "stderr_tail", "created_at")
    }


@dataclass
class SourceState:
    NEEDS_CREDENTIALS: ClassVar[str] = "needs_credentials"
    NEEDS_UPLOAD: ClassVar[str] = "needs_upload"
    CONNECTED: ClassVar[str] = "connected"
    FAILING: ClassVar[str] = "failing"
    CHECKING: ClassVar[str] = "checking"
    NOT_IN_REPO: ClassVar[str] = "not_in_repo"
    UNKNOWN: ClassVar[str] = "unknown"
    #: States that stop a report build before it starts.
    BLOCKING: ClassVar[frozenset] = frozenset(
        {"needs_credentials", "needs_upload", "failing", "unknown"}
    )

    name: str
    type: str
    declared: bool
    declaration: RepoDataSource | None
    binding: DataSource | None
    binding_scope: str | None
    state: str
    detail: str
    used_by: list[str] = field(default_factory=list)
    #: Required fields the effective configuration leaves blank.
    missing: list[str] = field(default_factory=list)
    last_check_at: object = None
    last_check_ok: bool | None = None
    last_check_error: str = ""

    @property
    def blocking(self) -> bool:
        return self.state in self.BLOCKING


def referenced_names(config: dict | None) -> list[str]:
    """Source names a report.yaml ``data_sources`` list references, in order."""
    names: list[str] = []
    for raw in (config or {}).get("data_sources") or []:
        name = raw if isinstance(raw, str) else (raw or {}).get("name", "")
        if name and name not in names:
            names.append(str(name))
    return names


def effective_fields(declaration, binding) -> dict:
    """The fields a source connects with. Declared non-secret config wins over
    the binding's, the binding supplies the credentials -- except a file
    source's ``path``, which follows the binding when the portal holds the
    upload."""
    if declaration is None:
        return binding.all_fields()
    if binding is None:
        return dict(declaration.config or {})
    fields = {**(binding.config or {}), **(declaration.config or {}), **(binding.credentials or {})}
    # This framework lifecycle option is operator-owned: keep a portal
    # override (including False) ahead of the repository declaration.
    if "new_connection_per_query" in (binding.config or {}):
        fields["new_connection_per_query"] = binding.config["new_connection_per_query"]
    if declaration.type in INLINE_TYPES and (binding.config or {}).get("path"):
        fields["path"] = binding.config["path"]
    return fields


def declaration_for(binding, studio=None) -> RepoDataSource | None:
    """The declaration a check of ``binding`` should run against: the given
    studio's, else the binding's own studio's, else (org row) the first
    declaring studio's in the organization."""
    qs = RepoDataSource.objects.filter(name=binding.name, present=True)
    if studio is not None:
        return qs.filter(studio=studio).first()
    if binding.studio_id:
        return qs.filter(studio_id=binding.studio_id).first()
    return qs.filter(studio__org_id=binding.org_id).order_by("studio_id").first()


def _state(name, declaration, binding, used_by) -> SourceState:
    st = SourceState(
        name=name,
        type=declaration.type if declaration else (binding.type if binding else ""),
        declared=declaration is not None,
        declaration=declaration,
        binding=binding,
        binding_scope=binding.scope if binding else None,
        state=SourceState.UNKNOWN,
        detail="not declared in the repository and not configured",
        used_by=list(used_by),
    )
    if binding is None and declaration is None:
        return st
    if binding is not None:
        st.last_check_at = binding.last_check_at
        st.last_check_ok = binding.last_check_ok
        st.last_check_error = binding.last_check_error

    if declaration is not None and declaration.file_error and st.type in INLINE_TYPES:
        # git sync refused to ship the declared file (it escapes the project,
        # is over the instance's cap, ...). Block the build with the reason
        # instead of letting it die mid-run on a file nobody delivered --
        # uploading it is one of the two ways out. Checked BEFORE the binding
        # branches: a binding that is not itself the file's source (no upload,
        # no path of its own) does not make the missing file any less missing.
        st.state, st.detail = SourceState.NEEDS_UPLOAD, declaration.file_error
        return st

    if binding is None:
        cfg = declaration.config or {}
        if st.type in INLINE_TYPES:
            if cfg.get("upload"):
                st.state, st.detail = SourceState.NEEDS_UPLOAD, "no file uploaded yet"
                return st
            if cfg.get("path"):
                st.state, st.detail = SourceState.CONNECTED, "file in repository"
                return st
        keys = [k for k in REQUIRED_FIELDS.get(st.type, []) if k in CREDENTIAL_KEYS]
        missing = missing_required(st.type, cfg)
        if not keys and not missing:
            # Nothing a binding could add (a Google Sheets declaration with
            # its spreadsheet, say): the declaration alone connects.
            st.state, st.detail = SourceState.CONNECTED, "declared in repository"
            return st
        st.state = SourceState.NEEDS_CREDENTIALS
        st.detail = "missing: " + ", ".join(keys or missing or ["credentials"])
        return st

    fields = effective_fields(declaration, binding)
    st.missing = missing_required(st.type, fields)
    # An undeclared binding is the portal's own: a gap is shown, not enforced.
    if st.missing and declaration is not None:
        if st.type in INLINE_TYPES and fields.get("upload"):
            st.state, st.detail = SourceState.NEEDS_UPLOAD, "no file uploaded yet"
        else:
            st.state, st.detail = SourceState.NEEDS_CREDENTIALS, "missing: " + ", ".join(st.missing)
        return st
    if binding.last_check_ok is None:
        st.state = SourceState.CONNECTED
        if declaration is None and not binding.awaiting_first_sync:
            st.state = SourceState.NOT_IN_REPO
        if st.missing:
            st.detail = "missing: " + ", ".join(st.missing)
        elif st.state == SourceState.NOT_IN_REPO:
            st.detail = "not declared in the repository"
        else:
            st.detail = "not testable" if st.type in UNTESTABLE else "not tested yet"
    elif binding.last_check_ok:
        st.state, st.detail = SourceState.CONNECTED, f"checked {timesince(binding.last_check_at)} ago"
    else:
        st.state, st.detail = SourceState.FAILING, binding.last_check_error or "last check failed"
    return st


def _used_by(studio) -> dict[str, list[str]]:
    from apps.reports.models import Report

    usage: dict[str, list[str]] = {}
    rows = Report.objects.filter(studio=studio, present_in_scan=True).values_list("slug", "config")
    for slug, config in rows:
        for name in referenced_names(config):
            usage.setdefault(name, []).append(slug)
    return usage


def source_states(studio) -> list[SourceState]:
    """Every source the studio declares, binds or references, by name."""
    declarations = {d.name: d for d in RepoDataSource.objects.filter(studio=studio, present=True)}
    bindings = {ds.name: ds for ds in sources_for_studio(studio)}
    usage = _used_by(studio)
    return [
        _state(name, declarations.get(name), bindings.get(name), usage.get(name, []))
        for name in sorted(set(declarations) | set(bindings) | set(usage))
    ]


def binding_state(binding, studio=None) -> SourceState:
    """State of one binding on its own (the organization settings page has no
    studio to list against)."""
    return _state(binding.name, declaration_for(binding, studio), binding, [])


def report_states(report) -> list[SourceState]:
    names = referenced_names(report.config)
    if not names:
        return []
    by_name = {s.name: s for s in source_states(report.studio)}
    return [by_name[n] for n in names]


def report_blockers(report) -> list[SourceState]:
    """Referenced sources that would stop this report's build."""
    return [s for s in report_states(report) if s.blocking]


def blocked_reports(studio, states: list[SourceState] | None = None) -> dict[str, list[SourceState]]:
    """slug -> blockers, for every present report with at least one. Pass
    ``states`` when the caller already holds ``source_states(studio)``."""
    from apps.reports.models import Report

    by_name = {s.name: s for s in (states if states is not None else source_states(studio))}
    out: dict[str, list[SourceState]] = {}
    rows = Report.objects.filter(studio=studio, present_in_scan=True).values_list("slug", "config")
    for slug, config in rows:
        blockers = [by_name[n] for n in referenced_names(config) if by_name[n].blocking]
        if blockers:
            out[slug] = blockers
    return out


def clear_awaiting_first_sync(studio) -> int:
    """A sync has looked at this studio's repository: its rows, and the
    organization's shared rows, no longer need the upgrade-day allowance."""
    return DataSource.objects.filter(
        Q(studio=studio) | Q(org_id=studio.org_id), awaiting_first_sync=True
    ).update(awaiting_first_sync=False)
