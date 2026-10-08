"""Reject ambiguous initial access intent before discovering or building content."""

import pytest

from trellum.report_config import validate_content_config


@pytest.mark.parametrize("kind", [None, "report", "analysis"])
@pytest.mark.parametrize("audience", ["studio", "private"])
def test_initial_audience_is_valid_for_both_content_kinds(kind, audience):
    config = {"initial_audience": audience}
    if kind is not None:
        config["kind"] = kind
    assert validate_content_config(config) is config


@pytest.mark.parametrize("kind", ["report", "analysis"])
def test_omitted_audience_preserves_host_default(kind):
    config = {"kind": kind}
    assert "initial_audience" not in validate_content_config(config)


@pytest.mark.parametrize("value", [None, True, False, "public", "Private", "", [], {}])
@pytest.mark.parametrize("kind", ["report", "analysis"])
def test_invalid_initial_audience_is_rejected(kind, value):
    with pytest.raises(ValueError, match="initial_audience"):
        validate_content_config({"kind": kind, "initial_audience": value})
