"""Project root resolution and project-level configuration.

Provides a single source of truth for locating the host project's root
directory.  All framework modules that need filesystem paths (reports/,
output/, events.yaml, .env, etc.) should call ``get_project_root()``
instead of deriving paths from ``__file__``.

Also owns ``config.yaml`` loading and the ``extensions`` block, which is how
a host application injects its own markup into generated reports without the
framework knowing anything about that host.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

FW_NOW_ENV = "FW_NOW"
FW_EXTENSIONS_ENV = "FW_EXTENSIONS_JSON"


def get_now_utc() -> datetime:
    """Current UTC time, overridable via the ``FW_NOW`` environment variable.

    Reports derive ``ctx.today`` and friends from this, and those dates end up
    in chart axes, titles and filter defaults. Pinning the clock is what makes
    a run reproducible, which visual-regression baselines depend on -- without
    it a baseline captured today fails tomorrow purely because the dates moved.

    Accepts ``YYYY-MM-DD`` or a full ISO 8601 timestamp. An unparseable value
    is ignored rather than raising, so a stray env var cannot break production.
    """
    raw = os.environ.get(FW_NOW_ENV, "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            print(
                f"  [warn] {FW_NOW_ENV}={raw!r} is not a valid ISO date/datetime "
                f"-- using the real clock.",
                flush=True,
            )
    return datetime.now(timezone.utc)

_project_root_override: str | None = None


def set_project_root(path: str) -> None:
    """Explicitly set the project root (for embedding or testing)."""
    global _project_root_override
    _project_root_override = os.path.abspath(path)


def get_project_root() -> str:
    """Resolve the project root.

    Priority:
      1. Explicit override via ``set_project_root()``
      2. ``FW_PROJECT_ROOT`` environment variable
      3. Current working directory
    """
    if _project_root_override:
        return _project_root_override
    return os.path.abspath(os.environ.get("FW_PROJECT_ROOT", os.getcwd()))


def load_project_config() -> dict:
    """Load the project's ``config.yaml``. Returns ``{}`` if there isn't one.

    The project's own file wins. The framework-relative path is kept as a
    fallback only for older projects that placed the file next to the
    framework package -- consumers mount the framework as a pinned submodule
    and cannot edit anything inside it, so that location was never usable for
    per-project configuration.

    A malformed file is not worth crashing a report build over: warn and carry
    on with defaults.
    """
    import yaml

    candidates = [
        os.path.join(get_project_root(), "config.yaml"),
        os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
        ),
    ]
    for cfg_path in candidates:
        if not os.path.isfile(cfg_path):
            continue
        try:
            with open(cfg_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as exc:
            print(f"  [warn] could not parse {cfg_path}: {exc}", flush=True)
            return {}
    return {}


def load_extensions() -> dict:
    """Resolve the ``extensions`` block that host applications render through.

    The framework emits no host-specific markup of its own. Anything serving
    reports behind its own control plane -- navigation back to an index, a
    support widget, an analytics script -- supplies it here, so the generated
    artifact stays neutral and a standalone report issues no requests to a
    server that isn't there.

    Resolution order:

      1. ``FW_EXTENSIONS_JSON`` -- a JSON object. This is how a parent process
         passes extensions to a report subprocess it spawns.
      2. the ``extensions:`` block of the project-root ``config.yaml``
      3. empty -- render nothing

    Returns a dict with at least ``nav_html`` (str), ``scripts`` (list of
    str) and ``live_query_url`` (str), so callers never have to defend
    against missing keys::

        extensions:
          nav_html: ""        # injected into the header nav group
          scripts: []         # <script src> entries injected before </body>
          live_query_url: ""  # host endpoint for declared live queries;
                              # empty/absent means the page never fetches

    ``live_query_url`` is where the runtime POSTs declared live queries
    (``ctx.declare_live_query``). Present means the serving host answers the
    live-query contract in docs/COMPATIBILITY.md; absent — standalone build,
    old host, share link, email snapshot — means the live control stays a
    disabled label over the build-time snapshot.
    """
    raw: dict = {}
    env = os.environ.get(FW_EXTENSIONS_ENV, "").strip()
    if env:
        try:
            parsed = json.loads(env)
            if isinstance(parsed, dict):
                raw = parsed
            else:
                print(
                    f"  [warn] {FW_EXTENSIONS_ENV} must be a JSON object, got "
                    f"{type(parsed).__name__} -- ignoring.",
                    flush=True,
                )
        except ValueError as exc:
            print(
                f"  [warn] {FW_EXTENSIONS_ENV} is not valid JSON ({exc}) "
                f"-- ignoring.",
                flush=True,
            )
    if not raw:
        block = load_project_config().get("extensions")
        if isinstance(block, dict):
            raw = block

    nav_html = raw.get("nav_html") or ""
    scripts = raw.get("scripts") or []
    if not isinstance(scripts, list):
        scripts = [scripts]
    return {
        "nav_html": str(nav_html),
        "scripts": [str(s) for s in scripts if s],
        "live_query_url": str(raw.get("live_query_url") or ""),
    }
