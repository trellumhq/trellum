"""Markdown → HTML for documentation pages.

Rendering happens per request and is cached on (path, mtime), so editing a
file is picked up without a restart while a warm page costs nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import markdown as md
from django.conf import settings
from django.utils.safestring import mark_safe

H1_RE = re.compile(r"^#\s+(?P<title>.+?)\s*$", re.MULTILINE)

EXTENSIONS = ["fenced_code", "tables", "toc", "attr_list", "sane_lists", "admonition"]


@dataclass(frozen=True)
class Rendered:
    title: str
    html: str
    toc: tuple[dict, ...]


FIGURE_RE = re.compile(r"^\{\{figure:(?P<name>[a-z0-9-]+)\}\}$", re.MULTILINE)
DOC_LINK_RE = re.compile(r"\[(?P<text>[^\]]+)\]\(/docs/(?P<version>[\w.-]+)/(?P<slug>[\w/-]+?)/?\)")
RELATIVE_DOC_LINK_RE = re.compile(
    r"(?P<open>\]\()(?P<target>(?![a-z]+:|/|#|\{\{)[^)#]+\.md)(?P<fragment>#[^)]*)?\)"
)
def agent_prompt() -> str:
    return (
        f"Read {settings.FRAMEWORK_REPO_URL}#readme. In this existing folder, "
        "preserve unrelated files and the existing Git history. Create a Python "
        "3.11+ virtual environment, install "
        "https://github.com/trellumhq/trellum/releases/download/v0.1.0/"
        "trellum-0.1.0-py3-none-any.whl, then run `python -m trellum` for the "
        f"{settings.SITE_BRAND} CLI's own instructions and add the smallest useful "
        "report here. Do not clone another repository or overwrite this one."
    )


def _substitute(text: str, figures_dir: Path | None = None) -> str:
    """Resolve the placeholders docs may use.

    ``{{BRAND}}`` keeps pages brand-agnostic. Repository, demo, and agent-prompt
    placeholders keep shared addresses and instructions in settings.
    ``{{figure:name}}`` pulls in a diagram from
    the version's ``_figures/`` directory — inline rather than an <img>, so the
    artwork inherits the page's theme tokens and recolours in dark mode instead
    of shipping two copies of every diagram.
    """
    text = text.replace("{{BRAND}}", settings.SITE_BRAND)
    text = text.replace("{{AGENT_PROMPT}}", agent_prompt())
    text = text.replace("{{FRAMEWORK_REPO}}", settings.FRAMEWORK_REPO_URL)
    text = text.replace("{{DEMO_URL}}", settings.DEMO_URL)

    if figures_dir is None:
        return text

    def _figure(match: re.Match) -> str:
        source = figures_dir / f"{match.group('name')}.html"
        if not source.is_file():
            # Loud rather than silent: a missing diagram is a broken page, and
            # the test suite fails on any leftover placeholder.
            return match.group(0)
        # Blank lines keep the block out of the markdown paragraph parser.
        return "\n\n" + source.read_text(encoding="utf-8").strip() + "\n\n"

    return FIGURE_RE.sub(_figure, text)


def _rewrite_relative_doc_links(text: str, source: Path) -> str:
    root = Path(settings.DOCS_ROOT).resolve()
    try:
        version = source.resolve().relative_to(root).parts[0]
    except (ValueError, IndexError):
        return text
    version_root = root / version
    if version != "latest":
        text = text.replace("](/docs/latest/", f"](/docs/{version}/")

    def replace(match: re.Match) -> str:
        target = (source.parent / match.group("target")).resolve()
        try:
            slug = target.relative_to(version_root).with_suffix("").as_posix()
        except ValueError:
            return match.group(0)
        return f"](/docs/{version}/{slug}/{match.group('fragment') or ''})"

    return RELATIVE_DOC_LINK_RE.sub(replace, text)


@lru_cache(maxsize=256)
def _render(path_str: str, mtime: float, figures_dir_str: str | None) -> Rendered:
    figures_dir = Path(figures_dir_str) if figures_dir_str else None
    source = Path(path_str)
    text = _substitute(source.read_text(encoding="utf-8"), figures_dir)
    text = _rewrite_relative_doc_links(text, source)
    # The first H1 is the page title; the template renders it in the header, so
    # it is stripped from the body to avoid printing it twice.
    match = H1_RE.search(text)
    title = match.group("title") if match else "Documentation"
    if match:
        text = text[: match.start()] + text[match.end() :]

    converter = md.Markdown(extensions=EXTENSIONS, output_format="html")
    html = converter.convert(text)
    toc = tuple(
        {"id": token["id"], "name": token["name"]}
        for token in getattr(converter, "toc_tokens", [])
    )
    return Rendered(title=title, html=mark_safe(html), toc=toc)


def render(path: Path, figures_dir: Path | None = None) -> Rendered:
    if not getattr(settings, "DOCS_CACHE", True):
        _render.cache_clear()
    return _render(
        str(path),
        path.stat().st_mtime,
        str(figures_dir) if figures_dir else None,
    )


def plain_text(path: Path, limit: int = 400) -> str:
    """Body text with markup stripped — the search index's excerpt source."""
    text = _substitute(path.read_text(encoding="utf-8"))
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"[#*`>_\[\]()|-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]
