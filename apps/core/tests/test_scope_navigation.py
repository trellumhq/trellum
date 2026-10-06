from pathlib import Path


ROOT = Path(__file__).parents[3]


def read_template(name):
    return (ROOT / "templates" / name).read_text()


def test_organization_navigation_has_single_scope_heading_and_studios_management_target():
    nav = read_template("_org_nav.html")

    assert nav.count('>Organization<') == 1
    assert "Manage studios" not in nav
    assert "or request.resolver_match.url_name == 'org-studios'" in nav


def test_shell_docs_link_is_permanent_and_accessible_on_mobile():
    shell = read_template("_shell.html")

    assert "{% load static docs %}" in shell
    assert 'class="tl-console-docs-link"' in shell
    assert 'target="_blank" rel="noopener"' in shell
    assert 'aria-label="Documentation"' in shell


def test_scope_styles_keep_security_at_bottom_and_mobile_docs_compact():
    css = (ROOT / "static" / "console-shell.css").read_text()

    assert ".tl-console-nav-scope-heading" in css
    assert '[data-console-nav-group="org-security"] { margin-top: auto; }' in css
    assert ".tl-console-docs-label { position: absolute" in css
