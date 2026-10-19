"""The shipped widget source: session-cookie auth, studio-scoped API base.

Text assertions on static/assistant.js for the contracts a browser run cannot
see (no bearer token, the API base, the stylesheet breakpoints, cross-module
selectors). Behavior is covered by test_widget_browser.py.
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
        assert "getCookie('csrftoken')" in source
        assert "X-CSRFToken" in source

    def test_csrf_header_only_on_mutating_methods(self, source):
        assert "method !== 'GET' && method !== 'HEAD'" in source


class TestContextDiscovery:
    def test_api_base_derived_from_studio_path(self, source):
        assert "^\\/s\\/([a-z0-9-]+)\\/([a-z0-9-]+)(?:\\/|$)" in source
        assert "'/s/' + _ctx[1] + '/' + _ctx[2] + '/api/assistant'" in source

    def test_all_endpoints_hang_off_the_api_base(self, source):
        for endpoint in ("/available", "/sessions"):
            assert f"API_BASE + '{endpoint}'" in source
        assert "'/api/assistant/sessions'" not in source
        assert "'/api/assistant/available'" not in source


class TestLayout:
    def test_breakpoints_match_the_stylesheet(self, source):
        assert "var BP_SHEET = 640;" in source
        assert "var BP_DOCK = 1024;" in source
        css = (settings.BASE_DIR / "static" / "assistant.css").read_text(encoding="utf-8")
        assert "@media (max-width: 640px)" in css
        assert "@media (max-width: 600px)" not in css


class TestSyntax:
    @pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
    def test_node_check_passes(self):
        result = subprocess.run(
            ["node", "--check", str(ASSISTANT_JS)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr


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
