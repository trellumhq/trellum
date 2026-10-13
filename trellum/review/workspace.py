"""Local analysis workspace catalog, build jobs and request validation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from trellum.report import BaseReport

_SLUG = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")


def catalog(project_root: str | Path) -> dict[str, dict]:
    """Return visible, enabled, confined report manifests in this project."""
    root = Path(project_root).resolve()
    reports_root = root / "reports"
    try:
        resolved_reports = reports_root.resolve(strict=True)
        if reports_root.is_symlink() or not resolved_reports.is_relative_to(root):
            return {}
    except OSError:
        return {}
    result = {}
    try:
        children = sorted(resolved_reports.iterdir(), key=lambda path: path.name)
    except OSError:
        return result
    for directory in children:
        if directory.name.startswith("_") or directory.is_symlink() or not directory.is_dir():
            continue
        try:
            resolved = directory.resolve(strict=True)
            if directory.is_symlink() or not resolved.is_relative_to(resolved_reports):
                continue
            manifest = resolved / "report.yaml"
            if manifest.is_symlink() or not manifest.is_file():
                continue
            config = BaseReport.load_config(str(resolved)) or {}
            if config.get("disabled") or config.get("hidden"):
                continue
            content_name = "slides.yaml" if config.get("format") == "presentation" else "content.md"
            required = content_name if config.get("kind", "report") == "analysis" else "generator.py"
            required_path = resolved / required
            if required_path.is_symlink() or not required_path.is_file():
                continue
            has_symlink = False
            for current, dirs, files in os.walk(resolved, followlinks=False):
                if any((Path(current) / name).is_symlink() for name in dirs + files):
                    has_symlink = True
                    break
                dirs[:] = [name for name in dirs if name != "__pycache__"]
            if has_symlink:
                continue
            slug = config.get("slug", directory.name)
            if resolved.name != slug or config.get("slug", slug) != slug:
                continue
            kind = "analysis" if config.get("kind", "report") == "analysis" else "report"
            if kind == "analysis" and slug != config.get("slug", slug):
                continue
            result[slug] = {"slug": slug, "name": str(config.get("name") or slug),
                            "kind": kind, "dir": resolved}
        except Exception:
            continue
    return result


def source_signature(directory: Path) -> str:
    """Fingerprint the visible source tree without traversing symlinks."""
    digest = hashlib.sha256()
    for current, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = sorted(name for name in dirs if not (Path(current) / name).is_symlink()
                         and name != "__pycache__")
        for name in sorted(files):
            path = Path(current) / name
            if path.is_symlink() or name.endswith((".pyc", ".pyo")):
                continue
            try:
                stat = path.stat()
                relative = path.relative_to(directory).as_posix()
                digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
            except OSError:
                continue
    return digest.hexdigest()


def source_latest_mtime(directory: Path) -> float:
    latest = 0.0
    for current, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()
                   and name != "__pycache__"]
        for name in files:
            path = Path(current) / name
            if path.is_symlink() or name.endswith((".pyc", ".pyo")):
                continue
            try:
                latest = max(latest, path.stat().st_mtime)
            except OSError:
                continue
    return latest


def _output_info(output_root: Path, slug: str, source_dir: Path) -> tuple[str, str | None, str | None]:
    target = output_root / slug
    try:
        if target.is_symlink() or not target.resolve().is_relative_to(output_root.resolve()):
            return "missing", None, None
        index = target / "index.html"
        meta = target / "_meta.json"
        if index.is_symlink() or meta.is_symlink() or not index.is_file() or not meta.is_file():
            return "missing", None, None
        metadata = json.loads(meta.read_text(encoding="utf-8"))
        if metadata.get("slug") != slug or metadata.get("last_status") != "success":
            return "missing", None, None
        if ((metadata.get("validation") or {}).get("summary") or {}).get("fail", 0):
            return "missing", None, None
        version = str(meta.stat().st_mtime_ns)
        built_at = metadata.get("last_run") or time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(meta.stat().st_mtime))
        try:
            stamp = source_latest_mtime(source_dir)
            signature = source_signature(source_dir)
            from trellum.review.http import STATE
            previous = STATE.workspace_builds.get(slug, {}).get("source_signature")
            stale = previous != signature if previous else stamp > meta.stat().st_mtime + 0.001
        except OSError:
            stale = True
        return ("stale" if stale else "ready"), version, built_at
    except (OSError, ValueError, TypeError):
        return "missing", None, None


class Workspace:
    """In-process bounded asynchronous builds and idempotency state."""
    def __init__(self):
        self.lock = threading.RLock()
        self.build_locks: dict[str, threading.Lock] = {}
        self.jobs: dict[tuple[str, str], dict] = {}
        self.requests: dict[str, dict] = {}
        self.captures: dict[str, dict] = {}
        self.feedback: dict[str, dict] = {}
        self._dir: str | None = None

    def bind(self, output_root: str):
        from trellum.review.http import STATE
        with self.lock:
            self._dir = output_root
            # ReviewState has already loaded its sidecar before dispatching a
            # request. Keep direct references so a fresh analysis session's
            # reset cannot leave a stale idempotency map behind.
            self.requests = STATE.workspace_requests
            self.captures = STATE.workspace_captures
            self.feedback = STATE.workspace_feedback

    def save(self):
        # ReviewState owns atomic persistence of all sidecar fields.
        from trellum.review.http import STATE
        with STATE._cond:
            STATE._save_locked()

    def _persist_fields(self):
        from trellum.review.http import STATE
        self.requests = STATE.workspace_requests
        self.captures = STATE.workspace_captures
        self.feedback = STATE.workspace_feedback

    def start_build(self, root: Path, slug: str) -> dict:
        key = (str(root.resolve()), slug)
        with self.lock:
            current = self.jobs.get(key)
            if current and current["state"] == "building":
                return dict(current)
            self.jobs[key] = {"state": "building", "error": None}
            signature_before = source_signature(root / "reports" / slug)
            def worker():
                job = self.jobs[key]
                staging_root = root / ".trellum-workspace-builds"
                staging = None
                try:
                    if staging_root.is_symlink():
                        raise ValueError("Workspace staging directory cannot be a symlink")
                    staging_root.mkdir(exist_ok=True)
                    if not staging_root.resolve().is_relative_to(root):
                        raise ValueError("Workspace staging directory must stay inside the project")
                    staging = Path(tempfile.mkdtemp(prefix=f"{slug}-", dir=staging_root)) / slug
                    with self.lock:
                        project_lock = self.build_locks.setdefault(key[0], threading.Lock())
                    with project_lock:
                        env = os.environ.copy()
                        code = ("from trellum.runner.execute import run_report; "
                                f"run_report('reports/{slug}', output_dir={str(staging)!r}, "
                                "fail_on_validation=True)")
                        subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                                       check=True, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, timeout=300)
                    if ((staging / "index.html").is_symlink() or (staging / "_meta.json").is_symlink()
                            or not (staging / "index.html").is_file()
                            or not (staging / "_meta.json").is_file()):
                        raise RuntimeError("Build completed without usable local report output")
                    output_root = root / "output"
                    if output_root.is_symlink() or not output_root.resolve().is_relative_to(root):
                        raise ValueError("Output directory must stay inside this project")
                    output_root.mkdir(exist_ok=True)
                    destination = output_root / slug
                    if destination.is_symlink() or not destination.resolve().is_relative_to(output_root.resolve()):
                        raise ValueError("Existing output must stay inside this project's output directory")
                    backup = staging_root / f"{slug}-previous-{time.time_ns()}"
                    had_previous = destination.exists()
                    if had_previous:
                        os.replace(destination, backup)
                    try:
                        os.replace(staging, destination)
                    except Exception:
                        if had_previous and backup.exists():
                            os.replace(backup, destination)
                        raise
                    if had_previous:
                        shutil.rmtree(backup, ignore_errors=True)
                    from trellum.review.http import STATE
                    with STATE._cond:
                        STATE.workspace_builds[slug] = {"source_signature": signature_before}
                        STATE._save_locked()
                    with self.lock:
                        job.update(state="ready", error=None)
                except subprocess.CalledProcessError as exc:
                    detail = (exc.stdout or "").strip()[-1000:] or str(exc)
                    with self.lock:
                        job.update(state="failed", error=detail)
                except Exception as exc:
                    with self.lock:
                        job.update(state="failed", error=str(exc)[:1000])
                finally:
                    if staging is not None:
                        shutil.rmtree(staging.parent, ignore_errors=True)
            threading.Thread(target=worker, daemon=True, name=f"fw-workspace-{slug}").start()
            return dict(self.jobs[key])

    def job(self, root: Path, slug: str):
        with self.lock:
            return dict(self.jobs.get((str(root.resolve()), slug), {}))


WORKSPACE = Workspace()


def fingerprint(value: dict) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         allow_nan=False, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("Request must contain valid JSON values") from exc
    return hashlib.sha256(raw).hexdigest()
