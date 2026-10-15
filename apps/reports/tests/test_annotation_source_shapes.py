"""Malformed annotation containers become visible errors, not page failures."""
from types import SimpleNamespace

import pytest

from apps.reports.overviews.annotations import load_studio_events


@pytest.mark.parametrize("source, message", [
    ("type_defaults: [true]", "`type_defaults` must be a mapping"),
    ("type_defaults: false", "`type_defaults` must be a mapping"),
    ("type_defaults: ''", "`type_defaults` must be a mapping"),
    ("events: 123", "`events` must be a list"),
    ("events: false", "`events` must be a list"),
    ("events: {}", "`events` must be a list"),
    ("events: text", "`events` must be a list"),
    ("[]", "must be a mapping"),
    ("false", "must be a mapping"),
])
def test_invalid_container_returns_parse_error(tmp_path, source, message):
    (tmp_path / "events.yaml").write_text(source)
    events, defaults, error = load_studio_events(SimpleNamespace(project_root=tmp_path))
    assert events == [] and defaults == {}
    assert message in error


@pytest.mark.parametrize("source", ["", "{}", "events: null\ntype_defaults: null"])
def test_empty_or_null_optional_sources_remain_empty(tmp_path, source):
    (tmp_path / "events.yaml").write_text(source)
    assert load_studio_events(SimpleNamespace(project_root=tmp_path)) == ([], {}, "")


def test_historical_duplicates_scope_and_visibility_are_preserved(tmp_path):
    (tmp_path / "events.yaml").write_text("""
type_defaults: {campaign: false}
events:
  - &event {date: 2020-01-02, label: Historic, type: campaign, studio: team-a}
  - *event
  - {date: '2020-01-03', label: Override, type: campaign, default_visible: true}
""")
    events, defaults, error = load_studio_events(SimpleNamespace(project_root=tmp_path))
    assert error == "" and defaults == {"campaign": False}
    assert len(events) == 3 and events[0] == events[1]
    assert events[0]["date"] == "2020-01-02"
    assert events[0]["scope"] == "team-a" and not events[0]["visible_default"]
    assert events[2]["scope"] == "shared" and events[2]["visible_default"]
