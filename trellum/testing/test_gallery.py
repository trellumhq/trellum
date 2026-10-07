"""The index page for a directory of built reports.

Most of these assert things that fail silently in a browser: a card linking to
a report that isn't there, a page that ignores the default theme, or an index
built from a hand-maintained list that has drifted from what was actually
built.
"""

import json

import pytest

from trellum import __version__
from trellum.licensing import OUTPUT_LICENSE
from trellum.reporting import gallery
from trellum.themes import DEFAULT_THEME, DEFAULT_THEME_NAME


def _make_report(root, slug, **meta):
    """A minimal built report: a directory with _meta.json and index.html."""
    d = root / slug
    d.mkdir()
    payload = {"slug": slug, "name": slug.replace("-", " ").title()}
    payload.update(meta)
    (d / "_meta.json").write_text(json.dumps(payload), encoding="utf-8")
    (d / "index.html").write_text("<html></html>", encoding="utf-8")
    return d


def test_collects_only_real_reports(tmp_path):
    _make_report(tmp_path, "alpha")
    _make_report(tmp_path, "beta")
    # _vendor is a sibling directory in every portable build and is not a report
    (tmp_path / "_vendor").mkdir()
    (tmp_path / "_vendor" / "chart.js").write_text("", encoding="utf-8")
    # A directory with metadata but no page is a failed build, not a report
    half = tmp_path / "half-built"
    half.mkdir()
    (half / "_meta.json").write_text('{"slug": "half-built"}', encoding="utf-8")

    slugs = [r["_slug"] for r in gallery.collect_reports(tmp_path)]
    assert slugs == ["alpha", "beta"]


def test_broken_metadata_degrades_the_card_rather_than_hiding_the_report(tmp_path):
    """A report that built and can be opened stays reachable.

    Corrupt metadata is exactly when you most want to click into a report, so
    it is listed under its directory name with whatever detail survives -- and
    one bad file must not take the rest of the gallery down with it.
    """
    _make_report(tmp_path, "good")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "_meta.json").write_text("{not json", encoding="utf-8")
    (bad / "index.html").write_text("<html></html>", encoding="utf-8")

    slugs = [r["_slug"] for r in gallery.collect_reports(tmp_path)]
    assert slugs == ["bad", "good"]

    gallery.build_gallery(tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'href="bad/index.html"' in html
    assert 'href="good/index.html"' in html


def test_a_directory_without_a_page_is_not_a_report(tmp_path):
    """index.html is what makes something a report -- it is the link target."""
    _make_report(tmp_path, "real")
    half = tmp_path / "half-built"
    half.mkdir()
    (half / "_meta.json").write_text('{"slug": "half-built"}', encoding="utf-8")

    assert [r["_slug"] for r in gallery.collect_reports(tmp_path)] == ["real"]


def test_failed_builds_are_flagged_but_still_linked(tmp_path):
    _make_report(tmp_path, "ok-one", last_status="success")
    _make_report(tmp_path, "broken", last_status="failed")
    gallery.build_gallery(tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")

    assert "did not build" in html
    assert 'href="broken/index.html"' in html
    # A healthy gallery carries no badge at all.
    assert html.count("did not build") == 1


def test_analysis_card_is_labeled_as_analysis(tmp_path):
    _make_report(tmp_path, "northwind-finding", kind="analysis",
                 category="Product", name="Where Northwind loses buyers")
    _make_report(tmp_path, "cart-funnel", kind="report", category="Product")

    gallery.build_gallery(tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")

    assert "Analysis · Product" in html
    assert "Product</div>" in html


def test_sorted_by_category_then_name(tmp_path):
    _make_report(tmp_path, "z-one", category="Alpha", name="Z One")
    _make_report(tmp_path, "a-one", category="Beta", name="A One")
    _make_report(tmp_path, "m-one", category="Alpha", name="M One")

    assert [r["_slug"] for r in gallery.collect_reports(tmp_path)] == [
        "m-one", "z-one", "a-one"]


def test_every_card_links_to_a_page_that_exists(tmp_path):
    """The failure this prevents is a 404 from your own index page."""
    for slug in ("alpha", "beta", "gamma"):
        _make_report(tmp_path, slug)
    gallery.build_gallery(tmp_path)

    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    for slug in ("alpha", "beta", "gamma"):
        assert f'href="{slug}/index.html"' in html
        assert (tmp_path / slug / "index.html").is_file()


def test_page_uses_the_default_theme(tmp_path):
    """The page must follow the framework default rather than pinning a
    palette of its own, or changing the default silently stops applying here."""
    _make_report(tmp_path, "alpha")
    gallery.build_gallery(tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")

    assert f'data-theme="{DEFAULT_THEME_NAME}"' in html
    assert DEFAULT_THEME.bg_main in html
    assert DEFAULT_THEME.text_main in html


def test_page_is_self_contained(tmp_path):
    """No external CSS, JS, fonts or images: the tree has to survive being
    copied somewhere with no network and no sibling assets.

    Checked by what actually triggers a fetch rather than by searching for
    "http", because the inlined logo carries an SVG namespace declaration --
    xmlns="http://www.w3.org/2000/svg" -- which is an identifier a browser
    never requests.
    """
    _make_report(tmp_path, "alpha")
    gallery.build_gallery(tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")

    assert "<svg" in html, "the logo should be inlined"
    # The phone-preview script is inline; what must never appear is a script
    # that fetches.
    assert "<script src" not in html
    assert "<script>" in html and "phone-overlay" in html

    for attr in ('src="http', "src='http",
                 "url(http", "@import"):
        assert attr not in html, f"unexpected external reference: {attr}"

    # Report links remain relative; the one external link is the exact runtime source.
    import re
    for href in re.findall(r'href="([^"]+)"', html):
        if href.startswith("https://github.com/trellumhq/trellum/tree/"):
            continue
        assert not href.startswith(("http", "//", "/")), f"unexpected link: {href}"


def test_metadata_is_escaped(tmp_path):
    """Report metadata is authored content and ends up in HTML."""
    _make_report(tmp_path, "alpha", name="A & B <script>alert(1)</script>",
                 description="1 < 2 & 3 > 2")
    gallery.build_gallery(tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")

    assert "<script>alert(1)</script>" not in html
    assert "&amp;" in html


def test_empty_directory_still_produces_a_page(tmp_path):
    gallery.build_gallery(tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "No built reports found" in html
    assert (tmp_path / OUTPUT_LICENSE).is_file()


def test_gallery_links_report_runtime_source(tmp_path):
    source = "https://code.example/trellum/tree/exact"
    _make_report(tmp_path, "alpha", framework_source_url=source)
    gallery.build_gallery(tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert f'href="{source}"' in html


def test_gallery_rejects_unsafe_report_source_url(tmp_path):
    _make_report(tmp_path, "alpha", framework_source_url="javascript:alert(1)")
    gallery.build_gallery(tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "javascript:" not in html
    assert f"https://github.com/trellumhq/trellum/tree/v{__version__}" in html


def test_nojekyll_is_opt_in(tmp_path):
    _make_report(tmp_path, "alpha")

    gallery.build_gallery(tmp_path)
    assert not (tmp_path / ".nojekyll").exists()

    gallery.build_gallery(tmp_path, nojekyll=True)
    assert (tmp_path / ".nojekyll").is_file()


def test_rejects_a_path_that_is_not_a_directory(tmp_path):
    f = tmp_path / "not-a-dir"
    f.write_text("", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        gallery.build_gallery(f)


def test_cli_reports_what_it_wrote(tmp_path, capsys):
    _make_report(tmp_path, "alpha")
    _make_report(tmp_path, "beta")

    assert gallery.main([str(tmp_path), "--title", "Demo", "--nojekyll"]) == 0

    out = capsys.readouterr().out
    assert "2 reports" in out
    assert (tmp_path / ".nojekyll").is_file()
    assert "Demo" in (tmp_path / "index.html").read_text(encoding="utf-8")


def test_no_promotional_banner_unless_asked_for(tmp_path):
    """The default page is unchanged, and carries nobody's marketing.

    This file ships under the project licence and is rendered by anyone
    publishing an output tree.
    A banner that appeared without being asked for would put one vendor's
    call to action on every user's page.
    """
    _make_report(tmp_path, "alpha")
    gallery.build_gallery(tmp_path)
    assert 'class="cta"' not in (tmp_path / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "text,url",
    [("Host your own.", ""), ("", "https://example.com/"), ("", "")],
)
def test_a_half_configured_banner_renders_nothing(tmp_path, text, url):
    """Half a banner is worse than none: a sentence with nowhere to go, or a
    bare host with no reason to click it."""
    _make_report(tmp_path, "alpha")
    gallery.build_gallery(tmp_path, cta_text=text, cta_url=url)
    assert 'class="cta"' not in (tmp_path / "index.html").read_text(encoding="utf-8")


def test_banner_shows_its_destination_host(tmp_path):
    """A reader should see where the banner leads before clicking it."""
    _make_report(tmp_path, "alpha")
    gallery.build_gallery(
        tmp_path,
        cta_text="Host your own reports.",
        cta_url="https://example.com/pricing?plan=team",
    )
    body = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'href="https://example.com/pricing?plan=team"' in body
    assert "Host your own reports." in body
    assert "example.com" in body


def test_banner_content_is_escaped(tmp_path):
    """Both halves are caller-supplied and reach the page as markup."""
    _make_report(tmp_path, "alpha")
    gallery.build_gallery(
        tmp_path,
        cta_text='<script>alert(1)</script>',
        cta_url='https://example.com/"><script>alert(2)</script>',
    )
    body = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in body
    assert "<script>alert(2)</script>" not in body


def test_banner_sits_above_the_reports(tmp_path):
    """Below the cards it would be seen by whoever scrolled to the bottom."""
    _make_report(tmp_path, "alpha")
    gallery.build_gallery(
        tmp_path, cta_text="Host your own.", cta_url="https://example.com/"
    )
    body = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert body.index('class="cta"') < body.index('class="card"')


def test_display_priority_orders_the_cards(tmp_path):
    """An author's display.priority leads the sort; unranked reports keep the
    old category-then-name order, after every ranked one."""
    _make_report(tmp_path, "zeta-flagship", display={"priority": 1})
    _make_report(tmp_path, "alpha-unranked")
    _make_report(tmp_path, "midway", display={"priority": 2})

    slugs = [r["_slug"] for r in gallery.collect_reports(tmp_path)]
    assert slugs == ["zeta-flagship", "midway", "alpha-unranked"]


def test_garbage_priority_sorts_as_unranked(tmp_path):
    _make_report(tmp_path, "bad-priority", display={"priority": "soon"})
    _make_report(tmp_path, "ranked", display={"priority": 3})

    slugs = [r["_slug"] for r in gallery.collect_reports(tmp_path)]
    assert slugs == ["ranked", "bad-priority"]


def test_phone_preview_button_and_overlay(tmp_path):
    """Every card gets a phone-width preview button; the overlay (and its
    script) appears exactly once for the whole page."""
    _make_report(tmp_path, "alpha")
    _make_report(tmp_path, "beta")
    gallery.build_gallery(tmp_path)

    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'data-href="alpha/index.html"' in html
    assert 'data-href="beta/index.html"' in html
    assert html.count('class="phone-btn"') == 2
    assert html.count('id="phone-overlay"') == 1
