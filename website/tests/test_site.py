import re
import shutil
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import pytest
from django.conf import settings

from apps.blog import posts as blog
from apps.docs import markdown, nav


@pytest.mark.parametrize(
    "path", ["/", "/privacy/", "/terms/", "/cookies/", "/tour/", "/docs/latest/", "/blog/"]
)
def test_public_pages_render(client, path):
    assert client.get(path).status_code == 200


def test_static_product_contract(client):
    body = client.get("/").content.decode()
    assert "AGPL-3.0-only" in body
    assert "https://github.com/trellumhq/trellum" in body
    assert 'href="/demo/"' in body
    assert "demo." + "trellum.dev" not in body
    assert "python -m pip install trellum" in body
    assert "who opened it" not in body
    assert "MFA on every tier" not in body
    assert "Review what ships" in body
    assert client.get("/waitlist/").status_code == 404
    assert client.get("/healthz").status_code == 404
    assert settings.MIDDLEWARE == []
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.dummy"


def test_tour_page_has_accessible_self_paced_screenshot_slides(client):
    body = client.get("/tour/").content.decode()
    assert 'data-tour-picker' in body
    assert 'data-tour-prev disabled' in body
    assert 'data-tour-next' in body
    assert 'data-tour-position aria-live="polite">1 of 14' in body
    assert 'data-slide="14"' in body
    assert body.count('class="tour-slide"') == 14
    assert body.count('<h3>What to look for</h3>') == 14
    assert body.count('target="_blank"') == 14
    assert body.count('alt="') >= 14
    assert "JavaScript is off. Scroll through the 14 features below" in body
    assert 'id="slide-portal-repository"' in body
    assert 'alt="Edit the repository URL, branch and reports directory settings."' in body
    assert 'alt="Read the organization-wide rate limit for report live queries."' in body
    assert 'alt="Review the disabled assistant’s provider, model and data-access settings."' in body
    assert 'width="1253" height="705"' in body
    assert 'width="1265" height="712"' in body
    assert 'static/tour.js' in body
    assert "/static/media/portal-tour.mp4" not in body
    assert 'autoplay' not in body
    assert "/docs/latest/install/try-it/" in body
    assert "/demo/" in body
    assert "do not depict an agent session" in body
    assert "No remote Git repository, Buddy provider or email service is configured" in body


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
    for path in ("/", "/privacy/", "/terms/", "/cookies/", "/tour/", "/docs/latest/"):
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


def test_canonical_latest_docs_render_and_links_resolve(client, tmp_path, settings):
    canonical = Path(__file__).resolve().parents[2] / "docs" / "customer"
    shutil.copytree(canonical, tmp_path / "latest")
    settings.DOCS_ROOT = tmp_path
    settings.DOCS_CACHE = False

    pages = nav.pages("latest")
    listed_slugs = {page.slug for page in pages}
    markdown_slugs = {
        path.relative_to(canonical).with_suffix("").as_posix()
        for path in canonical.rglob("*.md")
    }
    assert listed_slugs == markdown_slugs

    class PageLinks(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []
            self.ids = set()

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if "id" in attrs:
                self.ids.add(attrs["id"])
            if tag == "a" and attrs.get("href"):
                self.links.append(attrs["href"])

    rendered = {"/docs/latest/": client.get("/docs/latest/")}
    for page in pages:
        rendered[page.url] = client.get(page.url)

    unresolved = re.compile(
        r"\{\{(?:BRAND|AGENT_PROMPT|FRAMEWORK_REPO|DEMO_URL|figure:[a-z0-9-]+)\}\}"
    )
    parsed = {}
    for route, response in rendered.items():
        assert response.status_code == 200, route
        body = response.content.decode()
        assert not unresolved.search(body), route
        parser = PageLinks()
        parser.feed(body)
        parsed[route] = parser

    origin = "https://trellum.dev"
    for route, parser in parsed.items():
        for href in parser.links:
            target = urlparse(urljoin(f"{origin}{route}", href))
            if (
                target.netloc != "trellum.dev"
                or not target.path.startswith("/docs/latest/")
            ):
                continue
            assert target.path in parsed, f"{route}: missing docs route for {href}"
            if target.fragment:
                anchor = unquote(target.fragment)
                assert anchor in parsed[target.path].ids, (
                    f"{route}: missing #{anchor} in {target.path} (from {href})"
                )


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


def test_public_site_uses_the_console_palette():
    css = (Path(settings.BASE_DIR) / "static" / "site.css").read_text(encoding="utf-8")
    templates = Path(settings.BASE_DIR) / "templates"
    shell = (templates / "_shell.html").read_text(encoding="utf-8")
    landing = (templates / "pages" / "landing.html").read_text(encoding="utf-8")

    for token in (
        "--bg:#F6F7F8",
        "--bg-card:#FFFFFF",
        "--bg-hover:#EEF1F3",
        "--text:#172126",
        "--text2:#475569",
        "--border:#E2E6EA",
        "--bg:#111315",
        "--bg-card:#181B1F",
        "--bg-hover:#23272D",
        "--text:#F3F4F6",
        "--text2:#A7ADB8",
        "--border:#30363D",
        "--accent:#0F766E",
        "--accent-hover:#115E59",
        "--accent-ink:#2DD4BF",
        "--r-sm:8px; --r-md:8px; --r-lg:10px",
    ):
        assert token in css

    assert "linear-gradient" not in css
    assert "background:var(--scope-studio)" not in css
    assert 'class="strip"' not in shell
    assert all(name not in landing for name in ("cs-hair", "cs-duo", "rp-strip", "hb-dot"))


def test_no_hardcoded_colours_in_templates():
    colour = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\s*\(")
    root = Path(settings.BASE_DIR) / "templates"
    offenders = [
        path for path in root.rglob("*.html") if colour.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
