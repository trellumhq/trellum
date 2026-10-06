"""Once a day, quietly: is there a newer trellum release?

The end of a build and ``trellum doctor`` ask the releases feed at most once
a day per machine and say one thing when a newer release exists. Every
failure -- no network, blocked egress, an empty or malformed feed, an
unwritable home directory -- is silence, so a machine that cannot reach the
feed sees exactly the output it sees today. internal planning ticket #066.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from trellum import __version__

FEED_URL = "https://api.github.com/repos/trellumhq/trellum/releases?per_page=10"
UPGRADE = "pip install -U trellum"
TIMEOUT = 1.5
_OFF = {"0", "false", "no", "off"}


def fetch_releases(url: str, timeout: float = TIMEOUT) -> list[dict] | None:
    """The feed's stable releases, newest first, or None on any failure at all.

    Drafts, prereleases and tags that are not plain versions are dropped, so
    the first entry is the newest release worth naming.
    """
    try:
        import urllib.request

        req = urllib.request.Request(url, headers={
            "User-Agent": f"trellum/{__version__}",
            "Accept": "application/vnd.github+json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        releases = [
            {
                "tag": str(r["tag_name"]),
                "name": str(r.get("name") or r["tag_name"]),
                "notes": str(r.get("body") or ""),
                "url": str(r.get("html_url") or ""),
                "published_at": str(r.get("published_at") or ""),
            }
            for r in data
            if isinstance(r, dict) and parse_version(r.get("tag_name", ""))
            and not r.get("draft") and not r.get("prerelease")
        ]
        return sorted(releases, key=lambda r: parse_version(r["tag"]), reverse=True)
    except Exception:
        # A probe, like runner/ports.py: silence is the contract.
        return None


def parse_version(tag: str) -> tuple[int, ...] | None:
    # ponytail: no pre-release ordering; if tags ever carry rc suffixes,
    # switch to packaging.version.
    try:
        return tuple(int(p) for p in str(tag).strip().lstrip("v").split("."))
    except ValueError:
        return None


def is_newer(tag: str, than: str = __version__) -> bool:
    theirs, ours = parse_version(tag), parse_version(than)
    return bool(theirs and ours and theirs > ours)


def enabled() -> bool:
    """False under CI, ``FW_UPDATE_CHECK=0`` or ``update_check: false``.

    Reads config.yaml itself: ``project.load_project_config()`` warns aloud
    about a file it cannot parse, and this check adds no lines of its own.
    """
    if os.environ.get("FW_UPDATE_CHECK", "").lower() in _OFF or os.environ.get("CI"):
        return False
    try:
        import yaml

        from trellum.project import get_project_root

        with open(os.path.join(get_project_root(), "config.yaml"), encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception:
        return True
    return not isinstance(config, dict) or str(config.get("update_check", True)).lower() not in _OFF


# ── the once-a-day stamp ──────────────────────────────────────────────────
# Best-effort, like review/state.py's sidecar: a home directory that cannot be
# written just means the check runs again next time. Path.home() itself
# raises for a uid with no passwd entry and no HOME, so it stays inside the
# guards too.

def _stamp_path() -> Path:
    return Path.home() / ".trellum" / "update-check.json"


def _today() -> str:
    """The machine's own calendar day: "once a day" means the user's day."""
    return datetime.now(timezone.utc).astimezone().date().isoformat()


def _todays_stamp() -> dict | None:
    try:
        stamp = json.loads(_stamp_path().read_text(encoding="utf-8"))
        return stamp if isinstance(stamp, dict) and stamp.get("checked") == _today() else None
    except Exception:
        return None


def _write_stamp(latest: dict | None) -> None:
    """Record today's answer -- a failed one too, so an offline machine asks
    once a day rather than once a build."""
    payload = {"checked": _today(), "latest": None, "url": ""}
    if latest:
        payload.update(latest=latest["tag"].lstrip("v"), url=latest["url"])
    try:
        path = _stamp_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


# ── the build end ─────────────────────────────────────────────────────────

def start() -> tuple[threading.Thread, list] | None:
    """Begin the check in the background; None when there is nothing to do.
    The handle is the thread and the one-slot list its answer lands in."""
    if not enabled() or _todays_stamp():
        return None
    found: list = [None]

    def probe():
        releases = fetch_releases(FEED_URL)
        found[0] = releases[0] if releases else None
        _write_stamp(found[0])

    thread = threading.Thread(target=probe, name="trellum-update-check", daemon=True)
    thread.start()
    return thread, found


def finish(handle: tuple[threading.Thread, list] | None) -> None:
    """Print the hint if the probe came back with a newer release. Never waits:
    a probe still running when the build ends is discarded, stamp unwritten,
    and simply runs again next build."""
    if handle is None or handle[0].is_alive():
        return
    latest = handle[1][0]
    if latest and is_newer(latest["tag"]):
        print(f"\nHint: trellum {latest['tag'].lstrip('v')} is available "
              f"(this is {__version__}).\n"
              f"      Upgrade: {UPGRADE}    Notes: {latest['url']}", flush=True)


# ── doctor ────────────────────────────────────────────────────────────────

def doctor_detail() -> str:
    """The suffix for doctor's version row; "" whenever there is nothing to say."""
    if not enabled():
        return ""
    handle = start()                         # None: already asked today
    if handle:
        thread, found = handle
        # ponytail: the socket timeout does not bound name lookups, so this
        # join is what caps doctor's wait; a probe still running is dropped.
        thread.join(TIMEOUT)
        if thread.is_alive():
            return ""
        latest = found[0]["tag"].lstrip("v") if found[0] else ""
    else:
        latest = str((_todays_stamp() or {}).get("latest") or "")
    if is_newer(latest):
        return f" -- {latest} is available ({UPGRADE})"
    if parse_version(latest) == parse_version(__version__):
        return " (latest)"
    return ""
