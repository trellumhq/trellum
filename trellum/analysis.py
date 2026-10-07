"""Git-authored Markdown articles rendered through the report component shell."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from trellum.analysis_capture import (
    MAX_IMAGE_BYTES,
    confined_file,
    read_bounded,
    validate_png,
    validate_source,
)
from trellum.assets import load_css
from trellum.components.base import Component
from trellum.report import BaseReport


@dataclass
class Article(Component):
    markup: str
    _component_type = "article"

    def render_html(self, ctx) -> str:
        return f'<article class="fw-article" id="{ctx.next_id()}">{self.markup}</article>'

    @classmethod
    def css(cls) -> str:
        return load_css("components/article.css")


def _plain_text(tokens) -> str:
    return "".join(token.content for token in tokens or []
                   if token.type in ("text", "code_inline", "image"))


def _caption(source: dict) -> str:
    parts = []
    for label, title, identity in (("Report", "report_name", "report_slug"),
                                   ("Component", "component_title", "component_id")):
        value = source.get(title) or source.get(identity)
        if value:
            parts.append(f"{label}: {html.escape(value)}")
    for label, key in (("Captured", "captured_at"), ("Source built", "source_built_at")):
        if source.get(key):
            parts.append(f"{label}: {html.escape(_caption_time(source[key]))}")
    if source["filters"]:
        parts.append(f"Filters: {html.escape(_caption_filters(source['filters']))}")
    if source.get("url"):
        parts.append(f'<a href="{html.escape(source["url"], quote=True)}" '
                     'rel="noopener noreferrer">Source report</a>')
    return '<span class="fw-evidence-caption">' + " · ".join(parts) + "</span>"


def _caption_time(value: str) -> str:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    offset = timestamp.strftime("%z")
    zone = "UTC" if offset == "+0000" else f"UTC{offset[:3]}:{offset[3:]}"
    return f"{timestamp.day} {timestamp:%b %Y, %H:%M} {zone}"


def _filter_value(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))


def _standard_filter(state: dict) -> str:
    column = state["column"].replace("_", " ")
    mode, value, flag = state["mode"], state["value"], state.get("flagValue")
    if mode == "equals":
        text = "all values" if value == "__all__" else f"equals {_filter_value(value)}"
    elif mode == "in" and isinstance(value, list):
        text = "is one of " + ", ".join(_filter_value(item) for item in value) if value else "all values"
    elif mode in ("range", "numrange") and isinstance(value, dict) and set(value) <= {"min", "max"}:
        minimum, maximum = value.get("min"), value.get("max")
        if minimum is not None and maximum is not None:
            text = f"from {_filter_value(minimum)} to {_filter_value(maximum)}"
        elif minimum is not None:
            text = f"from {_filter_value(minimum)}"
        elif maximum is not None:
            text = f"up to {_filter_value(maximum)}"
        else:
            text = "all values"
    elif mode == "flag" and value in ("only", "exclude") and flag is not None:
        text = f"{'only' if value == 'only' else 'excludes'} {_filter_value(flag)}"
        flag = None  # Already represented by the readable operator.
    else:
        text = f"{mode}: {_filter_value(value)}"
    if flag is not None:
        text += f" (flag: {_filter_value(flag)})"
    return f"{column} {text}"


def _caption_filters(filters: dict) -> str:
    labels = []
    for dataset, entries in filters.items():
        if isinstance(entries, dict) and entries:
            states = list(entries.values())
            if all(isinstance(state, dict) and {"column", "mode", "value"} <= set(state)
                   and set(state) <= {"column", "mode", "value", "flagValue"}
                   and isinstance(state["column"], str) and isinstance(state["mode"], str)
                   for state in states):
                labels.append(f"{dataset}: " + "; ".join(_standard_filter(state) for state in states))
                continue
        labels.append(f"{dataset}: {_filter_value(entries)}")
    return "; ".join(labels)


def _link_allowed(url: str) -> bool:
    parts = urlsplit(url)
    return (not any(ord(c) < 32 or c == "\\" for c in url)
            and not url.startswith("//")
            and parts.scheme.lower() in ("", "http", "https", "mailto"))


def _article_markdown(root: Path, output: Path) -> str:
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    parser.validateLink = _link_allowed
    content = confined_file(root, "content.md").read_text(encoding="utf-8")
    tokens = parser.parse(content)
    toc, used, assets = [], set(), {}
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            title = _plain_text(tokens[index + 1].children)
            base = re.sub(r"[^\w-]+", "-", title.lower(), flags=re.UNICODE).strip("-") or "section"
            anchor, suffix = f"article-{base}", 2
            while anchor in used:
                anchor, suffix = f"article-{base}-{suffix}", suffix + 1
            used.add(anchor)
            token.attrSet("id", anchor)
            toc.append(f'<li class="fw-toc-{token.tag}"><a href="#{anchor}">'
                       f'{html.escape(title)}</a></li>')
        _prepare_images(token.children or [], root, assets)
    parser.renderer.rules["image"] = _image_renderer(parser, assets)
    markup = parser.renderer.render(tokens, parser.options, {})
    # Only validated bytes are copied; metadata is also a published artifact.
    for relative, (raw, source, metadata_raw) in assets.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        if source is not None:
            destination.with_suffix(".json").write_bytes(metadata_raw)
    nav = '<nav class="fw-article-toc" aria-label="Contents"><strong>Contents</strong><ol>'
    return (nav + "".join(toc) + "</ol></nav>" if toc else "") + markup


def _prepare_images(tokens, root: Path, assets: dict) -> None:
    for token in tokens:
        if token.type == "image":
            url = token.attrGet("src") or ""
            if urlsplit(url).scheme or url.startswith("//"):
                raise ValueError("Analysis images must be local PNG evidence")
            relative = unquote(url)
            path = confined_file(root, relative)
            if Path(relative).suffix.lower() != ".png":
                raise ValueError("Analysis images must be PNG evidence")
            raw = read_bounded(path, MAX_IMAGE_BYTES)
            validate_png(raw)
            source, metadata_raw = None, b""
            metadata_relative = Path(relative).with_suffix(".json").as_posix()
            if (root / metadata_relative).exists() or (root / metadata_relative).is_symlink():
                metadata = confined_file(root, metadata_relative)
                metadata_raw = read_bounded(metadata, 1024 * 1024)
                source = validate_source(json.loads(metadata_raw))
            # Use URL encoding from the parser, but portable relative paths on disk.
            assets[relative] = (raw, source, metadata_raw)
            token.meta["asset"] = relative
        _prepare_images(token.children or [], root, assets)


def _image_renderer(parser, assets):
    original = parser.renderer.rules["image"]

    def render(tokens, index, options, env):
        token = tokens[index]
        image = original(tokens, index, options, env)
        source = assets[token.meta["asset"]][1]
        caption = _caption(source) if source is not None else ""
        url = html.escape(token.attrGet("src") or "", quote=True)
        # Avoid nesting an anchor when the author already linked the image.
        linked = sum(t.type == "link_open" for t in tokens[:index]) > sum(
            t.type == "link_close" for t in tokens[:index])
        if not linked:
            image = f'<a href="{url}" target="_blank" rel="noopener" title="Enlarge image">{image}</a>'
        return f'<span class="fw-evidence">{image}{caption}</span>'

    return render


class AnalysisReport(BaseReport):
    report_dir: str

    def generate(self, ctx) -> None:
        markup = _article_markdown(Path(self.report_dir), Path(ctx.output_dir))
        author = ctx.config.get("author")
        if author:
            markup = f'<p class="fw-article-author">By {html.escape(author)}</p>' + markup
        title = html.escape(ctx.config.get("name", "Analysis"))
        markup = f'<h1 class="fw-article-title">{title}</h1>' + markup
        ctx.add_section("", [Article(markup)])
        ctx.add_validation_check("analysis-content-safe", "pass",
                                 "Markdown and local PNG provenance validated", component="Article")
