from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render

from . import markdown, nav


def _context(version: str, **extra):
    return {
        "version": version,
        "versions": nav.versions(),
        "sections": nav.sections(version),
        # Which top-level nav item the shell highlights.
        "nav_active": "docs",
        "unreleased": (nav.version_root(version) / ".unreleased").is_file(),
        **extra,
    }


def root(request):
    """/docs/ → the default version's index."""
    version = nav.resolve_version(None)
    if version is None:
        raise Http404("no documentation published")
    return redirect(f"/docs/{version}/", permanent=False)


def index(request, version):
    version = nav.resolve_version(version)
    if version is None:
        raise Http404("unknown documentation version")
    return render(request, "docs/index.html", _context(version))


def page(request, version, slug):
    version = nav.resolve_version(version)
    if version is None:
        raise Http404("unknown documentation version")

    source = nav.source_path(version, slug)
    if source is None:
        raise Http404("no such documentation page")

    current, previous, following = nav.find(version, slug)
    rendered = markdown.render(source, nav.figures_dir(version))
    return render(
        request,
        "docs/page.html",
        _context(
            version,
            page=current,
            slug=slug,
            title=rendered.title,
            body=rendered.html,
            toc=rendered.toc,
            previous=previous,
            next=following,
        ),
    )


def search_index(request, version):
    """Small JSON index; the sidebar box filters it client-side.

    The corpus is a few dozen pages, so shipping it whole beats standing up a
    search service or a second build toolchain.
    """
    version = nav.resolve_version(version)
    if version is None:
        raise Http404("unknown documentation version")

    entries = []
    for item in nav.pages(version):
        source = nav.source_path(version, item.slug)
        if source is None:
            continue
        entries.append(
            {
                "title": item.title,
                "section": item.section,
                "url": item.url,
                "text": markdown.plain_text(source),
            }
        )
    return JsonResponse({"version": version, "pages": entries})
