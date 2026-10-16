"""Bounded datasource lists: SQL paging, shadowing, search, banner, unknowns."""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.core import roles
from apps.datasources import catalog
from apps.datasources.models import DataSource, RepoDataSource
from apps.reports.models import Report

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(make_user, org, studio_tree, grant_studio):
    user = make_user("adm@demo.example", org=org)
    grant_studio(user, studio_tree, roles.ADMIN)
    return user


def _names(resp):
    return [r["name"] for r in resp.context["rows"]]


def test_studio_list_shadowing_search_and_unknowns(login, admin, org, studio_tree):
    DataSource.objects.create(org=org, name="warehouse", type="postgres", config={"host": "shared"},
                              description="shared one")
    DataSource.objects.create(studio=studio_tree, name="warehouse", type="mysql", config={"host": "own"})
    DataSource.objects.create(org=org, name="Zeta", type="sqlite", config={})
    RepoDataSource.objects.create(studio=studio_tree, name="declared", type="clickhouse", config={})
    Report.objects.create(studio=studio_tree, slug="r", config={"data_sources": ["warehouse", {"name": "ghost"}, "declared"]})
    url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
    resp = login(admin).get(url)
    assert _names(resp) == ["Zeta", "declared", "warehouse"]
    row = {r["name"]: r for r in resp.context["rows"]}
    assert row["warehouse"]["state"].type == "mysql" and row["warehouse"]["used_by"] == 1
    assert [s.name for s in resp.context["unknown"]] == ["ghost"]
    assert resp.context["unknown"][0].used_by == ["r"]
    assert _names(login(admin).get(url + "?q=click")) == ["declared"]      # type, via declaration
    assert _names(login(admin).get(url + "?q=SHARED")) == []               # shadowed description
    assert resp.context["total"] == 3


def test_banner_counts_without_listing_reports(login, admin, org, studio_tree):
    for n in ("a", "b"):
        RepoDataSource.objects.create(studio=studio_tree, name=n, type="postgres", config={})
    Report.objects.create(studio=studio_tree, slug="r1", config={"data_sources": ["a"]})
    Report.objects.create(studio=studio_tree, slug="r2", config={"data_sources": ["a", "b"]})
    Report.objects.create(studio=studio_tree, slug="gone", present_in_scan=False, config={"data_sources": ["a"]})
    banner = catalog.banner(studio_tree)
    assert (banner["count"], banner["waiting"], banner["noun"]) == (2, 2, "credentials")


def test_org_list_batches_declarations(login, org_admin, org, studio_tree, studio2):
    for i in range(5):
        DataSource.objects.create(org=org, name=f"s{i}", type="postgres", config={})
    for st in (studio_tree, studio2):
        RepoDataSource.objects.create(studio=st, name="s1", type="postgres", config={})
    resp = login(org_admin).get(f"/orgs/{org.slug}/settings/datasources")
    assert {r["name"]: r["declared_by"] for r in resp.context["rows"]}["s1"] == 2
    with CaptureQueriesContext(connection) as ctx:
        catalog.org_declarations(org, [f"s{i}" for i in range(5)])
    assert len(ctx.captured_queries) == 1  # one batch, not one lookup per binding


@pytest.mark.heavy
def test_lists_stay_bounded_at_1000_bindings_and_reports(login, admin, org, studio_tree):
    DataSource.objects.bulk_create(
        DataSource(org=org, name=f"shared{i:04d}", type="postgres", config={"host": "h"}) for i in range(1000))
    RepoDataSource.objects.bulk_create(
        RepoDataSource(studio=studio_tree, name=f"decl{i:04d}", type="postgres", config={}) for i in range(1000))
    Report.objects.bulk_create(
        Report(studio=studio_tree, slug=f"r{i:04d}", config={"data_sources": [f"decl{i:04d}", f"missing{i:04d}"]})
        for i in range(1000))
    url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
    client = login(admin)
    resp = client.get(url)
    assert len(resp.context["rows"]) == 50 and resp.context["total"] == 2000
    assert len(resp.context["unknown"]) == catalog.UNKNOWN_PAGE and resp.context["unknown_more"]
    assert resp.context["banner"]["count"] == 1000 and len(resp.context["banner"]["sources"]) == catalog.BANNER_SOURCES
    connection.queries_log.clear()  # the bounded log may be full after seeding
    with CaptureQueriesContext(connection) as ctx:  # the view's catalog reads
        names = list(catalog.studio_names(studio_tree)[:50])
        catalog.states_for(studio_tree, names)
        catalog.unknown_names(studio_tree)
        catalog.banner(studio_tree)
    assert 0 < len(ctx.captured_queries) <= 12  # constant in the number of sources
    print("heavy: catalog queries =", len(ctx.captured_queries))
    api = client.get(f"/s/{org.slug}/{studio_tree.slug}/api/datasources?page=1").json()
    assert len(api["sources"]) == 50 and api["total"] == 1000 and api["has_next"]
    # Without paging parameters the API keeps returning the whole list, as
    # before paging existed, in a number of queries that does not grow with it.
    with CaptureQueriesContext(connection) as ctx:
        api = client.get(f"/s/{org.slug}/{studio_tree.slug}/api/datasources").json()
    assert len(api["sources"]) == api["total"] == 1000 and not api["has_next"]
    assert len(ctx.captured_queries) <= 20, len(ctx.captured_queries)
