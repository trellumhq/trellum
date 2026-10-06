"""The shipped widget source: session-cookie auth, studio-scoped API base.

These are text assertions on static/assistant.js rather than browser tests — the
file is served verbatim inside the /api/assistant/widget.js bundle, so a
regression here silently breaks every built report page.
"""
import shutil
import subprocess

import pytest
from django.conf import settings

ASSISTANT_JS = settings.BASE_DIR / "static" / "assistant.js"


@pytest.fixture(scope="module")
def source() -> str:
    return ASSISTANT_JS.read_text(encoding="utf-8")


class TestAuth:
    def test_no_bearer_token_logic_left(self, source):
        # Both spellings: the bearer token was ripped out under its old name,
        # and must not reappear under the new one.
        assert "bi_portal_token" not in source
        assert "trellum_portal_token" not in source
        assert "Authorization" not in source
        assert "Bearer" not in source

    def test_uses_session_cookie_and_csrf(self, source):
        assert "credentials = 'same-origin'" in source
        assert "function getCookie(" in source
        assert "getCookie('csrftoken')" in source
        assert "X-CSRFToken" in source

    def test_csrf_header_only_on_mutating_methods(self, source):
        assert "method !== 'GET' && method !== 'HEAD'" in source

    def test_offers_a_login_link_on_401(self, source):
        assert "r.status === 401" in source
        assert "appendLoginPrompt" in source
        assert "/login?next=" in source


class TestContextDiscovery:
    def test_api_base_derived_from_studio_path(self, source):
        assert "^\\/s\\/([a-z0-9-]+)\\/([a-z0-9-]+)(?:\\/|$)" in source
        assert "'/s/' + _ctx[1] + '/' + _ctx[2] + '/api/assistant'" in source

    def test_stays_hidden_without_a_studio_context(self, source):
        assert "if (!API_BASE) return;" in source

    def test_all_endpoints_hang_off_the_api_base(self, source):
        for endpoint in ("/available", "/sessions"):
            assert f"API_BASE + '{endpoint}'" in source
        assert "'/api/assistant/sessions'" not in source
        assert "'/api/assistant/available'" not in source

    def test_localstorage_keys_unchanged(self, source):
        assert "var LS_SESSION = 'assistant_session_id';" in source


class TestProposals:
    def test_proposal_frames_render_a_card(self, source):
        assert "case 'proposal':" in source
        assert "function appendProposal(" in source
        # ... and a stored transcript replays it.
        assert "entry.role === 'tool' && entry.proposal" in source

    def test_secrets_are_masked_inputs_posted_to_the_approve_endpoint(self, source):
        assert "inp.type = 'password'" in source
        assert "API_BASE + '/proposals/'" in source
        assert "decide('approve', body)" in source
        assert "decide('reject', { reason: reason })" in source

    def test_a_decision_becomes_the_next_user_turn(self, source):
        assert "sendText(j.message)" in source
        assert "function sendText(text)" in source
        assert "var LS_OPEN = 'assistant_open';" in source


class TestPlacementAndLayout:
    def test_ask_button_mounts_into_the_page_header(self, source):
        # Report pages: .fw-header-right, in front of the Options menu wrap
        # that report_menu.js owns; dashboard: the legacy tools cluster or
        # the current console header, immediately before the account menu.
        assert "document.querySelector('.fw-header-right')" in source
        assert "document.querySelector('.shell-tools, .tl-console-header')" in source
        assert "tools.insertBefore(ask, account || null)" in source
        assert "byId('fwOptionsWrap')" in source
        assert "ask.id = 'assistantAsk';" in source
        assert "<span>Ask AI</span>" in source

    def test_breakpoints_match_the_stylesheet(self, source):
        assert "var BP_SHEET = 640;" in source
        assert "var BP_DOCK = 1024;" in source
        css = (settings.BASE_DIR / "static" / "assistant.css").read_text(encoding="utf-8")
        assert "@media (max-width: 640px)" in css
        assert "@media (max-width: 600px)" not in css

    def test_docking_pads_the_body_and_reflows_charts(self, source):
        assert "document.body.style.paddingRight = pad;" in source
        assert "'--assistant-w'" in source
        assert "window.dispatchEvent(new Event('resize'))" in source

    def test_keyboard_shortcut_and_escape(self, source):
        assert "ev.key === '/' && (ev.ctrlKey || ev.metaKey)" in source
        assert "ev.key === 'Escape' && isOpen()" in source

    def test_hosted_report_uses_the_persistent_console_widget(self, source):
        assert "HOSTED_BY_CONSOLE" in source
        assert "window.parent.TrellumConsoleReportHost.contextFor(window)" in source
        assert "mountSectionAsks(_hostedDocument)" in source

    def test_mobile_sheet_preserves_console_report_history(self, source):
        assert "Object.assign({}, history.state || {}," in source
        assert "assistantSheet: true" in source


class TestA11y:
    def test_panel_is_a_labelled_dialog(self, source):
        assert "panel.setAttribute('role', 'dialog');" in source
        assert "panel.setAttribute('aria-label', 'AI assistant');" in source

    def test_messages_are_a_live_region(self, source):
        assert 'id="assistantMessages" aria-live="polite"' in source

    def test_opener_reports_expanded_state(self, source):
        assert "ask.setAttribute('aria-expanded'" in source


class TestStreaming:
    def test_sse_comment_lines_are_skipped(self, source):
        assert "l.charAt(0) !== ':'" in source

    def test_stop_aborts_the_fetch(self, source):
        assert "new AbortController()" in source
        assert "signal: signal" in source
        assert "e.name === 'AbortError'" in source

    def test_error_codes_have_copy(self, source):
        assert "code === 'deadline'" in source
        assert "A reply is still streaming in another tab." in source
        assert "status === 409" in source

    def test_stale_tool_label_removed(self, source):
        assert "run_query" not in source


class TestProvenanceAndHistory:
    def test_provenance_footer(self, source):
        assert "ev.provenance" in source
        assert "entry.provenance" in source
        assert "How I got this" in source
        assert "Open in report" in source

    def test_links_stay_on_this_origin(self, source):
        assert "function sameOrigin(href)" in source
        assert "!sameOrigin(href)) return label;" in source

    def test_available_probe_carries_the_page(self, source):
        assert "return '?path=' + encodeURIComponent(contextPath())" in source
        assert "'&title=' + encodeURIComponent((_hostedDocument" in source
        assert "j.can_configure && j.settings_url" in source

    def test_pins_key_and_per_studio_session_map(self, source):
        assert "var LS_PINS = 'assistant_pins';" in source
        assert "var STUDIO_KEY = _ctx ? _ctx[1] + '/' + _ctx[2] : '';" in source
        assert "legacy[STUDIO_KEY] = raw;" in source

    def test_report_bound_sessions_are_keyed_and_created_for_current_report(self, source):
        assert "function reportKey(slug)" in source
        assert "body: JSON.stringify({ report: _contextReport || '' })" in source
        assert "s.scope === 'report' && s.report !== _contextReport" in source
        assert "s.report === _contextReport" in source

    def test_console_report_change_refreshes_access_and_context(self, source):
        assert "if (sessionScope === 'report'" in source
        assert "refreshAvailability();" in source
        assert "path: contextPath()" in source
        assert "_hostedDocument.location.pathname" in source

    def test_history_endpoint_and_chart_context(self, source):
        assert "{ method: 'DELETE' }" in source
        assert "'/feedback'" not in source  # the thumbs are gone; nothing read them
        assert "page.chart = _chart" in source


class TestSyntax:
    @pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
    def test_node_check_passes(self):
        result = subprocess.run(
            ["node", "--check", str(ASSISTANT_JS)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_bundle_contains_the_current_source(self, client, db, source):
        body = client.get("/api/assistant/widget.js").content.decode()
        assert "getCookie" in body
        assert "bi_portal_token" not in body
        assert "trellum_portal_token" not in body

    def test_mobile_setup_link_is_icon_sized(self):
        css = (settings.BASE_DIR / "static" / "assistant.css").read_text(encoding="utf-8")
        assert "@media (max-width: 767px)" in css
        assert ".assistant-setup span { display: none; }" in css
        assert "width: 44px; height: 44px" in css


class TestChromeHidersTrackTheMountedIds:
    """Two places hide the assistant for a viewer who must not see it: the
    share/embed serve (apps.reports.views._SHARE_HIDE_CHROME_CSS) and the
    emailed snapshot (apps.reports.snapshot._CHROME_HIDE_CSS). Both name the
    widget's ids as literal selectors, and neither imports anything from this
    app, so a rename here silently turns both into rules that match nothing.

    That has already happened once: the v2 widget replaced a single
    ``#assistantLauncher`` bubble with ``#assistantAsk`` and ``#assistantPill``
    and both selector lists kept naming the id that no longer existed, while
    their tests passed because the fixture fabricated that very element.

    Hiding is the second layer -- the widget only unhides itself once
    /available answers yes, which an anonymous visitor never gets -- but an
    embedded report is meant to be chromeless inside a customer's page
    whatever that check answers, so the second layer has to be real.
    """

    #: `el.id = 'assistantX'` -- how the widget names what it appends to the
    #: body. Ids inside the panel's innerHTML are written `id="..."` and are
    #: deliberately not matched: hiding the panel hides them with it.
    MOUNTED_ID_RE = r"\.id = '(assistant[A-Za-z]+)'"

    def test_every_mounted_id_is_hidden_on_shares_and_snapshots(self, source):
        import re

        from apps.reports.snapshot import _CHROME_HIDE_CSS
        from apps.reports.views import _SHARE_HIDE_CHROME_CSS

        mounted = set(re.findall(self.MOUNTED_ID_RE, source))
        assert mounted, "no mounted ids found -- the assignment style changed"
        for name in mounted:
            assert f"#{name}" in _SHARE_HIDE_CHROME_CSS, (
                f"#{name} is mounted on the page but a share/embed serve does "
                f"not hide it: add it to _SHARE_HIDE_CHROME_CSS."
            )
            assert f"#{name}" in _CHROME_HIDE_CSS, (
                f"#{name} is mounted on the page but an emailed snapshot does "
                f"not hide it: add it to _CHROME_HIDE_CSS."
            )
