"""Owner-fenced local annotation observations; never invoked by HTTP reads."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import subprocess
import threading
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from .annotation_projection import AdmissionError, Declaration, MISSING_DIGEST, RAW_BYTES, SCHEMA_VERSION, _catalog, _publish, declaration

LEASE_RENEW_SECONDS = 60


class WorkspaceUnavailable(Exception):
    pass


def workspace_identity():
    """Publish a flushed complete marker using atomic hard-link create-once."""
    root = Path(settings.DATA_DIR)
    marker = root / ".annotation-workspace"
    if not root.is_dir():
        raise WorkspaceUnavailable()
    if not marker.exists():
        temporary = root / (".annotation-workspace-" + secrets.token_hex(16))
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as file:
                file.write(secrets.token_hex(16).encode("ascii") + b"\n")
                file.flush()
                os.fsync(file.fileno())
            try:
                os.link(temporary, marker)
            except FileExistsError:
                pass
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            raise WorkspaceUnavailable() from exc
        finally:
            temporary.unlink(missing_ok=True)
    try:
        with marker.open("rb") as file:
            value = file.read(34)
        decoded = value.decode("ascii").strip()
        if len(decoded) != 32 or any(c not in "0123456789abcdef" for c in decoded):
            raise WorkspaceUnavailable()
        return decoded
    except (OSError, UnicodeError) as exc:
        raise WorkspaceUnavailable() from exc


def authoritative_handoff(studio, provenance_sha="", *, unowned_only=False):
    """Caller holds studio_sync_lock across its successful local operation."""
    from .models import AnnotationSourceState
    owner = workspace_identity()
    with transaction.atomic():
        catalog = _catalog(studio.pk)
        source, _ = AnnotationSourceState.objects.get_or_create(studio=studio, kind="global")
        source = AnnotationSourceState.objects.select_for_update().get(pk=source.pk)
        if unowned_only and source.owner_workspace_id:
            return False
        source.owner_workspace_id = owner
        source.ownership_generation += 1
        source.desired_generation += 1
        source.provenance_sha = provenance_sha
        source.status = "pending"
        source.lease_token = ""
        source.lease_until = None
        source.next_check_at = timezone.now()
        source.save()
        catalog.revision += 1
        catalog.save()
    return True


def _identity(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _read(path):
    opened = False
    try:
        # Never block opening a FIFO or follow a link into another workspace.
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        opened = True
        with os.fdopen(descriptor, "rb") as file:
            before = os.fstat(file.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise AdmissionError("unavailable")
            if before.st_size > RAW_BYTES:
                raise AdmissionError()
            raw = file.read(RAW_BYTES + 1)
            after = os.fstat(file.fileno())
        current = path.lstat()
        if not stat.S_ISREG(current.st_mode) or _identity(before) != _identity(after) or _identity(after) != _identity(current):
            raise AdmissionError("unavailable")
        if len(raw) > RAW_BYTES:
            raise AdmissionError()
        return raw, _identity(after)
    except FileNotFoundError as exc:
        if opened:
            raise AdmissionError("unavailable") from exc
        return None, None
    except OSError as exc:
        raise AdmissionError("unavailable") from exc


def observe(source_id, owner=None, *, cancelled=None):
    from .models import AnnotationSourceState
    owner = owner or workspace_identity()
    with transaction.atomic():
        source = AnnotationSourceState.objects.select_for_update().select_related("studio").get(pk=source_id)
        now = timezone.now()
        if source.owner_workspace_id != owner or (source.lease_until and source.lease_until > now):
            return 0
        source.lease_token = secrets.token_hex(16)
        source.lease_until = now + timedelta(seconds=180)
        source.save(update_fields=["lease_token", "lease_until"])
        fence = (owner, source.ownership_generation, source.desired_generation, source.lease_token, source.digest)
        path = Path(source.studio.project_root) / "events.yaml"
    renew_stop = threading.Event()
    def renew():
        close_old_connections()
        try:
            while not renew_stop.wait(LEASE_RENEW_SECONDS):
                now = timezone.now()
                updated = AnnotationSourceState.objects.filter(
                    pk=source_id, owner_workspace_id=fence[0], ownership_generation=fence[1],
                    desired_generation=fence[2], lease_token=fence[3], digest=fence[4],
                    lease_until__gt=now,
                ).update(lease_until=now + timedelta(seconds=180))
                if not updated:
                    return
        finally:
            close_old_connections()
    renew_thread = threading.Thread(target=renew, name="annotation-lease", daemon=True)
    renew_thread.start()
    raw = None
    try:
        raw, identity = _read(path)
        result = declaration(raw) if raw is not None else Declaration(MISSING_DIGEST, [], {}, "missing")
        # Verify after normalization, before opening the publication transaction.
        if identity is not None and _identity(path.lstat()) != identity:
            raise AdmissionError("unavailable")
        if identity is None and path.exists():
            raise AdmissionError("unavailable")
        error = ""
    except (AdmissionError, OSError) as exc:
        error = exc.code if isinstance(exc, AdmissionError) else "unavailable"
        result = Declaration(hashlib.sha256(raw).hexdigest() if raw is not None else source.digest, [], {}, error)
    finally:
        renew_stop.set()
        renew_thread.join(timeout=5)
    with transaction.atomic():
        catalog = _catalog(source.studio_id)
        current = AnnotationSourceState.objects.select_for_update().get(pk=source_id)
        if (current.owner_workspace_id, current.ownership_generation, current.desired_generation, current.lease_token, current.digest) != fence:
            return len(raw or b"")
        if cancelled is not None and cancelled.is_set():
            return len(raw or b"")
        if not current.lease_until or current.lease_until <= timezone.now():
            return len(raw or b"")
        if result.digest != current.digest or (not error and current.schema_version != SCHEMA_VERSION):
            current.desired_generation += 1
        _publish(current, catalog, result, error=error)
    return len(raw or b"")


def proven_git_upgrade(studio):
    """Only a matching published checkout and declared blob may auto-register."""
    from apps.runner.gitsync import studio_sync_lock
    from apps.studios.models import StudioRepo
    repo = StudioRepo.objects.filter(studio=studio).first()
    if repo is None or not repo.last_synced_sha:
        return False
    with studio_sync_lock(studio.pk) as locked:
        if not locked:
            return False
        repo.refresh_from_db()
        from apps.runner.gitsync import StudioGitSync
        relative = StudioGitSync(repo)._root_file_relpath("events.yaml")
        def git(*args):
            import tempfile
            # A spooled tempfile prevents an unbounded pipe capture in RAM;
            # cat-file -s admission below precedes reading blob bytes.
            with tempfile.TemporaryFile() as output:
                process = subprocess.run(["git", "-C", str(studio.repo_dir), *args], stdout=output, stderr=subprocess.DEVNULL, timeout=10, check=False)
                output.seek(0)
                data = output.read(RAW_BYTES + 1)
                return data if process.returncode == 0 and len(data) <= RAW_BYTES else None
        head = git("rev-parse", "HEAD")
        if head is None or head.decode().strip() != repo.last_synced_sha:
            return False
        size = git("cat-file", "-s", repo.last_synced_sha + ":" + relative)
        if size is None or not size.strip().isdigit() or int(size) > RAW_BYTES:
            return False
        blob = git("show", repo.last_synced_sha + ":" + relative)
        if blob is None:
            return False
        try:
            checkout, _ = _read(Path(studio.repo_dir) / relative)
            working, _ = _read(Path(studio.project_root) / "events.yaml")
            if blob != checkout or blob != working:
                return False
            declaration(blob)
        except AdmissionError:
            return False
        return authoritative_handoff(studio, repo.last_synced_sha, unowned_only=True)


class AnnotationThread(threading.Thread):
    def __init__(self, pools):
        super().__init__(name="annotations", daemon=True)
        self.pools = pools
        self.shutdown = threading.Event()
        self.upgrade_cursor = 0
        self.report_cursor = 0

    def stop(self):
        self.shutdown.set()
        self.join(timeout=60)

    def batch(self):
        from apps.studios.models import Studio
        from .models import AnnotationSourceState
        owner = workspace_identity()
        start = time.monotonic()
        self.report_cursor = backfill_reports(self.report_cursor, pools=self.pools, limit=1)
        admitted = 1
        consumed = RAW_BYTES
        candidates = AnnotationSourceState.objects.filter(kind="global", owner_workspace_id=owner, next_check_at__lte=timezone.now()).filter(Q(lease_until__isnull=True) | Q(lease_until__lte=timezone.now()))
        studios = Studio.objects.all()
        if self.pools:
            candidates = candidates.filter(studio__pool__in=self.pools)
            studios = studios.filter(pool__in=self.pools)
        for source_id in candidates.order_by("next_check_at", "pk").values_list("pk", flat=True)[:25]:
            if self.shutdown.is_set() or admitted >= 25 or consumed + RAW_BYTES > 64 * 1024 * 1024 or time.monotonic() - start >= 5:
                break
            consumed += observe(source_id, owner, cancelled=self.shutdown)
            admitted += 1
        # Keyset discovery does not repeatedly walk every studio on every tick.
        candidates = list(studios.filter(pk__gt=self.upgrade_cursor).exclude(annotation_sources__kind="global", annotation_sources__owner_workspace_id__gt="").order_by("pk")[:1])
        for studio in candidates:
            if self.shutdown.is_set() or time.monotonic() - start >= 5 or consumed + 3 * RAW_BYTES > 64 * 1024 * 1024:
                break
            self.upgrade_cursor = studio.pk
            proven_git_upgrade(studio)
        if not candidates:
            self.upgrade_cursor = 0

    def run(self):
        import logging
        while not self.shutdown.is_set():
            close_old_connections()
            try:
                self.batch()
            except Exception:
                logging.getLogger(__name__).exception("annotation reconciliation failed")
            finally:
                close_old_connections()
            self.shutdown.wait(15)


def start_for_worker(*, is_runner, pools):
    if not is_runner:
        return None
    thread = AnnotationThread(pools)
    thread.start()
    return thread


def backfill_reports(cursor=0, *, pools=None, limit=25):
    """Lock each narrow candidate, then admit only current declaration keys."""
    from django.db import connection
    from .models import Report, AnnotationSourceState
    from .annotation_projection import sync_report_declarations
    if connection.vendor != "postgresql":
        raise WorkspaceUnavailable()
    candidates = Report.objects.filter(pk__gt=cursor, present_in_scan=True).filter(Q(annotation_source__isnull=True) | ~Q(annotation_source__schema_version=SCHEMA_VERSION))
    if pools:
        candidates = candidates.filter(studio__pool__in=pools)
    identifiers = list(candidates.order_by("pk").values_list("pk", flat=True)[:limit])
    for pk in identifiers:
        with transaction.atomic():
            report = Report.objects.select_for_update().filter(pk=pk, present_in_scan=True).only("id", "studio_id").first()
            if report is None or AnnotationSourceState.objects.filter(report_id=pk, schema_version=SCHEMA_VERSION).exists():
                continue
            # This expression excludes unrelated annotation/config keys and
            # keeps missing studio distinct from explicitly null studio.
            with connection.cursor() as db:
                db.execute("""
                    WITH declaration AS (
                      SELECT CASE WHEN jsonb_typeof(config->'annotations')='object'
                        THEN jsonb_build_object('events', config->'annotations'->'events')
                        ELSE COALESCE(config->'annotations','null'::jsonb) END AS annotations,
                        config->'studio' AS scope, config ? 'studio' AS scope_present
                      FROM reports_report WHERE id=%s
                    ) SELECT CASE WHEN
                      octet_length(annotations::text) + octet_length(COALESCE(scope,'null'::jsonb)::text) <= 16777216
                      AND CASE WHEN jsonb_typeof(annotations->'events')='array'
                        THEN jsonb_array_length(annotations->'events') <= 100000 ELSE TRUE END
                      THEN jsonb_build_object('annotations', annotations, 'studio', scope, '_studio_present', scope_present)
                      ELSE NULL END FROM declaration
                """, [pk])
                config = db.fetchone()[0]
            if isinstance(config, str):
                config = json.loads(config)
            if config is not None:
                if not config.pop("_studio_present", False):
                    config.pop("studio", None)
                report.config = config
                sync_report_declarations(report)
            else:
                catalog = _catalog(report.studio_id)
                source, _ = AnnotationSourceState.objects.get_or_create(studio_id=report.studio_id, report_id=pk, kind="report")
                source = AnnotationSourceState.objects.select_for_update().get(pk=source.pk)
                _publish(source, catalog, Declaration(source.digest, [], {}, "oversized"), error="oversized")
    return identifiers[-1] if identifiers else 0
