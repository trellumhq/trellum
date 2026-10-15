"""Pickers render every choice up to the inline limit, a preview beyond it."""
from apps.core import choice_requests
from apps.core.choice_requests import inline_choices


def test_everything_inline_up_to_the_limit():
    rows, searchable = inline_choices(list(range(500)))
    assert len(rows) == 500 and not searchable


def test_preview_and_search_beyond_the_limit():
    rows, searchable = inline_choices(list(range(501)))
    assert rows == list(range(25)) and searchable


def test_limit_is_read_at_call_time(monkeypatch):
    monkeypatch.setattr(choice_requests, "INLINE_CHOICE_LIMIT", 3)
    assert inline_choices([1, 2, 3, 4]) == ([1, 2, 3, 4][:25], True)
