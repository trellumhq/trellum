"""Portable output: reports that survive being published under a subpath.

The bug these guard against is silent. Default output references vendor assets
at ``/_vendor/...``, an absolute path resolved against the domain root. Publish
that output to ``https://host/some/project/`` and every one of those requests
misses, so the page loads, the layout is fine, and no chart appears. There is no
error anywhere -- not in the build, not in the server log.
"""

import json
import re
from pathlib import Path

import pytest

from trellum.rendering import cdn


@pytest.fixture(autouse=True)
def restore_base():
    """Vendor base is module state; never let a test leak it into the next."""
    original = cdn.get_vendor_url_base()
    yield
    cdn.set_vendor_url_base(original)


def test_default_base_is_the_absolute_route():
    assert cdn.get_vendor_url_base() == "/_vendor/"
    assert cdn.get_cdn_url("chartjs").startswith("/_vendor/")


def test_portable_base_emits_relative_urls():
    cdn.set_vendor_url_base("../_vendor/")
    assert cdn.get_cdn_url("chartjs") == "../_vendor/chart.umd.min.js"


def test_setter_tolerates_a_missing_trailing_slash():
    """Every call site concatenates a bare filename, so this must not produce
    '../_vendorchart.umd.min.js'."""
    cdn.set_vendor_url_base("../_vendor")
    assert cdn.get_cdn_url("chartjs") == "../_vendor/chart.umd.min.js"


def test_tags_carry_no_root_absolute_paths_in_portable_mode():
    """The real assertion: nothing in the emitted HTML starts at the domain root.

    A single missed '/_vendor/' is enough to break a published gallery, so this
    checks every generated tag rather than a sample.
    """
    cdn.set_vendor_url_base("../_vendor/")
    tags = cdn.build_cdn_tags(set())

    assert tags, "expected the always-included libraries to emit tags"
    for url in re.findall(r'(?:src|href)="([^"]+)"', tags):
        assert not url.startswith("/"), f"root-absolute URL survived: {url}"
        assert url.startswith("../_vendor/"), f"unexpected URL: {url}"


def test_default_mode_still_emits_absolute_paths():
    """The existing contract is unchanged for anyone not asking for portable."""
    tags = cdn.build_cdn_tags(set())
    for url in re.findall(r'(?:src|href)="([^"]+)"', tags):
        assert url.startswith("/_vendor/"), f"default mode changed: {url}"


def test_bundled_font_css_uses_relative_urls():
    """inter.css is loaded BY the browser, so the framework's URL base cannot
    reach it -- its own font references have to be relative to the stylesheet or
    the fonts 404 under any prefix. This one is invisible in the HTML."""
    css = (cdn._VENDOR_DIR / "inter.css").read_text(encoding="utf-8")
    urls = re.findall(r"url\('([^']+)'\)", css)

    assert urls, "expected @font-face src urls in inter.css"
    for url in urls:
        assert not url.startswith("/"), f"absolute font URL in inter.css: {url}"
        assert url.startswith("fonts/"), f"unexpected font URL: {url}"


def test_demo_runtime_assets_follow_the_loaded_vendor_base():
    source = (Path(cdn.__file__).resolve().parents[1] / "demo" / "reports" /
              "player-overview" / "custom_sections.py").read_text(encoding="utf-8")

    assert "fetch('/_vendor/" not in source
    assert "new URL('countries-110m.json'," in source
    assert 'script[src$="topojson-client.min.js"]' in source


def test_copy_vendor_tree_lands_the_files_reports_ask_for(tmp_path):
    """Copying is the other half: the URLs can be perfect and still 404 if the
    files only exist inside the installed package."""
    dest = cdn.copy_vendor_tree(tmp_path)

    assert dest == tmp_path / "_vendor"
    assert (dest / "chart.umd.min.js").is_file()
    assert (dest / "inter.css").is_file()
    # The fonts are a directory deeper, and are exactly what the relative URLs
    # in inter.css resolve to.
    assert (dest / "fonts").is_dir()
    assert any((dest / "fonts").glob("inter-*.ttf"))


def test_copy_vendor_tree_is_repeatable(tmp_path):
    """Rebuilds overwrite an existing copy rather than failing."""
    cdn.copy_vendor_tree(tmp_path)
    dest = cdn.copy_vendor_tree(tmp_path)
    assert (dest / "chart.umd.min.js").is_file()


def test_vendor_manifest_only_claims_files_that_ship():
    manifest = json.loads((cdn._VENDOR_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    for name, entry in manifest["libraries"].items():
        files = entry.get("files") or [entry.get("file")]
        assert all(filename and (cdn._VENDOR_DIR / filename).is_file() for filename in files), name
