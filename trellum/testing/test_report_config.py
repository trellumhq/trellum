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


@pytest.mark.parametrize("format", ["article", "presentation"])
def test_analysis_formats_are_valid(format):
    config = {"kind": "analysis", "format": format}
    assert validate_content_config(config) is config


@pytest.mark.parametrize("format", [None, True, "slides", "", [], {}])
def test_invalid_analysis_format_is_rejected(format):
    with pytest.raises(ValueError, match="format"):
        validate_content_config({"kind": "analysis", "format": format})


@pytest.mark.parametrize("view", ["document", "slides"])
def test_presentation_view_is_opt_in(view):
    config = {"kind": "analysis", "format": "presentation", "presentation_view": view}
    assert validate_content_config(config) is config
    default = {"kind": "analysis", "format": "presentation"}
    assert "presentation_view" not in validate_content_config(default)


@pytest.mark.parametrize("value", [None, True, "Slides", "", "custom", [], {}])
def test_invalid_presentation_view_is_rejected(value):
    with pytest.raises(ValueError, match="presentation_view"):
        validate_content_config({"kind": "analysis", "format": "presentation", "presentation_view": value})


@pytest.mark.parametrize("config", [{}, {"kind": "report"},
                                   {"kind": "report", "format": "presentation"},
                                   {"kind": "analysis"}, {"kind": "analysis", "format": "article"}])
@pytest.mark.parametrize("view", ["document", "slides"])
def test_presentation_view_cannot_be_used_for_other_content(config, view):
    with pytest.raises(ValueError, match="requires kind: analysis and format: presentation"):
        validate_content_config({**config, "presentation_view": view})
