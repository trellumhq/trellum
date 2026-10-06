"""The shipped <trellum-report> source: origin checks, element name, protocol.

These are text assertions on static/report_embed_element.js rather than browser
tests — the file is served verbatim at /api/reports/embed-element.js and runs on
a *customer's* page, so a regression here breaks embeds we cannot see and cannot
redeploy. The two checks that matter most are the ones a host would otherwise
have to write itself and usually gets wrong: that an inbound message is accepted
only from this frame (``e.source``) and only from the src's own origin
(``e.origin``). See internal planning ticket #12.
"""
import shutil
import subprocess

import pytest
from django.conf import settings

ELEMENT_JS = settings.BASE_DIR / "static" / "report_embed_element.js"


@pytest.fixture(scope="module")
def source() -> str:
    return ELEMENT_JS.read_text(encoding="utf-8")


class TestRegistration:
    def test_defines_the_element_name(self, source):
        assert "customElements.define('trellum-report', TrellumReport)" in source

    def test_quiet_on_a_browser_without_custom_elements(self, source):
        # Fail quietly: an old browser gets no report, not a thrown error that
        # takes the host page's own scripts down with it.
        assert "if (!window.customElements || !window.CustomEvent) return;" in source

    def test_redefinition_is_not_an_error(self, source):
        assert "if (!customElements.get('trellum-report'))" in source

    def test_observes_every_documented_attribute(self, source):
        assert "return ['src', 'theme', 'min-height', 'title'];" in source


class TestMessageValidation:
    def test_inbound_messages_are_checked_by_source_and_origin(self, source):
        assert "e.source !== frame.contentWindow || e.origin !== self._origin()" in source

    def test_origin_is_derived_from_src(self, source):
        assert "new URL(this.getAttribute('src'), location.href).origin" in source

    def test_a_missing_or_bad_src_matches_no_origin(self, source):
        # '' rather than location.origin, so a src-less element cannot be
        # driven by same-origin messages from the host page itself.
        assert "catch (err) { return ''; }" in source

    def test_outbound_messages_are_addressed_not_wildcarded(self, source):
        assert "postMessage(msg, origin)" in source
        assert "'*'" not in source


class TestProtocol:
    def test_handles_the_three_message_types(self, source):
        assert "d.type === 'trellum:height'" in source
        assert "d.type === 'trellum:ready'" in source
        assert "{ type: 'trellum:reload' }" in source
        assert "{ type: 'trellum:theme', theme: theme }" in source

    def test_height_sizes_the_frame_and_re_emits_as_a_dom_event(self, source):
        assert "frame.style.height = h + 'px';" in source
        assert "new CustomEvent('trellum:height', { detail: { height: h } })" in source

    def test_ready_re_emits_as_a_dom_event(self, source):
        assert "new CustomEvent('trellum:ready')" in source

    def test_theme_waits_for_ready(self, source):
        # Posting before the frame's listener is attached silently drops it.
        assert "if (this._ready && theme)" in source
        assert "self._pushTheme();" in source

    def test_reload_is_a_public_method(self, source):
        assert "reload() { this._post({ type: 'trellum:reload' }); }" in source


class TestFrame:
    def test_iframe_matches_the_copy_snippet(self, source):
        assert "frame.setAttribute('loading', 'lazy');" in source
        assert "width:100%;border:0" in source
        assert "'Embedded report'" in source  # title fallback, for a11y

    def test_min_height_defaults_to_480(self, source):
        assert "var MIN_HEIGHT = 480;" in source
        assert "Number(this.getAttribute('min-height')) || MIN_HEIGHT" in source


class TestSyntax:
    def test_no_arrow_functions_or_optional_chaining(self, source):
        # Classic script on someone else's page: keep it parseable everywhere.
        assert "=>" not in source
        assert "?." not in source

    def test_no_shadow_dom_and_no_dependencies(self, source):
        assert "attachShadow" not in source
        assert "import " not in source

    def test_stays_small(self, source):
        assert len(source.splitlines()) < 100

    @pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
    def test_node_check_passes(self):
        result = subprocess.run(
            ["node", "--check", str(ELEMENT_JS)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
