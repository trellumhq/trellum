"""Bounded shared-workspace file metadata observations, never quota admission."""
from collections import deque
import os
from pathlib import Path
import re
import stat

from django.conf import settings
from django.db.models import Case, Count, F, Func, IntegerField, JSONField, Max, Q, TextField, Value, When
from django.db.models.functions import Cast

from apps.datasources.models import DataSource, INLINE_TYPES
from apps.core.workspace import workspace_identity

BATCH_ROWS = 100
METADATA_PER_BATCH = 200
MAX_CANDIDATES = 10000


class Incomplete(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class Metadata:
    def __init__(self):
        self.calls = 0
        self.roots = {}
        self.resolved_roots = {}

    def call(self, function, *args, **kwargs):
        if self.calls >= METADATA_PER_BATCH:
            raise BudgetExceeded()
        self.calls += 1
        return function(*args, **kwargs)

    def close(self):
        for descriptor in self.roots.values():
            os.close(descriptor)

    def resolve(self, path, *, base=Path("/")):
        """Count every lstat/readlink; preserve resolved '..' containment."""
        path = Path(path)
        current = Path("/") if path.is_absolute() else base
        remaining = deque(path.parts[1:] if path.is_absolute() else path.parts)
        links = 0
        while remaining:
            name = remaining.popleft()
            if name in ("", "."):
                continue
            if name == "..":
                current = current.parent
                continue
            candidate = current / name
            try:
                info = self.call(os.lstat, candidate)
            except FileNotFoundError:
                current = candidate
                continue
            if stat.S_ISLNK(info.st_mode):
                links += 1
                if links > 32:
                    raise Incomplete("symlink_limit")
                target = Path(self.call(os.readlink, candidate))
                if target.is_absolute():
                    current = Path("/")
                    parts = target.parts[1:]
                else:
                    parts = target.parts
                remaining.extendleft(reversed(parts))
            else:
                current = candidate
        return current

    def root_descriptor(self, root):
        if root in self.roots:
            return self.roots[root]
        descriptor = self.call(os.open, "/", os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in root.parts[1:]:
                next_descriptor = self.call(os.open, component, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            self.roots[root] = descriptor
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def size(self, raw_root, relative):
        if raw_root not in self.resolved_roots:
            self.resolved_roots[raw_root] = self.resolve(raw_root)
        root = self.resolved_roots[raw_root]
        target = self.resolve(relative, base=root)
        try:
            parts = target.relative_to(root).parts
        except ValueError:
            raise Incomplete("escaped_path") from None
        if not parts:
            raise Incomplete("nonregular")
        root_descriptor = self.root_descriptor(root)
        descriptor = root_descriptor
        try:
            for component in parts[:-1]:
                next_descriptor = self.call(os.open, component, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                if descriptor != root_descriptor:
                    os.close(descriptor)
                descriptor = next_descriptor
            file_descriptor = self.call(os.open, parts[-1], os.O_PATH | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            try:
                info = self.call(os.fstat, file_descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise Incomplete("nonregular")
                return info.st_size
            finally:
                os.close(file_descriptor)
        finally:
            if descriptor != root_descriptor:
                os.close(descriptor)


def _slug(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", value):
        raise Incomplete("invalid_scope")
    return value


def _root(row):
    data = Path(settings.DATA_DIR).absolute()
    if row["org_id"]:
        return data / "orgs" / _slug(row["org__slug"]) / "data-sources"
    return data / "studios" / _slug(row["studio__org__slug"]) / _slug(row["studio__slug"]) / "project"


def scan(org_id, desired_workspace):
    """One complete, interval-labelled reference total, or explicit incomplete."""
    result = {"quality": "incomplete", "inspected": 0, "total_candidates": 0,
              "metadata_calls": 0, "missing": 0, "scope": "shared-workspace"}
    if not desired_workspace or workspace_identity(settings.DATA_DIR) != desired_workspace:
        result["reason"] = "workspace_unavailable"
        return result
    base = DataSource.objects.filter(Q(org_id=org_id) | Q(studio__org_id=org_id), type__in=INLINE_TYPES)
    before = base.aggregate(total=Count("pk"), maximum=Max("pk"), updated=Max("updated_at"))
    result["total_candidates"] = before["total"]
    if before["total"] > MAX_CANDIDATES:
        result["reason"] = "candidate_limit"
        return result
    # Bound transferred JSON scalars in PostgreSQL before Python sees them.
    rows = base.annotate(
        path_size=Func(Cast(F("config__path"), TextField()), function="octet_length", output_field=IntegerField()),
        upload_size=Func(Cast(F("config__upload"), TextField()), function="octet_length", output_field=IntegerField()),
    ).annotate(
        safe_path=Case(When(path_size__lte=4098, then=F("config__path")), default=Value(None), output_field=JSONField()),
        safe_upload=Case(When(upload_size__lte=32, then=F("config__upload")), default=Value(None), output_field=JSONField()),
    )
    cursor, total, uploads = 0, 0, 0
    try:
        while True:
            batch = list(rows.filter(pk__gt=cursor).order_by("pk").values(
                "pk", "org_id", "org__slug", "studio__slug", "studio__org__slug",
                "path_size", "upload_size", "safe_path", "safe_upload",
            )[:BATCH_ROWS])
            if not batch:
                break
            metadata = Metadata()
            admitted = 0
            try:
                for row in batch:
                    if result["inspected"] >= MAX_CANDIDATES:
                        raise Incomplete("candidate_limit")
                    if row["upload_size"] and row["upload_size"] > 32:
                        raise Incomplete("invalid_upload_flag")
                    if row["safe_upload"]:
                        uploads += 1
                        relative = row["safe_path"]
                        if row["path_size"] and row["path_size"] > 4098:
                            raise Incomplete("path_limit")
                        if relative is not None and not isinstance(relative, str):
                            raise Incomplete("invalid_path")
                        relative = (relative or "").strip()
                        if len(relative.encode()) > 4096 or len(Path(relative).parts) > 64:
                            raise Incomplete("path_limit")
                        if relative:
                            try:
                                size = metadata.size(_root(row), relative)
                            except FileNotFoundError:
                                size = 0
                                result["missing"] += 1
                            total += size
                        else:
                            result["missing"] += 1
                    cursor = row["pk"]
                    result["inspected"] += 1
                    admitted += 1
            except BudgetExceeded:
                if not admitted:
                    raise Incomplete("metadata_limit") from None
                # Resume at the first unprocessed row with a fresh bounded batch.
            finally:
                result["metadata_calls"] += metadata.calls
                metadata.close()
        after = base.aggregate(total=Count("pk"), maximum=Max("pk"), updated=Max("updated_at"))
        if before != after:
            raise Incomplete("catalog_changed")
        if workspace_identity(settings.DATA_DIR) != desired_workspace:
            raise Incomplete("workspace_changed")
    except (Incomplete, OSError) as exc:
        result["reason"] = str(exc) if isinstance(exc, Incomplete) else "unreadable"
        return result
    if uploads and result["missing"] >= uploads and not total:
        # Uploads are on record but none was found: more likely a measurement
        # that cannot see the files than an organization with nothing stored.
        result.update(quality="partial", reason="no_files_found")
        return result
    result.update(quality="complete", bytes=total)
    return result
