"""Per-studio git sync — the delivery half of the sacred pipeline.

Port of ``portal/git_sync.py`` with configuration from the StudioRepo row
instead of env vars. Push to the studio's repository → the worker pulls →
the studio's ``project/reports/`` is refreshed → the registry re-scans →
(optionally) changed reports rebuild. Running jobs are unaffected: they
build from copy-on-run sandboxes.

Layout per studio:
    <studio>/repo/       sparse git checkout (only StudioRepo.path)
    <studio>/project/reports/   the working copy report builds read
    <studio>/project/events.yaml    project-root build files (with
    <studio>/project/metrics.yaml   metrics.yaml, config.yaml) mirrored from
    <studio>/project/config.yaml    the PARENT directory of StudioRepo.path
                                     inside the checkout; read by report
                                     builds, the annotations calendar and the
                                     metrics catalog, and -- for assistant.md
                                     -- the AI assistant's system prompt
                                     (see _root_file_relpath
                                     and PROJECT_ROOT_BUILD_FILES).
                                     config.yaml's `theme:` is also stamped
                                     onto Studio.repo_theme by
                                     apps.reports.scan.sync_studio_registry.
    data-sources/config.yaml         NOT materialized: read from the commit
                                     into RepoDataSource rows by
                                     _sync_repo_datasources. The project copy
                                     belongs to apps.datasources.materialize.
    <studio>/project/<declared path>  a file source's committed file (e.g.
                                     ``data/demo.sqlite``), copied blob by
                                     blob out of the published commit -- the
                                     ONLY thing outside the cone that reaches
                                     the project dir, and only because a
                                     declaration named it (_ship_declared_files).

Everything else in the repository stays where it is: a file no report path and
no declaration names never reaches this machine at all.

Which is also the rule for adding one: a feature that wants to read a file
from a studio's repository has to put it in _root_synced_files (or have a
declaration name it) or it will silently find nothing.
"""
from __future__ import annotations

import logging
import os
import posixpath
import re
import shutil
import subprocess
import threading
from pathlib import Path

import yaml
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("trellum.gitsync")

_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

#: Advisory-lock namespace for per-studio git sync. Postgres takes a pair of
#: int4s, so the studio id keys the second slot.
GITSYNC_LOCK_NAMESPACE = 0xB15117  # spells "BI SYNC", from before the rename

#: A studio-admin-controlled repo subpath is interpolated straight into git
#: argv, so it must never look like an option or escape its directory.
_SAFE_REPO_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def validate_repo_fields(path: str | None, branch: str | None) -> None:
    """Reject repo path/branch values that could inject git arguments.

    Raised as ``ValueError`` from the sync path and re-raised as a
    ``ValidationError`` from ``StudioRepo.clean`` so the settings form blocks
    a hostile value before it is ever stored.
    """
    p = path or ""
    if not _SAFE_REPO_PATH.match(p) or ".." in p.split("/"):
        raise ValueError(f"unsafe repo path: {p!r}")
    if (branch or "").startswith("-"):
        raise ValueError(f"unsafe branch: {branch!r}")


def safe_project_relpath(path: str) -> bool:
    """True when a tenant-declared file path is a plain relative path that
    stays inside the project: no absolute root, no drive letter, no backslash,
    no ``..``, no control characters, nothing git could read as an option.

    Same rule as :func:`validate_repo_fields`, loosened on exactly one point --
    a data file may have spaces or non-ASCII in its name, and a repo subpath
    may not -- and tightened on none. Containment is still re-checked against
    the resolved path afterwards, because a symlinked parent directory is not
    visible in the string.

    Length is capped at what ``RepoDataSource.shipped_path`` stores: a path
    the sync could copy but not record would raise at save time, after the
    file was already on disk.
    """
    if not path or path.startswith("-") or "\\" in path or ":" in path:
        return False
    if len(path) > 300:
        return False
    if any(ch < " " or ch == "\x7f" for ch in path):
        return False
    return all(part and part not in (".", "..") for part in path.split("/"))


def _studio_lock(studio_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(studio_id, threading.Lock())


class studio_sync_lock:
    """Cross-process mutex on one studio's checkout.

    A thread lock is enough inside one worker; with several runners against the
    same database, two processes could otherwise clone into the same directory
    at once. Postgres advisory locks are session-scoped, so this is held on the
    calling thread's connection and released in ``__exit__``.

    Non-blocking by design: if another runner is already syncing this studio,
    we skip this round rather than queue behind a slow clone.
    """

    def __init__(self, studio_id: int):
        self.studio_id = studio_id
        self.acquired = False

    def __enter__(self) -> bool:
        from django.db import connections

        with connections["default"].cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(%s, %s)",
                [GITSYNC_LOCK_NAMESPACE, self.studio_id],
            )
            self.acquired = bool(cur.fetchone()[0])
        return self.acquired

    def __exit__(self, *exc) -> None:
        if not self.acquired:
            return
        from django.db import connections

        try:
            with connections["default"].cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(%s, %s)",
                    [GITSYNC_LOCK_NAMESPACE, self.studio_id],
                )
        except Exception:  # noqa: BLE001 - a dropped connection released it anyway
            pass
        finally:
            self.acquired = False


class StudioGitSync:
    def __init__(self, repo):
        """``repo`` is a StudioRepo row (with studio+org preloaded)."""
        self.repo = repo
        self.studio = repo.studio
        self.cache_dir = str(self.studio.repo_dir)

    # ── plumbing ─────────────────────────────────────────────────────────
    @property
    def is_configured(self) -> bool:
        return bool(self.repo.repo_url)

    def _token(self) -> str:
        """The HTTPS token, or '' when this repo isn't token-authenticated."""
        if self.repo.auth_method != "https_token":
            return ""
        return self.repo.token or ""

    def _auth_env(self) -> dict:
        """Environment for every git invocation.

        The token used to be embedded in the clone URL, which parks it in
        ``.git/config`` — readable by every report build sharing the /data
        volume. GIT_ASKPASS keeps it in the process environment for the life
        of the git invocation only; nothing durable records it.

        It is applied to *every* command, not just the obvious network ones:
        a ``--filter=blob:none`` partial clone makes origin a promisor remote,
        so ``git checkout`` (and other read commands) lazily fetch blobs and
        need credentials too. ``GIT_TERMINAL_PROMPT=0`` is always set so a
        missing credential fails fast instead of blocking on a prompt.
        """
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        token = self._token()
        if token:
            askpass = Path(settings.BASE_DIR) / "docker" / "git-askpass.sh"
            env["GIT_ASKPASS"] = str(askpass)
            env["TRELLUM_GIT_ASKPASS_USER"] = "x-token-auth"
            env["TRELLUM_GIT_ASKPASS_TOKEN"] = token
        return env

    def _scrub(self, text: str) -> str:
        """Never let the token reach logs or the DB."""
        token = self.repo.token or ""
        if token and text:
            text = text.replace(token, "***")
        return re.sub(r"https://[^@/\s]+@", "https://***@", text or "")

    def _run_git(self, *args, cwd=None) -> tuple[int, str, str]:
        """Run a git command with the repo's credentials available (via
        GIT_ASKPASS) and terminal prompts disabled."""
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=cwd or self.cache_dir,
                capture_output=True,
                text=True,
                timeout=120,
                env=self._auth_env(),
            )
            return proc.returncode, self._scrub(proc.stdout.strip()), self._scrub(
                proc.stderr.strip()
            )
        except FileNotFoundError:
            return 1, "", "git is not installed on this host"
        except subprocess.TimeoutExpired:
            return 1, "", "git command timed out after 120s"
        except Exception as exc:  # noqa: BLE001
            return 1, "", self._scrub(str(exc))

    # ── clone / fetch ────────────────────────────────────────────────────
    def ensure_clone(self) -> None:
        """Clone on first run: partial + sparse, only the reports dir is
        materialized. The checkout is a cache for publish(); nothing here
        reaches the project dir."""
        validate_repo_fields(self.repo.path, self.repo.branch)
        if os.path.isdir(os.path.join(self.cache_dir, ".git")):
            return
        os.makedirs(self.cache_dir, exist_ok=True)
        logger.info(
            f"{self.studio}: cloning {self._scrub(self.repo.repo_url)} "
            f"(branch {self.repo.branch}, sparse: {self.repo.path}/)"
        )
        # The URL is clean (no embedded token); auth flows through GIT_ASKPASS.
        rc, _, err = self._run_git(
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            "--depth", "1",
            "--branch", self.repo.branch,
            "--single-branch",
            self.repo.repo_url, self.cache_dir,
            cwd=str(self.studio.data_root),
        )
        if rc != 0:
            raise RuntimeError(f"clone failed: {err}")
        for cmd in (
            ("sparse-checkout", "init", "--cone"),
            # ``--`` so a path beginning with '-' can't become a git option.
            ("sparse-checkout", "set", "--", self.repo.path),
            ("checkout",),
        ):
            rc, _, err = self._run_git(*cmd)
            if rc != 0:
                raise RuntimeError(f"{' '.join(cmd)} failed: {err}")

    def _root_file_relpath(self, name: str) -> str:
        """Path to a project-root file inside the checkout, at the PARENT
        directory of the sparse-checked-out reports path.

        ``repo.path="reports"`` -> ``"<name>"`` (repo root).
        ``repo.path="project/reports"`` -> ``"project/<name>"``.

        Always posixpath, never os.path: this is a path *inside a git
        checkout* (compared against ``git diff --name-only`` output, which
        is always forward-slash), not a filesystem path -- os.path.join
        would emit backslashes on Windows and silently stop matching.
        """
        parent = posixpath.dirname(self.repo.path.rstrip("/"))
        return posixpath.join(parent, name) if parent else name

    def _root_synced_files(self) -> list[str]:
        """Every project-root file this sync mirrors out of the checkout.

        Sourced from the runner, which is also where ``import_project``
        takes it from: a git-backed studio syncs and a seeded one is
        imported, and a file that only one of those copies is a file that
        exists on one deployment and silently does not on the other.

        Wider than ``PROJECT_ROOT_BUILD_FILES``: the assistant's briefing is
        materialized but never staged into the build sandbox.
        """
        from apps.runner.executor import project_root_materialized_files

        return list(project_root_materialized_files())

    def _empty_tree(self) -> str:
        rc, out, _ = self._run_git("hash-object", "-t", "tree", "/dev/null")
        return out if rc == 0 else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

    def fetch(self) -> dict:
        """Clone or fetch, then record the remote head and what publishing it
        would change relative to ``last_synced_sha``. Returns that pending
        summary (``{}`` when the published commit is the remote head). The
        project dir is not touched."""
        validate_repo_fields(self.repo.path, self.repo.branch)
        branch = self.repo.branch
        if not os.path.isdir(os.path.join(self.cache_dir, ".git")):
            self.ensure_clone()
        else:
            # Scrub any token a previous version embedded in the remote URL.
            self._run_git("remote", "set-url", "origin", self.repo.repo_url)
            # Explicit refspec: a single-branch clone only tracks the branch
            # it was cloned with, and the settings page may since have named
            # another. ``+`` accepts a rewritten branch.
            rc, _, err = self._run_git(
                "fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
            )
            if rc != 0:
                raise RuntimeError(f"fetch failed: {err}")
        rc, remote_sha, err = self._run_git("rev-parse", f"origin/{branch}")
        if rc != 0:
            raise RuntimeError(f"rev-parse failed: {err}")

        published = self.repo.last_synced_sha
        pending = {} if remote_sha == published else self._pending_summary(published, remote_sha)
        now = timezone.now()
        self.repo.remote_sha = remote_sha
        self.repo.remote_checked_at = now
        self.repo.last_sync_at = now
        self.repo.pending_changes = pending
        self.repo.save(
            update_fields=["remote_sha", "remote_checked_at", "last_sync_at", "pending_changes"]
        )
        return pending

    def _pending_summary(self, from_sha: str, to_sha: str) -> dict:
        """What publishing ``to_sha`` changes against ``from_sha`` (the
        published commit). A force-pushed branch (``rewritten``) lists its
        whole commit log but is still diffed against the published commit
        while that is a local object; a first publish (``initial``), or a
        published commit the cache no longer has, is diffed against the
        empty tree (``full_listing``: every report reads as added)."""
        from apps.datasources.repo_sync import DECLARATION_FILE

        initial = not from_sha
        rewritten = False
        if not initial:
            rc, _, _ = self._run_git("merge-base", "--is-ancestor", from_sha, to_sha)
            rewritten = rc != 0
        full = initial or (rewritten and self._run_git("cat-file", "-e", from_sha)[0] != 0)
        base = self._empty_tree() if full else from_sha

        commits: list[dict] = []
        log_range = to_sha if initial or rewritten else f"{from_sha}..{to_sha}"
        rc, out, err = self._run_git(
            "log", "--format=%H%x1f%an%x1f%aI%x1f%s", "--name-only", "--no-renames",
            "-n", "100", log_range,
        )
        if rc != 0:
            raise RuntimeError(f"log failed: {err}")
        rc, total, err = self._run_git("rev-list", "--count", log_range)
        if rc != 0:
            raise RuntimeError(f"rev-list failed: {err}")
        for line in out.split("\n"):
            if "\x1f" in line:
                sha, author, date, message = line.split("\x1f", 3)
                commits.append(
                    {"sha": sha, "author": author, "date": date, "message": message, "files": []}
                )
            elif line.strip() and commits and len(commits[-1]["files"]) < 200:
                # ponytail: 200 files per commit; a truncated flag if the UI needs it
                commits[-1]["files"].append(line.strip())

        root_relpaths = {self._root_file_relpath(n): n
                         for n in (*self._root_synced_files(), DECLARATION_FILE)}
        rc, out, err = self._run_git(
            "diff", "--name-status", "--no-renames", base, to_sha,
            "--", self.repo.path, *root_relpaths,
        )
        if rc != 0:
            raise RuntimeError(f"diff failed: {err}")
        report_prefix = self.repo.path.rstrip("/") + "/"
        statuses: dict[str, str] = {}  # slug -> A / D (report.yaml itself) / M (anything else)
        root_files: list[str] = []
        for line in out.split("\n"):
            if "\t" not in line:
                continue
            status, path = line.split("\t", 1)
            if path in root_relpaths:
                root_files.append(root_relpaths[path])
            elif path.startswith(report_prefix):
                slug, _, rest = path[len(report_prefix):].partition("/")
                if not slug:
                    continue
                if rest == "report.yaml" and status[0] in "AD":
                    statuses[slug] = status[0]
                else:
                    statuses.setdefault(slug, "M")
        reports = {
            "added": sorted(s for s, st in statuses.items() if st == "A"),
            "modified": sorted(s for s, st in statuses.items() if st == "M"),
            "removed": sorted(s for s, st in statuses.items() if st == "D"),
        }

        warnings: list[str] = []
        before, warning = self._declaration_entries("" if full else from_sha)
        if warning:
            warnings.append(warning)
        after, warning = self._declaration_entries(to_sha)
        if warning:
            warnings.append(warning)
        datasources = {"added": [], "removed": [], "changed": []}
        if before is not None and after is not None:
            datasources = {
                "added": sorted(set(after) - set(before)),
                "removed": sorted(set(before) - set(after)),
                "changed": sorted(n for n in set(before) & set(after) if before[n] != after[n]),
            }
        quota = self._quota_warning(reports["added"], reports["removed"])
        if quota:
            warnings.append(quota)

        return {
            "from": from_sha or None, "to": to_sha, "branch": self.repo.branch,
            "rewritten": rewritten, "initial": initial, "full_listing": full,
            "commits": commits, "commits_total": int(total.strip() or 0),
            "reports": reports, "root_files": sorted(root_files),
            "datasources": datasources, "warnings": warnings,
        }

    def _quota_warning(self, added: list[str], removed: list[str]) -> str:
        """Preview of scan.sync_studio_registry's report-cap check for the
        studio as it would be after the publish."""
        from apps.orgs import quotas
        from apps.reports.models import Report

        cap = quotas.limit(self.studio.org, "max_reports")
        if not cap:
            return ""
        present = set(
            Report.objects.filter(studio=self.studio, present_in_scan=True)
            .values_list("slug", flat=True)
        )
        elsewhere = (
            Report.objects.filter(studio__org=self.studio.org, present_in_scan=True)
            .exclude(studio=self.studio).count()
        )
        over = len((present | set(added)) - set(removed)) + elsewhere - cap
        if over <= 0:
            return ""
        return (
            f"{over} report(s) will not be registered: this organization is "
            f"limited to {cap} reports."
        )

    # ── publish ──────────────────────────────────────────────────────────
    def _copy_reports(self, only_slugs: set[str] | None = None) -> None:
        src_reports = os.path.join(self.cache_dir, self.repo.path)
        dst_reports = str(self.studio.reports_dir)
        if not os.path.isdir(src_reports):
            # Deleting the last report removes the whole dir from the sparse
            # checkout; the deletions must still propagate to the working copy.
            if only_slugs:
                for slug in only_slugs:
                    dst = os.path.join(dst_reports, slug)
                    if os.path.isdir(dst):
                        shutil.rmtree(dst)
                        logger.info(f"{self.studio}: removed deleted report {slug}")
            elif only_slugs is None:
                logger.warning(f"{self.studio}: {self.repo.path}/ not in repo")
            # else: only_slugs == set() -- e.g. an events.yaml-only push with
            # a reports/ dir that never existed. Nothing changed under
            # reports/, nothing to do, no warning to log.
            return
        os.makedirs(dst_reports, exist_ok=True)
        # `None` means "full copy" (fresh clone, or no changed-slugs info at
        # all). An explicit set -- even an EMPTY one, e.g. an events.yaml
        # -only push -- means "only these slugs changed". The old
        # `only_slugs if only_slugs else os.listdir(...)` treated an empty
        # set as falsy and fell through to a full re-copy of every report,
        # which is exactly wrong for an events-only change.
        slugs = os.listdir(src_reports) if only_slugs is None else only_slugs
        for slug in slugs:
            src = os.path.join(src_reports, slug)
            dst = os.path.join(dst_reports, slug)
            if os.path.isdir(src):
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            elif only_slugs and os.path.isdir(dst):
                shutil.rmtree(dst)  # deleted in git
                logger.info(f"{self.studio}: removed deleted report {slug}")

    def _copy_root_files(self) -> None:
        """Mirror the project-root build files (events.yaml, metrics.yaml)
        from the checkout to project_root, each if present.

        If a file is ABSENT from the checkout, its working copy is left
        untouched -- never deleted. ``import_project`` seeds these directly
        (outside of git sync), and a repo that simply doesn't declare one
        must not wipe out that seed. A repo that wants the calendar empty
        says so explicitly by pushing ``events: []``, not by omission; the
        same holds for a metrics.yaml with no metrics.

        Each is written via a temp file + ``os.replace`` (atomic on the same
        filesystem): report builds, the annotations calendar and the metrics
        catalog all read these files, potentially while a sync is mid-copy,
        and must never see a half-written one.
        """
        for name in self._root_synced_files():
            src = Path(self.cache_dir) / self._root_file_relpath(name)
            if not src.is_file():
                continue
            dst = Path(self.studio.project_root) / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + f".tmp{os.getpid()}")
            try:
                shutil.copyfile(src, tmp)
                os.replace(tmp, dst)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass

    # ── files a data source declares ─────────────────────────────────────
    def _tree_entry(self, rev: str, relpath: str) -> tuple[str, int, str]:
        """``(mode, size, error)`` for ``rev:relpath``; ``("", 0, "")`` when
        the repository has nothing there.

        ponytail: git cannot answer "how big is that blob" without the blob,
        so on a partial clone asking for the size lazy-fetches it into the
        local object store -- the cap keeps an oversized file out of the
        studio, not off the wire. Upgrade path if that ever bites: pre-fetch
        with ``--filter=blob:limit=<cap>`` and treat still-missing as over.
        """
        rc, out, err = self._run_git("ls-tree", "-l", rev, "--", relpath)
        if rc != 0:
            return "", 0, f"ls-tree failed: {err}"
        fields = out.partition("\t")[0].split()
        if len(fields) != 4:
            return "", 0, ""
        mode, _type, _oid, size = fields
        return mode, int(size) if size.isdigit() else 0, ""

    def _write_blob(self, rev: str, relpath: str, dst: Path) -> str:
        """Write ``rev:relpath`` to ``dst``; returns "" or an error.

        Binary and atomic, both load-bearing: a sqlite database does not
        survive text-mode anything, and a report build may be reading the file
        while a sync replaces it (same reasoning as _copy_root_files).
        """
        tmp = dst.with_name(dst.name + f".tmp{os.getpid()}")
        try:
            with open(tmp, "wb") as fh:
                proc = subprocess.run(
                    ["git", "cat-file", "blob", f"{rev}:{relpath}"],
                    cwd=self.cache_dir, stdout=fh, stderr=subprocess.PIPE,
                    timeout=120, env=self._auth_env(),
                )
            if proc.returncode != 0:
                return self._scrub(proc.stderr.decode("utf-8", "replace").strip())
            os.replace(tmp, dst)
            return ""
        except Exception as exc:  # noqa: BLE001 - one file must not sink the publish
            return self._scrub(str(exc))
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def _project_path(self, rel: str) -> Path | None:
        """Absolute path ``rel`` names inside the project dir, or None when it
        escapes -- checked on the RESOLVED path, so a symlinked parent
        directory cannot be a way out either."""
        root = Path(self.studio.project_root).resolve()
        target = Path(os.path.join(root, rel)).resolve()
        return target if root in target.parents else None

    def _portal_owned_path(self, dst: Path) -> bool:
        """True when ``dst`` lands somewhere the PORTAL writes itself: the
        report copy, the served output directory, the materialized
        data-sources config, or one of the mirrored root files -- the
        assistant's briefing included, since the sync writes that too. A
        declaration must not be able to inject an ``output/<slug>/index.html``,
        or race the materializer for ``data-sources/config.yaml``.

        Judged on the RESOLVED path and case-folded, because the string is not
        the file: ``Reports/`` and ``reports./`` are both the reports directory
        on Windows and macOS.
        """
        root = Path(self.studio.project_root).resolve()
        first = dst.relative_to(root).parts[0].rstrip(". ").casefold()
        return first in {"reports", "output", "data-sources", *self._root_synced_files()}

    def _ship_file(self, row, rev: str, uploads: tuple, remaining: int | None) -> tuple:
        """Deliver one declared source's committed file. Returns
        ``(shipped relpath, error, bytes read from git)``; ``("", "", 0)`` when
        this source has no repository file to ship.

        ``remaining`` is what is left of this publish's byte budget (None: no
        limit). Bytes are charged whether or not the file is shipped: reading
        the size is what pulls the blob into the object store.
        """
        from apps.core import instance as instance_config
        from apps.datasources.models import INLINE_TYPES

        upload_names, upload_paths = uploads
        cfg = row.config or {}
        rel = str(cfg.get("path") or "").strip()
        if (
            not row.present or row.type not in INLINE_TYPES or not rel
            # The portal owns these bytes: an `upload: true` declaration, or a
            # binding of THIS name that accepts uploads -- a committed file an
            # analyst also refreshes by hand is a supported combination
            # (models.TYPE_FIELDS). A push never overwrites an upload.
            or cfg.get("upload") or row.name in upload_names
        ):
            return "", "", 0
        if not safe_project_relpath(rel):
            return "", f"declared path is not inside the project directory: {rel!r}", 0
        dst = self._project_path(rel)
        if dst is None:
            return "", f"declared path is not inside the project directory: {rel!r}", 0
        if self._portal_owned_path(dst):
            return "", f"declared path is in a directory the portal writes itself: {rel}", 0
        if dst in upload_paths:
            # A DIFFERENT source's upload destination: names are not paths, and
            # the file on disk is what a copy would destroy.
            return "", f"declared path is where an uploaded data source keeps its file: {rel}", 0
        if remaining is not None and remaining <= 0:
            return "", f"not read: this publish reached its data-file budget ({rel})", 0

        source = self._root_file_relpath(rel)
        mode, size, err = self._tree_entry(rev, source)
        if err:
            return "", err, 0
        if not mode:
            return "", "", 0  # declared, not committed (yet)
        if mode not in ("100644", "100755"):
            # 120000 is a symlink -- following one is how a repository reads a
            # file it was never given. 040000/160000 are a directory/submodule.
            return "", f"{rel} is not a regular file in the repository", 0
        cap = instance_config.max_upload_bytes()
        if cap and size > cap:
            return "", (
                f"{rel} is {size // (1024 * 1024)} MB; this portal ships at most "
                f"{cap // (1024 * 1024)} MB per data source. Raise the limit or "
                f"upload the file instead."
            ), size
        if remaining is not None and size > remaining:
            return "", (
                f"{rel} was not shipped: this publish already used its "
                f"{cap // (1024 * 1024)} MB budget for declared data files."
            ), size
        dst.parent.mkdir(parents=True, exist_ok=True)
        err = self._write_blob(rev, source, dst)
        return ("", err, size) if err else (rel, "", size)

    def _unship(self, rel: str, upload_paths: set) -> None:
        """Drop a file an earlier sync shipped that ``rev`` no longer declares.
        Only ever removes what this sync wrote, only inside the project, and
        never a path an uploaded source has since claimed."""
        target = self._project_path(rel) if safe_project_relpath(rel) else None
        if target is None or target in upload_paths or not target.is_file():
            return
        try:
            target.unlink()
        except OSError:
            return
        logger.info(f"{self.studio}: removed {rel} (no longer declared)")

    def _portal_uploads(self) -> tuple[set, set]:
        """``(names, destination paths)`` whose bytes the PORTAL owns.

        By NAME because a declaration of the same name is the supported
        "committed file an analyst also refreshes by hand" combination, and by
        PATH because a declaration of a DIFFERENT name may point at another
        source's upload destination -- and it is the file on disk, not the
        name, that a copy would destroy.
        """
        from apps.datasources.materialize import upload_target
        from apps.datasources.models import sources_for_studio

        names, paths = set(), set()
        for ds in sources_for_studio(self.studio):
            if not ds.is_uploaded:
                continue
            names.add(ds.name)
            try:
                paths.add(upload_target(ds))
            except ValueError:  # a stored path that escapes its own root
                pass
        return names, paths

    def _ship_declared_files(self, rev: str) -> list[str]:
        """Copy the files declared as file/sqlite sources out of ``rev`` into
        the project dir, and drop the ones no longer declared. Returns
        warnings for the repo row.

        The declarations are already mirrored into ``RepoDataSource``, so the
        set of paths a repository actually needs is known here without
        widening the sparse cone: each blob is read straight out of the commit
        (``git cat-file``), exactly as the declaration file itself is. A
        repository with a 4 GB ``data/`` directory still pays only for the
        files it declares, and nothing it does not declare is ever fetched.

        ponytail: ONE number (the instance's max upload size) is both the
        per-file cap and the whole publish's budget, and a file is charged to
        it even when it is refused -- git cannot report a blob's size without
        fetching it, so reading the size is the download. That bounds what one
        publish can pull into ``<studio>/repo/.git`` and how long the single
        sync thread spends doing it. Split the two numbers if a repository
        legitimately declares several large files.
        """
        from apps.core import instance as instance_config
        from apps.datasources.models import RepoDataSource

        uploads = self._portal_uploads()
        remaining = instance_config.max_upload_bytes() or None
        warnings: list[str] = []
        for row in RepoDataSource.objects.filter(studio=self.studio):
            shipped, error, spent = self._ship_file(row, rev, uploads, remaining)
            if remaining is not None:
                remaining -= spent
            error = error[:300]
            if error:
                warnings.append(f"{row.name}: {error}")
                logger.warning(f"{self.studio}: data source {row.name}: {error}")
            if row.shipped_path and row.shipped_path != shipped:
                self._unship(row.shipped_path, uploads[1])
            if (shipped, error) != (row.shipped_path, row.file_error):
                row.shipped_path, row.file_error = shipped, error
                row.save(update_fields=["shipped_path", "file_error"])
        return warnings

    @staticmethod
    def _yaml_problem(exc: Exception) -> str:
        """An exception from parsing a tenant YAML file as text safe to show
        on the settings page: a YAML error's own text quotes the offending
        source line, so only the parser's problem and position are kept."""
        if isinstance(exc, yaml.MarkedYAMLError):
            mark = exc.problem_mark
            if mark is None:
                return str(exc.problem)
            where = f"line {mark.line + 1}"
            if mark.name and not mark.name.startswith("<"):
                where = f"{Path(mark.name).parent.name}/{Path(mark.name).name}, {where}"
            return f"{exc.problem} ({where})"
        if isinstance(exc, ValueError):  # parse_declaration's own fixed messages
            return str(exc)
        return type(exc).__name__

    def _declaration_text(self, rev: str) -> tuple[str, str]:
        """``(text, warning)`` of the data source declaration at ``rev``;
        ``""`` for an absent file, which is an empty declaration."""
        from apps.datasources.repo_sync import DECLARATION_FILE

        relpath = self._root_file_relpath(DECLARATION_FILE)
        # Tree-only lookup, answered locally: tells "absent" apart from "the
        # blob fetch failed", which _run_git reports with the same non-zero rc.
        rc, listed, err = self._run_git("ls-tree", rev, "--", relpath)
        if rc != 0:
            return "", f"ls-tree failed: {err}"
        if not listed:
            return "", ""
        rc, text, err = self._run_git("show", f"{rev}:{relpath}")
        if rc != 0:
            return "", f"show failed: {err}"
        return text, ""

    def _declaration_entries(self, rev: str) -> tuple[dict | None, str]:
        """Declared sources at ``rev`` as ``{name: (type, config)}`` (``{}``
        for the empty tree), or ``(None, warning)`` when unreadable."""
        from apps.datasources.repo_sync import DECLARATION_FILE, parse_declaration

        if not rev:
            return {}, ""
        text, warning = self._declaration_text(rev)
        if warning:
            return None, f"{DECLARATION_FILE}: {warning}"[:400]
        try:
            entries = parse_declaration(text)
        except Exception as exc:  # noqa: BLE001 - a broken tenant file is a warning, not a crash
            return None, f"{DECLARATION_FILE}: {self._yaml_problem(exc)}"[:400]
        return {e["name"]: (e["type"], e["config"]) for e in entries}, ""

    def _sync_repo_datasources(self, rev: str) -> str:
        """Mirror the repository's data source declaration at ``rev`` into
        ``RepoDataSource`` rows. The file is read straight from the commit
        (``git show``), so it is never checked out into the sparse cone nor
        copied into project_root, which ``materialize`` owns. Returns a short
        warning for the repo row when the file is unreadable or malformed,
        else ``""``.
        """
        from apps.datasources.repo_sync import DECLARATION_FILE, sync_repo_datasources

        def warn(message: str) -> str:
            message = " ".join(message.split())
            logger.warning(f"{self.studio}: {DECLARATION_FILE} not synced: {message}")
            return f"{DECLARATION_FILE}: {message}"[:400]

        text, warning = self._declaration_text(rev)
        if warning:
            return warn(warning)
        try:
            sync_repo_datasources(self.studio, text)
        except Exception as exc:  # noqa: BLE001 - a broken tenant file must never sink the sync
            return warn(self._yaml_problem(exc))
        return ""

    def publish(self, pending: dict) -> tuple[list[str], bool]:
        """Bring the project dir up to ``pending["to"]``: check the commit
        out, validate every report.yaml in the checkout, and only then copy.
        Returns ``(changed_slugs, published)``; a failed publish records a
        ``RepoPublish`` error row and leaves the project dir and
        ``last_synced_sha`` untouched."""
        from apps.reports.scan import ReportQuotaExceeded, sync_studio_registry
        from apps.studios.models import RepoPublish
        from trellum.runner import scan_report_configs

        to_sha = pending["to"]
        initial = bool(pending.get("initial"))
        reports = pending.get("reports") or {}
        added, modified, removed = (
            set(reports.get(k) or ()) for k in ("added", "modified", "removed")
        )
        trigger = (
            "initial" if initial
            else "manual" if self.repo.publish_requested
            else "webhook" if self.repo.sync_reason == "webhook"
            else "auto"
        )
        row = RepoPublish(
            studio=self.studio, from_sha=pending.get("from") or "", to_sha=to_sha,
            trigger=trigger, summary=pending,
            published_by=self.repo.publish_requested_by if self.repo.publish_requested else None,
        )
        rebuild = self.repo.publish_rebuild_override
        if rebuild is None:
            rebuild = self.repo.auto_run_changed
        # The request is consumed whichever way this goes; a persistent
        # failure must not retry every tick.
        consumed = self._spend_request()

        try:
            # ``checkout`` (not pull) keeps the sparse cone and survives a
            # rewritten branch; the detached HEAD is fine for a cache.
            rc, _, err = self._run_git("checkout", "--force", "--detach", to_sha)
            if rc != 0:
                raise RuntimeError(f"checkout failed: {err}")
            # Validate before anything reaches the project dir.
            try:
                scan_report_configs(os.path.join(self.cache_dir, self.repo.path))
            except Exception as exc:  # noqa: BLE001 - any broken report.yaml blocks the publish
                raise RuntimeError(f"report.yaml invalid: {self._yaml_problem(exc)}") from exc
        except Exception as exc:  # noqa: BLE001
            message = self._scrub(str(exc))[:2000]
            logger.error(f"{self.studio}: publish of {to_sha[:10]} failed: {message}")
            row.status, row.error = "error", message
            row.save()
            self.repo.last_error = message
            self.repo.save(update_fields=[*consumed, "last_error"])
            return [], False

        # Root files land on disk BEFORE the registry re-scan reads them
        # (sync_studio_registry -> sync_studio_metrics / the repo_theme
        # parse both read project_root off disk).
        if initial or pending.get("root_files"):
            self._copy_root_files()
        full = initial or bool(pending.get("full_listing"))
        self._copy_reports(only_slugs=None if full else added | modified | removed)

        warnings: list[str] = []
        try:
            sync_studio_registry(self.studio)
        except ReportQuotaExceeded as exc:
            # The reports that fit are live. Surface the overage on the repo
            # row, where the org admin already looks when a sync misbehaves.
            warnings.append(str(exc))
            logger.info(f"{self.studio}: {exc}")
        warnings.append(self._sync_repo_datasources(to_sha))
        try:
            # After the mirror, which is what knows the paths.
            warnings.extend(self._ship_declared_files(to_sha))
        except Exception as exc:  # noqa: BLE001 - the reports are already live
            logger.exception(f"{self.studio}: shipping declared files failed: {exc}")
            warnings.append(f"declared data files: {type(exc).__name__}")

        self.repo.last_synced_sha = to_sha
        self.repo.last_sync_at = timezone.now()
        self.repo.pending_changes = {}
        self.repo.last_error = "; ".join(w for w in warnings if w)
        self.repo.save(
            update_fields=[*consumed, "last_synced_sha", "last_sync_at", "pending_changes", "last_error"]
        )
        row.save()

        changed = sorted(added | modified | removed)
        logger.info(
            f"{self.studio}: published {to_sha[:10]}"
            + (f" ({', '.join(changed)})" if changed else " (full)")
        )
        # A full listing (first publish, or no published commit to diff
        # against) must not storm the queue with every report.
        if rebuild and not full and (added | modified):
            self._auto_run(added | modified)
        return changed, True

    def _spend_request(self) -> list[str]:
        """Clear the publish request on the in-memory repo; returns the
        field names for the caller's ``update_fields``."""
        self.repo.publish_requested = False
        self.repo.publish_requested_by = None
        self.repo.publish_requested_to = ""
        self.repo.publish_rebuild_override = None
        return [
            "publish_requested", "publish_requested_by", "publish_requested_to",
            "publish_rebuild_override",
        ]

    # ── the one entry point ──────────────────────────────────────────────
    def sync(self) -> dict:
        """Fetch, then publish when the mode or a request allows it.

        Never raises: failures land in StudioRepo.last_error and the dict.
        """
        from apps.studios.models import RepoPublish

        result = {"ok": False, "changed": [], "error": ""}
        if not self.is_configured:
            result["error"] = "no repository configured"
            return result

        lock = _studio_lock(self.studio.pk)
        if not lock.acquire(blocking=False):
            result["error"] = "sync already in progress"
            return result
        cross_process = studio_sync_lock(self.studio.pk)
        if not cross_process.__enter__():
            # Another runner owns this studio's checkout right now.
            lock.release()
            result["error"] = "sync already in progress on another worker"
            return result
        try:
            pending = self.fetch()
            changed: list[str] = []
            published = False
            reviewed = self.repo.publish_requested_to
            if pending and self.repo.publish_requested and reviewed and reviewed != pending["to"]:
                # The request named the commit it reviewed; the branch has
                # moved on since. Keep the new pending set, drop the request.
                self.repo.last_error = (
                    "The remote moved since you reviewed it — review and publish again."
                )
                self.repo.save(update_fields=[*self._spend_request(), "last_error"])
            elif pending and self.repo.publish_requested:
                changed, published = self.publish(pending)
            elif pending and self.repo.publish_mode == "auto":
                # A commit that already failed to publish is not retried on
                # its own; a fix is a new commit, a retry is a request.
                failed = RepoPublish.objects.filter(
                    studio=self.studio, to_sha=pending["to"], status="error"
                ).exists()
                if not failed:
                    changed, published = self.publish(pending)
            elif self.repo.publish_requested:
                # Nothing to publish: the request is spent.
                self.repo.save(update_fields=self._spend_request())
            if not published and not pending:
                self.repo.last_error = ""  # nothing pending, nothing wrong
            if not published and self.repo.last_synced_sha:
                # Declarations are read from the published commit, not the
                # checkout, so a fetch with nothing new still has them to
                # mirror (the first sync after an upgrade, or a clone made
                # before declarations were mirrored). A publish mirrors its
                # own commit, so only the no-publish path needs this.
                warning = self._sync_repo_datasources(self.repo.last_synced_sha)
                if warning:
                    self.repo.last_error = warning
            self.repo.sync_requested = False
            self.repo.sync_reason = "schedule"
            self.repo.save(update_fields=["sync_requested", "sync_reason", "last_error"])
            result.update(ok=True, changed=changed, had_changes=published)
            return result
        except Exception as exc:  # noqa: BLE001
            message = self._scrub(str(exc))
            logger.error(f"{self.studio}: sync failed: {message}")
            self.repo.last_error = message[:2000]
            # Every request is spent, or the repo stays due every tick.
            self.repo.sync_requested = False
            self.repo.sync_reason = "schedule"
            self.repo.save(update_fields=[
                "last_error", "sync_requested", "sync_reason", *self._spend_request(),
            ])
            result["error"] = message
            return result
        finally:
            cross_process.__exit__(None, None, None)
            lock.release()

    def _auto_run(self, changed_slugs: set[str]) -> None:
        """Enqueue rebuilds for reports whose files changed in the publish."""
        from apps.reports.models import Report
        from apps.runner.services import enqueue

        qs = Report.objects.filter(
            studio=self.studio, present_in_scan=True, disabled=False, slug__in=changed_slugs
        )
        n = 0
        for report in qs.select_related("studio", "studio__org"):
            if enqueue(report, trigger="git") == "queued":
                n += 1
        if n:
            logger.info(f"{self.studio}: auto-run enqueued {n} report(s)")


def due_repos():
    """StudioRepos that want a sync now: requested, or poll interval elapsed."""
    from apps.studios.models import StudioRepo

    now = timezone.now()
    qs = StudioRepo.objects.exclude(repo_url="").select_related("studio", "studio__org")
    due = []
    for repo in qs:
        if repo.sync_requested or repo.publish_requested:
            due.append(repo)
        elif repo.sync_interval_minutes > 0:
            if repo.last_sync_at is None or (
                now - repo.last_sync_at
            ).total_seconds() >= repo.sync_interval_minutes * 60:
                due.append(repo)
    return due


class GitSyncThread(threading.Thread):
    """Worker-side thread: processes due syncs sequentially so a slow clone
    never blocks run claiming."""

    def __init__(self, poll_seconds: int = 15):
        super().__init__(daemon=True, name="git-sync")
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        from django.db import close_old_connections

        while not self._stop.is_set():
            try:
                close_old_connections()
                for repo in due_repos():
                    if self._stop.is_set():
                        break
                    StudioGitSync(repo).sync()
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"{type(exc).__name__}: {exc}")
            self._stop.wait(self.poll_seconds)
