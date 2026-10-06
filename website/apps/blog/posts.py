"""The blog's index: which posts exist and in what order.

Posts are plain markdown in content/blog/, listed newest first in index.yml —
the same arrangement as the documentation, for the same reason: writing is
content, not data, so it belongs in the repository where it is reviewed like
any other change rather than in a database this site does not have.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from django.conf import settings

# A slug is a filename and a URL segment, so it is validated rather than
# sanitised. One segment only: posts are a flat list, unlike docs sections.
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Post:
    slug: str
    date: dt.date
    author: str
    summary: str

    @property
    def url(self) -> str:
        return f"/blog/{self.slug}/"


# There is deliberately no title here: the title is the markdown's first H1, so
# a post has exactly one of them and the index cannot drift from the page.


def _index_path() -> Path:
    return Path(settings.BLOG_ROOT) / "index.yml"


@lru_cache(maxsize=4)
def _load(mtime: float) -> tuple[Post, ...]:
    raw = yaml.safe_load(_index_path().read_text(encoding="utf-8")) or []
    loaded = []
    for entry in raw:
        slug = entry["slug"]
        if not SLUG_RE.match(slug):
            raise ValueError(f"blog index: {slug!r} is not a valid post slug")
        date = entry["date"]
        # YAML parses an ISO date natively; anything else is a quoted string
        # that would sort and format as text, so it is rejected at load time
        # rather than rendering as a surprise halfway down the listing.
        if not isinstance(date, dt.date):
            raise ValueError(f"blog index: {slug!r} has no ISO date (got {date!r})")
        loaded.append(
            Post(
                slug=slug,
                date=date,
                author=entry.get("author", ""),
                summary=entry.get("summary", ""),
            )
        )
    return tuple(loaded)


def posts() -> tuple[Post, ...]:
    """Published posts, newest first."""
    path = _index_path()
    if not path.is_file():
        return ()
    if not getattr(settings, "DOCS_CACHE", True):
        _load.cache_clear()

    return _load(path.stat().st_mtime)


def find(slug: str) -> Post | None:
    """The post for a slug, or None."""
    for post in posts():
        if post.slug == slug:
            return post
    return None


def figures_dir() -> Path:
    """Where posts keep their diagrams, mirroring the documentation's layout."""
    return Path(settings.BLOG_ROOT) / "_figures"


def source_path(slug: str) -> Path | None:
    """Filesystem path for a slug, or None if it escapes the blog root."""
    if not SLUG_RE.match(slug):
        return None
    root = Path(settings.BLOG_ROOT).resolve()
    candidate = (root / f"{slug}.md").resolve()
    if root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None
