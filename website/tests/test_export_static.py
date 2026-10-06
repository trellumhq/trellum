import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.docs import markdown, nav
from apps.pages.management.commands.export_static import Command
from trellum_website.settings import build

DEMO_FIXTURE = Path(__file__).parent / "fixtures" / "demo-gallery"


def export(path: Path, base_url="https://trellum.dev", demo_artifact=DEMO_FIXTURE):
    call_command(
        "export_static",
        output=path,
        base_url=base_url,
        demo_artifact=demo_artifact,
        verbosity=0,
    )


def test_build_settings_use_source_static_storage():
    assert build.STORAGES["staticfiles"]["BACKEND"] == (
        "django.contrib.staticfiles.storage.StaticFilesStorage"
    )


def test_export_writes_the_complete_finite_artifact(tmp_path):
    output = tmp_path / "site"
    export(output)
    routes = {relative for _, relative in Command._routes(nav.versions())}
    actual = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    assert routes <= actual
    tour_images = {
        f"static/media/tour/{name}.jpg"
        for name in (
            "portal-overview",
            "portal-datasources",
            "portal-player-report",
            "portal-metrics",
            "portal-repository",
            "portal-annotations",
            "portal-experiments",
            "portal-operations",
            "portal-sharing",
            "portal-analytics",
            "portal-live-queries",
            "portal-alerts",
            "portal-assistant",
            "store-health-dark",
        )
    }
    assert {
        "static/site.css",
        "static/site.js",
        "static/tour.js",
        "static/trellum-lattice.svg",
        "tour/index.html",
    } | tour_images <= actual
    assert {"demo/index.html", "demo/report/index.html", "demo/_vendor/chart.umd.min.js"} <= actual
    assert "demo/CNAME" not in actual
    assert "demo/robots.txt" not in actual
    assert (output / ".nojekyll").is_file()
    assert (output / "CNAME").read_text(encoding="utf-8") == "trellum.dev\n"
    redirect = (output / "docs/index.html").read_text(encoding="utf-8")
    assert 'http-equiv="refresh"' in redirect
    assert f'href="/docs/{nav.versions()[0]}/"' in redirect
    assert "That page isn't here" in (output / "404.html").read_text(encoding="utf-8")
    assert '<link rel="canonical" href="https://trellum.dev/">' in (output / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://trellum.dev",
        "https://user@trellum.dev",
        "https://trellum.dev/path",
        "https://trellum.dev?q=1",
        "https://trellum.dev\n.example",
    ],
)
def test_export_rejects_non_origin_base_urls(tmp_path, base_url):
    with pytest.raises(CommandError, match="base URL"):
        export(tmp_path / "site", base_url)


def test_export_requires_new_or_empty_destination(tmp_path):
    output = tmp_path / "site"
    output.mkdir()
    (output / "keep").write_text("user data", encoding="utf-8")
    with pytest.raises(CommandError, match="new or empty"):
        export(output)
    assert (output / "keep").read_text(encoding="utf-8") == "user data"


def test_export_rejects_missing_or_raw_demo_gallery(tmp_path):
    with pytest.raises(CommandError, match="artifact is missing"):
        export(tmp_path / "missing-site", demo_artifact=tmp_path / "missing-gallery")

    gallery = tmp_path / "gallery"
    shutil.copytree(DEMO_FIXTURE, gallery)
    (gallery / "warehouse.sqlite").write_bytes(b"not public")
    with pytest.raises(CommandError, match="raw warehouse file"):
        export(tmp_path / "raw-site", demo_artifact=gallery)


def test_export_refuses_source_and_ancestor_destinations():
    with pytest.raises(CommandError, match="protected source"):
        export(Path(settings.BASE_DIR))
    with pytest.raises(CommandError, match="protected source"):
        export(Path(settings.BASE_DIR).parent)
    assert not Command._protected_output(
        (Path(settings.BASE_DIR) / "dist").resolve(), Path(settings.BASE_DIR).resolve()
    )


def test_export_fails_for_missing_corpus_nav_and_page(tmp_path, settings):
    missing = tmp_path / "missing"
    settings.DOCS_ROOT = missing
    with pytest.raises(CommandError, match="corpus is missing"):
        export(tmp_path / "one")

    version = missing / "latest"
    version.mkdir(parents=True)
    with pytest.raises(CommandError, match="navigation is missing"):
        export(tmp_path / "two")

    (version / "nav.yml").write_text(
        "- section: Test\n  pages:\n    - title: Missing\n      slug: missing\n",
        encoding="utf-8",
    )
    with pytest.raises(CommandError, match="missing page"):
        export(tmp_path / "three")


def test_export_fails_for_missing_figure(tmp_path, settings):
    docs = tmp_path / "docs"
    shutil.copytree(settings.DOCS_ROOT, docs)
    name = next(
        match.group("name")
        for source in (docs / "latest").rglob("*.md")
        for match in markdown.FIGURE_RE.finditer(source.read_text(encoding="utf-8"))
    )
    figure = docs / "latest" / "_figures" / f"{name}.html"
    figure.unlink()
    settings.DOCS_ROOT = docs
    with pytest.raises(CommandError, match="unresolved template placeholder"):
        export(tmp_path / "site")


def test_export_fails_for_placeholder_and_duplicate_output(tmp_path):
    with pytest.raises(CommandError, match="unresolved template placeholder"):
        Command._write(tmp_path / "bad.html", b"<p>{{MISSING}}</p>")
    Command._write(tmp_path / "example.html", b"<pre><code>{{ example }}</code></pre>")
    Command._write(tmp_path / "search.json", b'{"text": "{{ example }}"}')

    routes = Command._routes(nav.versions())
    with patch.object(Command, "_routes", return_value=routes + [("/", routes[0][1])]):
        with pytest.raises(CommandError, match="duplicate output path"):
            export(tmp_path / "site")


def test_export_fails_for_broken_local_html_and_css_references(tmp_path):
    output = tmp_path / "site"
    export(output)
    index = output / "index.html"
    original = index.read_text(encoding="utf-8")
    index.write_text(original + '<a href="/missing/">broken</a>', encoding="utf-8")
    routes = {relative for _, relative in Command._routes(nav.versions())}
    imported = {
        path.relative_to(output).as_posix()
        for path in (output / "demo").rglob("*")
        if path.is_file()
    }
    with pytest.raises(CommandError, match="broken local reference"):
        Command._validate_artifact(output, routes, "trellum.dev", imported)

    index.write_text(original, encoding="utf-8")
    css = output / "static/site.css"
    css.write_text(
        css.read_text(encoding="utf-8") + "\n.x{background:url('missing.svg')}",
        encoding="utf-8",
    )
    with pytest.raises(CommandError, match="broken local reference"):
        Command._validate_artifact(output, routes, "trellum.dev", imported)
