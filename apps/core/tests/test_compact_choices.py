"""Packet decoding preserves native edits and rejects malformed transport."""
import json

import pytest
from django.http import QueryDict

from apps.core.compact_choices import CompactCheckboxSelectMultiple, decode_compact_selection


def data(packet, checked=()):
    result = QueryDict(mutable=True)
    result["reports__selection"] = json.dumps(packet)
    result.setlist("reports", checked)
    return result


def test_page_uncheck_and_offpage_retention():
    result = decode_compact_selection(data({"version": 1, "selected": ["1", "2", "3"], "editable": ["1", "2"]}, ["2", "4"]), "reports")
    assert result == ["3", "2", "4"] and not result.error


def test_duplicates_are_deduplicated_and_empty_is_explicit():
    assert decode_compact_selection(data({"version": 1, "selected": ["3", "3"], "editable": []}, ["3"]), "reports") == ["3"]
    assert decode_compact_selection(data({"version": 1, "selected": [], "editable": []}), "reports") == []


@pytest.mark.parametrize("packet", [None, [], {}, {"version": True, "selected": [], "editable": []}, {"version": 1, "selected": "1", "editable": []}, {"version": 1, "selected": [1], "editable": []}, {"version": 1, "selected": ["bad"], "editable": []}, {"version": 1, "selected": ["0"], "editable": []}, {"version": 1, "selected": ["9" * 100], "editable": []}])
def test_invalid_packet_is_explicit_and_raw_retained(packet):
    bound = data(packet, ["2"])
    result = decode_compact_selection(bound, "reports")
    assert result.error and result == ["2"]
    assert result.packet == bound["reports__selection"]


def test_invalid_json_and_multiple_packets_rejected():
    bound = QueryDict("reports=2&reports__selection=%7B", mutable=True)
    assert decode_compact_selection(bound, "reports").error
    bound.setlist("reports__selection", ['{}', '{}'])
    assert decode_compact_selection(bound, "reports").error


def test_legacy_repeated_values_remain_accepted():
    assert decode_compact_selection(QueryDict("reports=1&reports=2"), "reports") == ["1", "2"]


def test_widget_bounds_inputs_without_truncating_selected():
    widget = CompactCheckboxSelectMultiple(choices=[(str(i), f"Report {i}") for i in range(1, 1001)])
    widget.page_values = [str(i) for i in range(1, 26)]
    context = widget.get_context("reports", [str(i) for i in range(1, 1001)], {})["widget"]
    assert sum(len(options) for _, options, _ in context["optgroups"]) == 25
    assert len(context["selected_rows"]) == 1000
    assert len(context["selected_offpage"]) == 975
    assert len(json.loads(context["selection_packet"])["selected"]) == 1000


def test_nested_json_and_oversize_packet_are_visible_errors():
    bound = QueryDict(mutable=True)
    for raw in ("[" * 2000 + "]" * 2000, " " * (1024 * 1024 + 1)):
        bound["reports__selection"] = raw
        result = decode_compact_selection(bound, "reports")
        assert result.error
        assert result.packet == raw if len(raw) <= 1024 * 1024 else result.packet == "Invalid oversized selection packet"
    assert decode_compact_selection(data({"version": 1, "selected": [], "editable": [str(i) for i in range(1, 27)]}), "reports").error
