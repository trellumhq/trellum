"""Git-authored Markdown articles rendered through the report component shell."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import yaml
from markdown_it import MarkdownIt

from trellum.analysis_capture import (
    MAX_IMAGE_BYTES,
    confined_file,
    read_bounded,
    safe_name,
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


@dataclass
class Presentation(Component):
    markup: str
    _component_type = "presentation"

    def render_html(self, ctx) -> str:
        return f'<article class="fw-presentation" id="{ctx.next_id()}">{self.markup}</article>'

    @classmethod
    def css(cls) -> str:
        return Article.css() + "\n" + load_css("components/presentation.css")


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
    counters = {"passage": 0, "evidence": 0}
    _assign_review_targets(tokens, "article", used, counters)
    for token in tokens:
        _prepare_images(token.children or [], root, assets)
    parser.renderer.rules["image"] = _image_renderer(parser, assets)
    markup = parser.renderer.render(tokens, parser.options, {})
    _copy_assets(output, assets)
    nav = '<nav class="fw-article-toc" aria-label="Contents"><strong>Contents</strong><ol>'
    return (nav + "".join(toc) + "</ol></nav>" if toc else "") + markup


def _copy_assets(output: Path, assets: dict) -> None:
    # Only validated bytes are copied; metadata is also a published artifact.
    for relative, (raw, source, metadata_raw) in assets.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        if source is not None:
            destination.with_suffix(".json").write_bytes(metadata_raw)


def _prepare_asset(url: str, root: Path, assets: dict) -> str:
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
    assets[relative] = (raw, source, metadata_raw)
    return relative


def _prepare_images(tokens, root: Path, assets: dict) -> None:
    for token in tokens:
        if token.type == "image":
            url = token.attrGet("src") or ""
            # Use URL encoding from the parser, but portable relative paths on disk.
            token.meta["asset"] = _prepare_asset(url, root, assets)
        _prepare_images(token.children or [], root, assets)


def _assign_review_targets(tokens, prefix: str, used: set, counters: dict) -> None:
    """Give Markdown passages and evidence stable, collision-free review IDs."""
    for token in tokens:
        if token.type == "paragraph_open":
            counters["passage"] += 1
            _set_review_target(token, "Passage", f"{prefix}-passage-{counters['passage']}", used)
        elif token.type == "image":
            counters["evidence"] += 1
            _set_review_target(token, "Evidence", f"{prefix}-evidence-{counters['evidence']}", used)
        _assign_review_targets(token.children or [], prefix, used, counters)


def _set_review_target(token, kind: str, base_id: str, used: set) -> None:
    identifier = _reserve_review_id(base_id, used)
    token.attrSet("id", identifier)
    token.attrSet("data-fw-kind", kind)


def _reserve_review_id(base_id: str, used: set) -> str:
    identifier, suffix = base_id, 2
    while identifier in used:
        identifier, suffix = f"{base_id}-{suffix}", suffix + 1
    used.add(identifier)
    return identifier


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


def _presentation_slides(root: Path) -> list[dict]:
    try:
        document = yaml.safe_load(read_bounded(confined_file(root, "slides.yaml"), 1024 * 1024))
    except yaml.YAMLError as exc:
        raise ValueError(f"slides.yaml must be valid YAML: {exc}") from exc
    if (not isinstance(document, dict) or set(document) != {"version", "slides"}
            or type(document.get("version")) is not int or document["version"] != 1):
        raise ValueError("slides.yaml requires version: 1 and slides only")
    slides = document["slides"]
    if not isinstance(slides, list) or not slides:
        raise ValueError("slides.yaml slides must be a non-empty ordered list")
    used = set()
    for index, slide in enumerate(slides, 1):
        label = f"Slide {index}"
        if not isinstance(slide, dict):
            raise ValueError(f"{label} must be a mapping")
        layout = slide.get("layout")
        if layout not in ("title", "text", "image", "image-text"):
            raise ValueError(f"{label} layout must be title, text, image or image-text")
        required = {"id", "layout", "title"}
        if layout in ("text", "image-text"):
            required.add("text")
        if layout in ("image", "image-text"):
            required.update(("image", "alt"))
        allowed = required | ({"text"} if layout == "title" else set())
        if required - set(slide):
            raise ValueError(f"{label} requires {', '.join(sorted(required - set(slide)))}")
        if set(slide) - allowed:
            raise ValueError(f"{label} has unsupported fields: {', '.join(str(k) for k in set(slide) - allowed)}")
        for key in allowed & set(slide):
            if not isinstance(slide[key], str) or not slide[key].strip():
                raise ValueError(f"{label} {key} must be a non-empty string")
        try:
            safe_name(slide["id"])
        except ValueError as exc:
            raise ValueError(f"{label} id: {exc}") from exc
        if slide["id"] in used:
            raise ValueError(f"Duplicate slide id: {slide['id']!r}")
        used.add(slide["id"])
    return slides


def _presentation_markup(root: Path, output: Path) -> str:
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    parser.validateLink = _link_allowed
    assets, sections, links = {}, [], []
    parser.renderer.rules["image"] = _image_renderer(parser, assets)
    slides = _presentation_slides(root)
    used = {f"slide-{slide['id']}" for slide in slides}
    used.update(f"slide-{slide['id']}-title" for slide in slides)
    for slide in slides:
        anchor = f"slide-{slide['id']}"
        title = html.escape(slide["title"])
        text = ""
        counters = {"passage": 0, "evidence": 0}
        if "text" in slide:
            tokens = parser.parse(slide["text"])
            _assign_review_targets(tokens, anchor, used, counters)
            for token in tokens:
                _prepare_images(token.children or [], root, assets)
            text = '<div class="fw-slide-text">' + parser.renderer.render(tokens, parser.options, {}) + '</div>'
        evidence = ""
        if "image" in slide:
            try:
                relative = _prepare_asset(slide["image"], root, assets)
            except (OSError, ValueError) as exc:
                raise ValueError(f"Slide {slide['id']!r} image: {exc}") from exc
            image_url = html.escape(quote(relative, safe="/"), quote=True)
            alt = html.escape(slide["alt"], quote=True)
            source = assets[relative][1]
            caption = _caption(source) if source is not None else ""
            counters["evidence"] = counters.get("evidence", 0) + 1
            evidence_id = f"{anchor}-evidence-{counters['evidence']}"
            evidence_id = _reserve_review_id(evidence_id, used)
            evidence = (f'<figure class="fw-slide-evidence"><a href="{image_url}" target="_blank" '
                        f'rel="noopener" title="Enlarge image"><img id="{html.escape(evidence_id, quote=True)}" '
                        f'data-fw-kind="Evidence" src="{image_url}" alt="{alt}"></a>'
                        + (f'<figcaption>{caption}</figcaption>' if caption else "") + '</figure>')
        sections.append(f'<section class="fw-section fw-slide fw-slide--{slide["layout"]}" id="{anchor}" '
                        f'data-fw-section-title="{html.escape(slide["title"], quote=True)}" '
                        f'aria-labelledby="{anchor}-title"><h2 id="{anchor}-title">{title}</h2>'
                        f'<div class="fw-slide-body">{text}{evidence}</div></section>')
        links.append(f'<li><a href="#{anchor}">{title}</a></li>')
    _copy_assets(output, assets)
    return ('<nav class="fw-presentation-toc" aria-label="Slides"><strong>Slides</strong><ol>'
            + "".join(links) + '</ol></nav>' + "".join(sections))


class AnalysisReport(BaseReport):
    report_dir: str

    def generate(self, ctx) -> None:
        presentation = ctx.config.get("format", "article") == "presentation"
        render = _presentation_markup if presentation else _article_markdown
        markup = render(Path(self.report_dir), Path(ctx.output_dir))
        author = ctx.config.get("author")
        if author:
            markup = f'<p class="fw-article-author">By {html.escape(author)}</p>' + markup
        title = html.escape(ctx.config.get("name", "Analysis"))
        markup = f'<h1 class="fw-article-title">{title}</h1>' + markup
        ctx.add_section("", [(Presentation if presentation else Article)(markup)])
        ctx.add_validation_check("analysis-content-safe", "pass",
                                 "Markdown and local PNG provenance validated",
                                 component="Presentation" if presentation else "Article")
        if presentation:
            ctx.add_validation_check("analysis-slides-valid", "pass",
                                     "Version 1 slides, stable IDs and required fields validated",
                                     component="Presentation")
