"""Loading feedback is opt-in: templates mark the links and GET forms that open console pages.

static/console-shell.js shows "Loading <page>…" only for elements inside a ``data-console-nav`` region. Downloads,
exports, report pages and APIs must never be marked, because their response does not replace the page.
"""
import re
from html.parser import HTMLParser

import pytest

pytestmark = pytest.mark.django_db

VOID = {"meta", "link", "input", "br", "img", "hr", "source", "col", "area", "base", "wbr", "path", "circle", "line", "rect"}


class Links(HTMLParser):
    """Every <a>/<form> with its attributes and whether it sits inside (or carries) data-console-nav."""

    def __init__(self):
        super().__init__()
        self.stack, self.items = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in VOID:
            return
        if tag in ("a", "form"):
            marked = "data-console-nav" in attrs or any("data-console-nav" in open_attrs for _, open_attrs in self.stack)
            self.items.append((tag, attrs, marked))
        self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def links(response):
    parser = Links()
    parser.feed(response.content.decode())
    return parser.items


def target(tag, attrs):
    return (attrs.get("href") if tag == "a" else attrs.get("action")) or ""


def test_shell_navigation_and_in_page_console_links_are_marked(client, login, org, org_admin, studio_tree, report_row):
    login(org_admin)
    org_page = links(client.get(f"/orgs/{org.slug}/settings/members"))
    sidebar = [item for item in org_page if "tl-console-nav-link" in item[1].get("class", "")]
    assert len(sidebar) >= 8 and all(marked for _, _, marked in sidebar)
    assert any(target(tag, attrs).endswith("/settings/invites") and marked and "ui-btn" in attrs.get("class", "")
               for tag, attrs, marked in org_page), "in-page console links are marked too"
    studio_page = links(client.get(f"/s/{org.slug}/{studio_tree.slug}/analytics"))
    assert all(marked for _, attrs, marked in studio_page if "tl-console-nav-link" in attrs.get("class", ""))


@pytest.mark.parametrize("path, expects_excluded_link", [
    ("/orgs/{org}/settings/audit", True),
    ("/s/{org}/{studio}/analytics", False),
    ("/s/{org}/{studio}/experiments", False),
    ("/s/{org}/{studio}/alerts", False),
])
def test_downloads_exports_and_report_pages_are_never_marked(
    client, login, org, org_admin, studio_tree, report_row, path, expects_excluded_link
):
    login(org_admin)
    response = client.get(path.format(org=org.slug, studio=studio_tree.slug))
    assert response.status_code == 200
    excluded = [
        (tag, attrs, marked) for tag, attrs, marked in links(response)
        if re.search(r"(export|download)(\.\w+)?/?$|/api/|/r/[^/]+/?$", target(tag, attrs).split("?")[0])
    ]
    assert excluded or not expects_excluded_link, "the page was expected to link to an export"
    assert not any(marked for _, _, marked in excluded), excluded


def test_pages_outside_the_console_mark_nothing(client):
    assert not any(marked for _, _, marked in links(client.get("/login")))
