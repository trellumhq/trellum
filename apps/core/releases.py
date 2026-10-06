"""The latest trellum releases, for the operator's /system page.

Opt-in (``TRELLUM_UPDATE_CHECK``): the coordinator asks the releases feed once
a day and records the answer as an ``OpsState`` row; the page reads that row
and shows a Releases section only when there has ever been a successful
answer. A fetch that fails changes nothing and logs nothing, so an install
that cannot reach the feed looks exactly like one that never asked.
internal planning ticket #066.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import trellum_portal
from trellum.update_check import FEED_URL, fetch_releases, parse_version

KEY = "update_check"
FEED_HOST = urlsplit(FEED_URL).hostname        # named in /system's footer


def excerpt(notes: str, limit: int = 160) -> str:
    """The first paragraph of the notes that is not a heading, as plain text:
    bullets, links and emphasis stripped, cut at a word near ``limit``."""
    for block in re.split(r"\n\s*\n", notes or ""):
        lines = [ln.strip() for ln in block.splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        if not lines:
            continue
        text = " ".join(re.sub(r"^(?:[-*+]|\d+\.)\s+", "", ln) for ln in lines)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # [label](url) -> label
        text = re.sub(r"\*\*|__|`", "", text)                       # strong, code
        text = re.sub(r"(?<!\w)[*_](?=\w)|(?<=\w)[*_](?!\w)", "", text)  # *em*, not snake_case
        if len(text) > limit:
            text = text[:limit].rsplit(" ", 1)[0] + "…"
        return text
    return ""


def refresh() -> None:
    """Ask the feed and record the answer. On failure nothing is written: the
    last good row stays, or there never was one."""
    releases = fetch_releases(FEED_URL, timeout=5.0)
    if not releases:
        return
    from apps.core.models import OpsState

    OpsState.record(
        KEY,
        latest=releases[0]["tag"].lstrip("v"),
        releases=[
            {"tag": r["tag"], "name": r["name"], "date": r["published_at"][:10],
             "excerpt": excerpt(r["notes"]), "url": r["url"]}
            for r in releases[:3]           # the newest three: a pace, not a changelog
        ],
    )


def current() -> dict | None:
    """The recorded answer shaped for the template, or None when there has
    never been one -- which is what makes the section absent, not empty."""
    from apps.core.models import OpsState

    row = OpsState.objects.filter(key=KEY, ok=True).first()
    if row is None or not row.payload.get("latest"):
        return None
    mine = trellum_portal.__version__
    theirs, ours = parse_version(row.payload["latest"]), parse_version(mine)
    return {
        "latest": row.payload["latest"],
        "current": mine,
        # "unknown": this instance is ahead of the feed (a dev build) or a side
        # does not parse -- neither "up to date" nor "behind" would be honest.
        "state": ("behind" if theirs and ours and theirs > ours
                  else "latest" if theirs and theirs == ours else "unknown"),
        "releases": [{**r, "this": r["tag"].lstrip("v") == mine}
                     for r in row.payload.get("releases") or []],
        "checked_at": row.ran_at,
        "feed_host": FEED_HOST,
        "all_url": "https://github.com/trellumhq/trellum/releases",
    }
