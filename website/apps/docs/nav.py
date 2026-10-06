"""Documentation tree: versions, sidebar navigation, page lookup.

Content is plain markdown under content/docs/<version>/, ordered by that
version's nav.yml. Publishing a new documentation version is a directory copy —
no build step, no separate toolchain, and the version dropdown picks it up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from django.conf import settings

# Slugs address files on disk, so they are validated rather than sanitised:
# lowercase segments, no dots, no traversal.
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*$")
VERSION_RE = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


@dataclass(frozen=True)
class Page:
    title: str
    slug: str
    section: str
    version: str

    @property
    def url(self) -> str:
        return f"/docs/{self.version}/{self.slug}/"


@dataclass(frozen=True)
class Section:
    title: str
    summary: str
    pages: tuple[Page, ...]


def versions() -> list[str]:
    """Available documentation versions, newest first, 'latest' always first."""
    root = Path(settings.DOCS_ROOT)
    if not root.is_dir():
        return []

    def key(name: str) -> tuple[int, ...]:
        match = VERSION_RE.fullmatch(name)
        return tuple(map(int, match.groups())) if match else (-1,)

    names = sorted(
        (
            p.name
            for p in root.iterdir()
            if p.is_dir() and (p.name == "latest" or VERSION_RE.fullmatch(p.name))
        ),
        key=key,
        reverse=True,
    )
    if "latest" in names:
        names.remove("latest")
        names.insert(0, "latest")
    return names


def resolve_version(version: str | None) -> str | None:
    version = version or settings.DOCS_DEFAULT_VERSION
    return version if version in versions() else None


def _nav_path(version: str) -> Path:
    return Path(settings.DOCS_ROOT) / version / "nav.yml"


def version_root(version: str) -> Path:
    return Path(settings.DOCS_ROOT) / version


@lru_cache(maxsize=32)
def _load(version: str, path_str: str, mtime: float) -> tuple[Section, ...]:
    raw = yaml.safe_load(Path(path_str).read_text(encoding="utf-8")) or []
    sections = []
    for entry in raw:
        pages = tuple(
            Page(
                title=page["title"],
                slug=page["slug"],
                section=entry["section"],
                version=version,
            )
            for page in entry.get("pages", [])
        )
        sections.append(
            Section(
                title=entry["section"],
                summary=entry.get("summary", ""),
                pages=pages,
            )
        )
    return tuple(sections)


def sections(version: str) -> tuple[Section, ...]:
    path = _nav_path(version)
    if not path.is_file():
        return ()
    if not getattr(settings, "DOCS_CACHE", True):
        _load.cache_clear()

    return _load(version, str(path), path.stat().st_mtime)


def pages(version: str) -> list[Page]:
    """Every visible page in sidebar order — the sequence prev/next walks."""
    return [
        page
        for section in sections(version)
        for page in section.pages
    ]


def find(version: str, slug: str) -> tuple[Page | None, Page | None, Page | None]:
    """Return (page, previous, next) for a slug, or (None, None, None)."""
    ordered = pages(version)
    for index, page in enumerate(ordered):
        if page.slug == slug:
            return (
                page,
                ordered[index - 1] if index > 0 else None,
                ordered[index + 1] if index + 1 < len(ordered) else None,
            )
    return None, None, None


def figures_dir(version: str) -> Path:
    """Where a version keeps its diagrams. Copied along with the version."""
    return Path(settings.DOCS_ROOT) / version / "_figures"


def source_path(version: str, slug: str) -> Path | None:
    """Filesystem path for a slug, or None if it escapes the version root."""
    if not SLUG_RE.match(slug):
        return None
    root = (Path(settings.DOCS_ROOT) / version).resolve()
    candidate = (root / f"{slug}.md").resolve()
    if root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None
