"""The shipped share widget source, specifically what "Copy embed code" copies.

Text assertions on static/report_share.js rather than browser tests — the file
is served verbatim from /api/reports/share-widget.js into every built report
page, and the embed snippet is a string it builds by hand. An embedded report
hides its own scrollbars and reports its height with trellum:height, so a
snippet that lost the listener would silently go back to clipping tall reports
at min-height. Same shape as apps/buddy/tests/test_widget_js.py.
"""
import shutil
import subprocess

import pytest
from django.conf import settings

SHARE_JS = settings.BASE_DIR / "static" / "report_share.js"


@pytest.fixture(scope="module")
def source() -> str:
    return SHARE_JS.read_text(encoding="utf-8")


class TestEmbedSnippet:
    def test_button_copies_the_snippet_builder_output(self, source):
        assert "data-embed-copy" in source
        assert "function embedSnippet(url, title)" in source
        assert "embedSnippet(embedBtn.getAttribute('data-embed-copy')" in source

    def test_iframe_attributes_kept(self, source):
        assert "style=\"width:100%;border:0;min-height:480px\"" in source
        assert 'loading="lazy"' in source
        assert "esc(url)" in source and "esc(title)" in source

    def test_snippet_carries_a_height_listener(self, source):
        assert "<script>" in source and "<\\/script>" in source
        assert 'window.addEventListener("message"' in source
        assert '"trellum:height"' in source
        assert 'f.style.height = e.data.height + "px"' in source

    def test_snippet_validates_source_and_origin(self, source):
        assert "var portal = new URL(f.src).origin;" in source
        assert "e.source !== f.contentWindow || e.origin !== portal" in source

    def test_snippet_finds_its_own_iframe_not_a_shared_id(self, source):
        # Two embeds on one page must size independently, so the listener
        # walks back from its own <script> instead of looking up an id.
        assert "document.currentScript.previousElementSibling" in source


class TestEmbedAppearanceControls:
    """The theme picker and "Hide the filter bar" box (internal planning ticket #12), shown only
    once "Embed in another site" is ticked -- both are meaningless on a plain
    share link, so a stray control there would offer a promise the serve does
    not keep."""

    def test_controls_live_in_the_embed_only_field(self, source):
        assert 'id="rswEmbedAppearanceField" hidden' in source
        assert "document.getElementById('rswEmbedAppearanceField').hidden = !on;" in source

    def test_theme_options_come_from_the_pages_own_registry(self, source):
        # window._themes is the framework runtime's own registry, so no new
        # endpoint and no theme the report has no CSS for.
        assert "function themeOptionsHtml()" in source
        assert "Object.keys(window._themes || {})" in source
        assert "<option value=\"\">Studio default</option>" in source

    def test_both_values_are_sent_on_create(self, source):
        assert "body.embed_theme = document.getElementById('rswEmbedTheme').value;" in source
        assert (
            "body.embed_hide_filters = document.getElementById('rswEmbedHideFilters').checked;"
            in source
        )

    def test_hide_filters_checkbox_present(self, source):
        assert 'id="rswEmbedHideFilters"' in source
        assert "Hide the filter bar" in source


class TestPreview:
    """The Preview control on an existing embed link's row: the link's real
    URL in a small frame inside the drawer."""

    def test_button_only_on_embed_rows(self, source):
        assert 'data-preview="' in source
        # Sits in the same embed-only branch as "Copy embed code".
        embed_branch = source.split("if (link.embed) {", 1)[1].split("}", 1)[0]
        assert "data-preview" in embed_branch

    def test_frame_is_built_on_click_not_on_render(self, source):
        # The empty container renders; the <iframe> string only exists inside
        # the click handler, so a drawer with ten embed links loads nothing.
        assert "'<div class=\"rsw-preview\" hidden></div>'" in source
        assert "box.innerHTML = '<iframe src=\"'" in source
        assert "previewBtn.getAttribute('data-preview')" in source

    def test_second_click_collapses_and_empties(self, source):
        assert "var opening = box.hidden;" in source
        assert "boxes[i].innerHTML = '';" in source

    def test_only_one_preview_open_at_a_time(self, source):
        assert "function closePreviews()" in source
        assert "document.querySelectorAll('.rsw-preview')" in source
        # Every open frame is closed before the clicked one opens.
        assert "closePreviews();\n                    if (opening) {" in source

    def test_frame_is_small_and_scrolls_internally(self, source):
        assert ".rsw-preview iframe{display:block;width:100%;height:260px" in source


class TestSyntax:
    @pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
    def test_node_check_passes(self):
        result = subprocess.run(
            ["node", "--check", str(SHARE_JS)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_bundle_contains_the_current_source(self, client, db):
        body = client.get("/api/reports/share-widget.js").content.decode()
        assert "function embedSnippet(" in body
        assert '"trellum:height"' in body
