"""Onboarding batch matches declaration readiness without report materialization."""
from dataclasses import asdict

import pytest

from apps.datasources.models import DataSource, RepoDataSource
from apps.datasources.status import SourceState, declared_readiness_summary, source_states
from apps.reports.models import Report
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


def test_declared_batch_matches_files_uploads_credentials_and_scoped_shadow(org, studio, django_assert_num_queries):
    other = Studio.objects.create(org=org, slug="second", name="Second")
    for target in [studio, other]:
        for name, typ, cfg, error in [
            ("file", "csv", {"path": "data/file.csv"}, ""),
            ("upload", "csv", {"upload": True}, ""),
            ("refused", "csv", {"path": "../file.csv"}, "outside project"),
            ("credentials", "postgres", {"host": "db"}, ""),
            ("shadow", "postgres", {"host": "db"}, ""),
            ("conflict", "postgres", {"host": "db"}, ""),
        ]:
            RepoDataSource.objects.create(studio=target, name=name, type=typ, config=cfg, file_error=error, source_file="data-sources/config.yaml")
        Report.objects.create(studio=target, slug="usage", config={"data_sources": ["credentials", "not-declared"]})
    DataSource.objects.create(org=org, name="shadow", type="postgres", credentials={"user": "u", "password": "secret"})
    DataSource.objects.create(studio=other, name="shadow", type="postgres", credentials={"user": "u"})
    DataSource.objects.create(org=org, name="conflict", type="csv", credentials={"user": "u", "password": "secret"})
    expected = [(target, state) for target in sorted([studio, other], key=lambda item: item.pk)
                for state in source_states(target) if state.declared]
    waiting = [(target, state) for target, state in expected if state.state in (SourceState.NEEDS_UPLOAD, SourceState.NEEDS_CREDENTIALS)]
    with django_assert_num_queries(2):
        summary = declared_readiness_summary(org)
    assert summary.declared_count == len(expected)
    assert summary.waiting_count == len(waiting)
    assert summary.first_waiting_studio_slug == waiting[0][0].slug
    assert set(asdict(summary)) == {"declared_count", "waiting_count", "first_waiting_studio_slug"}
    assert "secret" not in repr(summary)
