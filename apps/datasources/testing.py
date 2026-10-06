"""Live "Test connection" for data sources.

Same connection path the AI assistant uses (apps/assistant/tools.py):
DataSource row → driver conn_info → ``drivers.connect(type, ...)`` —
no config materialization, no process-global framework state. Connect
attempts run on a worker thread with a hard deadline so an unreachable
host can't pin a gunicorn thread for minutes.
"""
from __future__ import annotations

import threading

from django.utils import timezone

from apps.datasources.models import CREDENTIAL_KEYS, INLINE_TYPES, DataSource, RepoDataSource

TIMEOUT_SECONDS = 10

#: Types we can meaningfully test from the web process.
UNTESTABLE = {
    "onedrive": "Connection test is not supported for OneDrive yet — run a report to verify.",
}


def _conn_info(ds) -> dict:
    info = {
        key: value
        for key, value in ds.all_fields().items()
        if key != "upload" and str(value or "").strip() != ""
    }
    if "port" in info:
        try:
            info["port"] = int(info["port"])
        except (TypeError, ValueError):
            pass
    return info


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


def _connect_and_close(ds, result: dict) -> None:
    try:
        info = _conn_info(ds)
        if ds.type == "google_sheets":
            # No driver to connect: open the spreadsheet the way a build does.
            from trellum.data.query import read_source

            df = read_source(
                "google_sheets", info["path"],
                credentials_path=info.get("credentials_path"),
                credentials_json=info.get("credentials_json"),
            )
            result["ok"] = True
            result["detail"] = f"Opened the spreadsheet ({len(df):,} rows in the first sheet)."
            return
        from trellum.data.drivers import connect

        conn = connect(ds.type, info)
        try:
            # A trivial round-trip where the driver exposes DBAPI cursors;
            # a successful connect alone is already a meaningful pass.
            cursor = getattr(conn, "cursor", None)
            if callable(cursor):
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()
                cur.close()
        finally:
            close = getattr(conn, "close", None)
            if callable(close):
                conn.close()
        result["ok"] = True
        result["detail"] = "Connected successfully."
    except Exception as exc:  # noqa: BLE001 - the point is to report it
        result["ok"] = False
        result["detail"] = f"{type(exc).__name__}: {exc}"


def test_datasource(ds) -> tuple[bool, str]:
    """Returns (ok, human detail). Never raises."""
    # File-ish types first: for an uploaded source "path" is missing until the
    # first upload, and "missing: path" is not what the admin needs to hear.
    if ds.type in INLINE_TYPES:
        from apps.datasources.materialize import stored_file

        path = stored_file(ds)
        if path is not None and path.is_file():
            return True, f"File found ({fmt_size(path.stat().st_size)})."
        if ds.is_uploaded:
            return False, "No file uploaded yet."
        return False, f"File not found at {ds.all_fields().get('path')}."

    missing = ds.missing_fields()
    if missing:
        return False, "Not configured — missing: " + ", ".join(missing)

    if ds.type in UNTESTABLE:
        return False, UNTESTABLE[ds.type]

    result: dict = {"ok": False, "detail": f"Timed out after {TIMEOUT_SECONDS}s."}
    worker = threading.Thread(target=_connect_and_close, args=(ds, result), daemon=True)
    worker.start()
    worker.join(TIMEOUT_SECONDS)
    if worker.is_alive():
        return False, (
            f"Timed out after {TIMEOUT_SECONDS}s — host unreachable, port blocked, "
            f"or the database is not accepting connections."
        )
    # Never echo credential values back.
    return bool(result["ok"]), ds.scrub(str(result["detail"]))


def run_check(binding, *, declared_type=None, declared_config=None) -> tuple[bool, str]:
    """Test ``binding`` the way a build would use it -- type and non-secret
    config from the declaration when there is one, credentials from the
    binding -- and store the outcome on the row. Never raises. Returns
    (ok, detail) like :func:`test_datasource`."""
    from apps.datasources.status import effective_fields

    probe = binding
    if declared_type:
        declaration = RepoDataSource(type=declared_type, config=declared_config or {})
        fields = effective_fields(declaration, binding)
        probe = DataSource(
            pk=binding.pk, studio_id=binding.studio_id, org_id=binding.org_id,
            name=binding.name, type=declared_type,
            config={k: v for k, v in fields.items() if k not in CREDENTIAL_KEYS},
            credentials={k: v for k, v in fields.items() if k in CREDENTIAL_KEYS},
        )
    if probe.type in UNTESTABLE or (probe.type not in INLINE_TYPES and probe.missing_fields()):
        # Not a connection outcome, so nothing to remember.
        return test_datasource(probe)
    try:
        ok, detail = test_datasource(probe)
    except Exception as exc:  # noqa: BLE001 - a check must never take its caller down
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    binding.last_check_at = timezone.now()
    binding.last_check_ok = ok
    binding.last_check_error = "" if ok else detail
    binding.save(update_fields=["last_check_at", "last_check_ok", "last_check_error"])
    return ok, detail


def run_state_check(state) -> tuple[bool, str]:
    """:func:`run_check` for a ``SourceState``, against its declaration."""
    declaration = state.declaration
    return run_check(
        state.binding,
        declared_type=declaration.type if declaration else None,
        declared_config=declaration.config if declaration else None,
    )


def check_binding(binding, studio=None) -> tuple[bool, str]:
    """Check ``binding`` as a build would use it (the studio's declaration;
    for an organization row, the first declaring studio's), then queue
    whatever reports the result unblocks."""
    from apps.datasources.status import binding_state

    ok, detail = run_state_check(binding_state(binding, studio))
    if ok:
        rebuild_unblocked(binding)
    return ok, detail


def rebuild_unblocked(binding) -> int:
    """Queue the reports that use ``binding`` and were waiting on it: nothing
    blocks them any more, and they have never built or were last blocked by
    this source. Returns how many were queued."""
    from apps.datasources.status import blocked_reports, referenced_names
    from apps.reports.models import Report
    from apps.runner.services import enqueue
    from trellum.meta import read_meta

    studios = [binding.studio] if binding.studio_id else binding.org.studios.all()
    queued = 0
    for studio in studios:
        reports = Report.objects.filter(
            studio=studio, present_in_scan=True, disabled=False
        ).exclude(slug__in=blocked_reports(studio)).select_related("studio")
        for report in reports:
            if binding.name not in referenced_names(report.config):
                continue
            blocked_by = read_meta(str(studio.output_dir / report.slug)).get("blocked_by") or []
            if report.last_built_at is None or binding.name in blocked_by:
                if enqueue(report, trigger="manual") == "queued":
                    queued += 1
    return queued
