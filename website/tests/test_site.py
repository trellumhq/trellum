import re
import shutil
from html import escape
from pathlib import Path

import pytest
from django.conf import settings

from apps.blog import posts as blog
from apps.docs import markdown, nav


@pytest.mark.parametrize(
    "path", ["/", "/privacy/", "/terms/", "/cookies/", "/docs/latest/", "/blog/"]
)
def test_public_pages_render(client, path):
    assert client.get(path).status_code == 200


def test_static_product_contract(client):
    body = client.get("/").content.decode()
    assert "AGPL-3.0-only" in body
    assert "https://github.com/trellumhq/trellum" in body
    assert 'href="/demo/"' in body
    assert "demo." + "trellum.dev" not in body
    assert "pip install trellum" not in body
    assert "who opened it" not in body
    assert "MFA on every tier" not in body
    assert "Review what ships" in body
    assert client.get("/waitlist/").status_code == 404
    assert client.get("/healthz").status_code == 404
    assert settings.MIDDLEWARE == []
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.dummy"


def test_legal_pages_describe_the_static_site(client):
    privacy = " ".join(client.get("/privacy/").content.decode().split())
    terms = " ".join(client.get("/terms/").content.decode().split())
    cookies = client.get("/cookies/").content.decode()
    assert "GitHub's privacy statement" in privacy
    assert "former unsubscribe URL no longer processes requests" in privacy
    assert "old records were deleted automatically" in privacy
    assert "AGPL-3.0-only" in terms
    assert "Third-party and contributor notices retain their own terms" in terms
    assert "under MIT" not in terms
    assert "order form" not in terms
    assert "sets no cookies" in cookies
    assert "pending legal review" not in privacy + terms + cookies


def test_robots_and_sitemap(client):
    robots = client.get("/robots.txt").content.decode()
    sitemap = client.get("/sitemap.xml").content.decode()
    assert "Sitemap: https://trellum.dev/sitemap.xml" in robots
    assert "Disallow: /demo/" in robots
    for path in ("/", "/privacy/", "/terms/", "/cookies/", "/docs/latest/"):
        assert f"https://trellum.dev{path}" in sitemap


def test_docs_root_redirects_to_default_version(client):
    response = client.get("/docs/")
    assert response.status_code == 302
    assert response["Location"] == "/docs/latest/"


def test_unreleased_docs_show_their_provenance(client):
    marker = nav.version_root("latest") / ".unreleased"
    body = client.get("/docs/latest/").content.decode()
    assert ("Development documentation" in body) == marker.is_file()


def test_versions_are_stable_semver_and_sorted_numerically(tmp_path, settings):
    for name in ("latest", "v0.9.0", "v0.10.0", "v0.10.0-rc1", "draft"):
        (tmp_path / name).mkdir()
    settings.DOCS_ROOT = tmp_path
    assert nav.versions() == ["latest", "v0.10.0", "v0.9.0"]


@pytest.mark.parametrize("page", nav.pages("latest"), ids=lambda page: page.slug)
def test_every_nav_page_renders(client, page):
    response = client.get(page.url)
    assert response.status_code == 200
    assert escape(page.title) in response.content.decode()


def test_nav_pages_all_have_source_files():
    for version in nav.versions():
        assert nav.pages(version)
        assert all(nav.source_path(version, page.slug) for page in nav.pages(version))


def test_every_referenced_figure_exists():
    for version in nav.versions():
        figures = nav.figures_dir(version)
        for page in nav.pages(version):
            source = nav.source_path(version, page.slug)
            for match in markdown.FIGURE_RE.finditer(source.read_text(encoding="utf-8")):
                assert (figures / f"{match.group('name')}.html").is_file()


def test_internal_doc_links_resolve():
    known = {
        (page.version, page.slug)
        for version in nav.versions()
        for page in nav.pages(version)
    }
    for version in nav.versions():
        for page in nav.pages(version):
            text = nav.source_path(version, page.slug).read_text(encoding="utf-8")
            for match in markdown.DOC_LINK_RE.finditer(text):
                assert (match.group("version"), match.group("slug")) in known
            rendered = markdown.render(nav.source_path(version, page.slug), nav.figures_dir(version))
            assert not re.search(r'href="[^"]+\.md', rendered.html)
            if version != "latest":
                assert 'href="/docs/latest/' not in rendered.html


def test_relative_doc_link_keeps_the_current_version():
    source = nav.source_path("latest", "operations/data-retention")
    body = markdown.render(source, nav.figures_dir("latest")).html
    assert '/docs/latest/operations/logs-and-monitoring/#the-audit-log' in body


def test_search_index_covers_every_page(client):
    payload = client.get("/docs/latest/search.json").json()
    assert {entry["url"] for entry in payload["pages"]} == {
        page.url for page in nav.pages("latest")
    }


def test_stable_version_routes(client, tmp_path, settings):
    shutil.copytree(nav.version_root("latest"), tmp_path / "v0.3.0")
    settings.DOCS_ROOT = tmp_path
    page = nav.pages("v0.3.0")[0]
    assert client.get("/docs/v0.3.0/").status_code == 200
    assert client.get("/docs/v0.3.0/search.json").json()["version"] == "v0.3.0"
    assert client.get(page.url).status_code == 200


def test_unknown_docs_paths_404(client):
    assert client.get("/docs/nope/").status_code == 404
    assert client.get("/docs/v9.9.9/").status_code == 404
    assert client.get("/docs/latest/not-a-page/").status_code == 404


def test_blog_corpus_renders(client):
    assert blog.posts()
    listing = client.get("/blog/").content.decode()
    for post in blog.posts():
        assert post.url in listing
        body = client.get(post.url).content.decode()
        assert "{{" not in body


def test_blog_feed_builds_without_a_database(client):
    response = client.get("/blog/rss.xml")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/rss+xml")


def test_assets_are_cache_busted(client):
    body = client.get("/").content.decode()
    assert re.search(r'/static/site\.css\?v=\d+', body)
    assert re.search(r'/static/site\.js\?v=\d+', body)


def test_no_hardcoded_colours_in_templates():
    colour = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\s*\(")
    root = Path(settings.BASE_DIR) / "templates"
    offenders = [
        path for path in root.rglob("*.html") if colour.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
