"""One shape for the pagination block of every JSON list response."""


def page_metadata(number: int, per_page: int, total: int) -> dict:
    """``page``/``per_page``/``total`` plus the neighbours that exist."""
    return {
        "page": number, "per_page": per_page, "total": total,
        "has_previous": number > 1, "has_next": number * per_page < total,
    }


def paginator_metadata(page) -> dict:
    """The same block for a Django ``Page``."""
    return page_metadata(page.number, page.paginator.per_page, page.paginator.count)
