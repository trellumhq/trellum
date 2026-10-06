from django.http import Http404
from django.shortcuts import render

from apps.docs import markdown

from . import posts as blog


def _context(**extra):
    # Which top-level nav item the shell highlights.
    return {"nav_active": "blog", **extra}


def index(request):
    """The listing. Titles come from each post's H1, never from the index.

    Rendering a post to read its title looks wasteful and is not: the renderer
    caches on (path, mtime), so the listing warms exactly the pages a reader is
    about to open, and a retitled post cannot disagree with its own card.
    """
    entries = []
    for post in blog.posts():
        source = blog.source_path(post.slug)
        if source is None:
            # An index entry with no file has nothing to link to; skip it
            # rather than publish a card that 404s.
            continue
        entries.append({"post": post, "title": markdown.render(source, blog.figures_dir()).title})
    return render(request, "blog/index.html", _context(entries=entries))


def post(request, slug):
    current = blog.find(slug)
    if current is None:
        raise Http404("no such post")

    source = blog.source_path(slug)
    if source is None:
        raise Http404("post has no source file")

    rendered = markdown.render(source, blog.figures_dir())
    return render(
        request,
        "blog/post.html",
        _context(post=current, title=rendered.title, body=rendered.html),
    )
