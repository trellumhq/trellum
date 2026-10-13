"""One primary action per screen, and one button system.

A bare <button> used to default to the accent background, so every Save,
Filter and Copy shouted as loudly as the one real primary action — and
navigation links styled as buttons ended up louder than the content they sat
above. Primary is now opt-in; these tests keep it that way.
"""
import re

from django.conf import settings

# Management templates only. The dashboard (portal/index.html) has its own
# chrome in portal.css, and _shell.html's menu buttons are not page actions.
TEMPLATE_DIRS = (
    "accounts", "orgs", "studios", "datasources", "registration", "core", "operator",
)


def _templates():
    root = settings.BASE_DIR / "templates"
    for sub in TEMPLATE_DIRS:
        # rglob, not glob: the first-run wizard lives in accounts/setup/, and a
        # non-recursive scan silently exempted a whole screen flow from these
        # rules.
        for path in sorted((root / sub).rglob("*.html")):
            yield path.relative_to(root).as_posix(), path.read_text(encoding="utf-8")


def test_no_button_uses_the_legacy_classes():
    # .secondary / bare .btn were the pre-ui.css system. One system now.
    for name, html in _templates():
        assert 'class="secondary"' not in html, f"{name} uses the legacy .secondary"
        assert 'class="danger"' not in html, f"{name} uses the legacy .danger"
        assert not re.search(r'class="btn[ "]', html), f"{name} uses the legacy .btn"


def test_every_submit_button_declares_its_weight():
    # A bare <button> is neutral now, so a submit that means to be the page's
    # primary has to say so. This catches the ones that silently went quiet.
    for name, html in _templates():
        for match in re.finditer(r"<button(?![^>]*\bclass=)[^>]*>", html):
            raise AssertionError(
                f"{name}: <button> with no class — {match.group(0)!r}. "
                f"Use .ui-btn primary / ghost / danger."
            )


def test_at_most_one_primary_per_form():
    # A form has exactly one action that commits it. A page may hold several
    # independent forms (the account page edits a profile and a password), so
    # the unit is the form, not the screen.
    for name, html in _templates():
        for i, form in enumerate(re.findall(r"<form\b.*?</form>", html, re.DOTALL)):
            count = form.count("ui-btn primary")
            assert count <= 1, (
                f"{name}: form #{i + 1} has {count} primary buttons — "
                f"only the action that commits the form is primary"
            )


def test_links_are_not_dressed_as_the_primary_action():
    # Navigation is normally neutral. A page header may also carry the one
    # primary add action on a list page, even when record rows contain forms.
    # Empty-state CTAs remain primary inside their explicit empty-state wrapper.
    for name, html in _templates():
        headers = re.findall(
            r'<header class="page-head(?:-row)?">.*?</header>', html, re.DOTALL,
        )
        for header in headers:
            primary_links = re.findall(r'<a class="ui-btn primary"', header)
            assert len(primary_links) <= 1, (
                f"{name}: page header has {len(primary_links)} primary links; "
                "a list header gets one add action."
            )
        if "<form" not in html:
            continue  # terminal pages (reset complete, SSO error) — the link is the action
        for match in re.finditer(r'<a class="ui-btn primary".{0,120}', html, re.DOTALL):
            snippet = match.group(0)
            context_start = max(0, match.start() - 120)
            before = html[context_start:match.start()]
            in_page_header = (
                max(
                    html.rfind('<header class="page-head">', 0, match.start()),
                    html.rfind('<header class="page-head-row">', 0, match.start()),
                )
                > html.rfind("</header>", 0, match.start())
            )
            in_form = html.rfind("<form", 0, match.start()) > html.rfind("</form>", 0, match.start())
            assert not in_form and ("ui-empty-action" in before or in_page_header), (
                f"{name}: primary link is outside a page header or empty state — "
                f"{snippet[:80]!r}. Navigation is .ui-btn ghost."
            )
