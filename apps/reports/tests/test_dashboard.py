"""The dashboard shell renders with the context blob for portal.js."""
import json

import pytest

from apps.core import roles

pytestmark = pytest.mark.django_db


@pytest.fixture
def viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("view@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


def test_dashboard_renders_with_ctx(login, viewer, org, studio_tree, report_row):
    resp = login(viewer).get(f"/s/{org.slug}/{studio_tree.slug}/")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'id="portal-ctx"' in html
    start = html.index('id="portal-ctx"')
    blob = html[html.index(">", start) + 1 : html.index("</script>", start)]
    ctx = json.loads(blob)
    assert ctx["prefix"] == f"/s/{org.slug}/{studio_tree.slug}"
    assert ctx["user"]["role"] == "viewer"
    assert ctx["studio"]["slug"] == studio_tree.slug
    # CSRF cookie is guaranteed for portal.js.
    assert "csrftoken" in resp.cookies


def test_dashboard_hidden_from_non_members(login, make_user, org, studio_tree):
    outsider = make_user("out@else.com")
    assert login(outsider).get(f"/s/{org.slug}/{studio_tree.slug}/").status_code == 404


def test_static_assets_referenced(login, viewer, org, studio_tree, report_row):
    html = login(viewer).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
    assert "portal.js" in html
    assert "portal.css" in html
