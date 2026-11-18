"""Local, read-only health inspection for a published studio's source files."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


_MESSAGES = {
    "ready": "Studio files ready.",
    "missing": "Studio files missing. Restore the published version to continue.",
    "mismatch": "Studio files need restoring. The local copy could not be verified.",
    "unchecked": "Studio files have not been checked.",
}


def _checked_path(path: Path, *, directory: bool) -> Path:
    """Require a real path, including its parents, without following links."""
    path = Path(os.path.abspath(path))
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode) or getattr(current, "is_junction", lambda: False)():
            raise ValueError("Unsafe studio path")
        is_directory = current != path or directory
        if not (stat.S_ISDIR(mode) if is_directory else stat.S_ISREG(mode)):
            raise ValueError("Invalid studio path")
    return path


def _safe_slug(slug: str) -> bool:
    return bool(slug) and slug not in {".", ".."} and not any(
        c in slug for c in "/\\:"
    ) and not any(ord(c) < 32 or ord(c) == 127 for c in slug)


def _checkout_head(cache: Path) -> str:
    """Inspect only this checkout; never discover a parent or fetch objects.

    Do not use StudioGitSync's credential-bearing environment here. The
    timeout kills the git subprocess, and rev-parse prints only a commit id.
    """
    cache = _checked_path(cache, directory=True)
    _checked_path(cache / ".git", directory=True)
    _checked_path(cache / ".git" / "HEAD", directory=False)
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("GIT_", "TRELLUM_GIT_"))
    }
    env.update({
        "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ALLOW_PROTOCOL": "",
    })
    proc = subprocess.run(
        ["git", "--git-dir", str(cache / ".git"), "--work-tree", str(cache),
         "-c", "credential.helper=", "-c", "core.fsmonitor=false",
         "rev-parse", "--verify", "HEAD^{commit}"],
        cwd=cache, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, timeout=3,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def check_studio_files(repo) -> dict:
    """Return ``{state, message, ready}`` without writes or network access.

    All present registry rows count, including disabled and private reports.
    Slugs stream in one query with bounded memory; intentionally removed rows
    do not require files. Messages never contain paths or report identities.
    """
    from apps.reports.models import Report

    state = "unchecked"
    if repo is not None and repo.repo_url and repo.last_synced_sha:
        try:
            studio = repo.studio
            _checked_path(studio.project_root, directory=True)
            head = _checkout_head(studio.repo_dir)
            if head != repo.last_synced_sha:
                state = "mismatch"
            else:
                state = "ready"
                slugs = Report.objects.filter(
                    studio_id=repo.studio_id, present_in_scan=True,
                ).order_by().values_list("slug", flat=True).iterator(chunk_size=200)
                for slug in slugs:
                    if not _safe_slug(slug):
                        state = "mismatch"
                        break
                    report_dir = studio.reports_dir / slug
                    _checked_path(report_dir, directory=True)
                    _checked_path(report_dir / "report.yaml", directory=False)
        except FileNotFoundError:
            state = "missing"
        except (OSError, ValueError, subprocess.SubprocessError):
            state = "mismatch"
    return {"state": state, "message": _MESSAGES[state], "ready": state == "ready"}
