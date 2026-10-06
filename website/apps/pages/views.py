import datetime as dt
from xml.sax.saxutils import escape

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render


def landing(request):
    return render(request, "pages/landing.html")


def privacy(request):
    return render(request, "pages/privacy.html")


def terms(request):
    return render(request, "pages/terms.html")


def cookies(request):
    return render(request, "pages/cookies.html")


def robots(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /demo/",
        f"Sitemap: {settings.SITE_BASE_URL}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


def _lastmod(path) -> str | None:
    """A page's last change, as the crawler sees it: the source file's mtime.

    Markdown on disk is the whole publishing system here, so the file's
    timestamp is the honest answer — there is no separate "published" field to
    keep in sync with it.
    """
    if path is None or not path.is_file():
        return None
    return dt.date.fromtimestamp(path.stat().st_mtime).isoformat()


def _url_entry(location: str, lastmod: str | None) -> str:
    parts = [f"<loc>{escape(settings.SITE_BASE_URL + location)}</loc>"]
    if lastmod:
        parts.append(f"<lastmod>{lastmod}</lastmod>")
    return "  <url>" + "".join(parts) + "</url>"


def sitemap(request):
    """Every page in the finite static site."""
    from apps.blog import posts as blog
    from apps.docs import nav

    entries = [
        _url_entry("/", None),
        _url_entry("/privacy/", None),
        _url_entry("/terms/", None),
        _url_entry("/cookies/", None),
    ]

    for page in nav.pages(settings.DOCS_DEFAULT_VERSION):
        entries.append(
            _url_entry(page.url, _lastmod(nav.source_path(page.version, page.slug)))
        )

    visible_posts = blog.posts()
    if visible_posts:
        entries.append(_url_entry("/blog/", None))
        for post in visible_posts:
            entries.append(_url_entry(post.url, _lastmod(blog.source_path(post.slug))))

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>\n"
    )
    return HttpResponse(body, content_type="application/xml")


def not_found(request, exception):
    return render(request, "404.html", status=404)
