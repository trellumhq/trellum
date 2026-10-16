"""One pagination block for every JSON list."""
from django.core.paginator import Paginator

from apps.core.pagination import page_metadata, paginator_metadata


def test_the_block_names_the_neighbours_that_exist():
    assert page_metadata(1, 25, 30) == {"page": 1, "per_page": 25, "total": 30, "has_previous": False, "has_next": True}
    assert page_metadata(2, 25, 30)["has_next"] is False and page_metadata(2, 25, 30)["has_previous"] is True
    assert page_metadata(1, 25, 0)["has_next"] is False
    assert page_metadata(1, 25, 25)["has_next"] is False


def test_a_django_page_gives_the_same_block():
    for total in (0, 1, 25, 26, 51):
        for number in (1, 2, 3):
            page = Paginator(list(range(total)), 25).get_page(number)
            assert paginator_metadata(page) == page_metadata(page.number, 25, total)


def test_every_list_builder_is_that_one_helper():
    from apps.orgs.groups import presentation
    from apps.reports import views

    assert views._page_metadata is paginator_metadata
    assert presentation.pagination is paginator_metadata
