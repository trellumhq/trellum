"""Deployment health checks — shared by `manage.py doctor` and /system."""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone

from django.conf import settings
from django.db import connections

#: Below this, the data volume is reported as failing rather than merely tight.
FREE_SPACE_WARN_GB = 2.0
#: An upload is refused if it would leave less than this free, whatever the
#: org's storage quota says — the volume is shared by every tenant and by the
#: builds themselves.
FREE_SPACE_RESERVE_BYTES = 1024 ** 3


def free_bytes() -> int:
    """Free bytes on the data volume, or a large number if unknowable."""
    try:
        return shutil.disk_usage(settings.DATA_DIR).free
    except OSError:
        return 1 << 62


def _inspect_sandbox_network() -> dict | None:
    """The sandbox network's attributes, or None when Docker is unreachable."""
    try:
        import docker

        return docker.from_env().networks.get(settings.TRELLUM_SANDBOX_NETWORK).attrs
    except Exception:  # noqa: BLE001 — no socket is the normal case in `web`
        return None


def _sandbox_egress_state() -> str:
    """Whether tenant report code can reach the internet, checked not assumed.

    The old answer was a host firewall script (docker/harden-sandbox-net.sh)
    whose own comment called the metadata-endpoint rule MANDATORY on a cloud VM
    — and which nothing ran, enforced or verified, so `doctor` reported green on
    a box whose sandboxes could read IMDS.

    Reading the network's `internal` flag from the daemon answers it directly:
    it is the property that actually decides, rather than evidence that somebody
    once ran a script.
    """
    if settings.TRELLUM_SANDBOX_EGRESS == "open":
        return "OPEN (TRELLUM_SANDBOX_EGRESS=open — tenant code can reach the internet)"

    attrs = _inspect_sandbox_network()
    if attrs is None:
        # The web container has no Docker socket, which is the normal case.
        return "denied (configured; not verifiable from here)"
    if attrs.get("Internal"):
        return "denied (internal network)"
    # Configured to deny but the network says otherwise: it was created by an
    # older release, and Docker will not change `internal` in place. Reporting
    # "denied" here would be a lie the operator acts on.
    return (
        f"MISCONFIGURED — the {settings.TRELLUM_SANDBOX_NETWORK} network is not internal; "
        f"recreate it with `docker network rm {settings.TRELLUM_SANDBOX_NETWORK}` while "
        f"no builds are running"
    )


def _reclaimable_detail() -> str:
    """Bytes past their retention window, as a size — never as an exception.

    A walk of the data volume must not be able to fail the check it decorates:
    an unreadable directory says nothing about whether cleanup is still
    running, which is what this check is actually for.
    """
    from apps.core import retention

    try:
        freed = retention.reclaimable_bytes()
    except Exception as exc:  # noqa: BLE001 — an aside, not the check
        return f"(unmeasurable: {type(exc).__name__})"
    for unit in ("B", "KB", "MB", "GB"):
        if freed < 1024 or unit == "GB":
            return f"{freed:.0f} {unit}" if unit == "B" else f"{freed:.1f} {unit}"
        freed /= 1024
    return f"{freed:.1f} GB"


def run_checks() -> list[dict]:
    """[{label, ok, detail}] — never raises."""
    results: list[dict] = []

    def check(label: str, fn):
        try:
            results.append({"label": label, "ok": True, "detail": fn() or "ok"})
        except Exception as exc:  # noqa: BLE001
            results.append({"label": label, "ok": False, "detail": str(exc)})

    def db():
        with connections["default"].cursor() as cur:
            cur.execute("SELECT version()")
            return cur.fetchone()[0].split(",")[0]

    check("database", db)

    def crypto():
        from apps.core.crypto import decrypt_str, encrypt_str

        assert decrypt_str(encrypt_str("doctor")) == "doctor"
        return "encrypt/decrypt round-trip"

    check("SECRET_ENCRYPTION_KEY", crypto)

    def volume():
        root = settings.DATA_DIR
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".doctor-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except PermissionError as exc:
            # The classic upgrade failure: the image now runs unprivileged, but
            # a volume created by an older root-running image is still owned by
            # root. Say so, and say what fixes it.
            import os

            if not hasattr(os, "getuid"):  # Windows dev box
                raise RuntimeError(f"{exc}. {root} is not writable.") from exc
            uid, gid = os.getuid(), os.getgid()
            raise RuntimeError(
                f"{exc}. The portal runs as uid {uid} but {root} is not "
                f"writable by it — usually a data volume created by an older, "
                f"root-running image. Fix once with the stack stopped: "
                f"docker compose run --rm --user root web "
                f"chown -R {uid}:{gid} /data"
            ) from exc
        return str(root)

    check("data volume writable", volume)

    def report_storage():
        """Prove the configured store is reachable before a build depends on it.

        A publish failure is only discovered at the end of a build otherwise —
        the most expensive possible moment to learn that a bucket name is wrong.
        """
        from apps.core import storage

        return storage.probe()

    check("report storage", report_storage)

    def report_access():
        """Prove report content is not readable without authentication.

        The most important check on an edge-served install: it fires an
        anonymous, credential-less request at the public content origin and
        demands it be refused. A 200 here means report data is world-readable —
        so this fails hard, which is the whole point of surfacing it in doctor
        rather than discovering it from a viewer (or an attacker).

        On the proxy posture there is nothing exposed and this is a note. On
        edge-signed it also parses the signing key, so a bad PEM surfaces here
        rather than on the first report page.
        """
        from apps.core import cdn

        detail = cdn.probe_exposure()  # raises on exposure or unverifiable
        if cdn.signs_grants():
            cdn._private_key()  # a bad key would 500 the first real grant
            detail += "; grant signing key ok"
        return detail

    check("report access", report_access)

    def free_space():
        usage = shutil.disk_usage(settings.DATA_DIR)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        detail = f"{free_gb:.1f} GB free of {total_gb:.1f} GB"
        # Uploaded data-source files are the one thing on this volume that is
        # not rebuildable, and a full volume takes the whole instance down —
        # not just whoever filled it.
        if free_gb < FREE_SPACE_WARN_GB:
            raise RuntimeError(f"{detail} — uploads and builds will start failing")
        return detail

    check("data volume free space", free_space)

    def git():
        if shutil.which("git") is None:
            raise RuntimeError("git is not installed (required for reports-repo sync)")
        return subprocess.check_output(["git", "--version"], text=True).strip()

    check("git", git)

    def framework():
        import trellum  # noqa: F401
        from apps.core.version import framework_tag
        from trellum.meta import META_SCHEMA_VERSION

        return (
            f"importable, meta schema v{META_SCHEMA_VERSION}, "
            f"release {framework_tag()}"
        )

    check("framework", framework)

    def migrations():
        """Is the schema in step with the code?

        `migrate` runs in its own one-shot container, and nothing downstream
        noticed when it failed: a web container serving against a stale schema
        reported perfectly healthy, right up until a query hit a column that
        was not there. This is the check that makes a half-finished upgrade
        visible instead of latent.
        """
        from apps.core.version import SCHEMA_OK, SCHEMA_PENDING, schema_state

        state, pending = schema_state()
        if state == SCHEMA_OK:
            return "schema matches the code"
        if state == SCHEMA_PENDING:
            shown = ", ".join(pending[:5])
            if len(pending) > 5:
                shown += f", +{len(pending) - 5} more"
            raise RuntimeError(
                f"{len(pending)} migration(s) not applied ({shown}). The schema is "
                f"behind this image — run `docker compose run --rm migrate`."
            )
        raise RuntimeError("could not read migration state from the database")

    check("migrations", migrations)

    def backups():
        """Is there a recent, verified backup?

        This is the highest-value alert in the product. The bundled database
        has no replication and no point-in-time recovery, so for the default
        topology these dumps are the whole recovery story — and the classic way
        to discover a backup system has been dead for six weeks is to need it.
        """
        from apps.core import backups as backup_state

        st = backup_state.state()
        if not st.configured:
            raise RuntimeError(
                f"no backup directory at {backup_state.backup_dir()}. The backup "
                f"service is part of the default stack — check `docker compose ps backup`."
            )
        if st.last_success_at is None:
            raise RuntimeError(st.detail or "no backup has ever succeeded")

        age = st.age_hours
        limit = settings.BACKUP_MAX_AGE_HOURS
        where = "off-host copy" if st.off_host_copy else "THIS HOST ONLY"
        if age > limit:
            raise RuntimeError(
                f"newest verified backup is {age:.0f}h old (limit {limit}h): "
                f"{st.detail or 'backups have stopped'}"
            )
        if not st.ok:
            raise RuntimeError(
                f"last backup run failed ({st.detail}); newest good one is {age:.0f}h old"
            )
        drilled_at, drill_ok = backup_state.last_drill()
        if drilled_at is None:
            drill = "never restore-tested"
        elif drill_ok:
            days = (datetime.now(timezone.utc) - drilled_at).days
            drill = f"restore-tested {days}d ago"
        else:
            raise RuntimeError(
                f"the last restore drill FAILED — these backups do not produce a "
                f"working database (newest is {age:.0f}h old)"
            )

        if not st.off_host_copy:
            # Not a failure — plenty of installs accept it — but it must not be
            # invisible, because it is the difference between a backup and
            # disaster recovery.
            return (f"verified {age:.0f}h ago, {drill}, kept on this host only "
                    f"(set BACKUP_REMOTE_TARGET)")
        return f"verified {age:.0f}h ago, {drill}, {where}"

    check("backups", backups)

    def retention():
        """Is anything still deleting the tables that only grow?

        Run rows, audit rows, sessions and assistant transcripts have no natural
        ceiling. The failure mode is not dramatic — the disk fills months later
        on an instance nobody was watching — which is exactly why it needs to be
        a check rather than a thing you notice.

        Since the data windows landed it answers a second question too: how
        much is sitting there past its window right now. That number is the
        one an operator acts on, and on a healthy instance it is near zero
        because cleanup ran last night.
        """
        from apps.core.models import OpsState

        if not settings.CLEANUP_ENABLED:
            return "disabled (CLEANUP_ENABLED=false)"

        row = OpsState.objects.filter(key="cleanup").first()
        if row is None:
            raise RuntimeError(
                "cleanup has never run. It is scheduled on the coordinator — "
                "check that one is alive, or run `manage.py cleanup` by hand."
            )

        age_h = (datetime.now(timezone.utc) - row.ran_at).total_seconds() / 3600
        limit = settings.CLEANUP_MAX_AGE_HOURS
        removed = sum((row.payload or {}).get("removed", {}).values())
        if age_h > limit:
            raise RuntimeError(
                f"cleanup last ran {age_h:.0f}h ago (limit {limit}h) — retention "
                f"has stopped and these tables only grow"
            )
        if not row.ok:
            failed = ", ".join((row.payload or {}).get("failures", {})) or "unknown"
            raise RuntimeError(f"last cleanup had failing target(s): {failed}")
        return (
            f"ran {age_h:.0f}h ago, removed {removed} item(s), "
            f"{_reclaimable_detail()} reclaimable"
        )

    check("retention", retention)

    def worker():
        from apps.runner.models import WorkerHeartbeat

        alive = list(WorkerHeartbeat.alive().order_by("-last_beat_at"))
        if not alive:
            raise RuntimeError("no live worker heartbeat (is the worker container running?)")

        runners = [w for w in alive if w.role in (w.ROLE_ALL, w.ROLE_RUNNER)]
        coordinators = [w for w in alive if w.role in (w.ROLE_ALL, w.ROLE_COORDINATOR)]
        # Each half fails differently and silently: no runner means builds
        # queue forever; no coordinator means schedules never fire.
        if not runners:
            raise RuntimeError(
                "no live runner — builds will queue forever "
                f"({len(coordinators)} coordinator(s) alive)"
            )
        if not coordinators:
            raise RuntimeError(
                "no live coordinator — scheduled reports will not fire "
                f"({len(runners)} runner(s) alive)"
            )
        running = sum(w.running_count for w in runners)
        capacity = sum(w.max_concurrent for w in runners)
        return (
            f"{len(runners)} runner(s), {len(coordinators)} coordinator(s), "
            f"{running}/{capacity} builds running"
        )

    check("worker", worker)

    def sandbox():
        """Are report builds sandboxed? This check usually runs in the web
        container, which has no Docker socket, so it judges by the configured
        mode and what live runners actually report — not by probing here."""
        from apps.runner.models import WorkerHeartbeat
        from apps.runner.sandbox import sandbox_mode

        mode = sandbox_mode()
        if mode != "docker":
            if settings.DEBUG:
                return f"{mode!r} (DEBUG — dev instance)"
            raise RuntimeError(
                f"TRELLUM_SANDBOX={mode!r}: tenant report code runs unsandboxed. "
                f"Set TRELLUM_SANDBOX=docker in production."
            )

        runners = [
            w
            for w in WorkerHeartbeat.alive().order_by("-last_beat_at")
            if w.role in (w.ROLE_ALL, w.ROLE_RUNNER)
        ]
        if not runners:
            # Absence of a runner is the worker check's job, not this one.
            return "configured 'docker' (no live runner to confirm)"
        mismatched = [w.worker_id for w in runners if w.sandbox_mode != "docker"]
        if mismatched:
            raise RuntimeError(
                f"TRELLUM_SANDBOX=docker but runner(s) {', '.join(mismatched)} report "
                f"a different mode — they may have failed sandbox preflight."
            )
        egress = _sandbox_egress_state()
        return f"docker on {len(runners)} runner(s), egress {egress}"

    check("report sandbox", sandbox)

    def production_readiness():
        """Configuration that is fine to evaluate with and wrong to run on.

        These are not code faults, so nothing else surfaces them — and each one
        is discovered at the worst possible moment otherwise: the bundled
        database when a disk fills, DEBUG when a traceback reaches a user.
        """
        if settings.DEBUG:
            return "skipped (DEBUG is on — development instance)"

        problems = []
        notes = []
        db_host = settings.DATABASES.get("default", {}).get("HOST", "")
        if db_host in ("db", "localhost", "127.0.0.1", ""):
            # One VM running web, worker and db together is the supported
            # default topology, so the bundled database is not itself a fault —
            # failing on it condemned the install the product recommends.
            #
            # What actually threatens the data is running it with nothing to
            # restore from. That is the condition, and now that the `backups`
            # check exists it can be stated precisely instead of as advice.
            from apps.core import backups as backup_state

            st = backup_state.state()
            if st.last_success_at is None:
                problems.append(
                    f"bundled database (host {db_host or 'local'!r}) with no verified "
                    f"backup: it has no replication and no point-in-time recovery, so "
                    f"there is currently nothing to restore from"
                )
            else:
                notes.append(
                    f"bundled database (host {db_host or 'local'!r}) — supported; it has "
                    f"no replication or point-in-time recovery, so the nightly backup is "
                    f"the whole recovery story"
                    + ("" if st.off_host_copy else " (and it is not copied off this host)")
                )
        if "*" in getattr(settings, "ALLOWED_HOSTS", []):
            problems.append("ALLOWED_HOSTS contains '*', which disables host validation")
        base_url = getattr(settings, "PORTAL_BASE_URL", "")
        if base_url.startswith("http://") and "localhost" not in base_url:
            problems.append(f"PORTAL_BASE_URL is plain HTTP ({base_url})")

        if problems:
            raise RuntimeError("; ".join(problems))
        if notes:
            return "; ".join(notes)
        return "no evaluation-only settings in use"

    check("production readiness", production_readiness)
    return results
