"""Authenticated, shared and embedded report assets keep host SQL private."""
import pytest

from apps.core import roles, storage
from apps.reports.models import OrgSharePolicy, ShareLink


pytestmark = pytest.mark.django_db


@pytest.fixture
def private_output(studio_tree, report_row):
    out = studio_tree.output_dir / report_row.slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text("<html><head></head><body>public</body></html>")
    (out / "data.json").write_text("{}")
    (out / "_live_queries.json").write_text('{"queries":{"q":{"sql":"SELECT 1"}}}')
    (out / "_live_queries.json.gz").write_bytes(b"private sibling")
    return out


@pytest.mark.parametrize("surface", ["authenticated", "share", "embed"])
@pytest.mark.parametrize("method", ["get", "head"])
def test_http_manifest_denied(client, make_user, org, grant_studio, studio_tree, report_row, private_output, surface, method):
    if surface == "authenticated":
        user = make_user("private-viewer@example.com", org=org)
        grant_studio(user, studio_tree, roles.VIEWER)
        client.force_login(user)
        prefix = f"/s/{org.slug}/{studio_tree.slug}/r/{report_row.slug}/"
    else:
        OrgSharePolicy.objects.create(org=org, share_links_enabled=True, embed_links_enabled=True)
        link = ShareLink.objects.create(report=report_row, embed=surface == "embed", embed_origins=["https://embed.example.com"])
        prefix = f"/share/{link.token}/"
    for path in ("_live_queries.json", "_live_queries.json.gz", "_LIVE_QUERIES.JSON", "%5flive_queries.json", "%255flive_queries.json", "_live_queries.json%20", "_live_queries.json.", "_live_queries.json::$DATA", "_live_queries.json%3A%3A%24DATA", "_live_queries.json.gz::$DATA", "_live_queries.json.gz%3A%3A%24DATA"):
        response = getattr(client, method)(prefix + path)
        assert response.status_code == 404, (surface, method, path)
    for path in ("index.html", "data.json"):
        response = getattr(client, method)(prefix + path)
        assert response.status_code == 200
    assert storage.read_live_queries(studio_tree, report_row.slug)["queries"]["q"]["sql"] == "SELECT 1"


def test_portal_denies_resolved_manifest_symlink(private_output):
    from django.http import Http404
    from apps.reports.views import _traversal_guarded_path

    alias = private_output / "apparently-public.json"
    try:
        alias.symlink_to(private_output / "_live_queries.json")
    except OSError:
        pytest.skip("Temporary filesystem does not permit symlinks")
    with pytest.raises(Http404):
        _traversal_guarded_path(private_output.resolve(), alias.name)
